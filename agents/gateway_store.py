import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


DEFAULT_PROJECT_ID = "demo-project"
DEFAULT_DEV_KEY = "dev-ingest-key"


def database_path() -> str:
    configured: str = os.getenv("DATABASE_PATH", "")
    if configured:
        return configured
    root: Path = Path(__file__).resolve().parent.parent
    return str(root / "data" / "gateway.db")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path: str = database_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn: sqlite3.Connection = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_store() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tenants (
                tenant_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                name TEXT NOT NULL,
                environment TEXT NOT NULL DEFAULT 'production',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_api_keys (
                key_hash TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS service_business_config (
                project_id TEXT NOT NULL,
                service TEXT NOT NULL,
                total_users INTEGER NOT NULL,
                revenue_per_user_per_minute REAL NOT NULL,
                impact_metric TEXT NOT NULL DEFAULT 'error_rate',
                PRIMARY KEY (project_id, service)
            );
            CREATE TABLE IF NOT EXISTS evidence_events (
                event_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                event_type TEXT NOT NULL,
                service TEXT NOT NULL,
                environment TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                raw_payload_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_chunks (
                chunk_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                service TEXT NOT NULL,
                source_type TEXT NOT NULL,
                label TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                service TEXT NOT NULL,
                environment TEXT NOT NULL,
                record_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incident_jobs (
                job_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_invocations (
                invocation_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incident_memory (
                memory_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                incident_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS connector_configs (
                connector_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                connector_type TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
    seed_configured_api_keys()


def seed_configured_api_keys() -> None:
    ensure_project(DEFAULT_PROJECT_ID, "Demo Project")
    configured: str = os.getenv("INGEST_API_KEYS", "")
    entries: list[str] = [item.strip() for item in configured.split(",") if item.strip()]
    if not entries and os.getenv("APP_ENV", "development") != "production":
        entries = [f"{DEFAULT_PROJECT_ID}:{DEFAULT_DEV_KEY}"]
    for entry in entries:
        if ":" not in entry:
            continue
        project_id, raw_key = entry.split(":", 1)
        ensure_project(project_id.strip(), project_id.strip())
        add_api_key(project_id.strip(), raw_key.strip(), "env")


def ensure_project(project_id: str, name: str, tenant_id: str = "default") -> None:
    now: str = datetime.now().isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at) VALUES (?, ?, ?)",
            (tenant_id, "Default Tenant", now),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO projects (project_id, tenant_id, name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, tenant_id, name, now),
        )


def add_api_key(project_id: str, raw_key: str, label: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO project_api_keys (key_hash, project_id, label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (_hash_key(raw_key), project_id, label, datetime.now().isoformat()),
        )


def authenticate_api_key(raw_key: str) -> Optional[str]:
    if not raw_key:
        return None
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM project_api_keys WHERE key_hash = ?",
                (_hash_key(raw_key),),
            ).fetchone()
    except sqlite3.OperationalError:
        init_store()
        with connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM project_api_keys WHERE key_hash = ?",
                (_hash_key(raw_key),),
            ).fetchone()
    return str(row["project_id"]) if row else None


def save_evidence_event(event: Dict[str, Any], chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
    now: str = datetime.now().isoformat()
    event_id: str = event.get("event_id") or str(uuid.uuid4())
    event["event_id"] = event_id
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO evidence_events (
                event_id, project_id, source, event_type, service, environment,
                timestamp, payload_json, raw_payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event["project_id"],
                event["source"],
                event["event_type"],
                event["service"],
                event["environment"],
                event["timestamp"],
                json.dumps(event["payload"], default=str),
                json.dumps(event.get("raw_payload"), default=str)
                if event.get("raw_payload") is not None
                else None,
                now,
            ),
        )
        for chunk in chunks:
            conn.execute(
                """
                INSERT INTO evidence_chunks (
                    chunk_id, event_id, project_id, service, source_type, label, text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk.get("chunk_id") or str(uuid.uuid4()),
                    event_id,
                    event["project_id"],
                    event["service"],
                    chunk["source_type"],
                    chunk["label"],
                    chunk["text"],
                    now,
                ),
            )
    return event


def create_incident(
    project_id: str,
    service: str,
    environment: str,
    alert_description: str,
    severity: str,
    timestamp: str,
) -> Dict[str, Any]:
    now: str = datetime.now().isoformat()
    incident_id: str = str(uuid.uuid4())
    job_id: str = str(uuid.uuid4())
    record: Dict[str, Any] = {
        "incident_id": incident_id,
        "project_id": project_id,
        "service": service,
        "environment": environment,
        "timestamp": timestamp,
        "alert_description": alert_description,
        "severity": severity,
        "current_status": "queued",
        "created_at": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO incidents (
                incident_id, project_id, service, environment, record_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (incident_id, project_id, service, environment, json.dumps(record), now, now),
        )
        conn.execute(
            """
            INSERT INTO incident_jobs (
                job_id, incident_id, project_id, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?)
            """,
            (job_id, incident_id, project_id, now, now),
        )
    return {"incident_id": incident_id, "job_id": job_id, "status": "queued"}


def claim_next_job() -> Optional[Dict[str, Any]]:
    now: str = datetime.now().isoformat()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM incident_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            """
            UPDATE incident_jobs
            SET status = 'running', attempts = attempts + 1, updated_at = ?
            WHERE job_id = ? AND status = 'queued'
            """,
            (now, row["job_id"]),
        )
    return dict(row)


def update_job(job_id: str, status: str, error: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE incident_jobs SET status = ?, error = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (status, error or None, datetime.now().isoformat(), job_id),
        )


def get_incident(incident_id: str, project_id: str | None = None) -> Optional[Dict[str, Any]]:
    query: str = "SELECT record_json FROM incidents WHERE incident_id = ?"
    args: list[Any] = [incident_id]
    if project_id:
        query += " AND project_id = ?"
        args.append(project_id)
    with connect() as conn:
        row = conn.execute(query, args).fetchone()
    return json.loads(row["record_json"]) if row else None


def save_incident_record(project_id: str, record: Dict[str, Any]) -> None:
    incident_id: str = str(record["incident_id"])
    now: str = datetime.now().isoformat()
    with connect() as conn:
        conn.execute(
            """
            UPDATE incidents
            SET record_json = ?, updated_at = ?
            WHERE incident_id = ? AND project_id = ?
            """,
            (json.dumps(record, default=str), now, incident_id, project_id),
        )
        for invocation in record.get("agent_invocations", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO agent_invocations (
                    invocation_id, incident_id, project_id, agent, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _invocation_id(incident_id, invocation),
                    incident_id,
                    project_id,
                    invocation.get("agent", "unknown"),
                    json.dumps(invocation, default=str),
                    invocation.get("timestamp") or now,
                ),
            )


def load_state_inputs(project_id: str, service: str) -> Dict[str, List[Dict[str, Any]]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT event_type, timestamp, payload_json, source
            FROM evidence_events
            WHERE project_id = ? AND service = ?
            ORDER BY timestamp ASC, created_at ASC
            """,
            (project_id, service),
        ).fetchall()
    logs: List[Dict[str, Any]] = []
    metrics: List[Dict[str, Any]] = []
    deployments: List[Dict[str, Any]] = []
    for row in rows:
        payload: Dict[str, Any] = json.loads(row["payload_json"])
        event_type: str = row["event_type"]
        if event_type in {"log", "app_error", "supabase_event"}:
            logs.extend(_payload_to_logs(payload, service, row["timestamp"]))
        elif event_type in {"metric", "business_metric"}:
            metrics.extend(_payload_to_metrics(payload))
        elif event_type in {"deployment", "github_push", "github_pr"}:
            deployments.append(_payload_to_deployment(payload, service, row["timestamp"], row["source"]))
    return {"logs": logs, "metrics": metrics, "deployments": deployments}


def list_evidence_chunks(project_id: str, service: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, source_type, label, text
            FROM evidence_chunks
            WHERE project_id = ? AND service = ?
            ORDER BY created_at ASC
            """,
            (project_id, service),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_service_business_config(
    project_id: str,
    service: str,
    total_users: int,
    revenue_per_user_per_minute: float,
    impact_metric: str = "error_rate",
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO service_business_config (
                project_id, service, total_users, revenue_per_user_per_minute, impact_metric
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, service) DO UPDATE SET
                total_users = excluded.total_users,
                revenue_per_user_per_minute = excluded.revenue_per_user_per_minute,
                impact_metric = excluded.impact_metric
            """,
            (project_id, service, total_users, revenue_per_user_per_minute, impact_metric),
        )


def get_service_business_config(project_id: str, service: str) -> Optional[Dict[str, Any]]:
    if not project_id:
        return None
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT total_users, revenue_per_user_per_minute, impact_metric
                FROM service_business_config
                WHERE project_id = ? AND service = ?
                """,
                (project_id, service),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def _payload_to_logs(payload: Dict[str, Any], service: str, timestamp: str) -> List[Dict[str, Any]]:
    items: Any = payload.get("logs") or payload.get("events") or payload.get("records")
    if not isinstance(items, list):
        items = [payload]
    logs: List[Dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            logs.append({"timestamp": timestamp, "level": "ERROR", "service": service, "message": item})
        elif isinstance(item, dict):
            logs.append(
                {
                    "timestamp": str(item.get("timestamp") or item.get("time") or timestamp),
                    "level": str(item.get("level") or item.get("severity") or "INFO"),
                    "service": str(item.get("service") or service),
                    "message": str(item.get("message") or item.get("msg") or item),
                    "error_type": str(item.get("error_type") or item.get("type") or ""),
                }
            )
    return logs


def _payload_to_metrics(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    metrics: Any = payload.get("metrics")
    if not isinstance(metrics, list):
        metrics = [payload]
    result: List[Dict[str, Any]] = []
    for item in metrics:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "metric_name": str(item.get("metric_name") or item.get("name") or "unknown"),
                "value": float(item.get("value") or item.get("current") or 0),
                "window": str(item.get("window") or "incident"),
            }
        )
        if "baseline" in item:
            result.append(
                {
                    "metric_name": str(item.get("metric_name") or item.get("name") or "unknown"),
                    "value": float(item.get("baseline") or 0),
                    "window": "baseline",
                }
            )
    return result


def _payload_to_deployment(
    payload: Dict[str, Any], service: str, timestamp: str, source: str
) -> Dict[str, Any]:
    return {
        "timestamp": str(payload.get("timestamp") or timestamp),
        "service": str(payload.get("service") or service),
        "version": str(payload.get("version") or payload.get("sha") or payload.get("after") or "unknown"),
        "changes": payload.get("changes") or payload.get("commits") or [json.dumps(payload, default=str)[:500]],
        "deployed_by": str(payload.get("deployed_by") or payload.get("sender") or source),
        "status": str(payload.get("status") or "completed"),
    }


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _invocation_id(incident_id: str, invocation: Dict[str, Any]) -> str:
    basis: str = json.dumps(invocation, sort_keys=True, default=str)
    return hashlib.sha256(f"{incident_id}:{basis}".encode("utf-8")).hexdigest()
