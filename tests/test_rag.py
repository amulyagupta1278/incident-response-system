import pytest

from agents.qa import answer_question
from agents.rag import answer_with_rag, build_incident_chunks, retrieve_chunks


def sample_record() -> dict:
    return {
        "incident_id": "rag-test",
        "timestamp": "2026-07-09T10:00:00Z",
        "service": "payment-api",
        "severity": "critical",
        "alert_description": "Database connection pool exhaustion detected",
        "current_status": "complete",
        "root_cause": {
            "hypothesis": "Database connection pool exhaustion",
            "confidence": 0.91,
            "supporting_evidence": [
                "checkout workers waited on database connections",
                "error logs show pool exhausted",
            ],
        },
        "log_anomalies": [
            {
                "type": "connection_error",
                "count": 38,
                "severity": "critical",
                "evidence": ["ERROR connection pool exhausted"],
            }
        ],
        "log_context_cache": {
            "total_logs_scanned": 120,
            "hierarchy": [
                {"severity": "critical", "type": "connection_error", "count": 38}
            ],
            "error_contexts": [
                {
                    "error": "connection pool exhausted",
                    "before": ["WARN pool wait rising"],
                    "after": ["ERROR checkout failed"],
                }
            ],
        },
        "affected_users": 4200,
        "estimated_revenue_impact_per_minute": 840.0,
        "revenue_impact_justification": {
            "formula": "affected_users * revenue_per_user_per_minute",
            "affected_users": 4200,
            "revenue_per_user_per_minute": 0.2,
            "revenue_impact_per_minute": 840.0,
            "lower_bound_per_minute": 420.0,
            "upper_bound_per_minute": 1260.0,
        },
        "recovery_recommendations": ["Increase database pool size"],
    }


def test_build_incident_chunks_includes_core_evidence() -> None:
    chunks = build_incident_chunks(sample_record())
    labels = {chunk.label for chunk in chunks}

    assert "Alert metadata" in labels
    assert "Root cause" in labels
    assert "Revenue impact justification" in labels
    assert any(chunk.source_type == "log_context" for chunk in chunks)


@pytest.mark.asyncio
async def test_retrieve_chunks_uses_keyword_fallback_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    chunks = build_incident_chunks(sample_record())

    retrieved = await retrieve_chunks("What proves connection pool exhaustion?", chunks, top_k=3)

    assert retrieved
    assert any("connection" in chunk.text.lower() for chunk in retrieved)


@pytest.mark.asyncio
async def test_answer_question_returns_cited_retrieval_fallback_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = await answer_question(sample_record(), "How is revenue calculated?")

    assert result["source"] == "rag:retrieval-fallback"
    assert "Revenue impact uses" in result["answer"]
    assert result["citations"]
    assert result["retrieved_chunks"]


@pytest.mark.asyncio
async def test_rag_refuses_uncited_llm_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete_json(**_: object) -> dict:
        return {"answer": "Unsupported claim", "citations": []}

    monkeypatch.setattr("agents.rag.llm_available", lambda: False)
    monkeypatch.setattr("agents.rag.complete_json", fake_complete_json)

    result = await answer_with_rag(sample_record(), "What is the root cause?")

    assert result["citations"] == []
    assert result["answer"].startswith("Retrieved evidence was insufficient")
