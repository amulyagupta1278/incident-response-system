from typing import Any
from datetime import datetime
from agents import IncidentState


def executive_summary(state: IncidentState) -> IncidentState:
    if not state.recovery_recommendations:
        state.recovery_recommendations = _default_recommendations(state)

    root_cause_info: str = ""
    if state.root_cause:
        root_cause_info = f"Root Cause: {state.root_cause['hypothesis']} (Confidence: {state.root_cause['confidence']*100:.0f}%)"

    log_summary: str = ""
    if state.log_anomalies:
        log_types: list[str] = [anomaly["type"] for anomaly in state.log_anomalies]
        log_summary = f"Log Anomalies: {', '.join(log_types)}"

    metric_summary: str = ""
    if state.metric_anomalies:
        metric_summaries: list[str] = [
            f"{m['metric_name']}: {m['percent_change']:.1f}% change"
            for m in state.metric_anomalies
        ]
        metric_summary = f"Metric Anomalies: {', '.join(metric_summaries)}"

    engineering_sections: list[str] = [
        f"Service: {state.service}",
        f"Alert: {state.alert_description}",
        f"Severity: {state.severity}",
        "",
        log_summary if log_summary else "No log anomalies detected",
        "",
        metric_summary if metric_summary else "No metric anomalies detected",
        "",
        root_cause_info if root_cause_info else "Root cause analysis pending"
    ]

    if state.root_cause and state.root_cause.get("supporting_evidence"):
        engineering_sections.append("")
        engineering_sections.append("Supporting Evidence:")
        for evidence in state.root_cause["supporting_evidence"]:
            engineering_sections.append(f"  • {evidence}")

    if state.recovery_recommendations:
        engineering_sections.append("")
        engineering_sections.append("Recovery Recommendations:")
        for rec in state.recovery_recommendations:
            engineering_sections.append(f"  • {rec}")

    state.engineering_summary = "\n".join(engineering_sections)

    executive_sections: list[str] = [
        f"INCIDENT REPORT: {state.service.upper()}",
        f"Timestamp: {state.timestamp}",
        f"Severity: {state.severity.upper()}",
        "",
        "IMPACT",
        f"  Affected Users: {state.affected_users:,}",
        f"  Revenue Impact: ${state.estimated_revenue_impact_per_minute:.2f}/minute",
        "",
        "ROOT CAUSE",
        f"  {root_cause_info if root_cause_info else 'Analysis in progress'}",
        "",
        "CURRENT STATUS",
        f"  Analysis agents invoked: {len(state.agent_invocations)}",
        f"  Log anomalies found: {len(state.log_anomalies)}",
        f"  Metric anomalies found: {len(state.metric_anomalies)}"
    ]

    state.executive_summary = "\n".join(executive_sections)

    invocation: dict[str, Any] = {
        "agent": "executive_summary",
        "timestamp": datetime.now().isoformat(),
        "action": "generate_summaries",
        "findings": {
            "engineering_summary_length": len(state.engineering_summary),
            "executive_summary_length": len(state.executive_summary),
            "recovery_recommendations": len(state.recovery_recommendations)
        }
    }
    state.agent_invocations.append(invocation)

    return state


def _default_recommendations(state: IncidentState) -> list[str]:
    hypothesis: str = (state.root_cause or {}).get("hypothesis", "").lower()

    if "pool" in hypothesis or "connection" in hypothesis:
        return [
            "Rollback the deployment that reduced the connection pool size",
            "Temporarily raise the DB connection pool limit",
            "Monitor connection wait times until error rate returns to baseline",
            "Add an alert on pool utilization above 80%",
        ]
    if "memory" in hypothesis or "leak" in hypothesis:
        return [
            "Restart affected instances on a rolling basis to reclaim memory",
            "Bisect recent code changes for objects not being released",
            "Capture a heap dump from a degraded instance for analysis",
            "Add an alert on sustained memory growth and GC pause times",
        ]
    if "cascad" in hypothesis or "downstream" in hypothesis or "timeout" in hypothesis:
        return [
            "Enable circuit breaker on calls to the degraded downstream service",
            "Reduce downstream call timeout and add retry budget limits",
            "Engage the downstream service owning team",
            "Serve degraded/cached responses until the dependency recovers",
        ]
    return [
        "Investigate root cause hypothesis",
        "Check recent deployments and rollback if necessary",
        "Monitor affected service metrics closely",
        "Prepare communication for impacted customers",
        "Execute recovery procedures once root cause confirmed",
    ]
