"""Memgraph-backed graph/query memory for Jarvis and incident RAG.

Memgraph is the primary backend because this system needs fast, real-time
property-graph updates for incidents and graph-aware assistant answers.  A
small in-process fallback keeps local demos and tests usable when Memgraph is
not running; it is intentionally reported as a fallback backend in status.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from agents.knowledge_base import KnowledgeChunk
from agents.service_catalog import topology_for_service


QUERY_CACHE_VERSION = "graph-query-v2"


@dataclass(frozen=True)
class CachedAnswer:
    payload: Dict[str, Any]
    confidence: float
    provider: str
    model: str
    match: str
    similarity: float
    created_at: str


_MEMORY_ANSWERS: Dict[str, Dict[str, Any]] = {}
_MEMORY_INCIDENTS: Dict[str, Dict[str, Any]] = {}
_MEMORY_EVIDENCE: Dict[str, Dict[str, Any]] = {}
_MEMGRAPH_AVAILABLE: bool | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def cache_ttl_seconds() -> int:
    return _int_env("QUERY_CACHE_TTL_SECONDS", 86_400)


def minimum_cache_confidence() -> float:
    return _float_env("QUERY_CACHE_MIN_CONFIDENCE", 0.72)


def semantic_cache_threshold() -> float:
    return _float_env("QUERY_CACHE_SIMILARITY_THRESHOLD", 0.90)


def normalize_query(question: str) -> str:
    return " ".join(re.findall(r"[\w\u0900-\u097f]+", question.casefold()))


def _memgraph_config() -> Dict[str, str]:
    config = {
        "uri": os.getenv("MEMGRAPH_URI", "bolt://127.0.0.1:7687"),
        "username": os.getenv("MEMGRAPH_USERNAME", ""),
        "password": os.getenv("MEMGRAPH_PASSWORD", ""),
    }
    try:
        from agents.connector_registry import runtime_connectors

        records = runtime_connectors("memgraph")
        if records:
            record = sorted(records, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[0]
            connector_config = record.get("config") or {}
            password_env = str(connector_config.get("password_env") or "").strip()
            config.update(
                {
                    "uri": str(connector_config.get("uri") or config["uri"]),
                    "username": str(connector_config.get("username") or config["username"]),
                    "password": os.getenv(password_env, "") if password_env else str(connector_config.get("password") or config["password"]),
                }
            )
    except Exception:
        pass
    return config


def _driver() -> Any | None:
    if os.getenv("GRAPH_MEMORY_BACKEND", "memgraph").strip().casefold() == "memory":
        return None
    try:
        from neo4j import GraphDatabase
    except Exception:
        return None
    config = _memgraph_config()
    auth = (config["username"], config["password"]) if config["username"] else None
    try:
        driver = GraphDatabase.driver(config["uri"], auth=auth)
        driver.verify_connectivity()
        return driver
    except Exception as exc:
        global _MEMGRAPH_AVAILABLE
        _MEMGRAPH_AVAILABLE = False
        print(f"[query_memory] Memgraph unavailable, using in-memory graph fallback: {exc}")
        return None


def _run_write(query: str, **params: Any) -> bool:
    driver = _driver()
    if not driver:
        return False
    try:
        with driver:
            with driver.session() as session:
                session.run(query, **params).consume()
        global _MEMGRAPH_AVAILABLE
        _MEMGRAPH_AVAILABLE = True
        return True
    except Exception as exc:
        print(f"[query_memory] Memgraph write failed, using fallback: {exc}")
        return False


def _run_read(query: str, **params: Any) -> List[Dict[str, Any]] | None:
    driver = _driver()
    if not driver:
        return None
    try:
        with driver:
            with driver.session() as session:
                rows = [dict(record) for record in session.run(query, **params)]
        global _MEMGRAPH_AVAILABLE
        _MEMGRAPH_AVAILABLE = True
        return rows
    except Exception as exc:
        print(f"[query_memory] Memgraph read failed, using fallback: {exc}")
        return None


def _context_projection(
    record: Dict[str, Any] | None,
    knowledge_context: Dict[str, Any] | None,
    system_context: Dict[str, Any] | None,
) -> Dict[str, Any]:
    record = record or {}
    knowledge_context = knowledge_context or {}
    incident_fields = (
        "incident_id",
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
        "estimated_cost_impact_per_minute",
        "revenue_impact_justification",
        "recovery_recommendations",
        "troubleshooting_plan",
        "similar_incidents",
    )
    knowledge_results = [
        {
            "source_path": item.get("source_path"),
            "kind": item.get("kind"),
            "content": item.get("content"),
            "score": item.get("score"),
        }
        for item in (knowledge_context.get("results") or [])[:5]
        if isinstance(item, dict)
    ]
    return {
        "incident": {key: record.get(key) for key in incident_fields if key in record},
        "knowledge": knowledge_results,
        "system": system_context or {},
    }


def context_fingerprint(
    record: Dict[str, Any] | None,
    knowledge_context: Dict[str, Any] | None,
    system_context: Dict[str, Any] | None = None,
) -> str:
    payload = json.dumps(
        _context_projection(record, knowledge_context, system_context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    version = os.getenv("QUERY_CACHE_VERSION", QUERY_CACHE_VERSION)
    return hashlib.sha256(f"{version}\n{payload}".encode("utf-8")).hexdigest()


def _cache_key(query: str, fingerprint: str, language: str) -> str:
    material = f"{normalize_query(query)}\n{fingerprint}\n{language.casefold()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _query_similarity(left: str, right: str) -> float:
    left_terms = set(left.split())
    right_terms = set(right.split())
    if not left_terms or not right_terms:
        return 0.0
    return len(left_terms & right_terms) / len(left_terms | right_terms)


def _row_to_cached(row: Dict[str, Any], match: str, similarity: float) -> CachedAnswer | None:
    try:
        payload = json.loads(str(row["answer_json"]))
    except (KeyError, json.JSONDecodeError):
        return None
    return CachedAnswer(
        payload=payload if isinstance(payload, dict) else {},
        confidence=float(row.get("confidence") or 0.0),
        provider=str(row.get("provider") or "unknown"),
        model=str(row.get("model") or "unknown"),
        match=match,
        similarity=round(similarity, 3),
        created_at=str(row.get("created_at") or ""),
    )


def _active_memory_rows(fingerprint: str, language: str) -> List[Dict[str, Any]]:
    now = _now().isoformat()
    expired = [key for key, item in _MEMORY_ANSWERS.items() if str(item.get("expires_at")) <= now]
    for key in expired:
        _MEMORY_ANSWERS.pop(key, None)
    return [
        item
        for item in _MEMORY_ANSWERS.values()
        if item.get("context_fingerprint") == fingerprint
        and item.get("language") == language.casefold()
        and float(item.get("confidence") or 0.0) >= minimum_cache_confidence()
    ]


def lookup_answer(question: str, fingerprint: str, language: str = "en") -> CachedAnswer | None:
    normalized = normalize_query(question)
    if not normalized:
        return None
    now = _now().isoformat()
    key = _cache_key(question, fingerprint, language)
    rows = _run_read(
        """
        MATCH (answer:QueryAnswer)
        WHERE answer.context_fingerprint = $fingerprint
          AND answer.language = $language
          AND answer.expires_at > $now
          AND answer.confidence >= $minimum_confidence
        RETURN answer.cache_key AS cache_key,
               answer.normalized_query AS normalized_query,
               answer.answer_json AS answer_json,
               answer.confidence AS confidence,
               answer.provider AS provider,
               answer.model AS model,
               answer.created_at AS created_at
        ORDER BY answer.last_accessed_at DESC
        LIMIT 50
        """,
        fingerprint=fingerprint,
        language=language.casefold(),
        now=now,
        minimum_confidence=minimum_cache_confidence(),
    )
    candidates = rows if rows is not None else _active_memory_rows(fingerprint, language)
    exact = next((item for item in candidates if item.get("cache_key") == key), None)
    if exact:
        if rows is not None:
            _run_write(
                """
                MATCH (answer:QueryAnswer {cache_key: $cache_key})
                SET answer.hit_count = coalesce(answer.hit_count, 0) + 1,
                    answer.last_accessed_at = $now
                """,
                cache_key=key,
                now=now,
            )
        else:
            exact["hit_count"] = int(exact.get("hit_count") or 0) + 1
            exact["last_accessed_at"] = now
        return _row_to_cached(exact, "exact", 1.0)
    ranked = [
        (_query_similarity(normalized, str(item.get("normalized_query") or "")), item)
        for item in candidates
    ]
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked or ranked[0][0] < semantic_cache_threshold():
        return None
    similarity, row = ranked[0]
    if rows is not None:
        _run_write(
            """
            MATCH (answer:QueryAnswer {cache_key: $cache_key})
            SET answer.hit_count = coalesce(answer.hit_count, 0) + 1,
                answer.last_accessed_at = $now
            """,
            cache_key=row.get("cache_key"),
            now=now,
        )
    else:
        row["hit_count"] = int(row.get("hit_count") or 0) + 1
        row["last_accessed_at"] = now
    return _row_to_cached(row, "semantic", similarity)


def remember_answer(
    question: str,
    fingerprint: str,
    language: str,
    payload: Dict[str, Any],
    *,
    provider: str,
    model: str,
    ttl_seconds: int | None = None,
) -> bool:
    normalized = normalize_query(question)
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    if not normalized or not payload.get("answer") or confidence < minimum_cache_confidence():
        return False
    created = _now()
    expires = created + timedelta(seconds=ttl_seconds or cache_ttl_seconds())
    stored_payload = {key: value for key, value in payload.items() if key != "routing"}
    row = {
        "cache_key": _cache_key(question, fingerprint, language),
        "normalized_query": normalized,
        "context_fingerprint": fingerprint,
        "language": language.casefold(),
        "answer_json": json.dumps(stored_payload, ensure_ascii=False, default=str),
        "confidence": confidence,
        "provider": provider,
        "model": model,
        "cache_version": os.getenv("QUERY_CACHE_VERSION", QUERY_CACHE_VERSION),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "last_accessed_at": created.isoformat(),
        "hit_count": 0,
    }
    if _run_write(
        """
        MERGE (answer:QueryAnswer {cache_key: $cache_key})
        SET answer.normalized_query = $normalized_query,
            answer.context_fingerprint = $context_fingerprint,
            answer.language = $language,
            answer.answer_json = $answer_json,
            answer.confidence = $confidence,
            answer.provider = $provider,
            answer.model = $model,
            answer.cache_version = $cache_version,
            answer.created_at = $created_at,
            answer.expires_at = $expires_at,
            answer.last_accessed_at = $last_accessed_at,
            answer.hit_count = $hit_count
        """,
        **row,
    ):
        return True
    _MEMORY_ANSWERS[row["cache_key"]] = row
    return True


def invalidate_query_memory(*, fingerprint: str | None = None) -> int:
    if fingerprint:
        rows = _run_read(
            "MATCH (answer:QueryAnswer) WHERE answer.context_fingerprint = $fingerprint RETURN count(answer) AS count",
            fingerprint=fingerprint,
        )
        _run_write(
            "MATCH (answer:QueryAnswer) WHERE answer.context_fingerprint = $fingerprint DETACH DELETE answer",
            fingerprint=fingerprint,
        )
        deleted = int((rows or [{"count": 0}])[0].get("count") or 0) if rows is not None else 0
        fallback_keys = [key for key, item in _MEMORY_ANSWERS.items() if item.get("context_fingerprint") == fingerprint]
    else:
        rows = _run_read("MATCH (answer:QueryAnswer) RETURN count(answer) AS count")
        _run_write("MATCH (answer:QueryAnswer) DETACH DELETE answer")
        deleted = int((rows or [{"count": 0}])[0].get("count") or 0) if rows is not None else 0
        fallback_keys = list(_MEMORY_ANSWERS)
    for key in fallback_keys:
        _MEMORY_ANSWERS.pop(key, None)
    return deleted + len(fallback_keys)


def _resolution(record: Dict[str, Any]) -> str:
    decisions = record.get("remediation_decisions") or {}
    if any(value.get("decision") == "approved" for value in decisions.values()):
        return "Automated remediation"
    lifecycle = str(record.get("lifecycle_status") or record.get("current_status") or "").lower()
    if "review" in lifecycle:
        return "Human review required"
    if lifecycle in {"resolved", "closed", "complete"}:
        return "Resolved"
    return "Investigation pending"


def _root_cause_label(record: Dict[str, Any]) -> str:
    return str((record.get("root_cause") or {}).get("hypothesis") or "Pending root cause")


def _node_props(record: Dict[str, Any]) -> Dict[str, Any]:
    topology = topology_for_service(str(record.get("service") or "unknown"))
    raw_environment = record.get("environment")
    environment = raw_environment if isinstance(raw_environment, dict) else dict(topology.get("environment") or {})
    if isinstance(raw_environment, str) and raw_environment:
        environment["tier"] = raw_environment
    return {
        "incident_id": str(record.get("incident_id") or ""),
        "service": str(record.get("service") or "unknown"),
        "severity": str(record.get("severity") or "unknown"),
        "alert_description": str(record.get("alert_description") or ""),
        "current_status": str(record.get("current_status") or ""),
        "lifecycle_status": str(record.get("lifecycle_status") or ""),
        "agent_status": str(record.get("agent_status") or ""),
        "business_risk_level": str(record.get("business_risk_level") or "unknown"),
        "affected_users": int(record.get("affected_users") or 0),
        "impact_per_minute": float(record.get("estimated_revenue_impact_per_minute") or 0.0),
        "root_cause": _root_cause_label(record),
        "resolution": _resolution(record),
        "updated_at": _now().isoformat(),
        "payload_json": json.dumps(record, ensure_ascii=False, default=str),
        "owner_team": str((record.get("ownership") or topology.get("owner") or {}).get("team") or "Unknown"),
        "owner_primary": str((record.get("ownership") or topology.get("owner") or {}).get("primary") or "platform-oncall"),
        "environment_json": json.dumps(environment, ensure_ascii=False, default=str),
        "dependencies_json": json.dumps(record.get("dependencies") or topology.get("dependencies") or [], ensure_ascii=False, default=str),
        "upstream_json": json.dumps(record.get("upstream_services") or topology.get("upstream_services") or [], ensure_ascii=False, default=str),
        "runbooks_json": json.dumps(record.get("runbooks") or topology.get("runbooks") or [], ensure_ascii=False, default=str),
        "escalation_json": json.dumps(record.get("escalation_path") or topology.get("escalation_path") or [], ensure_ascii=False, default=str),
        "rollback_json": json.dumps(record.get("rollback_plan") or topology.get("rollback") or {}, ensure_ascii=False, default=str),
        "blast_radius_json": json.dumps(record.get("blast_radius") or topology.get("blast_radius") or {}, ensure_ascii=False, default=str),
    }


def upsert_incident_graph(record: Dict[str, Any]) -> bool:
    incident_id = str(record.get("incident_id") or "").strip()
    if not incident_id:
        return False
    props = _node_props(record)
    service_id = f"service:{props['service']}"
    owner_id = f"owner:{props['owner_primary']}"
    environment = json.loads(props.get("environment_json") or "{}")
    environment_id = "env:" + ":".join(str(environment.get(key, "unknown")) for key in ("tier", "region", "cluster"))
    cause_id = f"cause:{hashlib.sha1(props['root_cause'].casefold().encode('utf-8')).hexdigest()[:16]}"
    resolution_id = f"resolution:{props['resolution']}"
    dependencies = json.loads(props.get("dependencies_json") or "[]")
    upstream_services = json.loads(props.get("upstream_json") or "[]")
    runbooks = json.loads(props.get("runbooks_json") or "[]")
    escalation_path = json.loads(props.get("escalation_json") or "[]")
    
    # 1. Upsert to Memgraph if connection is alive
    if _run_write(
        """
        MERGE (incident:Incident {incident_id: $incident_id})
        SET incident += $props
        MERGE (service:Service {id: $service_id})
        SET service.name = $service,
            service.owner_primary = $owner_primary,
            service.owner_team = $owner_team,
            service.dependencies_json = $dependencies_json,
            service.upstream_json = $upstream_json,
            service.runbooks_json = $runbooks_json,
            service.environment_json = $environment_json,
            service.blast_radius_json = $blast_radius_json,
            service.escalation_json = $escalation_json,
            service.environment_tier = $environment_tier,
            service.environment_region = $environment_region,
            service.environment_cluster = $environment_cluster,
            service.environment_namespace = $environment_namespace
        MERGE (owner:Owner {id: $owner_id})
        SET owner.primary = $owner_primary, owner.team = $owner_team
        MERGE (env:Environment {id: $environment_id})
        SET env.payload_json = $environment_json,
            env.tier = $environment_tier,
            env.region = $environment_region,
            env.cluster = $environment_cluster,
            env.namespace = $environment_namespace
        MERGE (cause:Cause {id: $cause_id})
        SET cause.label = $root_cause
        MERGE (resolution:Resolution {id: $resolution_id})
        SET resolution.label = $resolution, resolution.rollback_json = $rollback_json
        MERGE (incident)-[:AFFECTS]->(service)
        MERGE (service)-[:OWNED_BY]->(owner)
        MERGE (service)-[:RUNS_IN]->(env)
        MERGE (incident)-[:CAUSED_BY]->(cause)
        MERGE (incident)-[:RESOLVED_BY]->(resolution)
        """,
        incident_id=incident_id,
        props=props,
        service_id=service_id,
        service=props["service"],
        owner_id=owner_id,
        owner_primary=props["owner_primary"],
        owner_team=props["owner_team"],
        environment_id=environment_id,
        environment_json=props["environment_json"],
        rollback_json=props["rollback_json"],
        cause_id=cause_id,
        root_cause=props["root_cause"],
        resolution_id=resolution_id,
        resolution=props["resolution"],
        dependencies_json=props["dependencies_json"],
        upstream_json=props["upstream_json"],
        runbooks_json=props["runbooks_json"],
        escalation_json=props["escalation_json"],
        blast_radius_json=props["blast_radius_json"],
        environment_tier=environment.get("tier", "unknown"),
        environment_region=environment.get("region", "unknown"),
        environment_cluster=environment.get("cluster", "unknown"),
        environment_namespace=environment.get("namespace", "unknown"),
    ):
        _upsert_topology_edges_memgraph(service_id, dependencies, upstream_services, runbooks, escalation_path)
        
        # Upsert agent invocations and handoffs in Memgraph
        invocations = record.get("agent_invocations") or []
        for i, inv in enumerate(invocations):
            span_id = str(inv.get("span_id") or f"{incident_id}:span-{i}")
            inv_props = {
                "span_id": span_id,
                "agent": str(inv.get("agent") or ""),
                "action": str(inv.get("action") or ""),
                "source": str(inv.get("source") or ""),
                "timestamp": str(inv.get("timestamp") or ""),
                "iteration": int(inv.get("iteration") or 0),
                "reasoning": str(inv.get("reasoning") or ""),
                "findings_json": json.dumps(inv.get("findings") or {}, ensure_ascii=False, default=str),
                "parent_span_id": str(inv.get("parent_span_id") or ""),
            }
            _run_write(
                """
                MATCH (incident:Incident {incident_id: $incident_id})
                MERGE (inv:AgentInvocation {id: $span_id})
                SET inv += $inv_props
                MERGE (incident)-[:HAS_INVOCATION]->(inv)
                """,
                incident_id=incident_id,
                span_id=span_id,
                inv_props=inv_props,
            )
            # Link to parent invocation for sequential workflow trace
            parent_id = inv_props["parent_span_id"]
            if parent_id:
                _run_write(
                    """
                    MATCH (parent:AgentInvocation {id: $parent_id})
                    MATCH (current:AgentInvocation {id: $span_id})
                    MERGE (parent)-[r:HANDED_OFF_TO]->(current)
                    SET r.reason = $reason
                    """,
                    parent_id=parent_id,
                    span_id=span_id,
                    reason=str(inv.get("handoff_reason") or "workflow handoff"),
                )
        return True

    # 2. Falling back to in-memory store
    _MEMORY_INCIDENTS[incident_id] = {
        **props,
        "topology": {
            "dependencies": dependencies,
            "upstream_services": upstream_services,
            "runbooks": runbooks,
            "escalation_path": escalation_path,
            "environment": environment,
        },
        "invocations": record.get("agent_invocations") or [],
    }
    return True




def _upsert_topology_edges_memgraph(service_id: str, dependencies: List[str], upstream_services: List[str], runbooks: List[str], escalation_path: List[str]) -> None:
    for dep in dependencies:
        _run_write("""
        MATCH (service:Service {id: $service_id})
        MERGE (dep:Service {id: $dep_id})
        SET dep.name = $dep_name
        MERGE (service)-[:DEPENDS_ON]->(dep)
        """, service_id=service_id, dep_id=f"service:{dep}", dep_name=str(dep))
    for upstream in upstream_services:
        _run_write("""
        MATCH (service:Service {id: $service_id})
        MERGE (up:Service {id: $up_id})
        SET up.name = $up_name
        MERGE (up)-[:CALLS]->(service)
        """, service_id=service_id, up_id=f"service:{upstream}", up_name=str(upstream))
    for runbook in runbooks:
        _run_write("""
        MATCH (service:Service {id: $service_id})
        MERGE (runbook:Runbook {id: $runbook_id})
        SET runbook.path = $runbook
        MERGE (service)-[:HAS_RUNBOOK]->(runbook)
        """, service_id=service_id, runbook_id=f"runbook:{runbook}", runbook=str(runbook))
    previous = None
    for step in escalation_path:
        current = f"oncall:{step}"
        _run_write("MERGE (person:Escalation {id: $id}) SET person.name = $name", id=current, name=str(step))
        if previous:
            _run_write("""
            MATCH (a:Escalation {id: $previous}), (b:Escalation {id: $current})
            MERGE (a)-[:ESCALATES_TO]->(b)
            """, previous=previous, current=current)
        previous = current


def upsert_incidents_graph(records: Iterable[Dict[str, Any]]) -> None:
    for record in records:
        upsert_incident_graph(record)


def _evidence_id(item: Dict[str, Any]) -> str:
    source = str(item.get("source_path") or item.get("citation") or item.get("title") or item.get("content") or "")
    return f"evidence:{hashlib.sha1(source.encode('utf-8')).hexdigest()[:20]}"


def upsert_knowledge_chunks_as_graph_nodes(
    chunks: Iterable[KnowledgeChunk],
    *,
    record: Dict[str, Any] | None = None,
) -> int:
    incident_id = str((record or {}).get("incident_id") or "").strip()
    stored = 0
    for chunk in chunks:
        evidence_id = str(chunk.chunk_id or _evidence_id({"source_path": chunk.source_path, "title": chunk.title, "content": chunk.content}))
        props = {
            "id": evidence_id,
            "title": chunk.title,
            "kind": chunk.kind,
            "source_path": chunk.source_path,
            "content": chunk.content[:2400],
            "tags": str(chunk.tags or ""),
            "chunk_id": chunk.chunk_id,
            "vector_source": "qdrant" if chunk.chunk_id else "local_semantic",
            "updated_at": chunk.updated_at,
        }
        query = (
            """
            MERGE (evidence:Evidence {id: $id})
            SET evidence += $props
            """
        )
        params = {"id": evidence_id, "props": props}
        if incident_id:
            query += (
                """
                WITH evidence
                MATCH (incident:Incident {incident_id: $incident_id})
                MERGE (incident)-[:GROUNDED_BY]->(evidence)
                """
            )
            params["incident_id"] = incident_id
        if _run_write(query, **params):
            stored += 1
            continue
        _MEMORY_EVIDENCE[evidence_id] = {**props, "incident_id": incident_id}
        stored += 1
    return stored


def upsert_knowledge_evidence(
    question: str,
    knowledge_context: Dict[str, Any],
    *,
    record: Dict[str, Any] | None = None,
) -> int:
    """Attach retrieved documents/runbooks/postmortems to the graph.

    The retrieval layer still decides which snippets are relevant; this function
    turns those snippets into graph evidence so Jarvis can explain decisions and
    find similar historical inputs without re-sending large context.
    """
    results = [item for item in (knowledge_context.get("results") or []) if isinstance(item, dict)]
    if not results:
        return 0
    incident_id = str((record or {}).get("incident_id") or "").strip()
    if incident_id and record:
        upsert_incident_graph(record)
    stored = 0
    for item in results[:8]:
        evidence_id = str(item.get("chunk_id") or _evidence_id(item))
        tags = item.get("tags") or []
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
        props = {
            "id": evidence_id,
            "title": str(item.get("title") or item.get("source_path") or "Operational evidence"),
            "kind": str(item.get("kind") or "knowledge"),
            "source_path": str(item.get("source_path") or ""),
            "citation": str(item.get("citation") or item.get("source_path") or ""),
            "content": str(item.get("content") or "")[:2400],
            "score": float(item.get("score") or 0.0),
            "chunk_id": str(item.get("chunk_id") or ""),
            "tags": ",".join(tags),
            "vector_source": "qdrant" if item.get("chunk_id") else "local_semantic",
            "last_query": question[:500],
            "updated_at": _now().isoformat(),
        }
        query = (
            """
            MERGE (evidence:Evidence {id: $id})
            SET evidence += $props
            """
            + (
                """
                WITH evidence
                MATCH (incident:Incident {incident_id: $incident_id})
                MERGE (incident)-[:GROUNDED_BY]->(evidence)
                """
                if incident_id
                else ""
            )
        )
        params = {"id": evidence_id, "props": props}
        if incident_id:
            params["incident_id"] = incident_id
        if _run_write(query, **params):
            stored += 1
            continue
        _MEMORY_EVIDENCE[evidence_id] = {**props, "incident_id": incident_id}
        stored += 1
    return stored


def _normalize_related_incident_ids(record: Dict[str, Any]) -> list[str]:
    related_items = record.get("related_incidents") or record.get("similar_incidents") or []
    if not isinstance(related_items, list):
        related_items = [related_items]
    related_ids: list[str] = []
    for item in related_items:
        if isinstance(item, dict):
            incident_id = str(item.get("incident_id") or item.get("id") or item)
        else:
            incident_id = str(item)
        if incident_id:
            related_ids.append(incident_id)
    return list(dict.fromkeys(related_ids))


def upsert_operational_incident_knowledge(record: Dict[str, Any]) -> int:
    """Create operational knowledge chunks and persist them in the knowledge store and graph."""
    from agents.knowledge_base import build_operational_knowledge_chunks, upsert_knowledge_chunks

    incident_id = str(record.get("incident_id") or "").strip()
    if not incident_id:
        return 0

    upsert_incident_graph(record)
    chunks = build_operational_knowledge_chunks(record)
    if not chunks:
        return 0

    upsert_knowledge_chunks(chunks)
    stored = upsert_knowledge_chunks_as_graph_nodes(chunks, record=record)

    related_ids = _normalize_related_incident_ids(record)
    if related_ids:
        for related_id in related_ids:
            if not related_id or related_id == incident_id:
                continue
            if not _run_write(
                """
                MERGE (incident:Incident {incident_id: $incident_id})
                MERGE (related:Incident {incident_id: $related_id})
                MERGE (incident)-[:RELATED_TO]->(related)
                """,
                incident_id=incident_id,
                related_id=related_id,
            ):
                memory_incident = _MEMORY_INCIDENTS.get(incident_id) or {}
                memory_incident = {**memory_incident, "related_incidents": related_ids}
                _MEMORY_INCIDENTS[incident_id] = memory_incident
    return stored


def incident_graph_snapshot(records: Iterable[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    if records:
        upsert_incidents_graph(records)
    rows = _run_read(
        """
        MATCH (incident:Incident)
        OPTIONAL MATCH (incident)-[rel]->(target)
        RETURN incident, rel, target
        ORDER BY incident.updated_at DESC
        LIMIT 300
        """
    )
    if rows is not None:
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []
        for row in rows:
            incident = dict(row.get("incident") or {})
            iid = str(incident.get("incident_id") or "")
            if iid:
                incident_node = {
                    "id": iid,
                    "type": "incident",
                    "label": iid[:8],
                    "detail": incident.get("alert_description"),
                    "severity": incident.get("severity"),
                    "status": incident.get("current_status"),
                    "impact_per_minute": incident.get("impact_per_minute"),
                }
                incident_node.update({
                    key: value
                    for key, value in incident.items()
                    if key not in {"incident_id", "alert_description", "severity", "current_status", "impact_per_minute"}
                })
                nodes[iid] = incident_node
            target = dict(row.get("target") or {})
            rel = row.get("rel")
            target_id = str(target.get("id") or target.get("name") or target.get("label") or "")
            if iid and target_id and rel:
                label = str(target.get("name") or target.get("label") or target_id)
                node_type = target_id.split(":", 1)[0] if ":" in target_id else "entity"
                target_node = {"id": target_id, "type": node_type, "label": label}
                target_node.update({
                    key: value
                    for key, value in target.items()
                    if key not in {"id", "name", "label"}
                })
                nodes[target_id] = target_node
                edges.append({"source": iid, "target": target_id, "relation": str(rel.type).lower()})
        return {"nodes": list(nodes.values()), "edges": edges, "backend": "memgraph"}
    nodes = []
    edges = []
    for incident in _MEMORY_INCIDENTS.values():
        iid = str(incident.get("incident_id"))
        service_id = f"service:{incident.get('service', 'unknown')}"
        root_cause = incident.get('root_cause') or {}
        root_cause_label = str(root_cause.get('hypothesis') if isinstance(root_cause, dict) else root_cause or 'Pending root cause')
        resolution_label = str(incident.get('resolution') or 'Investigation pending')
        resolution_id = f"resolution:{resolution_label}"
        topology = incident.get("topology") or {}
        owner_id = f"owner:{incident.get('owner_primary', 'platform-oncall')}"
        env_id = "env:" + str(hashlib.sha1(str(topology.get("environment", {})).encode("utf-8")).hexdigest()[:10])
        environment = topology.get("environment") or {}
        cause_id = f"cause:{hashlib.sha1(root_cause_label.casefold().encode('utf-8')).hexdigest()[:16]}"
        nodes.extend(
            [
                {
                    "id": iid,
                    "type": "incident",
                    "label": iid[:8],
                    "detail": incident.get("alert_description"),
                    "severity": incident.get("severity"),
                    "status": incident.get("current_status"),
                    "impact_per_minute": incident.get("impact_per_minute"),
                    "service": incident.get("service"),
                    "owner_team": incident.get("owner_team"),
                    "owner_primary": incident.get("owner_primary"),
                    "blast_radius": incident.get("blast_radius"),
                    "business_risk_level": incident.get("business_risk_level"),
                    "affected_users": incident.get("affected_users"),
                },
                {
                    "id": service_id,
                    "type": "service",
                    "label": incident.get("service", "unknown"),
                    "owner_primary": incident.get("owner_primary"),
                    "owner_team": incident.get("owner_team"),
                    "dependencies": topology.get("dependencies"),
                    "upstream_services": topology.get("upstream_services"),
                    "runbooks": topology.get("runbooks"),
                    "escalation_path": topology.get("escalation_path"),
                    "environment": environment,
                    "rollback_plan": incident.get("rollback_plan"),
                    "business_risk_level": incident.get("business_risk_level"),
                },
                {
                    "id": owner_id,
                    "type": "owner",
                    "label": incident.get("owner_team") or incident.get("owner_primary"),
                    "team": incident.get("owner_team"),
                    "primary": incident.get("owner_primary"),
                },
                {
                    "id": env_id,
                    "type": "environment",
                    "label": str(environment.get("cluster", "environment")),
                    "detail": environment,
                    "tier": environment.get("tier"),
                    "region": environment.get("region"),
                    "cluster": environment.get("cluster"),
                    "namespace": environment.get("namespace"),
                },
                {"id": cause_id, "type": "cause", "label": root_cause_label, "detail": root_cause_label},
                {"id": resolution_id, "type": "resolution", "label": resolution_label, "rollback_plan": incident.get("rollback_plan")},
            ]
        )
        edges.extend(
            [
                {"source": iid, "target": service_id, "relation": "affects"},
                {"source": service_id, "target": owner_id, "relation": "owned_by"},
                {"source": service_id, "target": env_id, "relation": "runs_in"},
                {"source": iid, "target": cause_id, "relation": "caused_by"},
                {"source": iid, "target": resolution_id, "relation": "resolved_by"},
            ]
        )
    for incident in _MEMORY_INCIDENTS.values():
        iid = str(incident.get("incident_id"))
        service_id = f"service:{incident.get('service', 'unknown')}"
        topology = incident.get("topology") or {}
        for dep in topology.get("dependencies", []):
            dep_id = f"service:{dep}"
            nodes.append({"id": dep_id, "type": "service", "label": dep})
            edges.append({"source": service_id, "target": dep_id, "relation": "depends_on"})
        for upstream in topology.get("upstream_services", []):
            upstream_id = f"service:{upstream}"
            nodes.append({"id": upstream_id, "type": "service", "label": upstream})
            edges.append({"source": upstream_id, "target": service_id, "relation": "calls"})
        for runbook in topology.get("runbooks", []):
            rb_id = f"runbook:{runbook}"
            nodes.append({"id": rb_id, "type": "runbook", "label": str(runbook).split('/')[-1], "detail": runbook})
            edges.append({"source": service_id, "target": rb_id, "relation": "has_runbook"})
        previous = None
        for step in topology.get("escalation_path", []):
            esc_id = f"oncall:{step}"
            nodes.append({"id": esc_id, "type": "escalation", "label": step})
            if previous:
                edges.append({"source": previous, "target": esc_id, "relation": "escalates_to"})
            else:
                edges.append({"source": iid, "target": esc_id, "relation": "escalates_to"})
            previous = esc_id
        for related_id in incident.get("related_incidents", []):
            if not related_id:
                continue
            edges.append({"source": iid, "target": str(related_id), "relation": "related_to"})

    for evidence in _MEMORY_EVIDENCE.values():
        evidence_id = str(evidence.get("id"))
        nodes.append(
            {
                "id": evidence_id,
                "type": "evidence",
                "label": evidence.get("title") or evidence.get("source_path") or "Evidence",
                "detail": evidence.get("content"),
                "severity": evidence.get("kind"),
                "kind": evidence.get("kind"),
                "chunk_id": evidence.get("chunk_id"),
                "vector_source": evidence.get("vector_source"),
                "source_path": evidence.get("source_path"),
                "tags": [tag.strip() for tag in str(evidence.get("tags") or "").split(",") if tag.strip()],
            }
        )
        incident_id = str(evidence.get("incident_id") or "")
        if incident_id:
            edges.append({"source": incident_id, "target": evidence_id, "relation": "grounded_by"})
    unique_nodes = {node["id"]: node for node in nodes}
    return {"nodes": list(unique_nodes.values()), "edges": edges, "backend": "in_memory_graph"}


def cache_stats() -> Dict[str, Any]:
    now = _now().isoformat()
    rows = _run_read(
        """
        MATCH (answer:QueryAnswer)
        RETURN count(answer) AS total,
               sum(CASE WHEN answer.expires_at > $now THEN 1 ELSE 0 END) AS active,
               sum(CASE WHEN answer.expires_at <= $now THEN 1 ELSE 0 END) AS expired,
               sum(coalesce(answer.hit_count, 0)) AS hits
        """,
        now=now,
    )
    if rows is not None:
        row = rows[0] if rows else {}
        backend = "memgraph"
        total = int(row.get("total") or 0)
        active = int(row.get("active") or 0)
        expired = int(row.get("expired") or 0)
        hits = int(row.get("hits") or 0)
    else:
        expired_keys = [key for key, item in _MEMORY_ANSWERS.items() if str(item.get("expires_at")) <= now]
        total = len(_MEMORY_ANSWERS)
        expired = len(expired_keys)
        active = total - expired
        hits = sum(int(item.get("hit_count") or 0) for item in _MEMORY_ANSWERS.values())
        backend = "in_memory_graph"
    return {
        "backend": backend,
        "active_entries": active,
        "expired_entries": expired,
        "total_entries": total,
        "hits": hits,
        "ttl_seconds": cache_ttl_seconds(),
        "minimum_confidence": minimum_cache_confidence(),
        "similarity_threshold": semantic_cache_threshold(),
        "version": os.getenv("QUERY_CACHE_VERSION", QUERY_CACHE_VERSION),
        "memgraph_uri": _memgraph_config()["uri"],
        "memgraph_available": _MEMGRAPH_AVAILABLE is True,
    }
