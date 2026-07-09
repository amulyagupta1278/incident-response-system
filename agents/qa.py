from typing import Any, Dict

from agents.llm import llm_available, llm_strict_mode
from agents.rag import EvidenceChunk, answer_with_rag, build_incident_chunks, retrieve_chunks


async def answer_question(record: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Answer a question through retrieval-grounded incident evidence.

    With OpenAI configured, this is a proper RAG path: build evidence chunks,
    retrieve the most relevant chunks, then answer only from those chunks with
    citations. Without an LLM provider, return deterministic retrieval output
    with citations so the UI remains usable without allowing uncited claims.
    """
    if llm_available():
        try:
            return await answer_with_rag(record, question)
        except Exception as exc:
            if llm_strict_mode():
                raise RuntimeError(f"RAG answer failed in strict mode: {exc}") from exc
            print(f"[qa] RAG answer failed, using retrieval fallback: {exc}")
    return await _retrieval_fallback_answer(record, question)


async def _retrieval_fallback_answer(record: Dict[str, Any], question: str) -> Dict[str, Any]:
    chunks: list[EvidenceChunk] = build_incident_chunks(record)
    if not chunks:
        return {
            "answer": "No incident evidence is available yet.",
            "source": "rag:retrieval-fallback:none",
            "citations": [],
            "retrieved_chunks": [],
        }
    retrieved: list[EvidenceChunk] = await retrieve_chunks(question, chunks)
    if not retrieved:
        return {
            "answer": "Retrieved evidence was insufficient to produce a cited answer.",
            "source": "rag:retrieval-fallback",
            "citations": [],
            "retrieved_chunks": [],
        }
    return {
        "answer": _heuristic_answer(record, question),
        "source": "rag:retrieval-fallback",
        "citations": [
            {"chunk_id": chunk.chunk_id, "label": chunk.label}
            for chunk in retrieved[:3]
        ],
        "retrieved_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "label": chunk.label,
                "source_type": chunk.source_type,
                "text": chunk.text,
            }
            for chunk in retrieved
        ],
    }


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
    if "rollback" in q:
        risk: Dict[str, Any] = record.get("deployment_risk_report") or {}
        edges = record.get("evidence_edges") or []
        if risk:
            return (
                f"Report-only recommendation: {risk.get('recommended_action', 'need_more_evidence')}. "
                f"Deployment correlation score is {risk.get('deployment_correlation_score', 0)}; "
                "human approval is required before action."
            )
        if edges:
            return (
                "Rollback cannot be executed automatically. Evidence graph contains deploy/failure "
                "correlation, so human should inspect changed files and error burst before approval."
            )
        return "Need more deployment evidence before rollback recommendation."
    if "deploy" in q or "change" in q or "before" in q:
        edges = record.get("evidence_edges") or []
        if edges:
            edge_types = ", ".join(str(edge.get("edge_type")) for edge in edges[:3])
            return f"Deployment evidence graph edges found: {edge_types}."
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
