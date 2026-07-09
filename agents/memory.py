import json
import os
from datetime import datetime
from typing import Any, Dict, List

from agents import IncidentState

MEMORY_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "incident_memory.json",
)


def _load() -> List[Dict[str, Any]]:
    try:
        with open(MEMORY_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    with open(MEMORY_PATH, "w") as f:
        json.dump(items, f, indent=2)


def find_similar_incidents(state: IncidentState) -> List[Dict[str, Any]]:
    """Match the current incident against resolved past incidents by root
    cause and by service + anomaly-signature overlap. Returns the most
    recent matches first (max 3)."""
    hypothesis: str = (state.root_cause or {}).get("hypothesis", "")
    log_types = {a.get("type", "") for a in state.log_anomalies}

    matches: List[Dict[str, Any]] = []
    for past in _load():
        if past.get("incident_id") == state.incident_id:
            continue
        same_cause: bool = bool(hypothesis) and past.get("hypothesis") == hypothesis
        signature_overlap = log_types & set(past.get("log_anomaly_types", []))
        same_service: bool = past.get("service") == state.service
        if same_cause or (same_service and signature_overlap):
            match = dict(past)
            match["match_reason"] = (
                "same root cause"
                if same_cause
                else "same service with overlapping anomaly signature"
            )
            matches.append(match)
    return matches[::-1][:3]


def record_incident(record: Dict[str, Any]) -> None:
    """Persist a completed incident so future investigations can cite it."""
    items: List[Dict[str, Any]] = _load()
    if any(p.get("incident_id") == record.get("incident_id") for p in items):
        return
    root_cause: Dict[str, Any] = record.get("root_cause") or {}
    items.append(
        {
            "number": len(items) + 1,
            "incident_id": record.get("incident_id"),
            "resolved_at": datetime.now().isoformat(),
            "service": record.get("service"),
            "severity": record.get("severity"),
            "hypothesis": root_cause.get("hypothesis", ""),
            "confidence": root_cause.get("confidence", 0),
            "log_anomaly_types": sorted(
                {a.get("type", "") for a in record.get("log_anomalies", [])}
            ),
            "recovery_recommendations": (record.get("recovery_recommendations") or [])[:3],
        }
    )
    _save(items)
