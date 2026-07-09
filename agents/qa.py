import json
from typing import Any, Dict, Tuple

from agents.llm import complete_json, get_model, llm_available

ANSWER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}

CONTEXT_KEYS = (
    "service",
    "severity",
    "alert_description",
    "current_status",
    "root_cause",
    "log_anomalies",
    "log_context_cache",
    "metric_anomalies",
    "deployment_changes",
    "affected_users",
    "estimated_revenue_impact_per_minute",
    "revenue_impact_justification",
    "recovery_recommendations",
    "similar_incidents",
)


async def answer_question(record: Dict[str, Any], question: str) -> Tuple[str, str]:
    """Answer a natural-language question about one incident, grounded in its
    record. LLM when configured, keyword heuristics otherwise."""
    if llm_available():
        context: Dict[str, Any] = {k: record.get(k) for k in CONTEXT_KEYS}
        prompt: str = (
            f"Incident data:\n{json.dumps(context, default=str)}\n\n"
            f"Question: {question}\n\n"
            "Answer in 1-3 plain-text sentences, grounded strictly in the data "
            "above. If the data does not contain the answer, say so explicitly."
        )
        try:
            result: Dict[str, Any] = await complete_json(
                system="You answer questions about a production incident using only the provided data.",
                prompt=prompt,
                schema=ANSWER_SCHEMA,
                schema_name="incident_answer",
            )
            answer: str = str(result.get("answer", "")).strip()
            if answer:
                return answer, f"llm:{get_model()}"
        except Exception as exc:
            print(f"[qa] LLM answer failed, using heuristic: {exc}")
    return _heuristic_answer(record, question), "heuristic"


def _heuristic_answer(record: Dict[str, Any], question: str) -> str:
    q: str = question.lower()
    rc: Dict[str, Any] = record.get("root_cause") or {}

    if "similar" in q or "seen" in q or "history" in q:
        sims = record.get("similar_incidents", [])
        if not sims:
            return "No similar past incidents found in memory."
        s = sims[0]
        return (
            f"Yes - matches incident #{s.get('number')} on {s.get('service')}: "
            f"{s.get('hypothesis')} ({s.get('match_reason')})."
        )
    if "deploy" in q or "change" in q or "before" in q:
        return rc.get("deploy_correlation") or "No deployment correlation identified."
    if "root cause" in q or "cause" in q or "why" in q:
        if not rc:
            return "Root cause has not been determined yet - agents are still investigating."
        return (
            f"Most likely root cause: {rc.get('hypothesis')} "
            f"({rc.get('confidence', 0) * 100:.0f}% confidence)."
        )
    if "justify" in q or "justification" in q or "formula" in q or "revenue" in q:
        impact = record.get("revenue_impact_justification") or {}
        if not impact:
            return (
                f"{record.get('affected_users', 0):,} users affected; estimated revenue "
                f"impact ${record.get('estimated_revenue_impact_per_minute', 0):.2f}/minute."
            )
        return (
            f"Revenue impact uses {impact.get('formula')}: "
            f"{impact.get('affected_users', 0):,} users x "
            f"${impact.get('revenue_per_user_per_minute', 0):.2f}/user/min = "
            f"${impact.get('revenue_impact_per_minute', 0):.2f}/minute. "
            f"Bounded range is ${impact.get('lower_bound_per_minute', 0):.2f}-"
            f"${impact.get('upper_bound_per_minute', 0):.2f}/minute."
        )
    if "user" in q or "affect" in q or "impact" in q:
        return (
            f"{record.get('affected_users', 0):,} users affected; estimated revenue "
            f"impact ${record.get('estimated_revenue_impact_per_minute', 0):.2f}/minute."
        )
    if "log" in q or "error" in q:
        cache = record.get("log_context_cache") or {}
        if "context" in q or "cache" in q or "hierarchy" in q or "history" in q:
            hierarchy = ", ".join(
                f"{h.get('severity')}/{h.get('type')} ({h.get('count')})"
                for h in cache.get("hierarchy", [])
            )
            return (
                f"Centralized log cache scanned {cache.get('total_logs_scanned', 0)} logs "
                f"and kept {len(cache.get('error_contexts', []))} error context windows. "
                f"Hierarchy: {hierarchy or 'none'}."
            )
        types = ", ".join(
            f"{a.get('type')} (x{a.get('count')})" for a in record.get("log_anomalies", [])
        )
        return f"Log anomalies detected: {types or 'none'}."
    if "metric" in q:
        spikes = ", ".join(
            f"{m.get('metric_name')} {m.get('percent_change', 0):+.0f}%"
            for m in record.get("metric_anomalies", [])
        )
        return f"Metric anomalies: {spikes or 'none detected'}."
    if "fix" in q or "recover" in q or "recommend" in q or "next" in q:
        recs = record.get("recovery_recommendations", [])
        if not recs:
            return "No recovery recommendations yet."
        return "Recommended actions: " + " ".join(
            f"({i + 1}) {r}" for i, r in enumerate(recs[:3])
        )
    if "status" in q:
        return f"Current status: {record.get('current_status')}."
    return (
        f"Status: {record.get('current_status')}. "
        f"Root cause: {rc.get('hypothesis', 'not yet determined')}. "
        "Try asking about: root cause, impact, deployments, logs, metrics, "
        "similar incidents, or recommended fixes."
    )
