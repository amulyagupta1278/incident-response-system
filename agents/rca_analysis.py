from typing import Any
from datetime import datetime
from agents import IncidentState


def rca_analysis(state: IncidentState) -> IncidentState:
    hypothesis: str = "Unknown root cause"
    confidence: float = 0.0
    supporting_evidence: list[str] = []

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

    elif memory_spike and has_gc_logs and not recent_deployment:
        hypothesis = "Memory Leak"
        confidence = 0.75
        supporting_evidence = [
            "Memory metric increasing significantly",
            "GC pause warnings in logs",
            "No recent deployment (likely code regression)",
            "Gradual performance degradation pattern"
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

    elif has_timeout_logs and latency_spike:
        hypothesis = "Resource Saturation"
        confidence = 0.65
        supporting_evidence = [
            "Timeout errors in logs",
            "Latency metric significantly elevated",
            "Resource constraints likely exceeded"
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
        "supporting_evidence": supporting_evidence
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
