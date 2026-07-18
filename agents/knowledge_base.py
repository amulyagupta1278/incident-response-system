from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agents.connector_registry import runtime_connectors
from agents.obsidian_connector import load_obsidian_documents


KNOWLEDGE_DB_PATH = Path("data") / "knowledge_base.sqlite3"
DATA_UPLOADS_DIR = Path("data") / "uploads"
DEFAULT_QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "incident-knowledge")
DEFAULT_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    title: str
    source_path: str
    kind: str
    content: str
    updated_at: str
    tags: str = ""


@dataclass(frozen=True)
class KnowledgeHit:
    title: str
    source_path: str
    kind: str
    content: str
    score: float
    chunk_id: str
    tags: List[str]

    @property
    def citation(self) -> str:
        suffix = f"#{self.tags[0]}" if self.tags else ""
        return f"{self.source_path}{suffix}"


SOURCE_FILES = [
    "README.md",
    "ARCHITECTURE.md",
    "WORKFLOW.md",
    "SUBMISSION.md",
    "OPEN_SOURCE_STACK.md",
    "D_DRIVE_RUNBOOK.md",
    "web/README.md",
    ".env.example",
    "evals/golden/scenarios.json",
]
STRUCTURED_KNOWLEDGE_FILES = ["data/knowledge/demo_rag_documents.json"]


def _connect() -> sqlite3.Connection:
    KNOWLEDGE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(KNOWLEDGE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_knowledge_base(repo_root: str | os.PathLike[str] = ".") -> None:
    repo_root = str(Path(repo_root).resolve())
    docs = list(_load_seed_documents(Path(repo_root)))
    docs.extend(load_obsidian_documents(Path(repo_root)))
    docs.extend(_load_uploaded_documents())
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts
            USING fts5(chunk_id UNINDEXED, title, source_path, kind, tags, content, tokenize='unicode61 remove_diacritics 2')
            """
        )
        conn.execute("DELETE FROM knowledge_chunks")
        conn.execute("DELETE FROM knowledge_fts")
        for doc in docs:
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_chunks
                (chunk_id, title, source_path, kind, content, updated_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (doc.chunk_id, doc.title, doc.source_path, doc.kind, doc.content, doc.updated_at, doc.tags),
            )
            conn.execute(
                """
                INSERT INTO knowledge_fts
                (chunk_id, title, source_path, kind, tags, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (doc.chunk_id, doc.title, doc.source_path, doc.kind, doc.tags, doc.content),
            )
            _index_chunk_vector(doc)
        conn.commit()


def search_knowledge(
    query: str,
    *,
    max_results: int = 5,
    incident_context: dict[str, Any] | None = None,
) -> list[KnowledgeHit]:
    normalized_query = _normalize_query(query)
    if not normalized_query:
        return []

    vector_hits = _vector_search(query, max_results=max_results)
    lexical_hits: list[KnowledgeHit] = []
    with _connect() as conn:
        try:
            rows = conn.execute(
                """
                SELECT chunk_id, title, source_path, kind, tags, content, bm25(knowledge_fts) AS score
                FROM knowledge_fts
                WHERE knowledge_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (normalized_query, max(max_results * 8, 40)),
            ).fetchall()
            for row in rows:
                lexical_hits.append(
                    KnowledgeHit(
                        title=row["title"],
                        source_path=row["source_path"],
                        kind=row["kind"],
                        content=_snippet(row["content"], query),
                        score=_relevance_score(
                            query,
                            title=str(row["title"]),
                            tags=str(row["tags"] or ""),
                            content=str(row["content"]),
                            kind=str(row["kind"]),
                            bm25_score=float(row["score"]),
                        ),
                        chunk_id=row["chunk_id"],
                        tags=[tag for tag in str(row["tags"] or "").split(",") if tag],
                    )
                )
        except sqlite3.OperationalError:
            lexical_hits = _fallback_search(conn, query, max_results=max_results)

    local_vector_hits = [] if vector_hits else _local_vector_search(query, max_results=max_results)
    hits = _merge_vector_and_lexical_hits(vector_hits or local_vector_hits, lexical_hits, max_results)

    if incident_context:
        hits = _merge_incident_context_hits(query, incident_context, hits)

    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:max_results]


def build_knowledge_context(query: str, incident_context: dict[str, Any] | None = None) -> dict[str, Any]:
    hits = search_knowledge(query, incident_context=incident_context)
    return {
        "query": query,
        "results": [
            {
                "title": hit.title,
                "source_path": hit.source_path,
                "kind": hit.kind,
                "content": hit.content,
                "score": round(hit.score, 3),
                "citation": hit.citation,
            }
            for hit in hits
        ],
        "confidence": round(_confidence_from_hits(hits), 3),
        "retrieval_backend": retrieval_backend(),
    }


def _load_seed_documents(repo_root: Path) -> Iterable[KnowledgeChunk]:
    now = datetime.now().isoformat()
    for rel_path in SOURCE_FILES:
        source = repo_root / rel_path
        if not source.exists() or not source.is_file():
            continue
        try:
            content = source.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for index, chunk in enumerate(_chunk_document(content)):
            yield KnowledgeChunk(
                chunk_id=f"{rel_path}:{index}",
                title=_title_for_path(rel_path),
                source_path=rel_path,
                kind=_kind_for_path(rel_path),
                content=chunk,
                updated_at=now,
                tags=_tags_for_path(rel_path),
            )

    for rel_path in STRUCTURED_KNOWLEDGE_FILES:
        source = repo_root / rel_path
        if not source.exists():
            continue
        try:
            records = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(records, list):
            continue
        for index, item in enumerate(records):
            if not isinstance(item, dict) or not item.get("content"):
                continue
            tags = item.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            embedding_text = str(item.get("embedding_text", "")).strip()
            content = str(item["content"]).strip()
            if embedding_text:
                content = f"Retrieval summary: {embedding_text}\n\n{content}"
            yield KnowledgeChunk(
                chunk_id=str(item.get("id") or f"{rel_path}:{index}"),
                title=str(item.get("title") or f"Demo Knowledge {index + 1}"),
                source_path=rel_path,
                kind=str(item.get("kind") or "knowledge"),
                content=content,
                updated_at=now,
                tags=",".join(str(tag) for tag in tags if str(tag).strip()),
            )

    memory_path = repo_root / "data" / "incident_memory.json"
    if memory_path.exists():
        try:
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
        except Exception:
            payload = []
        if isinstance(payload, list):
            for index, item in enumerate(payload):
                if not isinstance(item, dict):
                    continue
                content = json.dumps(item, ensure_ascii=False, indent=2, default=str)
                yield KnowledgeChunk(
                    chunk_id=f"incident_memory:{index}",
                    title=f"Incident Memory {index + 1}",
                    source_path="data/incident_memory.json",
                    kind="memory",
                    content=content,
                    updated_at=now,
                    tags="incident,history,postmortem",
                )


def _load_uploaded_documents() -> Iterable[KnowledgeChunk]:
    if not DATA_UPLOADS_DIR.exists() or not DATA_UPLOADS_DIR.is_dir():
        return
    for file_path in sorted(DATA_UPLOADS_DIR.iterdir()):
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        yield KnowledgeChunk(
            chunk_id=f"upload:{file_path.name}",
            title=file_path.name,
            source_path=str(file_path),
            kind="uploaded",
            content=content,
            updated_at=datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
            tags="upload,doc",
        )


def insert_uploaded_document(content: str, filename: str) -> KnowledgeChunk:
    DATA_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(filename).name or f"uploaded-{uuid.uuid4().hex}.txt"
    target = DATA_UPLOADS_DIR / safe_filename
    count = 0
    while target.exists():
        count += 1
        target = DATA_UPLOADS_DIR / f"{Path(safe_filename).stem}-{count}{Path(safe_filename).suffix or '.txt'}"
    target.write_text(content, encoding="utf-8")
    chunk_id = f"upload:{target.name}:{uuid.uuid4().hex}"
    chunk = KnowledgeChunk(
        chunk_id=chunk_id,
        title=target.name,
        source_path=str(target),
        kind="uploaded",
        content=content,
        updated_at=datetime.now().isoformat(),
        tags="upload,doc",
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO knowledge_chunks
            (chunk_id, title, source_path, kind, content, updated_at, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk.chunk_id, chunk.title, chunk.source_path, chunk.kind, chunk.content, chunk.updated_at, chunk.tags),
        )
        conn.execute(
            """
            INSERT INTO knowledge_fts
            (chunk_id, title, source_path, kind, tags, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chunk.chunk_id, chunk.title, chunk.source_path, chunk.kind, chunk.tags, chunk.content),
        )
        conn.commit()
    _index_chunk_vector(chunk)
    return chunk


def upsert_knowledge_chunks(chunks: Iterable[KnowledgeChunk]) -> int:
    stored = 0
    chunk_list = list(chunks)
    with _connect() as conn:
        for chunk in chunk_list:
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_chunks
                (chunk_id, title, source_path, kind, content, updated_at, tags)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chunk.chunk_id, chunk.title, chunk.source_path, chunk.kind, chunk.content, chunk.updated_at, chunk.tags),
            )
            conn.execute(
                """
                INSERT INTO knowledge_fts
                (chunk_id, title, source_path, kind, tags, content)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chunk.chunk_id, chunk.title, chunk.source_path, chunk.kind, chunk.tags, chunk.content),
            )
            stored += 1
        conn.commit()

    for chunk in chunk_list:
        try:
            _index_chunk_vector(chunk)
        except Exception:
            continue
    return stored


def build_operational_knowledge_chunks(record: Dict[str, Any]) -> list[KnowledgeChunk]:
    incident_id = str(record.get("incident_id") or "unknown")
    now = datetime.now().isoformat()
    chunks: list[KnowledgeChunk] = []

    def _make_chunk(kind: str, title: str, source_path: str, content: str, tags: list[str]) -> KnowledgeChunk:
        chunk_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()[:10]
        chunk_id = f"incident:{incident_id}:{kind}:{chunk_hash}"
        return KnowledgeChunk(
            chunk_id=chunk_id,
            title=title,
            source_path=source_path,
            kind=kind,
            content=content,
            updated_at=now,
            tags=",".join([tag for tag in tags if tag]),
        )

    deployment_history = record.get("deployment_history") or record.get("deployment_changes") or []
    for index, item in enumerate(deployment_history):
        if not isinstance(item, dict):
            item = {"description": str(item)}
        description = " ".join(str(item.get(key, "")).strip() for key in ("changes", "description") if item.get(key))
        content = (
            f"Deployment {index + 1} for incident {incident_id}:\n"
            f"- timestamp: {item.get('timestamp')}\n"
            f"- version: {item.get('version')}\n"
            f"- source: {item.get('source')}\n"
            f"- changes: {description}\n"
        )
        chunks.append(
            _make_chunk(
                "deployment_history",
                f"Deployment history #{index + 1}",
                f"incident:{incident_id}:deployment_history",
                content,
                ["deployment", "history", "incident"],
            )
        )

    configuration_changes = record.get("configuration_changes") or []
    for index, item in enumerate(configuration_changes):
        if not isinstance(item, dict):
            item = {"description": str(item)}
        content = (
            f"Configuration change {index + 1} for incident {incident_id}:\n"
            f"- source: {item.get('source')}\n"
            f"- timestamp: {item.get('timestamp')}\n"
            f"- description: {item.get('description') or item.get('changes')}\n"
        )
        chunks.append(
            _make_chunk(
                "configuration_change",
                f"Configuration change #{index + 1}",
                f"incident:{incident_id}:configuration_changes",
                content,
                ["configuration", "incident"],
            )
        )

    runbooks = record.get("runbooks") or []
    for index, runbook in enumerate(runbooks):
        content = f"Incident {incident_id} references runbook: {runbook}"
        chunks.append(
            _make_chunk(
                "runbook_reference",
                f"Runbook reference #{index + 1}",
                f"incident:{incident_id}:runbooks",
                content,
                ["runbook", "incident"],
            )
        )

    business_criticality = str(record.get("business_criticality") or record.get("business_risk_level") or "").strip()
    if business_criticality:
        content = (
            f"Business criticality for incident {incident_id}: {business_criticality}\n"
            f"Service: {record.get('service')}\n"
            f"Severity: {record.get('severity')}\n"
            f"Impact: {record.get('estimated_revenue_impact_per_minute') or record.get('estimated_cost_impact_per_minute')}\n"
        )
        chunks.append(
            _make_chunk(
                "business_criticality",
                "Business criticality summary",
                f"incident:{incident_id}:business_criticality",
                content,
                ["business_criticality", "impact", "incident"],
            )
        )

    related_incidents = record.get("related_incidents") or record.get("similar_incidents") or []
    if related_incidents:
        related_list: list[str] = []
        for item in related_incidents:
            if isinstance(item, dict):
                related_list.append(str(item.get("incident_id") or item.get("id") or item))
            else:
                related_list.append(str(item))
        content = f"Related incidents for {incident_id}: {', '.join(related_list)}"
        chunks.append(
            _make_chunk(
                "related_incidents",
                "Related incident references",
                f"incident:{incident_id}:related_incidents",
                content,
                ["related_incidents", "incident"],
            )
        )

    return chunks


def _qdrant_store() -> Optional[Tuple["QdrantClient", str]]:
    records = runtime_connectors("qdrant")
    if not records:
        return None
    record = sorted(records, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[0]
    config = record.get("config") or {}
    endpoint = str(config.get("endpoint") or os.getenv("QDRANT_URL") or "").strip()
    if not endpoint:
        return None
    api_key_env = str(config.get("api_key_env") or "").strip()
    api_key = os.getenv(api_key_env, "").strip() if api_key_env else str(config.get("api_key") or "").strip()
    collection = str(config.get("collection") or os.getenv("QDRANT_COLLECTION") or DEFAULT_QDRANT_COLLECTION).strip()
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=endpoint, api_key=api_key or None, prefer_grpc=False)
        return client, collection
    except Exception:
        return None


def _get_embedding_model_key() -> str:
    return str(os.getenv("OPENAI_EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL).strip()


def _embed_texts(texts: list[str]) -> list[list[float]]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OpenAI API key is required for embedding generation.")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, api_base=os.getenv("OPENAI_BASE_URL", "") or None)
        response = client.embeddings.create(
            model=_get_embedding_model_key(),
            input=texts,
        )
        return [item.embedding for item in response.data]
    except Exception as exc:
        raise RuntimeError(f"Embedding generation failed: {exc}") from exc


def _index_chunk_vector(chunk: KnowledgeChunk) -> None:
    store = _qdrant_store()
    if not store:
        return
    client, collection = store
    try:
        embedding = _embed_texts([chunk.content])[0]
        from qdrant_client.http.models import PointStruct, Distance

        try:
            client.get_collection(collection_name=collection)
        except Exception:
            client.recreate_collection(
                collection_name=collection,
                vectors={"size": len(embedding), "distance": Distance.COSINE},
            )
        payload = {
            "title": chunk.title,
            "source_path": chunk.source_path,
            "kind": chunk.kind,
            "content": chunk.content,
            "tags": chunk.tags,
        }
        point = PointStruct(id=chunk.chunk_id, vector=embedding, payload=payload)
        client.upsert(collection_name=collection, points=[point])
    except Exception:
        return


def _vector_search(query: str, *, max_results: int = 5) -> list[KnowledgeHit]:
    store = _qdrant_store()
    if not store:
        return []
    client, collection = store
    try:
        embedding = _embed_texts([query])[0]
        points = client.search(
            collection_name=collection,
            query_vector=embedding,
            limit=max_results,
            with_payload=True,
            with_vectors=False,
        )
    except Exception:
        return []

    hits: list[KnowledgeHit] = []
    for point in points:
        payload = getattr(point, "payload", None) or {}
        content = str(payload.get("content") or "")
        title = str(payload.get("title") or payload.get("source_path") or "uploaded document")
        kind = str(payload.get("kind") or "uploaded")
        tags = [tag for tag in str(payload.get("tags") or "").split(",") if tag]
        hits.append(
            KnowledgeHit(
                title=title,
                source_path=str(payload.get("source_path") or "qdrant://unknown"),
                kind=kind,
                content=_snippet(content, query),
                score=float(getattr(point, "score", 0.0)) * 1.2,
                chunk_id=str(point.id),
                tags=tags,
            )
        )
    return hits



def retrieval_backend() -> Dict[str, Any]:
    """Expose the active RAG backend so tests/UI can show whether semantic retrieval is live."""
    if _qdrant_store():
        return {"primary": "qdrant", "fallback": "sqlite_fts", "semantic": True}
    return {"primary": "local_semantic_vectors", "fallback": "sqlite_fts", "semantic": True}


def _hash_vector(text: str, dimensions: int = 96) -> list[float]:
    vec = [0.0] * dimensions
    for term in _terms(text):
        import hashlib
        digest = hashlib.sha256(term.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % dimensions
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _local_vector_search(query: str, *, max_results: int = 5) -> list[KnowledgeHit]:
    """Deterministic local semantic fallback when Qdrant/OpenAI embeddings are unavailable.

    This keeps the Vector RAG layer testable in offline demos: Qdrant remains the
    preferred backend, while hashed token vectors provide semantic-ish retrieval
    instead of degrading to docs-only FTS.
    """
    query_vec = _hash_vector(query)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT chunk_id, title, source_path, kind, tags, content FROM knowledge_chunks"
        ).fetchall()
    hits: list[KnowledgeHit] = []
    for row in rows:
        content = str(row["content"] or "")
        material = f"{row['title']} {row['source_path']} {row['kind']} {row['tags']} {content}"
        score = max(0.0, _cosine(query_vec, _hash_vector(material)))
        if score <= 0.02:
            continue
        hits.append(
            KnowledgeHit(
                title=str(row["title"]),
                source_path=str(row["source_path"]),
                kind=str(row["kind"]),
                content=_snippet(content, query),
                score=min(1.0, 0.35 + score),
                chunk_id=str(row["chunk_id"]),
                tags=[tag for tag in str(row["tags"] or "").split(",") if tag],
            )
        )
    hits.sort(key=lambda item: item.score, reverse=True)
    return hits[:max_results]


def _merge_vector_and_lexical_hits(
    vector_hits: list[KnowledgeHit],
    lexical_hits: list[KnowledgeHit],
    max_results: int,
) -> list[KnowledgeHit]:
    if not vector_hits:
        return lexical_hits[:max_results]
    merged: dict[str, KnowledgeHit] = {hit.chunk_id: hit for hit in vector_hits}
    for hit in lexical_hits:
        if hit.chunk_id in merged:
            existing = merged[hit.chunk_id]
            merged[hit.chunk_id] = KnowledgeHit(
                title=existing.title,
                source_path=existing.source_path,
                kind=existing.kind,
                content=existing.content,
                score=max(existing.score, hit.score) + 0.1,
                chunk_id=existing.chunk_id,
                tags=existing.tags,
            )
        else:
            merged[hit.chunk_id] = hit
    return sorted(merged.values(), key=lambda item: item.score, reverse=True)[:max_results]


def _fallback_search(conn: sqlite3.Connection, query: str, *, max_results: int) -> list[KnowledgeHit]:
    rows = conn.execute(
        "SELECT chunk_id, title, source_path, kind, tags, content FROM knowledge_chunks"
    ).fetchall()
    query_terms = _terms(query)
    scored: list[KnowledgeHit] = []
    for row in rows:
        content = str(row["content"])
        title = str(row["title"])
        haystack_terms = _terms(f"{title} {row['source_path']} {content}")
        overlap = len(query_terms & haystack_terms)
        if not overlap:
            continue
        score = min(1.0, overlap / max(len(query_terms), 1))
        scored.append(
            KnowledgeHit(
                title=title,
                source_path=str(row["source_path"]),
                kind=str(row["kind"]),
                content=_snippet(content, query),
                score=score,
                chunk_id=str(row["chunk_id"]),
                tags=[tag for tag in str(row["tags"] or "").split(",") if tag],
            )
        )
    scored.sort(key=lambda item: item.score, reverse=True)
    return scored[:max_results]


def _merge_incident_context_hits(query: str, incident_context: dict[str, Any], hits: list[KnowledgeHit]) -> list[KnowledgeHit]:
    synthetic = _incident_context_chunk(query, incident_context)
    if synthetic:
        hits = [synthetic, *hits]
    return hits


def _incident_context_chunk(query: str, incident_context: dict[str, Any]) -> KnowledgeHit | None:
    payload = {
        "service": incident_context.get("service"),
        "alert_description": incident_context.get("alert_description"),
        "root_cause": (incident_context.get("root_cause") or {}).get("hypothesis"),
        "confidence": (incident_context.get("root_cause") or {}).get("confidence"),
        "affected_users": incident_context.get("affected_users"),
        "revenue_impact_per_minute": incident_context.get("estimated_revenue_impact_per_minute"),
        "cost_impact_per_minute": incident_context.get("estimated_cost_impact_per_minute"),
        "troubleshooting_plan": incident_context.get("troubleshooting_plan", []),
        "stakeholder_updates": incident_context.get("stakeholder_updates", {}),
    }
    if not any(value for value in payload.values()):
        return None
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return KnowledgeHit(
        title=f"Incident Context: {incident_context.get('service', 'unknown')}",
        source_path=f"incident://{incident_context.get('incident_id', 'live')}",
        kind="incident",
        content=_snippet(text, query, max_chars=360),
        score=0.92,
        chunk_id=f"incident:{incident_context.get('incident_id', 'live')}",
        tags=["incident", "live", "context"],
    )


def _confidence_from_hits(hits: list[KnowledgeHit]) -> float:
    if not hits:
        return 0.0
    top = hits[0].score
    spread = min(1.0, sum(hit.score for hit in hits[:3]) / max(len(hits[:3]), 1))
    return min(0.98, (top * 0.7) + (spread * 0.3))


def _relevance_score(
    query: str,
    *,
    title: str,
    tags: str,
    content: str,
    kind: str,
    bm25_score: float,
) -> float:
    """Rerank lexical candidates using query intent and metadata.

    FTS5 BM25 values are negative with more-negative values representing a
    stronger match, so treating their absolute value as distance reverses the
    order. This bounded score combines token overlap, title/tag matches, BM25,
    and the knowledge type expected for common incident-response intents.
    """
    query_terms = _search_terms(query)
    body_terms = _search_terms(content)
    metadata_terms = _search_terms(f"{title} {tags}")
    if not query_terms:
        return 0.0
    body_overlap = len(query_terms & body_terms) / len(query_terms)
    metadata_overlap = len(query_terms & metadata_terms) / len(query_terms)
    bm25_strength = min(1.0, abs(bm25_score) / 12.0)
    score = (body_overlap * 0.55) + (metadata_overlap * 0.25) + (bm25_strength * 0.20)

    q = query.lower()
    if any(term in q for term in ("fix", "remedi", "recover", "runbook", "mitigat", "prevent")):
        if kind == "runbook":
            score += 0.35
    if any(term in q for term in ("similar", "compare", "difference", "versus", " vs ")):
        if kind == "similar-incident":
            score += 0.35
    if any(term in q for term in ("impact", "user", "revenue", "cost")):
        if kind in {"postmortem", "service-profile"}:
            score += 0.22
    if any(term in q for term in ("why", "root cause", "happened")) and kind == "postmortem":
        score += 0.18
    return min(0.99, score)


_SEARCH_STOP_WORDS = {
    "a", "an", "and", "are", "can", "did", "do", "for", "from", "how",
    "in", "is", "it", "many", "me", "of", "or", "the", "this", "to",
    "was", "we", "were", "what", "when", "why", "with", "you",
}
_SEARCH_ALIASES = {
    "db": "database",
    "incidence": "incident",
    "retries": "retry",
    "users": "user",
}


def _search_terms(text: str) -> set[str]:
    return {
        _SEARCH_ALIASES.get(term, term)
        for term in re.sub(r"[^\w\u0900-\u097F]+", " ", text.lower()).split()
        if term and term not in _SEARCH_STOP_WORDS
    }


def _snippet(content: str, query: str, *, max_chars: int = 420) -> str:
    plain = re.sub(r"\s+", " ", content).strip()
    terms = _terms(query)
    lower = plain.lower()
    match_index = -1
    for term in terms:
        idx = lower.find(term)
        if idx != -1:
            match_index = idx
            break
    if match_index == -1:
        return plain[:max_chars]
    start = max(0, match_index - 120)
    end = min(len(plain), match_index + max_chars - 120)
    return plain[start:end].strip()


def _chunk_document(content: str, *, max_chars: int = 1100) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", content) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}".strip()
        else:
            if current:
                chunks.append(current.strip())
            current = paragraph
    if current:
        chunks.append(current.strip())
    return chunks or [content.strip()]


def _normalize_query(query: str) -> str:
    terms = [term for term in _terms(query) if len(term) > 1]
    return " OR ".join(dict.fromkeys(terms))


def _terms(text: str) -> set[str]:
    return {
        term
        for term in re.sub(r"[^\w\u0900-\u097F]+", " ", text.lower()).split()
        if term
    }


def _title_for_path(path: str) -> str:
    return Path(path).stem.replace("_", " ").title()


def _kind_for_path(path: str) -> str:
    if path.endswith(".json"):
        return "scenario"
    if path.endswith(".md"):
        return "doc"
    if path.endswith(".example"):
        return "config"
    return "file"


def _tags_for_path(path: str) -> str:
    lower = path.lower()
    tags = []
    if "workflow" in lower:
        tags.append("workflow")
    if "architecture" in lower:
        tags.append("architecture")
    if "runbook" in lower:
        tags.append("runbook")
    if "incident" in lower:
        tags.append("incident")
    if "voice" in lower:
        tags.append("voice")
    if "rag" in lower:
        tags.append("rag")
    return ",".join(tags)


# ---------------------------------------------------------------------------
# Knowledge-Graph + Vector RAG layer
# ---------------------------------------------------------------------------
# A thin knowledge-graph (KG) + OFFLINE vector-RAG layer built on top of the
# existing SQLite FTS implementation. `build_knowledge_context` and every other
# existing function are preserved unchanged for backward compatibility.
#
# The KG is an in-memory directed graph linking:
#     service -> incident -> root_cause -> deployment
# together with recovery/recommendation nodes. `kg_query` seeds facts from FTS
# and then walks the graph so related facts surface together -- every fact carries
# a citation string. `vector_search` is an OFFLINE semantic path: it tries
# sentence-transformers (a local model, no network) and otherwise falls back to a
# TF-IDF / keyword-overlap scorer over the same SQLite docs. All new functions
# degrade gracefully (empty results, confidence 0.0) when a model/embedding backend
# is unavailable.
# ---------------------------------------------------------------------------


@dataclass
class _KGNode:
    node_id: str
    node_type: str  # service | incident | root_cause | deployment | recovery
    label: str
    citation: str
    confidence: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)


class KnowledgeGraph:
    """In-memory relationship graph: service -> incident -> root_cause -> deployment."""

    def __init__(self) -> None:
        self.nodes: Dict[str, _KGNode] = {}
        self.edges: Dict[str, set] = {}  # node_id -> set(node_id)

    def add_node(self, node: _KGNode) -> None:
        self.nodes[node.node_id] = node
        self.edges.setdefault(node.node_id, set())

    def relate(self, source: str, target: str) -> None:
        self.edges.setdefault(source, set()).add(target)
        self.edges.setdefault(target, set()).add(source)

    def neighbors(self, node_id: str) -> List[_KGNode]:
        result: List[_KGNode] = []
        for nbr in self.edges.get(node_id, set()):
            node = self.nodes.get(nbr)
            if node is not None:
                result.append(node)
        return result

    def nodes_of_type(self, node_type: str) -> List[_KGNode]:
        return [n for n in self.nodes.values() if n.node_type == node_type]

    def match_service(self, service: str) -> List[_KGNode]:
        service = (service or "").strip().lower()
        if not service:
            return []
        matched: List[_KGNode] = []
        for node in self.nodes.values():
            if node.node_type == "service" and service in node.label.lower():
                matched.append(node)
        return matched

    def clear(self) -> None:
        self.nodes.clear()
        self.edges.clear()


# Module-level singleton graph, shared across queries within a process.
_KNOWLEDGE_GRAPH = KnowledgeGraph()


# ---------------------------------------------------------------------------
# Durable persistence for the KG + vector corpus (SQLite)
# ---------------------------------------------------------------------------
# The in-memory graph above does not survive process restarts. To make the
# Knowledge-Graph + Vector-RAG layer durable we mirror nodes/edges (and the
# vector corpus chunks) into a dedicated SQLite database, consistent with the
# existing FTS usage elsewhere in this module. Everything degrades gracefully:
# if the KG database is unavailable for any reason we keep the existing
# in-memory behaviour and log a warning instead of crashing.
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

KG_DB_PATH = Path("data") / "knowledge_graph.sqlite3"

_kg_db_conn: Optional[sqlite3.Connection] = None
_kg_db_available: bool = True
_kg_loaded: bool = False


def _ensure_kg_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS kg_nodes (
            node_id   TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            label     TEXT NOT NULL,
            citation  TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.0,
            payload   TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            PRIMARY KEY (source, target)
        );
        CREATE TABLE IF NOT EXISTS vector_docs (
            doc_key         TEXT PRIMARY KEY,
            service         TEXT NOT NULL DEFAULT '',
            incident_id     TEXT NOT NULL DEFAULT '',
            root_cause_hash TEXT NOT NULL DEFAULT '',
            kind            TEXT NOT NULL DEFAULT '',
            content         TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT '',
            embedding       TEXT,
            metadata        TEXT NOT NULL DEFAULT '{}',
            updated_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vector_docs_incident ON vector_docs(incident_id);
        CREATE INDEX IF NOT EXISTS idx_vector_docs_service  ON vector_docs(service);
        """
    )


def _kg_db() -> Optional[sqlite3.Connection]:
    """Lazily open the durable KG database, guarding against a missing data/ dir.

    Returns None if the database cannot be opened so callers can transparently
    fall back to the existing in-memory behaviour.
    """
    global _kg_db_conn, _kg_db_available
    if not _kg_db_available:
        return None
    if _kg_db_conn is not None:
        return _kg_db_conn
    try:
        KG_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(KG_DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_kg_schema(conn)
        conn.commit()
        _kg_db_conn = conn
        return conn
    except Exception as exc:  # pragma: no cover - environment failure path
        _kg_db_available = False
        logger.warning(
            "KG SQLite persistence unavailable; falling back to in-memory graph: %s", exc
        )
        return None


def _kg_load_from_db() -> None:
    """Load the historical graph + vector corpus into the in-memory structures.

    Runs at most once per process. Safe to call repeatedly.
    """
    global _kg_loaded
    if _kg_loaded:
        return
    _kg_loaded = True
    conn = _kg_db()
    if conn is None:
        return
    try:
        rows = conn.execute(
            "SELECT node_id, node_type, label, citation, confidence, payload FROM kg_nodes"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"]) if row["payload"] else {}
            except Exception:
                payload = {}
            _KNOWLEDGE_GRAPH.add_node(
                _KGNode(
                    node_id=row["node_id"],
                    node_type=row["node_type"],
                    label=row["label"],
                    citation=row["citation"],
                    confidence=float(row["confidence"] or 0.0),
                    payload=payload,
                )
            )
        edge_rows = conn.execute("SELECT source, target FROM kg_edges").fetchall()
        for row in edge_rows:
            _KNOWLEDGE_GRAPH.relate(row["source"], row["target"])
    except Exception as exc:  # pragma: no cover - environment failure path
        logger.warning("Failed to load historical KG from SQLite; using in-memory graph: %s", exc)


def _persist_node(node: _KGNode) -> None:
    conn = _kg_db()
    if conn is None:
        return
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO kg_nodes
            (node_id, node_type, label, citation, confidence, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                node.node_id,
                node.node_type,
                node.label,
                node.citation,
                node.confidence,
                json.dumps(node.payload, ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
    except Exception:  # pragma: no cover - environment failure path
        pass


def _persist_edge(source: str, target: str) -> None:
    conn = _kg_db()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT OR REPLACE INTO kg_edges (source, target) VALUES (?, ?)",
            (source, target),
        )
        conn.commit()
    except Exception:  # pragma: no cover - environment failure path
        pass


def _persist_vector_doc(
    service: str,
    incident_id: str,
    root_cause_hash: str,
    chunk: KnowledgeChunk,
) -> None:
    """Persist a KG-derived corpus chunk so vector_search can rebuild its index.

    Keyed by the stable chunk_id (which already incorporates a content hash),
    making re-indexing idempotent. The embedding column is left for optional
    precomputed vectors; vector_search rebuilds on demand from stored content.
    """
    conn = _kg_db()
    if conn is None:
        return
    try:
        metadata = json.dumps(
            {"title": chunk.title, "tags": chunk.tags}, ensure_ascii=False
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO vector_docs
            (doc_key, service, incident_id, root_cause_hash, kind, content, source, embedding, metadata, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_id,
                service,
                incident_id,
                root_cause_hash,
                chunk.kind,
                chunk.content,
                chunk.source_path,
                None,
                metadata,
                chunk.updated_at,
            ),
        )
        conn.commit()
    except Exception:  # pragma: no cover - environment failure path
        pass


# Load any historical graph on module import so incidents indexed in previous
# runs are available immediately. Degrades to in-memory if the DB is unavailable.
_kg_load_from_db()


def kg_query(query: str, incident_context: dict | None = None) -> dict:
    """Query the KG + FTS for CITED, confident evidence.

    Returns {"facts": [{"fact", "citation", "confidence"}, ...], "confidence": float}.
    Degrades gracefully: empty facts and 0.0 confidence when nothing matches.
    """
    incident_context = incident_context or {}
    facts: List[Dict[str, Any]] = []
    seen: set = set()

    def _add_fact(fact: str, citation: str, confidence: float) -> None:
        fact = (fact or "").strip()
        citation = (citation or "").strip()
        if not fact or not citation or citation in seen:
            return
        seen.add(citation)
        facts.append({
            "fact": _snippet(fact, query, max_chars=600),
            "citation": citation,
            "confidence": round(float(confidence), 3),
        })

    # 1) Seed facts from the existing SQLite FTS / hybrid search.
    try:
        seed_hits = search_knowledge(query, incident_context=incident_context)
    except Exception:
        seed_hits = []
    for hit in seed_hits:
        _add_fact(hit.content, hit.citation, hit.score)

    # 2) Walk the KG from the incident's service (or a service named in the query)
    #    so related incidents, root causes, deployments and recovery steps surface
    #    together with their citations.
    context_service = str(incident_context.get("service") or "")
    query_service = context_service
    if not query_service:
        for node in _KNOWLEDGE_GRAPH.nodes_of_type("service"):
            if node.label.lower() in query.lower():
                query_service = node.label
                break

    seed_nodes = _KNOWLEDGE_GRAPH.match_service(query_service) if query_service else []
    query_terms = _terms(query)
    for node in _KNOWLEDGE_GRAPH.nodes.values():
        haystack = _terms(f"{node.label} {node.citation} {json.dumps(node.payload, default=str)}")
        if query_terms & haystack:
            seed_nodes.append(node)

    visited: set = set()
    for node in seed_nodes:
        if node.node_id in visited:
            continue
        visited.add(node.node_id)
        _add_fact(node.label, node.citation, node.confidence)
        for nbr in _KNOWLEDGE_GRAPH.neighbors(node.node_id):
            if nbr.node_id in visited:
                continue
            visited.add(nbr.node_id)
            _add_fact(nbr.label, nbr.citation, nbr.confidence)

    if not facts:
        return {"facts": [], "confidence": 0.0}

    facts.sort(key=lambda f: f["confidence"], reverse=True)
    top = max(f["confidence"] for f in facts)
    spread = sum(f["confidence"] for f in facts[:3]) / max(len(facts[:3]), 1)
    overall = round(min(0.98, (top * 0.7) + (spread * 0.3)), 3)
    return {"facts": facts, "confidence": overall}


def vector_search(content: str, top_k: int = 5) -> List[dict]:
    """Offline semantic search over the same SQLite docs.

    Tries sentence-transformers (a local model, no network) first; otherwise falls
    back to a TF-IDF / keyword-overlap scorer over the SQLite knowledge chunks.
    Always returns a list of {"content", "score", "source", "metadata"} dicts.
    """
    top_k = max(1, int(top_k))
    content = (content or "").strip()
    if not content:
        return []
    try:
        return _vector_search_sentence_transformers(content, top_k)
    except Exception:
        pass
    try:
        return _vector_search_tfidf(content, top_k)
    except Exception:
        return []


def _load_all_chunks() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT chunk_id, title, source_path, kind, tags, content FROM knowledge_chunks"
        ).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        items.append({
            "chunk_id": str(row["chunk_id"]),
            "title": str(row["title"]),
            "source_path": str(row["source_path"]),
            "kind": str(row["kind"]),
            "tags": [t for t in str(row["tags"] or "").split(",") if t],
            "content": str(row["content"] or ""),
        })
    return items


def _vector_search_sentence_transformers(content: str, top_k: int) -> List[dict]:
    from sentence_transformers import SentenceTransformer

    model_name = os.getenv("SENTENCE_TRANSFORMER_MODEL", "all-MiniLM-L6-v2")
    model = SentenceTransformer(model_name)  # local; no network when cached
    chunks = _load_all_chunks()
    if not chunks:
        return []
    corpus = [c["content"] for c in chunks]
    embeddings = model.encode([content] + corpus, normalize_embeddings=True)
    query_vec = embeddings[0]
    scored: List[dict] = []
    for idx, chunk in enumerate(chunks):
        score = float(query_vec @ embeddings[idx + 1])
        if score <= 0.0:
            continue
        scored.append({
            "content": _snippet(chunk["content"], content),
            "score": round(min(1.0, max(0.0, score)), 4),
            "source": chunk["source_path"],
            "metadata": {
                "chunk_id": chunk["chunk_id"],
                "title": chunk["title"],
                "kind": chunk["kind"],
                "tags": chunk["tags"],
            },
        })
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top_k]


def _vector_search_tfidf(content: str, top_k: int) -> List[dict]:
    chunks = _load_all_chunks()
    if not chunks:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer

        vectorizer = TfidfVectorizer(stop_words="english")
        matrix = vectorizer.fit_transform([content] + [c["content"] for c in chunks])
        query_vec = matrix[0]
        scores = (matrix[1:] @ query_vec.T).toarray().ravel()
        score_iter = list(enumerate(scores))
    except Exception:
        # Keyword-overlap fallback when sklearn is unavailable.
        q_terms = _terms(content)
        score_iter = []
        for idx, chunk in enumerate(chunks):
            c_terms = _terms(chunk["content"])
            overlap = len(q_terms & c_terms)
            score_iter.append((idx, overlap / max(len(q_terms), 1)))

    scored: List[dict] = []
    for idx, score in score_iter:
        score = float(score)
        if score <= 0.0:
            continue
        chunk = chunks[idx]
        scored.append({
            "content": _snippet(chunk["content"], content),
            "score": round(min(1.0, max(0.0, score)), 4),
            "source": chunk["source_path"],
            "metadata": {
                "chunk_id": chunk["chunk_id"],
                "title": chunk["title"],
                "kind": chunk["kind"],
                "tags": chunk["tags"],
            },
        })
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:top_k]


def _kg_chunk(incident_id: str, kind: str, source_path: str, content: str, tags: list, now: str) -> KnowledgeChunk:
    chunk_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()[:10]
    return KnowledgeChunk(
        chunk_id=f"kg:{incident_id}:{kind}:{chunk_hash}",
        title=f"{kind.replace('_', ' ').title()} ({incident_id})",
        source_path=source_path,
        kind=kind,
        content=content,
        updated_at=now,
        tags=",".join(tags),
    )


def index_incident(incident_state) -> None:
    """Extract facts from a completed IncidentState and index them into the KG + vector store.

    `incident_state` may be an IncidentState dataclass or a plain dict. Facts about the
    service, root cause, deployment correlation and recovery steps are upserted with
    citations (into the in-memory KG and the SQLite FTS/vector corpus) so future
    queries can match them. Degrades gracefully: never raises on missing fields.
    """
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    incident_id = str(_get(incident_state, "incident_id") or "unknown")
    service = str(_get(incident_state, "service") or "unknown")
    severity = str(_get(incident_state, "severity") or "unknown")
    rca_confidence = float(_get(incident_state, "rca_confidence") or 0.0)
    now = datetime.now().isoformat()

    # Stable idempotency key for the whole incident's facts: service + incident_id
    # + a hash of the root-cause hypothesis. Re-indexing the same incident must
    # not duplicate facts, so every persisted row below is keyed deterministically.
    root_cause = _get(incident_state, "root_cause") or {}
    hypothesis = ""
    if isinstance(root_cause, dict):
        hypothesis = root_cause.get("hypothesis") or root_cause.get("summary") or ""
    root_cause_hash = hashlib.sha1(
        f"{service}|{incident_id}|{hypothesis}".encode("utf-8")
    ).hexdigest()[:16]

    service_id = f"service:{service.lower()}"
    incident_node_id = f"incident:{incident_id}"
    _KNOWLEDGE_GRAPH.add_node(_KGNode(
        node_id=service_id, node_type="service", label=service,
        citation=f"service-profile:{service}", confidence=0.0,
        payload={"service": service},
    ))
    _persist_node(_KNOWLEDGE_GRAPH.nodes[service_id])
    _KNOWLEDGE_GRAPH.add_node(_KGNode(
        node_id=incident_node_id, node_type="incident",
        label=f"Incident {incident_id} ({service})",
        citation=f"incident:{incident_id}", confidence=rca_confidence,
        payload={"service": service, "severity": severity, "incident_id": incident_id},
    ))
    _persist_node(_KNOWLEDGE_GRAPH.nodes[incident_node_id])
    _KNOWLEDGE_GRAPH.relate(service_id, incident_node_id)
    _persist_edge(service_id, incident_node_id)

    chunks: List[KnowledgeChunk] = []

    # Root cause(s)
    root_cause = _get(incident_state, "root_cause") or {}
    hypothesis = ""
    if isinstance(root_cause, dict):
        hypothesis = root_cause.get("hypothesis") or root_cause.get("summary") or ""
        confidence = float(root_cause.get("confidence") or rca_confidence or 0.0)
        if hypothesis:
            rc_id = f"rc:{incident_id}"
            _KNOWLEDGE_GRAPH.add_node(_KGNode(
                node_id=rc_id, node_type="root_cause",
                label=f"Root cause: {hypothesis}",
                citation=f"incident:{incident_id}:root_cause", confidence=confidence,
                payload={"hypothesis": hypothesis, "confidence": confidence},
            ))
            _persist_node(_KNOWLEDGE_GRAPH.nodes[rc_id])
            _KNOWLEDGE_GRAPH.relate(incident_node_id, rc_id)
            _persist_edge(incident_node_id, rc_id)
            chunks.append(_kg_chunk(
                incident_id, "root_cause", f"incident:{incident_id}:root_cause",
                f"Root cause for incident {incident_id} ({service}): {hypothesis}",
                ["root_cause", "incident", "postmortem"], now,
            ))

    # Deployment correlation
    deployments = _get(incident_state, "deployment_changes") or _get(incident_state, "deployment_analysis") or []
    if isinstance(deployments, dict):
        deployments = [deployments]
    if isinstance(deployments, list):
        for idx, dep in enumerate(deployments):
            if not isinstance(dep, dict):
                dep = {"description": str(dep)}
            version = str(dep.get("version") or dep.get("source") or f"deploy-{idx + 1}")
            description = " ".join(str(dep.get(k, "")) for k in ("changes", "description") if dep.get(k)).strip()
            dep_id = f"deploy:{incident_id}:{version}"
            _KNOWLEDGE_GRAPH.add_node(_KGNode(
                node_id=dep_id, node_type="deployment",
                label=f"Deployment {version}: {description[:120]}",
                citation=f"incident:{incident_id}:deployment:{version}", confidence=0.0,
                payload={"version": version, "description": description},
            ))
            _persist_node(_KNOWLEDGE_GRAPH.nodes[dep_id])
            _KNOWLEDGE_GRAPH.relate(incident_node_id, dep_id)
            _persist_edge(incident_node_id, dep_id)
            if hypothesis:
                _KNOWLEDGE_GRAPH.relate(f"rc:{incident_id}", dep_id)
                _persist_edge(f"rc:{incident_id}", dep_id)
            chunks.append(_kg_chunk(
                incident_id, "deployment", f"incident:{incident_id}:deployment:{version}",
                f"Deployment {version} correlated with incident {incident_id}: {description}",
                ["deployment", "incident"], now,
            ))

    # Recovery steps / recommendations
    recovery = _get(incident_state, "recovery_recommendations") or []
    recovery_plan = _get(incident_state, "recovery_plan") or {}
    if isinstance(recovery_plan, dict):
        steps = recovery_plan.get("steps") or recovery_plan.get("actions") or []
        if isinstance(steps, list):
            recovery = list(recovery) + [s for s in steps if isinstance(s, str)]
    if isinstance(recovery, list):
        for idx, step in enumerate(recovery):
            step_text = str(step).strip()
            if not step_text:
                continue
            rec_id = f"recovery:{incident_id}:{idx}"
            _KNOWLEDGE_GRAPH.add_node(_KGNode(
                node_id=rec_id, node_type="recovery",
                label=f"Recovery step {idx + 1}: {step_text}",
                citation=f"incident:{incident_id}:recovery:{idx}",
                confidence=min(1.0, 0.6 + rca_confidence * 0.3),
                payload={"step": step_text},
            ))
            _persist_node(_KNOWLEDGE_GRAPH.nodes[rec_id])
            _KNOWLEDGE_GRAPH.relate(incident_node_id, rec_id)
            _persist_edge(incident_node_id, rec_id)
            chunks.append(_kg_chunk(
                incident_id, "recovery", f"incident:{incident_id}:recovery:{idx}",
                f"Recovery recommendation for incident {incident_id}: {step_text}",
                ["recovery", "runbook", "incident"], now,
            ))

    if chunks:
        try:
            upsert_knowledge_chunks(chunks)
        except Exception:
            pass
        for chunk in chunks:
            try:
                _persist_vector_doc(service, incident_id, root_cause_hash, chunk)
            except Exception:
                pass

