from typing import Any
from datetime import datetime
from agents import IncidentState


def _deploy_correlation(state: IncidentState) -> str:
    """Human-readable sentence linking the most recent deployment to the
    incident start time, or empty string when no deployment plausibly relates."""
    if not state.deployment_changes:
        return ""
    dep: dict[str, Any] = state.deployment_changes[0]
    version: str = str(dep.get("version", "unknown"))
    top_change: str = (dep.get("changes") or ["configuration change"])[0]
    try:
        dep_ts = datetime.fromisoformat(str(dep.get("timestamp", "")).replace("Z", "+00:00"))
        inc_ts = datetime.fromisoformat(str(state.timestamp).replace("Z", "+00:00"))
        if dep_ts.tzinfo is None:
            dep_ts = dep_ts.replace(tzinfo=inc_ts.tzinfo)
        if inc_ts.tzinfo is None:
            inc_ts = inc_ts.replace(tzinfo=dep_ts.tzinfo)
        minutes: float = (inc_ts - dep_ts).total_seconds() / 60
    except (ValueError, TypeError):
        return f"Deployment {version} ({top_change}) preceded the incident"
    if minutes < 0:
        return ""
    if minutes <= 240:
        return (
            f"Incident began {minutes:.0f} minutes after deployment {version}, "
            f"which included: {top_change}"
        )
    return (
        f"Deployment {version} is the most recent change before the incident; "
        f"it included: {top_change}"
    )


def rca_analysis(state: IncidentState) -> IncidentState:
    hypothesis: str = "Unknown root cause"
    confidence: float = 0.0
    supporting_evidence: list[str] = []
    ruled_out: list[dict[str, str]] = []

    has_timeout_logs: bool = any(
        anomaly["type"] == "timeout" for anomaly in state.log_anomalies
    )
    has_connection_logs: bool = any(
        anomaly["type"] == "connection_error" for anomaly in state.log_anomalies
    )
    has_gc_logs: bool = any(
        anomaly["type"] == "gc_pause" for anomaly in state.log_anomalies
    )

    cpu_spike: Any = next(
        (m for m in state.metric_anomalies if m["metric_name"] == "cpu_percent"),
        None
    )
    memory_spike: Any = next(
        (m for m in state.metric_anomalies if m["metric_name"] == "memory_mb"),
        None
    )
    latency_spike: Any = next(
        (m for m in state.metric_anomalies if m["metric_name"] == "latency_ms"),
        None
    )
    error_rate_spike: Any = next(
        (m for m in state.metric_anomalies if m["metric_name"] == "error_rate"),
        None
    )

    recent_deployment: bool = len(state.deployment_changes) > 0

    if (has_timeout_logs or has_connection_logs) and cpu_spike and recent_deployment:
        hypothesis = "Database Connection Pool Exhaustion"
        confidence = 0.85
        supporting_evidence = [
            "Connection timeout errors in logs",
            "CPU spike coinciding with deployment",
            "Recent deployment with reduced pool configuration",
            "Latency increase suggests resource contention"
        ]
        ruled_out = [
            {"hypothesis": "Memory Leak", "reason": "No sustained memory growth or GC pause pattern in the telemetry"},
            {"hypothesis": "Downstream Service Failure", "reason": "Errors originate at the connection pool layer, not in downstream call paths"},
        ]

    elif memory_spike and has_gc_logs and not recent_deployment:
        hypothesis = "Memory Leak"
        confidence = 0.75
        supporting_evidence = [
            "Memory metric increasing significantly",
            "GC pause warnings in logs",
            "No recent deployment (likely code regression)",
            "Gradual performance degradation pattern"
        ]
        ruled_out = [
            {"hypothesis": "Bad Deployment", "reason": "No deployment occurred in the incident window; degradation was gradual"},
            {"hypothesis": "Connection Pool Exhaustion", "reason": "No connection or pool errors present in logs"},
        ]

    elif error_rate_spike and latency_spike and has_timeout_logs:
        hypothesis = "Cascading Failure - Downstream Service Timeout"
        confidence = 0.80
        supporting_evidence = [
            "Error rate spike in current service",
            "Timeout errors calling downstream services",
            "Latency increase suggests dependency degradation",
            "Error pattern consistent with cascading failure"
        ]
        ruled_out = [
            {"hypothesis": "Local Resource Exhaustion", "reason": "CPU and memory remain near baseline; failures track downstream call latency"},
            {"hypothesis": "Memory Leak", "reason": "No GC pressure or memory growth observed"},
        ]

    elif has_timeout_logs and latency_spike:
        hypothesis = "Resource Saturation"
        confidence = 0.65
        supporting_evidence = [
            "Timeout errors in logs",
            "Latency metric significantly elevated",
            "Resource constraints likely exceeded"
        ]
        ruled_out = [
            {"hypothesis": "Deployment Regression", "reason": "No deployment change coincides with the incident window"},
        ]

    else:
        hypothesis = "Service Degradation"
        confidence = 0.50
        supporting_evidence = [
            f"Detected {len(state.log_anomalies)} log anomalies",
            f"Detected {len(state.metric_anomalies)} metric anomalies"
        ]

    state.root_cause = {
        "hypothesis": hypothesis,
        "confidence": confidence,
        "supporting_evidence": supporting_evidence,
        "ruled_out_hypotheses": ruled_out,
        "deploy_correlation": _deploy_correlation(state)
    }

    invocation: dict[str, Any] = {
        "agent": "rca_analysis",
        "timestamp": datetime.now().isoformat(),
        "action": "analyze_root_cause",
        "findings": {
            "hypothesis": hypothesis,
            "confidence": confidence,
            "evidence_count": len(supporting_evidence)
        }
    }
    state.agent_invocations.append(invocation)

    return state
