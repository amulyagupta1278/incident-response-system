from __future__ import annotations

import hashlib
from typing import Any, Dict, List


def _stable_hash(payload: object) -> str:
    raw: str = repr(payload).encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:10]


def attach_evidence_ids(service: str, logs: List[Dict[str, Any]], metrics: List[Dict[str, Any]], deployments: List[Dict[str, Any]]) -> None:
    for index, entry in enumerate(logs):
        if "evidence_id" not in entry:
            entry["evidence_id"] = f"log:{service}:{index}:{_stable_hash(entry.get('message',''))}"
    for index, entry in enumerate(metrics):
        if "evidence_id" not in entry:
            name: str = str(entry.get("metric_name", "unknown")).lower()
            window: str = str(entry.get("window", "")).lower()
            entry["evidence_id"] = f"metric:{service}:{window}:{name}:{index}"
    for index, entry in enumerate(deployments):
        if "evidence_id" not in entry:
            version: str = str(entry.get("version", "unknown"))
            entry["evidence_id"] = f"deploy:{service}:{version}:{index}"


def build_evidence_catalog(
    logs: List[Dict[str, Any]],
    metrics: List[Dict[str, Any]],
    deployments: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    catalog: Dict[str, Dict[str, Any]] = {}
    for entry in logs:
        ev_id = entry.get("evidence_id")
        if ev_id:
            catalog[str(ev_id)] = {
                "evidence_id": ev_id,
                "type": "log",
                "timestamp": entry.get("timestamp"),
                "level": entry.get("level"),
                "message": entry.get("message"),
                "source": "mock_logs",
            }
    for entry in metrics:
        ev_id = entry.get("evidence_id")
        if ev_id:
            catalog[str(ev_id)] = {
                "evidence_id": ev_id,
                "type": "metric",
                "metric_name": entry.get("metric_name"),
                "window": entry.get("window"),
                "value": entry.get("value"),
                "source": "mock_metrics",
            }
    for entry in deployments:
        ev_id = entry.get("evidence_id")
        if ev_id:
            catalog[str(ev_id)] = {
                "evidence_id": ev_id,
                "type": "deployment",
                "timestamp": entry.get("timestamp"),
                "version": entry.get("version"),
                "changes": entry.get("changes", []),
                "source": "mock_deployments",
            }
    return catalog


def evidence_refs_from_state(state: Any, claims: List[str]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    claim_iter = iter(claims or [])

    for anomaly in state.log_anomalies:
        claim = next(claim_iter, f"Log anomaly: {anomaly.get('type')}")
        for ref in anomaly.get("evidence_refs", [])[:2]:
            ev_id = ref.get("evidence_id")
            if ev_id:
                refs.append(
                    {
                        "claim": claim,
                        "evidence_id": ev_id,
                        "evidence_type": "log",
                    }
                )

    for metric in state.metric_anomalies:
        claim = next(claim_iter, f"Metric anomaly: {metric.get('metric_name')}")
        ev_id = metric.get("evidence_id")
        if ev_id:
            refs.append(
                {
                    "claim": claim,
                    "evidence_id": ev_id,
                    "evidence_type": "metric",
                }
            )

    for dep in state.deployment_changes[:2]:
        claim = next(claim_iter, f"Deployment change: {dep.get('version')}")
        ev_id = dep.get("evidence_id")
        if ev_id:
            refs.append(
                {
                    "claim": claim,
                    "evidence_id": ev_id,
                    "evidence_type": "deployment",
                }
            )
    return refs[:12]
