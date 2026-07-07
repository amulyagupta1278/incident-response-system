from typing import Any
from datetime import datetime
from agents import IncidentState


def log_analysis(state: IncidentState) -> IncidentState:
    error_types: dict[str, dict[str, Any]] = {}
    timeout_count: int = 0
    connection_error_count: int = 0
    gc_warning_count: int = 0

    for log_entry in state.raw_logs:
        level: str = log_entry.get("level", "").upper()
        message: str = log_entry.get("message", "").lower()

        if level in ["ERROR", "CRITICAL"]:
            if "timeout" in message or "timeout" in log_entry.get("error_type", "").lower():
                timeout_count += 1
                if "timeout" not in error_types:
                    error_types["timeout"] = {
                        "count": 0,
                        "severity": "high",
                        "evidence": []
                    }
                error_types["timeout"]["count"] += 1
                error_types["timeout"]["evidence"].append(log_entry.get("message", ""))

            if "connection" in message or "pool" in message:
                connection_error_count += 1
                if "connection_error" not in error_types:
                    error_types["connection_error"] = {
                        "count": 0,
                        "severity": "high",
                        "evidence": []
                    }
                error_types["connection_error"]["count"] += 1
                error_types["connection_error"]["evidence"].append(log_entry.get("message", ""))

        if "gc" in message and "warning" in message.lower():
            gc_warning_count += 1
            if "gc_pause" not in error_types:
                error_types["gc_pause"] = {
                    "count": 0,
                    "severity": "medium",
                    "evidence": []
                }
            error_types["gc_pause"]["count"] += 1
            error_types["gc_pause"]["evidence"].append(log_entry.get("message", ""))

    for error_type, details in error_types.items():
        anomaly: dict[str, Any] = {
            "type": error_type,
            "count": details["count"],
            "severity": details["severity"],
            "baseline_count": 1,
            "incident_count": details["count"],
            "spike_factor": details["count"] if details["count"] > 1 else 1,
            "evidence": details["evidence"][:3]
        }
        state.log_anomalies.append(anomaly)

    invocation: dict[str, Any] = {
        "agent": "log_analysis",
        "timestamp": datetime.now().isoformat(),
        "action": "analyze_logs",
        "findings": {
            "anomalies_detected": len(state.log_anomalies),
            "timeout_errors": timeout_count,
            "connection_errors": connection_error_count,
            "gc_warnings": gc_warning_count
        }
    }
    state.agent_invocations.append(invocation)

    return state
