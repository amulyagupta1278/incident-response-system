import json
import math
import re
from dataclasses import dataclass
from typing import Any

from agents.llm import complete_json, embed_texts, get_embedding_model, get_model, llm_available


RAG_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chunk_id": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["chunk_id", "label"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["answer", "citations"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class EvidenceChunk:
    chunk_id: str
    label: str
    source_type: str
    text: str


def build_incident_chunks(record: dict[str, Any]) -> list[EvidenceChunk]:
    chunks: list[EvidenceChunk] = []

    def add(source_type: str, label: str, text: Any) -> None:
        if text is None or text == "":
            return
        chunk_id: str = f"C{len(chunks) + 1:03d}"
        chunks.append(
            EvidenceChunk(
                chunk_id=chunk_id,
                label=label,
                source_type=source_type,
                text=_compact_text(text),
            )
        )

    add(
        "metadata",
        "Alert metadata",
        {
            "service": record.get("service"),
            "severity": record.get("severity"),
            "timestamp": record.get("timestamp"),
            "alert_description": record.get("alert_description"),
            "current_status": record.get("current_status"),
        },
    )
    add("root_cause", "Root cause", record.get("root_cause"))
    add("business_impact", "Revenue impact justification", record.get("revenue_impact_justification"))
    add("business_impact", "Affected users and revenue", {
        "affected_users": record.get("affected_users"),
        "estimated_revenue_impact_per_minute": record.get("estimated_revenue_impact_per_minute"),
    })

    for index, anomaly in enumerate(record.get("log_anomalies", []), start=1):
        add("log_anomaly", f"Log anomaly {index}: {anomaly.get('type')}", anomaly)

    log_cache: dict[str, Any] = record.get("log_context_cache") or {}
    for index, item in enumerate(log_cache.get("hierarchy", []), start=1):
        add("log_hierarchy", f"Log hierarchy {index}: {item.get('type')}", item)
    for index, ctx in enumerate(log_cache.get("error_contexts", []), start=1):
        add("log_context", f"Error context window {index}", ctx)

    for index, metric in enumerate(record.get("metric_anomalies", []), start=1):
        add("metric_anomaly", f"Metric anomaly {index}: {metric.get('metric_name')}", metric)

    for index, deployment in enumerate(record.get("deployment_changes", []), start=1):
        add("deployment", f"Deployment {index}", deployment)

    for index, rec in enumerate(record.get("recovery_recommendations", []), start=1):
        add("recovery", f"Recovery recommendation {index}", rec)

    for index, similar in enumerate(record.get("similar_incidents", []), start=1):
        add("memory", f"Similar incident {index}", similar)

    for index, invocation in enumerate(record.get("agent_invocations", []), start=1):
        add("audit", f"Agent invocation {index}: {invocation.get('agent')}", invocation)

    add("summary", "Engineering summary", record.get("engineering_summary"))
    add("summary", "Executive summary", record.get("executive_summary"))
    return chunks


async def answer_with_rag(record: dict[str, Any], question: str) -> dict[str, Any]:
    chunks: list[EvidenceChunk] = build_incident_chunks(record)
    if not chunks:
        return {
            "answer": "No incident evidence is available yet.",
            "source": "rag:none",
            "citations": [],
        }

    retrieved: list[EvidenceChunk] = await retrieve_chunks(question, chunks)
    context: str = "\n\n".join(
        f"[{chunk.chunk_id}] {chunk.label}\n{chunk.text}" for chunk in retrieved
    )
    allowed_ids: set[str] = {chunk.chunk_id for chunk in retrieved}
    prompt: str = (
        "Answer the incident question using only the retrieved evidence chunks. "
        "If the evidence is insufficient, say exactly what is missing. "
        "Cite every factual claim with chunk ids from the retrieved evidence.\n\n"
        f"Question: {question}\n\n"
        f"Retrieved evidence:\n{context}"
    )
    result: dict[str, Any] = await complete_json(
        system="You answer production incident questions with strict retrieval grounding.",
        prompt=prompt,
        schema=RAG_ANSWER_SCHEMA,
        schema_name="incident_rag_answer",
    )
    citations: list[dict[str, str]] = [
        {
            "chunk_id": str(item.get("chunk_id")),
            "label": _label_for_id(retrieved, str(item.get("chunk_id"))),
        }
        for item in result.get("citations", [])
        if str(item.get("chunk_id")) in allowed_ids
    ]
    answer: str = str(result.get("answer", "")).strip()
    if not citations:
        answer = (
            "Retrieved evidence was insufficient to produce a cited answer. "
            "Ask again after logs, metrics, RCA, or business impact evidence is available."
        )
    return {
        "answer": answer,
        "source": f"rag:llm:{get_model()}+{get_embedding_model()}",
        "citations": citations,
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


async def retrieve_chunks(
    question: str, chunks: list[EvidenceChunk], top_k: int = 8
) -> list[EvidenceChunk]:
    if llm_available():
        texts: list[str] = [question] + [chunk.text for chunk in chunks]
        vectors: list[list[float]] = await embed_texts(texts)
        query_vector: list[float] = vectors[0]
        scored: list[tuple[float, EvidenceChunk]] = [
            (_cosine(query_vector, vector), chunk)
            for chunk, vector in zip(chunks, vectors[1:])
        ]
    else:
        scored = [(_keyword_score(question, chunk.text), chunk) for chunk in chunks]

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[EvidenceChunk] = [chunk for score, chunk in scored[:top_k] if score > 0]
    return selected or chunks[: min(top_k, len(chunks))]


def _label_for_id(chunks: list[EvidenceChunk], chunk_id: str) -> str:
    for chunk in chunks:
        if chunk.chunk_id == chunk_id:
            return chunk.label
    return chunk_id


def _compact_text(value: Any) -> str:
    if isinstance(value, str):
        return value[:4000]
    return json.dumps(value, default=str, ensure_ascii=False)[:4000]


def _cosine(left: list[float], right: list[float]) -> float:
    numerator: float = sum(a * b for a, b in zip(left, right))
    left_norm: float = math.sqrt(sum(a * a for a in left))
    right_norm: float = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def _keyword_score(question: str, text: str) -> float:
    question_terms: set[str] = _terms(question)
    text_terms: set[str] = _terms(text)
    if not question_terms or not text_terms:
        return 0.0
    return len(question_terms & text_terms) / len(question_terms)


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(term) > 2}
