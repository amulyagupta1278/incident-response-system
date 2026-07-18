from __future__ import annotations

from typing import Any, Dict

from agents import IncidentState


def evaluate_quality_gates(values: Dict[str, Any] | IncidentState) -> Dict[str, Any]:
    data = vars(values) if isinstance(values, IncidentState) else values
    root_cause = data.get("root_cause") or {}
    evidence_catalog = data.get("evidence_catalog") or {}
    refs = root_cause.get("supporting_evidence_refs") or []
    recommendations = data.get("recovery_recommendations") or []

    valid_ref_count = sum(
        1 for ref in refs
        if ref.get("evidence_id") in evidence_catalog
    )
    gates = {
        "rca_exists": bool(root_cause.get("hypothesis")),
        "confidence_breakdown_exists": bool(root_cause.get("confidence_breakdown")),
        "supporting_evidence_exists": bool(root_cause.get("supporting_evidence")),
        "evidence_refs_valid": bool(refs) and valid_ref_count == len(refs),
        "impact_calculated": bool(data.get("affected_users", 0) > 0),
        "recommendations_generated": bool(recommendations),
        "postmortem_exportable": bool(data.get("executive_summary") and root_cause),
    }
    gates["overall_passed"] = all(gates.values())
    gates["valid_evidence_ref_count"] = valid_ref_count
    gates["total_evidence_ref_count"] = len(refs)
    return gates
