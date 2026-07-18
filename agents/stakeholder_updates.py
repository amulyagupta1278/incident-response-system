from __future__ import annotations

from typing import Any, Dict, List

from agents import IncidentState
from agents.telemetry import Timer, record_invocation


def _risk_level(state: IncidentState) -> str:
    if state.estimated_revenue_impact_per_minute >= 1500 or state.affected_users >= 10000:
        return "critical"
    if state.estimated_revenue_impact_per_minute >= 600 or state.affected_users >= 2500:
        return "high"
    if state.estimated_revenue_impact_per_minute >= 150 or state.affected_users >= 500:
        return "medium"
    return "low"


def _troubleshooting_steps(state: IncidentState, risk_level: str) -> List[str]:
    hypothesis = (state.root_cause or {}).get("hypothesis", "").lower()
    if "memory" in hypothesis:
        return [
            "Identify the leaking process and confirm the growth curve from metrics",
            "Capture heap diagnostics before restarting affected workers",
            "Roll the bad build back or hotfix the allocation path",
            "Set a memory and GC pause guardrail before reopening traffic",
        ]
    if "pool" in hypothesis or "connection" in hypothesis:
        return [
            "Confirm current database pool settings and compare them to the last deploy",
            "Check connection wait time, queue depth, and saturation on the DB tier",
            "Roll back the deployment or temporarily raise the pool size",
            "Notify the DB owning team and keep the service on a watchlist",
        ]
    if "timeout" in hypothesis or "cascad" in hypothesis or "downstream" in hypothesis:
        return [
            "Isolate the slow downstream dependency and check its recent health",
            "Apply backoff, circuit breaking, or traffic shedding for the failing call path",
            "Coordinate with the downstream owning team and set a joint incident bridge",
            "Monitor latency, retry rate, and error budget burn until recovery",
        ]
    if "cache stampede" in hypothesis:
        return [
            "Enable request coalescing for hot keys to stop duplicate origin fetches",
            "Add TTL jitter and stale-while-revalidate before re-enabling full traffic",
            "Cap retry attempts with exponential backoff and a strict retry budget",
            "Watch cache-miss rate, origin qps, latency, and cost until stable",
        ]
    if "traffic" in hypothesis or "cost" in hypothesis or "retry" in hypothesis:
        return [
            "Throttle or cap non-essential traffic and stop retry amplification",
            "Validate cache, queue, and rate-limit settings before re-enabling the burst path",
            "Alert finance and service owners because spend is increasing alongside saturation",
            "Watch qps, latency, and cost-per-minute until the surge is controlled",
        ]
    return [
        "Recheck the highest-signal logs, metrics, and deployment changes",
        "Confirm the RCA with the owning engineer before making customer promises",
        "Prepare a rollback or mitigation step and validate the blast radius",
        "Keep the incident bridge open until the quality gates are satisfied",
    ]


def stakeholder_updates(state: IncidentState) -> IncidentState:
    t = Timer.begin()
    risk_level = _risk_level(state)
    state.business_risk_level = risk_level
    state.troubleshooting_plan = _troubleshooting_steps(state, risk_level)

    deploy_note = (state.root_cause or {}).get("deploy_correlation", "") or "No deployment correlation confirmed yet."
    state.kpi_guardrails = {
        "primary_kpis": ["latency_ms", "error_rate", "affected_users"],
        "operational_guardrails": [
            "Keep error_rate under 5% before declaring service stable",
            "Keep latency_ms under 2x baseline or 300ms, whichever is lower",
            "Keep affected_users trending down for two consecutive checks",
        ],
        "business_guardrails": [
            "Escalate to business owners if revenue impact exceeds $500/min",
            "Escalate to finance if cost impact exceeds $150/min",
            "Notify the incident commander when the plan needs rollback approval",
        ],
        "risk_level": risk_level,
    }
    state.stakeholder_updates = {
        "engineering": (
            f"Engineering update: {state.service} is in {risk_level} risk state. "
            f"RCA hypothesis is {(state.root_cause or {}).get('hypothesis', 'pending')}. "
            f"Immediate next step: {state.troubleshooting_plan[0]}"
        ),
        "business": (
            f"Business update: {state.affected_users:,} users may be impacted and "
            f"revenue exposure is ${state.estimated_revenue_impact_per_minute:.2f}/min. "
            f"Cost exposure is ${state.estimated_cost_impact_per_minute:.2f}/min. "
            f"Teams should expect a status update within the next response cycle."
        ),
        "customers": (
            "Customer update: we are investigating elevated service impact, "
            "have identified the most likely failure mode, and are working a mitigation."
        ),
        "ops": (
            f"Ops update: maintain the bridge, validate rollback readiness, and track the KPI guardrails. {deploy_note}"
        ),
    }
    state.escalation_summary = (
        f"{state.service} routed to {risk_level} business risk handling; "
        f"impact={state.affected_users:,} users, "
        f"revenue=${state.estimated_revenue_impact_per_minute:.2f}/min, "
        f"cost=${state.estimated_cost_impact_per_minute:.2f}/min"
    )

    record_invocation(
        state,
        agent="stakeholder_updates",
        action="prepare_updates_and_troubleshooting",
        source="heuristic",
        reasoning=state.escalation_summary,
        findings={
            "risk_level": risk_level,
            "kpi_guardrails": state.kpi_guardrails,
            "troubleshooting_steps": len(state.troubleshooting_plan),
        },
        latency_ms=t.ms(),
    )
    return state

