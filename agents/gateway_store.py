import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


DEFAULT_PROJECT_ID = "demo-project"
DEFAULT_DEV_KEY = "dev-ingest-key"
DEFAULT_BROWSER_KEY = "dev-browser-key"


def database_path() -> str:
    configured: str = os.getenv("DATABASE_PATH", "")
    if configured:
        return configured
    root: Path = Path(__file__).resolve().parent.parent
    return str(root / "data" / "gateway.db")


def database_backend() -> str:
    url: str = os.getenv("DATABASE_URL", "").strip().lower()
    if url.startswith(("postgres://", "postgresql://")):
        return "postgres_configured_unsupported"
    return "sqlite"


def production_sqlite_allowed() -> bool:
    return os.getenv("ALLOW_SQLITE_IN_PRODUCTION", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE TABLE IF NOT EXISTS browser_public_keys (
                key_hash TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
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
            CREATE TABLE IF NOT EXISTS incident_evidence_edges (
                edge_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                service TEXT NOT NULL,
                left_event_id TEXT NOT NULL,
                right_event_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                explanation TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS release_markers (
                release_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                service TEXT NOT NULL,
                release_sha TEXT NOT NULL,
                source_event_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deployment_risk_reports (
                report_id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                service TEXT NOT NULL,
                report_json TEXT NOT NULL,
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
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                delivery_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                connector_type TEXT NOT NULL,
                received_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                audit_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        _ensure_column(conn, "project_api_keys", "revoked_at", "TEXT")
    seed_configured_api_keys()
    seed_configured_browser_keys()


def _ensure_column(
    conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str
) -> None:
    columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(str(column["name"]) == column_name for column in columns):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


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


def seed_configured_browser_keys() -> None:
    configured: str = os.getenv("BROWSER_PUBLIC_KEYS", "")
    entries: list[str] = [item.strip() for item in configured.split(",") if item.strip()]
    if not entries and os.getenv("APP_ENV", "development") != "production":
        entries = [f"{DEFAULT_PROJECT_ID}:{DEFAULT_BROWSER_KEY}"]
    for entry in entries:
        if ":" not in entry:
            continue
        project_id, raw_key = entry.split(":", 1)
        ensure_project(project_id.strip(), project_id.strip())
        add_browser_key(project_id.strip(), raw_key.strip(), "env")


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


def project_exists(project_id: str) -> bool:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        init_store()
        return project_exists(project_id)
    return bool(row)


def add_api_key(project_id: str, raw_key: str, label: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO project_api_keys (key_hash, project_id, label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (_hash_key(raw_key), project_id, label, datetime.now().isoformat()),
        )


def revoke_project_api_keys(project_id: str, label: str | None = None) -> int:
    now: str = datetime.now().isoformat()
    with connect() as conn:
        if label:
            cursor = conn.execute(
                """
                UPDATE project_api_keys
                SET revoked_at = ?
                WHERE project_id = ? AND label = ? AND revoked_at IS NULL
                """,
                (now, project_id, label),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE project_api_keys
                SET revoked_at = ?
                WHERE project_id = ? AND revoked_at IS NULL
                """,
                (now, project_id),
            )
    return int(cursor.rowcount)


def add_browser_key(project_id: str, raw_key: str, label: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO browser_public_keys (key_hash, project_id, label, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (_hash_key(raw_key), project_id, label, datetime.now().isoformat()),
        )


def revoke_browser_keys(project_id: str, label: str | None = None) -> int:
    now: str = datetime.now().isoformat()
    with connect() as conn:
        if label:
            cursor = conn.execute(
                """
                UPDATE browser_public_keys
                SET revoked_at = ?
                WHERE project_id = ? AND label = ? AND revoked_at IS NULL
                """,
                (now, project_id, label),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE browser_public_keys
                SET revoked_at = ?
                WHERE project_id = ? AND revoked_at IS NULL
                """,
                (now, project_id),
            )
    return int(cursor.rowcount)


def upsert_connector_config(
    project_id: str, connector_type: str, config: Dict[str, Any]
) -> None:
    now: str = datetime.now().isoformat()
    connector_id: str = f"{project_id}:{connector_type}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO connector_configs (
                connector_id, project_id, connector_type, config_json, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(connector_id) DO UPDATE SET
                config_json = excluded.config_json
            """,
            (
                connector_id,
                project_id,
                connector_type,
                json.dumps(config, default=str),
                now,
            ),
        )


def get_connector_config(project_id: str, connector_type: str) -> Optional[Dict[str, Any]]:
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT config_json FROM connector_configs
                WHERE project_id = ? AND connector_type = ?
                """,
                (project_id, connector_type),
            ).fetchone()
    except sqlite3.OperationalError:
        init_store()
        return get_connector_config(project_id, connector_type)
    return json.loads(row["config_json"]) if row else None


def authenticate_api_key(raw_key: str) -> Optional[str]:
    if not raw_key:
        return None
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT project_id FROM project_api_keys
                WHERE key_hash = ? AND revoked_at IS NULL
                """,
                (_hash_key(raw_key),),
            ).fetchone()
    except sqlite3.OperationalError:
        init_store()
        with connect() as conn:
            row = conn.execute(
                """
                SELECT project_id FROM project_api_keys
                WHERE key_hash = ? AND revoked_at IS NULL
                """,
                (_hash_key(raw_key),),
            ).fetchone()
    return str(row["project_id"]) if row else None


def authenticate_browser_key(raw_key: str) -> Optional[str]:
    if not raw_key:
        return None
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT project_id FROM browser_public_keys
                WHERE key_hash = ? AND revoked_at IS NULL
                """,
                (_hash_key(raw_key),),
            ).fetchone()
    except sqlite3.OperationalError:
        init_store()
        with connect() as conn:
            row = conn.execute(
                """
                SELECT project_id FROM browser_public_keys
                WHERE key_hash = ? AND revoked_at IS NULL
                """,
                (_hash_key(raw_key),),
            ).fetchone()
    return str(row["project_id"]) if row else None


def project_has_browser_key(project_id: str) -> bool:
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT key_hash FROM browser_public_keys
                WHERE project_id = ? AND revoked_at IS NULL
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        init_store()
        return project_has_browser_key(project_id)
    return bool(row)


def record_webhook_delivery(
    project_id: str, connector_type: str, delivery_id: str
) -> bool:
    if not delivery_id:
        return True
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO webhook_deliveries (
                    delivery_id, project_id, connector_type, received_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    f"{project_id}:{connector_type}:{delivery_id}",
                    project_id,
                    connector_type,
                    datetime.now().isoformat(),
                ),
            )
    except sqlite3.IntegrityError:
        return False
    except sqlite3.OperationalError:
        init_store()
        return record_webhook_delivery(project_id, connector_type, delivery_id)
    return True


def record_audit_event(
    project_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str,
    details: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    now: str = datetime.now().isoformat()
    record: Dict[str, Any] = {
        "audit_id": str(uuid.uuid4()),
        "project_id": project_id,
        "event_type": event_type,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "details": _safe_audit_details(details or {}),
        "created_at": now,
    }
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events (
                    audit_id, project_id, event_type, actor_type, actor_id,
                    details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["audit_id"],
                    project_id,
                    event_type,
                    actor_type,
                    actor_id,
                    json.dumps(record["details"], default=str),
                    now,
                ),
            )
    except sqlite3.OperationalError:
        init_store()
        return record_audit_event(project_id, event_type, actor_type, actor_id, details)
    return record


def list_audit_events(project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    safe_limit: int = min(max(limit, 1), 500)
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT audit_id, project_id, event_type, actor_type, actor_id,
                       details_json, created_at
                FROM audit_events
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, safe_limit),
            ).fetchall()
    except sqlite3.OperationalError:
        init_store()
        return list_audit_events(project_id, limit)
    events: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = dict(row)
        item["details"] = json.loads(item.pop("details_json"))
        events.append(item)
    return events


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
    _record_release_marker(event)
    _build_edges_for_event(event)
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


def create_incident_if_needed(
    project_id: str,
    service: str,
    environment: str,
    alert_description: str,
    severity: str,
    timestamp: str,
    dedupe_minutes: int = 10,
) -> Optional[Dict[str, Any]]:
    cutoff: str = (datetime.now() - timedelta(minutes=dedupe_minutes)).isoformat()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT incident_id FROM incidents
            WHERE project_id = ? AND service = ? AND created_at >= ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, service, cutoff),
        ).fetchone()
    if row:
        return None
    return create_incident(project_id, service, environment, alert_description, severity, timestamp)


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


def list_incidents(project_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    safe_limit: int = min(max(int(limit), 1), 500)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT record_json FROM incidents
            WHERE project_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (project_id, safe_limit),
        ).fetchall()
    return [json.loads(row["record_json"]) for row in rows]


def save_incident_record(project_id: str, record: Dict[str, Any]) -> None:
    incident_id: str = str(record["incident_id"])
    now: str = datetime.now().isoformat()
    record = dict(record)
    record["deployment_risk_report"] = build_deployment_risk_report(
        project_id, str(record.get("service") or ""), record
    )
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
        if record.get("deployment_risk_report"):
            conn.execute(
                """
                INSERT OR REPLACE INTO deployment_risk_reports (
                    report_id, incident_id, project_id, service, report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _report_id(incident_id),
                    incident_id,
                    project_id,
                    record.get("service", ""),
                    json.dumps(record["deployment_risk_report"], default=str),
                    now,
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
        if event_type in {"log", "app_error", "supabase_event", "browser_error", "api_failure"}:
            logs.extend(_payload_to_logs(payload, service, row["timestamp"]))
        elif event_type in {"metric", "business_metric", "frontend_performance"}:
            metrics.extend(_payload_to_metrics(payload))
        elif event_type in {"deployment", "github_push", "github_pr", "release_marker"}:
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


def list_evidence_edges(project_id: str, service: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT edge_type, confidence, explanation, created_at
            FROM incident_evidence_edges
            WHERE project_id = ? AND service = ?
            ORDER BY created_at DESC
            LIMIT 25
            """,
            (project_id, service),
        ).fetchall()
    return [dict(row) for row in rows]


def count_recent_events(
    project_id: str, service: str, event_types: set[str], minutes: int
) -> int:
    cutoff: str = (datetime.now() - timedelta(minutes=minutes)).isoformat()
    placeholders: str = ",".join("?" for _ in event_types)
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count FROM evidence_events
            WHERE project_id = ? AND service = ? AND event_type IN ({placeholders})
            AND created_at >= ?
            """,
            [project_id, service, *sorted(event_types), cutoff],
        ).fetchone()
    return int(row["count"] if row else 0)


def build_deployment_risk_report(
    project_id: str, service: str, record: Dict[str, Any]
) -> Dict[str, Any]:
    release = _latest_release_marker(project_id, service)
    browser_failures: int = count_recent_events(
        project_id, service, {"browser_error", "api_failure"}, 30
    )
    edges = list_evidence_edges(project_id, service)
    deploy_edges = [edge for edge in edges if edge["edge_type"] == "deployment_precedes_error"]
    score: float = min(0.95, 0.35 + 0.15 * len(deploy_edges) + 0.05 * browser_failures) if release else 0.0
    action: str = "need_more_evidence"
    if score >= 0.75 and browser_failures >= 3:
        action = "rollback"
    elif score >= 0.55:
        action = "hotfix"
    elif browser_failures:
        action = "wait"
    return {
        "mode": "report_only",
        "deployment_correlation_score": round(score, 2),
        "rollback_confidence": round(score if action == "rollback" else min(score, 0.5), 2),
        "recommended_action": action,
        "release_sha": release.get("release_sha", "") if release else "",
        "suspect_change_summary": release.get("summary", "") if release else "No release marker linked yet.",
        "browser_failures_last_30m": browser_failures,
        "changed_files_evidence": release.get("summary", "") if release else "",
        "evidence_edges": deploy_edges[:5],
    }


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
                    "message": str(
                        item.get("message")
                        or item.get("msg")
                        or item.get("stack")
                        or item.get("api_url")
                        or item
                    ),
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


def _record_release_marker(event: Dict[str, Any]) -> None:
    payload: Dict[str, Any] = event["payload"]
    release_sha: str = str(
        payload.get("release_sha") or payload.get("after") or payload.get("sha") or ""
    )
    if not release_sha and event["event_type"] not in {"github_push", "deployment", "release_marker"}:
        return
    if not release_sha:
        release_sha = str(uuid.uuid4())
    summary: str = json.dumps(payload.get("commits") or payload, default=str)[:1000]
    now: str = datetime.now().isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO release_markers (
                release_id, project_id, service, release_sha, source_event_id,
                timestamp, summary, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{event['project_id']}:{event['service']}:{release_sha}",
                event["project_id"],
                event["service"],
                release_sha,
                event["event_id"],
                event["timestamp"],
                summary,
                now,
            ),
        )


def _build_edges_for_event(event: Dict[str, Any]) -> None:
    payload: Dict[str, Any] = event["payload"]
    release_sha: str = str(payload.get("release_sha") or payload.get("after") or "")
    error_signature: str = _signature(payload)
    event_time: str = event["timestamp"]
    with connect() as conn:
        candidate_rows = conn.execute(
            """
            SELECT event_id, event_type, timestamp, payload_json
            FROM evidence_events
            WHERE project_id = ? AND service = ? AND event_id != ?
            ORDER BY created_at DESC
            LIMIT 50
            """,
            (event["project_id"], event["service"], event["event_id"]),
        ).fetchall()
    edges: list[tuple[str, str, float, str]] = []
    for row in candidate_rows:
        other_payload = json.loads(row["payload_json"])
        other_release: str = str(other_payload.get("release_sha") or other_payload.get("after") or "")
        other_signature: str = _signature(other_payload)
        if release_sha and other_release and release_sha == other_release:
            edges.append((row["event_id"], "same_release", 0.9, f"Both events reference release {release_sha}."))
        if error_signature and other_signature and error_signature == other_signature:
            edges.append((row["event_id"], "same_error_signature", 0.8, f"Shared error signature {error_signature}."))
        if row["event_type"] in {"github_push", "deployment", "release_marker"} and event["event_type"] in {"browser_error", "api_failure"}:
            if str(row["timestamp"]) <= event_time:
                edges.append((row["event_id"], "deployment_precedes_error", 0.75, "Deployment/change evidence precedes browser/API failure."))
    now = datetime.now().isoformat()
    with connect() as conn:
        for other_id, edge_type, confidence, explanation in edges[:10]:
            conn.execute(
                """
                INSERT OR IGNORE INTO incident_evidence_edges (
                    edge_id, project_id, service, left_event_id, right_event_id,
                    edge_type, confidence, explanation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _edge_id(event["event_id"], other_id, edge_type),
                    event["project_id"],
                    event["service"],
                    other_id,
                    event["event_id"],
                    edge_type,
                    confidence,
                    explanation,
                    now,
                ),
            )


def _latest_release_marker(project_id: str, service: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT release_sha, summary, timestamp
            FROM release_markers
            WHERE project_id = ? AND service = ?
            ORDER BY timestamp DESC, created_at DESC
            LIMIT 1
            """,
            (project_id, service),
        ).fetchone()
    return dict(row) if row else {}


def _signature(payload: Dict[str, Any]) -> str:
    text: str = str(payload.get("message") or payload.get("stack") or payload.get("api_url") or "")
    text = " ".join(text.lower().split())
    return hashlib.sha256(text[:300].encode("utf-8")).hexdigest()[:16] if text else ""


def _edge_id(left: str, right: str, edge_type: str) -> str:
    return hashlib.sha256(f"{left}:{right}:{edge_type}".encode("utf-8")).hexdigest()


def _report_id(incident_id: str) -> str:
    return hashlib.sha256(f"risk-report:{incident_id}".encode("utf-8")).hexdigest()


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


def _safe_audit_details(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(
                marker in key_text.lower()
                for marker in ("secret", "token", "password", "authorization", "credentials", "key")
            ):
                cleaned[key_text] = "[REDACTED]"
            else:
                cleaned[key_text] = _safe_audit_details(item)
        return cleaned
    if isinstance(value, list):
        return [_safe_audit_details(item) for item in value]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "...[TRUNCATED]"
    return value


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _invocation_id(incident_id: str, invocation: Dict[str, Any]) -> str:
    basis: str = json.dumps(invocation, sort_keys=True, default=str)
    return hashlib.sha256(f"{incident_id}:{basis}".encode("utf-8")).hexdigest()
