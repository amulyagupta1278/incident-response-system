from typing import Any
from datetime import datetime
from agents import IncidentState


def _classify_log_pattern(log_entry: dict[str, Any]) -> tuple[str, str]:
    level: str = log_entry.get("level", "").upper()
    message: str = log_entry.get("message", "").lower()
    error_type: str = log_entry.get("error_type", "").lower()

    if level in ["ERROR", "CRITICAL"]:
        if "timeout" in message or "timeout" in error_type:
            return "timeout", "high"
        if "connection" in message or "pool" in message:
            return "connection_error", "high"
        if "memory" in message or "heap" in message:
            return "memory_pressure", "critical" if level == "CRITICAL" else "high"
        if "cascading" in message or "unavailable" in message:
            return "dependency_failure", "critical"
        return "runtime_error", "high"

    if "gc" in message and "warning" in message:
        return "gc_pause", "medium"

    return "", ""


def log_analysis(state: IncidentState) -> IncidentState:
    error_types: dict[str, dict[str, Any]] = {}
    level_counts: dict[str, int] = {}
    timeline: list[dict[str, Any]] = []
    error_contexts: list[dict[str, Any]] = []
    timeout_count: int = 0
    connection_error_count: int = 0
    gc_warning_count: int = 0

    for index, log_entry in enumerate(state.raw_logs):
        level: str = log_entry.get("level", "").upper()
        message: str = log_entry.get("message", "").lower()
        level_counts[level or "UNKNOWN"] = level_counts.get(level or "UNKNOWN", 0) + 1

        pattern, severity = _classify_log_pattern(log_entry)
        if not pattern:
            continue

        if pattern == "timeout":
            timeout_count += 1
        if pattern == "connection_error":
            connection_error_count += 1
        if pattern == "gc_pause":
            gc_warning_count += 1

        if pattern not in error_types:
            error_types[pattern] = {
                "count": 0,
                "severity": severity,
                "evidence": [],
                "first_seen": log_entry.get("timestamp"),
                "last_seen": log_entry.get("timestamp"),
                "levels": {},
            }
        error_types[pattern]["count"] += 1
        error_types[pattern]["last_seen"] = log_entry.get("timestamp")
        error_types[pattern]["levels"][level] = (
            error_types[pattern]["levels"].get(level, 0) + 1
        )
        error_types[pattern]["evidence"].append(log_entry.get("message", ""))
        timeline.append(
            {
                "timestamp": log_entry.get("timestamp"),
                "level": level,
                "pattern": pattern,
                "message": log_entry.get("message", ""),
            }
        )
        if level in ["ERROR", "CRITICAL"]:
            start: int = max(0, index - 1)
            end: int = min(len(state.raw_logs), index + 2)
            error_contexts.append(
                {
                    "index": index,
                    "timestamp": log_entry.get("timestamp"),
                    "level": level,
                    "pattern": pattern,
                    "message": log_entry.get("message", ""),
                    "context_window": state.raw_logs[start:end],
                }
            )

    for error_type, details in error_types.items():
        anomaly: dict[str, Any] = {
            "type": error_type,
            "count": details["count"],
            "severity": details["severity"],
            "baseline_count": 1,
            "incident_count": details["count"],
            "spike_factor": details["count"] if details["count"] > 1 else 1,
            "evidence": details["evidence"][:3],
            "first_seen": details["first_seen"],
            "last_seen": details["last_seen"],
            "levels": details["levels"],
        }
        state.log_anomalies.append(anomaly)

    state.log_context_cache = {
        "total_logs_scanned": len(state.raw_logs),
        "level_counts": level_counts,
        "hierarchy": sorted(
            [
                {
                    "type": error_type,
                    "severity": details["severity"],
                    "count": details["count"],
                    "first_seen": details["first_seen"],
                    "last_seen": details["last_seen"],
                    "levels": details["levels"],
                }
                for error_type, details in error_types.items()
            ],
            key=lambda item: (
                {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(
                    item["severity"], 4
                ),
                -item["count"],
            ),
        ),
        "timeline": timeline,
        "error_contexts": error_contexts[:10],
        "cache_policy": "All logs are centralized for analysis; UI shows top 10 error context windows.",
    }

    invocation: dict[str, Any] = {
        "agent": "log_analysis",
        "timestamp": datetime.now().isoformat(),
        "action": "analyze_logs",
        "reasoning": (
            f"Scanned {len(state.raw_logs)} log entries; found {timeout_count} timeout errors, "
            f"{connection_error_count} connection/pool errors, {gc_warning_count} GC warnings "
            f"→ {len(state.log_anomalies)} anomaly pattern(s)"
        ),
        "findings": {
            "anomalies_detected": len(state.log_anomalies),
            "timeout_errors": timeout_count,
            "connection_errors": connection_error_count,
            "gc_warnings": gc_warning_count,
            "centralized_logs": len(state.raw_logs),
            "cached_error_contexts": len(state.log_context_cache["error_contexts"]),
        }
    }
    state.agent_invocations.append(invocation)

    return state
