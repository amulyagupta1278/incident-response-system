"""Deployment Analysis Agent — AIOC Agent #4.

Dedicated agent that reviews recent deployments, configuration changes, and
infrastructure modifications to identify potential incident triggers.

This is intentionally separate from the RCA agent: deployment correlation is
a factual investigation step (what changed, when) while RCA is an inference
step (why did it fail). Separating them avoids anchoring the RCA hypothesis
on the deployment before log/metric evidence is weighed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents import IncidentState
from agents.telemetry import Timer, record_invocation


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp string tolerantly."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _incident_ts(state: IncidentState) -> Optional[datetime]:
    return _parse_ts(state.timestamp)


def _minutes_before_incident(dep_ts: datetime, inc_ts: datetime) -> float:
    """Positive = deployment before incident, negative = after."""
    if dep_ts.tzinfo is None:
        dep_ts = dep_ts.replace(tzinfo=timezone.utc)
    if inc_ts.tzinfo is None:
        inc_ts = inc_ts.replace(tzinfo=timezone.utc)
    return (inc_ts - dep_ts).total_seconds() / 60.0


def _classify_correlation(minutes: float) -> str:
    if minutes < 0:
        return "after_incident"       # deployment happened after alert — unrelated
    if minutes <= 15:
        return "likely_trigger"       # very close in time — strong signal
    if minutes <= 60:
        return "probable_cause"       # within an hour — likely related
    if minutes <= 240:
        return "possible_cause"       # within 4 hours — possible
    return "historical"               # older deployment — context only


def _analyze_single_deployment(
    dep: Dict[str, Any],
    inc_ts: Optional[datetime],
    index: int,
) -> Dict[str, Any]:
    """Produce a structured finding for one deployment entry."""
    changes = dep.get("changes") or []
    if isinstance(changes, str):
        changes = [changes]

    risk_keywords = {
        "pool", "limit", "timeout", "connection", "thread",
        "config", "flag", "env", "replica", "scale", "memory",
        "heap", "gc", "cache", "retry", "circuit",
    }
    risky_changes = [
        c for c in changes
        if any(kw in str(c).lower() for kw in risk_keywords)
    ]

    dep_ts = _parse_ts(dep.get("timestamp"))
    correlation = "unknown"
    minutes_before: Optional[float] = None

    if dep_ts and inc_ts:
        minutes_before = _minutes_before_incident(dep_ts, inc_ts)
        correlation = _classify_correlation(minutes_before)

    return {
        "index": index + 1,
        "version": dep.get("version", "unknown"),
        "timestamp": dep.get("timestamp"),
        "source": dep.get("source", "unknown"),
        "changes": changes,
        "risky_changes": risky_changes,
        "has_risky_changes": bool(risky_changes),
        "correlation": correlation,
        "minutes_before_incident": round(minutes_before, 1) if minutes_before is not None else None,
    }


def _correlation_summary(findings: List[Dict[str, Any]]) -> str:
    """Produce a one-sentence human-readable summary of deployment correlation."""
    triggers = [f for f in findings if f["correlation"] == "likely_trigger"]
    probable = [f for f in findings if f["correlation"] == "probable_cause"]
    possible = [f for f in findings if f["correlation"] == "possible_cause"]

    if triggers:
        top = triggers[0]
        risky = top["risky_changes"]
        change_str = risky[0] if risky else (top["changes"][0] if top["changes"] else "configuration change")
        return (
            f"Deployment {top['version']} was pushed {top['minutes_before_incident']:.0f} minute(s) "
            f"before the incident and included a potentially risky change: {change_str}."
        )
    if probable:
        top = probable[0]
        return (
            f"Deployment {top['version']} occurred ~{top['minutes_before_incident']:.0f} minutes "
            f"before the incident — probable contributing factor."
        )
    if possible:
        top = possible[0]
        return (
            f"Deployment {top['version']} occurred ~{top['minutes_before_incident']:.0f} minutes "
            f"before the incident — possible contributing factor."
        )
    if findings:
        return "No recent deployments found within a 4-hour window before the incident."
    return "No deployment changes recorded for this incident."


def _configuration_risk_summary(config_changes: List[Dict[str, Any]]) -> List[str]:
    """Extract the riskiest config changes as bullet strings."""
    risk_items: List[str] = []
    for item in config_changes[:5]:
        desc = item.get("description") or item.get("changes") or ""
        source = item.get("source", "unknown")
        ts = item.get("timestamp", "")
        risk_items.append(f"{source} @ {str(ts)[:19]}: {desc}")
    return risk_items


def deployment_analysis(state: IncidentState) -> IncidentState:
    """Dedicated Deployment Analysis Agent.

    Analyses deployment_changes and configuration_changes from the incident
    context, classifies temporal correlation with the incident, flags risky
    change patterns, and produces a structured report consumed by the RCA agent.
    """
    t = Timer.begin()
    inc_ts = _incident_ts(state)

    # --- Analyse deployment changes ---
    findings: List[Dict[str, Any]] = []
    for i, dep in enumerate(state.deployment_changes or []):
        if not isinstance(dep, dict):
            dep = {"description": str(dep)}
        findings.append(_analyze_single_deployment(dep, inc_ts, i))

    # --- Sort by proximity to incident ---
    def sort_key(f: Dict[str, Any]) -> float:
        m = f.get("minutes_before_incident")
        if m is None:
            return float("inf")
        return abs(m) if m >= 0 else float("inf")

    findings.sort(key=sort_key)

    # --- Config changes from context ---
    config_changes = getattr(state, "configuration_changes", []) or []
    config_risk = _configuration_risk_summary(config_changes)

    # --- Overall risk assessment ---
    risky_findings = [f for f in findings if f["has_risky_changes"]]
    trigger_findings = [f for f in findings if f["correlation"] == "likely_trigger"]
    correlation_str = _correlation_summary(findings)

    overall_risk = "low"
    if trigger_findings and risky_findings:
        overall_risk = "high"
    elif trigger_findings or (risky_findings and findings[0]["correlation"] in {"probable_cause"}):
        overall_risk = "medium"

    # --- Store structured deployment analysis on state ---
    state.deployment_analysis = {
        "deployment_count": len(findings),
        "findings": findings,
        "risky_deployments": risky_findings,
        "trigger_deployments": trigger_findings,
        "configuration_changes": config_risk,
        "correlation_summary": correlation_str,
        "overall_risk": overall_risk,
        "most_recent_deployment": findings[0] if findings else None,
    }

    state.completed_steps.add("deployment_analysis")
    state.current_status = "deployments_analyzed"

    record_invocation(
        state,
        agent="deployment_analysis_agent",
        action="analyze_deployment_changes",
        source="heuristic",
        reasoning=correlation_str,
        findings={
            "deployment_count": len(findings),
            "risky_deployment_count": len(risky_findings),
            "trigger_deployment_count": len(trigger_findings),
            "overall_risk": overall_risk,
            "correlation_summary": correlation_str,
        },
        latency_ms=t.ms(),
    )

    return state
