from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List


def _date(record: Dict[str, Any]) -> datetime:
    value = record.get("created_at") or record.get("timestamp")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _resolution(record: Dict[str, Any]) -> str:
    decisions = record.get("remediation_decisions") or {}
    approved = [key for key, value in decisions.items() if value.get("decision") == "approved"]
    if approved:
        return "Automated remediation"
    lifecycle = str(record.get("lifecycle_status") or "").lower()
    if "review" in lifecycle:
        return "Human review required"
    if lifecycle in {"resolved", "closed", "complete"}:
        return "Resolved"
    return "Investigation pending"


def signature(record: Dict[str, Any]) -> str:
    root = (record.get("root_cause") or {}).get("hypothesis") or record.get("alert_description") or "unknown"
    normalized = re.sub(r"[^a-z0-9 ]", "", str(root).lower())
    words = [word for word in normalized.split() if len(word) > 3][:7]
    return f"{record.get('service', 'unknown')}:{'-'.join(words)}"


def incident_analytics(records: Iterable[Dict[str, Any]], period: str = "week") -> Dict[str, Any]:
    records = list(records)
    days = {"day": 1, "week": 7, "month": 30}.get(period, 7)
    now = datetime.now(timezone.utc)
    selected = [record for record in records if _date(record) >= now - timedelta(days=days)]
    resolution_counts = Counter(_resolution(record) for record in selected)
    service_counts = Counter(str(record.get("service") or "unknown") for record in selected)
    signatures: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        signatures[signature(record)].append(record)
    recurring = []
    for key, items in signatures.items():
        if len(items) < 2:
            continue
        impact = sum(float(item.get("estimated_revenue_impact_per_minute") or 0) for item in items)
        recurring.append({
            "signature": key,
            "count": len(items),
            "service": items[0].get("service", "unknown"),
            "resolution": _resolution(items[-1]),
            "impact_per_minute": round(impact, 2),
            "incident_ids": [item.get("incident_id") for item in items],
        })
    return {
        "period": period,
        "window_days": days,
        "total": len(selected),
        "resolved": sum(1 for item in selected if item.get("current_status") == "complete"),
        "active": sum(1 for item in selected if item.get("current_status") != "complete"),
        "synthetic_count": sum(1 for item in selected if item.get("synthetic")),
        "recurring": sorted(recurring, key=lambda item: item["count"], reverse=True),
        "resolution_buckets": [{"label": key, "count": value} for key, value in resolution_counts.most_common()],
        "service_buckets": [{"label": key, "count": value} for key, value in service_counts.most_common()],
        "impact_per_minute": round(sum(float(item.get("estimated_revenue_impact_per_minute") or 0) for item in selected), 2),
    }


def knowledge_graph(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    for record in records:
        iid = str(record.get("incident_id"))
        service_name = record.get("service", "unknown")
        service = f"service:{service_name}"
        rc = record.get("root_cause") or {}
        cause_text = rc.get("hypothesis") or "Pending root cause"
        cause = f"cause:{signature(record)}"
        resolution_label = _resolution(record)
        resolution = f"resolution:{resolution_label}"

        # Incident node — rich detail for search
        severity = record.get("severity", "unknown")
        alert = record.get("alert_description", "")
        impact = record.get("estimated_revenue_impact_per_minute", 0)
        users = record.get("affected_users", 0)
        status = record.get("current_status", "unknown")
        detail = f"{alert}. Severity: {severity}. Status: {status}."
        if cause_text and cause_text != "Pending root cause":
            detail += f" RCA: {cause_text}."
        if users:
            detail += f" {users:,} users affected."
        if impact:
            detail += f" ${impact:,.0f}/min impact."

        nodes[iid] = {
            "id": iid, "type": "incident", "label": f"{service_name} [{iid[:8]}]",
            "detail": detail, "severity": severity, "status": status,
            "impact_per_minute": impact, "affected_users": users,
        }
        nodes[service] = {"id": service, "type": "service", "label": service_name,
                          "detail": f"Service: {service_name}"}
        nodes[cause] = {"id": cause, "type": "cause", "label": cause_text[:80],
                        "detail": cause_text}
        nodes[resolution] = {"id": resolution, "type": "resolution", "label": resolution_label,
                             "detail": f"Resolution: {resolution_label}"}
        edges.extend([
            {"source": iid, "target": service, "relation": "affects"},
            {"source": iid, "target": cause, "relation": "caused_by"},
            {"source": iid, "target": resolution, "relation": "resolved_by"},
        ])

        # Owner node + edge
        ownership = record.get("ownership") or {}
        owner_team = ownership.get("team") or ownership.get("primary")
        if owner_team:
            owner_id = f"owner:{owner_team}"
            nodes[owner_id] = {"id": owner_id, "type": "entity", "label": owner_team,
                               "detail": f"Team: {owner_team}. Primary: {ownership.get('primary', 'unknown')}"}
            edges.append({"source": service, "target": owner_id, "relation": "owned_by"})

        # Dependency edges between services
        for dep in (record.get("dependencies") or []):
            dep_id = f"service:{dep}"
            if dep_id not in nodes:
                nodes[dep_id] = {"id": dep_id, "type": "service", "label": dep,
                                 "detail": f"Service: {dep} (dependency)"}
            edges.append({"source": service, "target": dep_id, "relation": "depends_on"})

        # Evidence nodes from agent invocations (top findings)
        for inv in (record.get("agent_invocations") or [])[:6]:
            agent = inv.get("agent", "unknown")
            reasoning = inv.get("reasoning", "")
            if reasoning and len(reasoning) > 20:
                ev_id = f"evidence:{iid[:8]}:{agent}"
                nodes[ev_id] = {
                    "id": ev_id, "type": "evidence",
                    "label": f"{agent} finding",
                    "detail": reasoning[:300],
                }
                edges.append({"source": iid, "target": ev_id, "relation": "grounded_by"})

    return {"nodes": list(nodes.values()), "edges": edges}

