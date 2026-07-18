"""Recovery Recommendation Agent — AIOC Agent #7.

Dedicated agent that generates and prioritises recovery actions based on the
confirmed root cause, business impact, and deployment analysis. This is
intentionally separate from the Executive Summary agent:

  - Recovery Recommendation Agent = what engineers should DO (ordered, actionable steps)
  - Executive Summary Agent       = what leadership should KNOW (narrative, context)

Keeping them separate allows each to be independently re-generated, reviewed,
or approved without conflating operational response with communications.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from agents import IncidentState
from agents.llm import complete_json, get_model, llm_available
from agents.telemetry import Timer, record_invocation

RECOVERY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "recovery_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "integer"},
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                    "risk_level": {"type": "string"},
                    "estimated_impact": {"type": "string"},
                    "requires_approval": {"type": "boolean"},
                },
                "required": ["priority", "action", "rationale", "risk_level", "estimated_impact", "requires_approval"],
                "additionalProperties": False,
            },
        },
        "rollback_recommended": {"type": "boolean"},
        "rollback_rationale": {"type": "string"},
        "safety_checks": {"type": "array", "items": {"type": "string"}},
        "escalation_trigger": {"type": "string"},
    },
    "required": ["recovery_steps", "rollback_recommended", "rollback_rationale", "safety_checks", "escalation_trigger"],
    "additionalProperties": False,
}


# --- Heuristic fallback rules ---

_HYPOTHESIS_PLAYBOOKS: Dict[str, List[str]] = {
    "db pool exhaustion": [
        "Increase database connection pool size to restore capacity (verify DB can handle the load first)",
        "Restart affected service pods to release stale connections",
        "Roll back the deployment that reduced pool size if a recent deploy is confirmed",
        "Set connection pool monitoring alert at 80% utilisation",
        "Review and increase pool size ceiling in service configuration",
    ],
    "memory leak": [
        "Restart affected worker processes to release leaked memory",
        "Roll back the code change that introduced the memory regression",
        "Enable heap dump collection for post-incident analysis",
        "Add memory saturation alert at 85% heap usage",
        "Review recent code commits for objects not being garbage-collected",
    ],
    "cache stampede": [
        "Enable cache coalescing (request collapsing) to prevent thundering herd",
        "Pre-warm cache for hot keys before traffic ramp-up",
        "Roll back the deployment that disabled cache coalescing",
        "Add cache hit-rate monitoring and alert on sudden drops",
        "Tune TTL values to stagger expiry windows",
    ],
    "retry storm": [
        "Apply circuit breaker to the upstream dependency",
        "Reduce retry count and add exponential back-off with jitter",
        "Scale up the affected service to absorb retry load",
        "Roll back the retry configuration change if recently deployed",
        "Add retry rate monitoring per client",
    ],
    "timeout": [
        "Increase timeout thresholds to match observed p99 latency",
        "Scale up the dependency that is responding slowly",
        "Enable request hedging for latency-sensitive calls",
        "Roll back any timeout config change from recent deployment",
        "Add distributed tracing to identify slow span origins",
    ],
    "traffic surge": [
        "Enable horizontal auto-scaling for the affected service",
        "Apply rate limiting at the edge to protect downstream services",
        "Activate CDN caching for cacheable endpoints to reduce origin load",
        "Scale up capacity in the affected service tier immediately",
        "Review capacity plan against current traffic growth rate",
    ],
}

_HIGH_RISK_KEYWORDS = {"rollback", "restart", "failover", "disable", "drain", "scale", "traffic"}
_DEPLOYMENT_ROLLBACK_KEYWORDS = {"pool", "config", "flag", "limit", "timeout", "connection"}


def _heuristic_steps(state: IncidentState) -> List[Dict[str, Any]]:
    """Return ordered, risk-tagged recovery steps from pattern matching."""
    hypothesis = str((state.root_cause or {}).get("hypothesis", "")).lower()
    steps: List[str] = []

    for key, playbook in _HYPOTHESIS_PLAYBOOKS.items():
        if key in hypothesis:
            steps = playbook
            break

    if not steps:
        steps = [
            "Identify and isolate the affected component",
            "Review recent deployment changes for correlation",
            "Check upstream dependency health dashboards",
            "Scale up the affected service if resource saturation is detected",
            "Follow service runbook for standard recovery procedures",
        ]

    result: List[Dict[str, Any]] = []
    for i, step in enumerate(steps, 1):
        step_lower = step.lower()
        risk = "high" if any(kw in step_lower for kw in _HIGH_RISK_KEYWORDS) else "low"
        result.append({
            "priority": i,
            "action": step,
            "rationale": f"Derived from '{hypothesis}' root cause pattern",
            "risk_level": risk,
            "estimated_impact": "Reduces incident severity" if i == 1 else "Prevents recurrence",
            "requires_approval": risk == "high",
        })
    return result


def _rollback_recommended(state: IncidentState) -> tuple[bool, str]:
    """Determine if rollback is recommended based on deployment correlation."""
    dep_analysis = getattr(state, "deployment_analysis", {}) or {}
    trigger_deps = dep_analysis.get("trigger_deployments", [])
    risky_deps = dep_analysis.get("risky_deployments", [])

    if trigger_deps and risky_deps:
        top = trigger_deps[0]
        risky_changes = top.get("risky_changes", [])
        change = risky_changes[0] if risky_changes else "configuration change"
        return True, (
            f"Deployment {top.get('version', 'unknown')} ({change}) was pushed "
            f"{top.get('minutes_before_incident', '?')} minute(s) before the incident. "
            f"Rolling back is recommended to restore the known-good state."
        )
    if trigger_deps:
        top = trigger_deps[0]
        return True, (
            f"Deployment {top.get('version', 'unknown')} closely preceded the incident. "
            f"Rollback is a safe first-response option while root cause is confirmed."
        )
    return False, "No deployment closely correlated with the incident. Manual investigation required before rollback."


def _safety_checks(state: IncidentState) -> List[str]:
    """Generate safety checks based on rollback plan and service context."""
    checks: List[str] = []
    rollback = getattr(state, "rollback_plan", {}) or {}
    if rollback.get("safety_check"):
        checks.append(rollback["safety_check"])
    checks += [
        "Confirm DB connection pool utilisation < 80% before declaring resolved",
        f"Monitor error rate for {state.service} for 10 minutes post-recovery",
        "Verify all upstream consumers are receiving healthy responses",
        "Confirm no secondary alerts have fired on dependent services",
    ]
    return checks[:5]


async def recovery_recommendations(state: IncidentState) -> IncidentState:
    """Dedicated Recovery Recommendation Agent.

    Generates and risk-ranks recovery actions from RCA + business impact +
    deployment findings. Uses LLM for nuanced step generation when available,
    falls back to hypothesis-pattern playbooks.
    """
    t = Timer.begin()
    steps: List[Dict[str, Any]] = []
    source = "heuristic_fallback"
    rollback_rec, rollback_rat = _rollback_recommended(state)

    if llm_available():
        dep_analysis = getattr(state, "deployment_analysis", {}) or {}
        prompt = (
            "You are a senior SRE generating a prioritised recovery plan for a production incident.\n\n"
            f"Service: {state.service}\n"
            f"Severity: {state.severity}\n"
            f"Root cause: {(state.root_cause or {}).get('hypothesis', 'Unknown')}\n"
            f"Confidence: {state.rca_confidence:.0%}\n"
            f"Affected users: {state.affected_users:,}\n"
            f"Revenue impact: ${state.estimated_revenue_impact_per_minute:.2f}/minute\n"
            f"Deployment correlation: {dep_analysis.get('correlation_summary', 'Not analyzed')}\n"
            f"Deployment risk: {dep_analysis.get('overall_risk', 'unknown')}\n"
            f"Rollback strategy: {(getattr(state, 'rollback_plan', {}) or {}).get('strategy', 'Not defined')}\n"
            f"Supporting evidence: {(state.root_cause or {}).get('supporting_evidence', [])}\n\n"
            "Generate 4-6 specific, actionable recovery steps ordered by priority. "
            "Mark high-risk steps (rollback, restart, failover) as requiring_approval=true. "
            "Include a rollback recommendation, safety checks, and escalation trigger condition."
        )
        try:
            result = await complete_json(
                system="You are an expert SRE. Generate precise recovery steps grounded in the evidence.",
                prompt=prompt,
                schema=RECOVERY_SCHEMA,
                schema_name="recovery_plan",
            )
            steps = result.get("recovery_steps", [])
            rollback_rec = result.get("rollback_recommended", rollback_rec)
            rollback_rat = result.get("rollback_rationale", rollback_rat)
            safety = result.get("safety_checks", [])
            escalation = result.get("escalation_trigger", "")
            source = f"llm:{get_model()}"
        except Exception as exc:
            print(f"[recovery_recommendations] LLM call failed, using heuristic: {exc}")
            steps = []

    if not steps:
        steps = _heuristic_steps(state)
        safety = _safety_checks(state)
        escalation = (
            f"Escalate to {(state.escalation_path or ['incident-commander'])[-1]} "
            f"if error rate exceeds 50% or revenue impact exceeds ${state.estimated_revenue_impact_per_minute * 2:.0f}/minute"
        )

    # Store as flat list of action strings (backward compatible) + structured plan
    state.recovery_recommendations = [step["action"] for step in steps]
    state.recovery_plan = {
        "steps": steps,
        "rollback_recommended": rollback_rec,
        "rollback_rationale": rollback_rat,
        "safety_checks": safety,
        "escalation_trigger": escalation,
        "generated_by": source,
    }
    state.completed_steps.add("recovery_recommendations")
    state.current_status = "recovery_planned"

    high_risk_steps = [s for s in steps if s.get("requires_approval")]
    record_invocation(
        state,
        agent="recovery_recommendation_agent",
        action="generate_recovery_plan",
        source=source,
        reasoning=(
            f"Generated {len(steps)} recovery steps from '{(state.root_cause or {}).get('hypothesis', 'Unknown')}' root cause. "
            f"Rollback {'recommended' if rollback_rec else 'not recommended'}. "
            f"{len(high_risk_steps)} step(s) require human approval."
        ),
        findings={
            "step_count": len(steps),
            "high_risk_steps": len(high_risk_steps),
            "rollback_recommended": rollback_rec,
            "source": source,
        },
        latency_ms=t.ms(),
    )

    return state
