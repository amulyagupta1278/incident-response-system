import asyncio
import os
import hmac
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

load_dotenv()

from agents import IncidentState
from agents.agentic_system import get_compiled_graph
from agents.event_gateway import (
    browser_event_to_body,
    github_event_to_body,
    normalize_event,
    supabase_event_to_body,
)
from agents.gateway_store import (
    authenticate_api_key,
    authenticate_browser_key,
    claim_next_job,
    create_incident,
    create_incident_if_needed,
    get_incident as get_persistent_incident,
    init_store,
    list_evidence_chunks,
    list_evidence_edges,
    load_state_inputs,
    record_webhook_delivery,
    save_evidence_event,
    save_incident_record,
    update_job,
    upsert_service_business_config,
)
from agents.llm import get_model, get_provider, get_timeout_seconds, llm_strict_mode
from agents.memory import record_incident
from agents.notify import post_war_room, war_room_configured
from agents.qa import answer_question

_gateway_worker_task: asyncio.Task | None = None
_rate_limits: Dict[str, List[float]] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:
    global _gateway_worker_task
    init_store()
    if os.getenv("GATEWAY_WORKER_ENABLED", "true").lower() in {"1", "true", "yes", "on"}:
        _gateway_worker_task = asyncio.create_task(_gateway_worker_loop())
    try:
        yield
    finally:
        if _gateway_worker_task:
            _gateway_worker_task.cancel()


app: FastAPI = FastAPI(title="AI Operations Command Center", lifespan=lifespan)


def _allowed_origins() -> List[str]:
    raw: str = os.getenv("ALLOWED_ORIGINS", "")
    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8011",
        "http://127.0.0.1:8011",
        "http://localhost:8012",
        "http://127.0.0.1:8012",
    ]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

incident_store: Dict[str, Dict[str, Any]] = {}
incident_order: List[str] = []


def _serialize_state(values: Dict[str, Any]) -> Dict[str, Any]:
    completed: Any = values.get("completed_steps", set())
    return {
        "incident_id": values.get("incident_id"),
        "timestamp": values.get("timestamp"),
        "alert_description": values.get("alert_description"),
        "service": values.get("service"),
        "severity": values.get("severity"),
        "project_id": values.get("project_id", ""),
        "environment": values.get("environment", "production"),
        "log_source_path": values.get("log_source_path", ""),
        "analysis_iterations": values.get("analysis_iterations", 0),
        "rca_confidence": values.get("rca_confidence", 0.0),
        "current_status": values.get("current_status", "initial"),
        "completed_steps": sorted(completed) if completed else [],
        "log_anomalies": values.get("log_anomalies", []),
        "log_context_cache": values.get("log_context_cache", {}),
        "metric_anomalies": values.get("metric_anomalies", []),
        "deployment_changes": values.get("deployment_changes", []),
        "root_cause": values.get("root_cause"),
        "affected_users": values.get("affected_users", 0),
        "estimated_revenue_impact_per_minute": values.get(
            "estimated_revenue_impact_per_minute", 0.0
        ),
        "revenue_impact_justification": values.get(
            "revenue_impact_justification", {}
        ),
        "engineering_summary": values.get("engineering_summary", ""),
        "executive_summary": values.get("executive_summary", ""),
        "recovery_recommendations": values.get("recovery_recommendations", []),
        "similar_incidents": values.get("similar_incidents", []),
        "agent_invocations": values.get("agent_invocations", []),
    }


async def _run_analysis(incident_id: str, state: IncidentState) -> None:
    """Stream the agent graph, updating the store after every node so the
    dashboard can render live agent activity."""
    graph: Any = get_compiled_graph()
    notified: set = set()
    try:
        async for values in graph.astream(
            dict(vars(state)),
            config={"recursion_limit": 60},
            stream_mode="values",
        ):
            if not isinstance(values, dict):
                values = dict(vars(values))
            record: Dict[str, Any] = _serialize_state(values)
            record["created_at"] = incident_store[incident_id].get("created_at")
            incident_store[incident_id] = record

            root_cause: Dict[str, Any] = record.get("root_cause") or {}
            if root_cause and "rca" not in notified:
                notified.add("rca")
                deploy_note: str = (
                    f" ⚡ {root_cause['deploy_correlation']}"
                    if root_cause.get("deploy_correlation")
                    else ""
                )
                await post_war_room(
                    f"🔍 Root cause identified for *{record.get('service')}*: "
                    f"{root_cause.get('hypothesis')} "
                    f"({root_cause.get('confidence', 0) * 100:.0f}% confidence)."
                    f"{deploy_note}"
                )

        if incident_store[incident_id].get("current_status") != "complete":
            incident_store[incident_id]["current_status"] = "complete"
        record_incident(incident_store[incident_id])

        final: Dict[str, Any] = incident_store[incident_id]
        await post_war_room(
            f"✅ Investigation complete for *{final.get('service')}*: "
            f"{(final.get('root_cause') or {}).get('hypothesis', 'unknown cause')}. "
            f"{final.get('affected_users', 0):,} users affected, "
            f"${final.get('estimated_revenue_impact_per_minute', 0):.2f}/min revenue impact. "
            f"Full report: http://localhost:8000/incident/{incident_id}"
        )
    except Exception as exc:
        print(f"[app] analysis failed for {incident_id}: {exc}")
        incident_store[incident_id]["current_status"] = "failed"
        incident_store[incident_id]["error"] = str(exc)


async def _run_persistent_analysis(job: Dict[str, Any]) -> None:
    incident_id: str = str(job["incident_id"])
    project_id: str = str(job["project_id"])
    record: Dict[str, Any] | None = get_persistent_incident(incident_id, project_id)
    if not record:
        update_job(str(job["job_id"]), "failed", "incident not found")
        return
    inputs: Dict[str, List[Dict[str, Any]]] = load_state_inputs(
        project_id, str(record.get("service"))
    )
    state: IncidentState = IncidentState(
        incident_id=incident_id,
        timestamp=str(record.get("timestamp") or datetime.now().isoformat()),
        alert_description=str(record.get("alert_description") or ""),
        service=str(record.get("service") or "unknown"),
        severity=str(record.get("severity") or "unknown"),
        project_id=project_id,
        environment=str(record.get("environment") or "production"),
        raw_logs=inputs["logs"],
        raw_metrics=inputs["metrics"],
        deployment_changes=inputs["deployments"],
    )
    graph: Any = get_compiled_graph()
    try:
        async for values in graph.astream(
            dict(vars(state)),
            config={"recursion_limit": 60},
            stream_mode="values",
        ):
            if not isinstance(values, dict):
                values = dict(vars(values))
            next_record: Dict[str, Any] = _serialize_state(values)
            next_record["project_id"] = project_id
            next_record["environment"] = record.get("environment", "production")
            next_record["created_at"] = record.get("created_at")
            save_incident_record(project_id, next_record)
        final_record: Dict[str, Any] | None = get_persistent_incident(incident_id, project_id)
        if final_record and final_record.get("current_status") != "complete":
            final_record["current_status"] = "complete"
            save_incident_record(project_id, final_record)
        update_job(str(job["job_id"]), "complete")
    except Exception as exc:
        failed: Dict[str, Any] | None = get_persistent_incident(incident_id, project_id)
        if failed:
            failed["current_status"] = "failed"
            failed["error"] = str(exc)
            save_incident_record(project_id, failed)
        update_job(str(job["job_id"]), "failed", str(exc))


async def _gateway_worker_loop() -> None:
    while True:
        job: Dict[str, Any] | None = claim_next_job()
        if job:
            await _run_persistent_analysis(job)
            continue
        await asyncio.sleep(0.75)


def _extract_bearer(authorization: str = "") -> str:
    prefix: str = "Bearer "
    return authorization[len(prefix):].strip() if authorization.startswith(prefix) else ""


def _require_project(authorization: str = "") -> str:
    project_id: str | None = authenticate_api_key(_extract_bearer(authorization))
    if not project_id:
        raise HTTPException(status_code=401, detail="valid bearer API key required")
    _enforce_rate_limit(project_id)
    return project_id


def _require_browser_project(body: Dict[str, Any], authorization: str = "") -> str:
    raw_key: str = _extract_bearer(authorization) or str(body.get("public_key") or "")
    project_id: str | None = authenticate_browser_key(raw_key)
    if not project_id:
        raise HTTPException(status_code=401, detail="valid browser key required")
    _enforce_rate_limit(f"browser:{project_id}")
    body_project_id: str = str(body.get("project_id") or "")
    if body_project_id and body_project_id != project_id:
        raise HTTPException(status_code=403, detail="browser key project mismatch")
    return project_id


def _env_enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes", "on"}


def _demo_mode_enabled() -> bool:
    default: str = "false" if os.getenv("APP_ENV", "development").lower() == "production" else "true"
    return _env_enabled("DEMO_MODE", default)


def _require_demo_mode() -> None:
    if not _demo_mode_enabled():
        raise HTTPException(status_code=404, detail="Not found")


def _enforce_rate_limit(project_id: str) -> None:
    limit: int = int(os.getenv("INGEST_RATE_LIMIT_PER_MINUTE", "120"))
    now: float = time.time()
    window_start: float = now - 60
    bucket: List[float] = [ts for ts in _rate_limits.get(project_id, []) if ts >= window_start]
    if len(bucket) >= limit:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    bucket.append(now)
    _rate_limits[project_id] = bucket


async def _request_json_with_limit(request: Request) -> Dict[str, Any]:
    max_bytes: int = int(os.getenv("MAX_INGEST_PAYLOAD_BYTES", "262144"))
    body: bytes = await request.body()
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail="payload too large")
    try:
        parsed: Any = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    return parsed


def _browser_allowed_origins() -> List[str]:
    raw: str = os.getenv("BROWSER_ALLOWED_ORIGINS") or os.getenv("ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def _enforce_browser_origin(request: Request) -> None:
    allowed: List[str] = _browser_allowed_origins()
    if not allowed:
        return
    origin: str = request.headers.get("origin", "")
    if origin not in allowed:
        raise HTTPException(status_code=403, detail="browser origin not allowed")


def _webhook_signatures_required() -> bool:
    if _env_enabled("CONNECTOR_SIGNATURES_REQUIRED"):
        return True
    return os.getenv("APP_ENV", "development").lower() == "production"


def _verify_signature_with_secret(
    body: bytes, signature: str, secret: str, secret_label: str
) -> None:
    if not secret:
        if _webhook_signatures_required():
            raise HTTPException(
                status_code=401,
                detail=f"{secret_label} must be configured before accepting connector webhooks",
            )
        return
    if not signature:
        raise HTTPException(status_code=401, detail="missing webhook signature")
    expected: str = "sha256=" + hmac.new(secret.encode(), body, "sha256").hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="invalid webhook signature")


def _verify_webhook_signature(body: bytes, signature: str, secret_env: str) -> None:
    _verify_signature_with_secret(body, signature, os.getenv(secret_env, ""), secret_env)


def _project_secret(env_name: str, project_id: str) -> str:
    entries: list[str] = [
        item.strip() for item in os.getenv(env_name, "").split(",") if item.strip()
    ]
    for entry in entries:
        if ":" not in entry:
            continue
        entry_project, secret = entry.split(":", 1)
        if entry_project.strip() == project_id:
            return secret.strip()
    return ""


def _github_webhook_secret(project_id: str) -> str:
    return _project_secret("GITHUB_WEBHOOK_SECRETS", project_id) or os.getenv(
        "GITHUB_WEBHOOK_SECRET", ""
    )


def _record_connector_delivery(
    project_id: str, connector_type: str, delivery_id: str
) -> None:
    if not record_webhook_delivery(project_id, connector_type, delivery_id):
        raise HTTPException(status_code=409, detail="duplicate webhook delivery")


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "").rstrip("/") or "http://localhost:8000"


def _connector_setup_payload(project_id: str) -> Dict[str, Any]:
    base_url: str = _public_base_url()
    return {
        "project_id": project_id,
        "security_model": {
            "server_key": "Use Authorization: Bearer <project-api-key> only from trusted servers.",
            "browser_key": "Use separate public browser key only for /api/v1/browser/events.",
            "project_scope": "Authenticated key decides project_id; body project_id cannot override key scope.",
            "webhook_signatures": "Set CONNECTOR_SIGNATURES_REQUIRED=true in production.",
            "replay_protection": "Send stable delivery id headers so duplicate webhooks are rejected.",
        },
        "endpoints": {
            "universal_ingest": f"{base_url}/api/v1/events",
            "browser_ingest": f"{base_url}/api/v1/browser/events",
            "github_webhook": f"{base_url}/api/v1/connectors/github/webhook",
            "github_direct_webhook": f"{base_url}/api/v1/connectors/github/{project_id}/webhook",
            "supabase_webhook": f"{base_url}/api/v1/connectors/supabase/webhook",
            "create_incident": f"{base_url}/api/v1/incidents",
            "ask_incident": f"{base_url}/api/v1/incidents/{{incident_id}}/ask",
        },
        "browser_snippet": (
            f'<script src="{base_url}/sdk/immune-agent.js" '
            f'data-project-id="{project_id}" '
            'data-public-key="<browser-public-key>" '
            'data-service="<service-name>" '
            'data-environment="production" '
            'data-release-sha="<git-sha>"></script>'
        ),
        "github_webhook": {
            "url": f"{base_url}/api/v1/connectors/github/{project_id}/webhook",
            "content_type": "application/json",
            "secret_env": "GITHUB_WEBHOOK_SECRETS or GITHUB_WEBHOOK_SECRET",
            "github_ui_setup": {
                "payload_url": f"{base_url}/api/v1/connectors/github/{project_id}/webhook",
                "content_type": "application/json",
                "secret": "project GitHub webhook secret",
                "events": ["push", "pull_request", "deployment", "release"],
            },
            "required_headers": {
                "X-GitHub-Event": "<github-event>",
                "X-GitHub-Delivery": "<delivery-id>",
                "X-Hub-Signature-256": "sha256=<hmac>",
            },
        },
        "supabase_webhook": {
            "url": f"{base_url}/api/v1/connectors/supabase/webhook",
            "content_type": "application/json",
            "secret_env": "SUPABASE_WEBHOOK_SECRET",
            "required_headers": {
                "Authorization": "Bearer <project-api-key>",
                "X-Supabase-Signature": "sha256=<hmac>",
                "X-Supabase-Delivery": "<delivery-id>",
            },
        },
        "production_env": {
            "APP_ENV": "production",
            "CONNECTOR_SIGNATURES_REQUIRED": "true",
            "ALLOWED_ORIGINS": "https://your-nextjs-app.example",
            "BROWSER_ALLOWED_ORIGINS": "https://your-website.example",
            "RAW_PAYLOAD_RETENTION_DAYS": "0",
        },
    }


def _with_gateway_context(record: Dict[str, Any]) -> Dict[str, Any]:
    enriched: Dict[str, Any] = dict(record)
    project_id: str = str(enriched.get("project_id") or "")
    service: str = str(enriched.get("service") or "")
    if project_id and service:
        enriched["external_evidence_chunks"] = list_evidence_chunks(project_id, service)
        enriched["evidence_edges"] = list_evidence_edges(project_id, service)
    return enriched


@app.get("/api/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy"}


@app.get("/api/config")
async def get_config() -> Dict[str, Any]:
    provider: Any = get_provider()
    return {
        "llm_provider": provider or "heuristic",
        "llm_model": get_model(),
        "llm_strict": llm_strict_mode(),
        "llm_timeout_seconds": get_timeout_seconds(),
        "agentic": True,
        "war_room": war_room_configured(),
    }


@app.get("/api/graph")
async def get_graph_visualization() -> Dict[str, str]:
    return {"mermaid": get_compiled_graph().get_graph().draw_mermaid()}


@app.post("/api/v1/events")
async def ingest_event(
    request: Request, authorization: str = Header(default="")
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    body: Dict[str, Any] = await _request_json_with_limit(request)
    body["project_id"] = project_id
    try:
        event, chunks = normalize_event(body, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    saved: Dict[str, Any] = save_evidence_event(event, chunks)
    return {
        "event_id": saved["event_id"],
        "project_id": project_id,
        "chunks_created": len(chunks),
        "status": "accepted",
    }


@app.post("/api/v1/browser/events")
async def ingest_browser_event(
    request: Request, authorization: str = Header(default="")
) -> Dict[str, Any]:
    _enforce_browser_origin(request)
    body: Dict[str, Any] = await _request_json_with_limit(request)
    project_id: str = _require_browser_project(body, authorization)
    browser_body: Dict[str, Any] = browser_event_to_body(body, project_id)
    event, chunks = normalize_event(browser_body, project_id)
    saved: Dict[str, Any] = save_evidence_event(event, chunks)

    incident = None
    if event["event_type"] in {"browser_error", "api_failure"}:
        recent_count: int = int(os.getenv("BROWSER_ERROR_TRIGGER_COUNT", "3"))
        window_minutes: int = int(os.getenv("BROWSER_ERROR_TRIGGER_WINDOW_MINUTES", "5"))
        from agents.gateway_store import count_recent_events

        count: int = count_recent_events(
            project_id, event["service"], {"browser_error", "api_failure"}, window_minutes
        )
        if count >= recent_count:
            incident = create_incident_if_needed(
                project_id=project_id,
                service=event["service"],
                environment=event["environment"],
                alert_description=f"Browser/API failure burst on {event['service']} ({count} events/{window_minutes}m)",
                severity="critical" if count >= recent_count * 2 else "high",
                timestamp=event["timestamp"],
            )
    return {
        "event_id": saved["event_id"],
        "project_id": project_id,
        "chunks_created": len(chunks),
        "incident": incident,
        "status": "accepted",
    }


@app.post("/api/v1/incidents")
async def create_gateway_incident(
    request: Request, authorization: str = Header(default="")
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    body: Dict[str, Any] = await _request_json_with_limit(request)
    service: str = str(body.get("service", "")).strip()
    if not service:
        raise HTTPException(status_code=400, detail="service is required")
    return create_incident(
        project_id=project_id,
        service=service,
        environment=str(body.get("environment") or "production"),
        alert_description=str(body.get("alert_description") or body.get("description") or ""),
        severity=str(body.get("severity") or "unknown"),
        timestamp=str(body.get("timestamp") or datetime.now().isoformat()),
    )


@app.post("/api/v1/service-config")
async def upsert_gateway_service_config(
    request: Request, authorization: str = Header(default="")
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    body: Dict[str, Any] = await _request_json_with_limit(request)
    service: str = str(body.get("service", "")).strip()
    if not service:
        raise HTTPException(status_code=400, detail="service is required")
    total_users: int = int(body.get("total_users", 0))
    revenue_rate: float = float(body.get("revenue_per_user_per_minute", 0))
    if total_users <= 0 or revenue_rate <= 0:
        raise HTTPException(
            status_code=400,
            detail="total_users and revenue_per_user_per_minute must be positive",
        )
    upsert_service_business_config(
        project_id=project_id,
        service=service,
        total_users=total_users,
        revenue_per_user_per_minute=revenue_rate,
        impact_metric=str(body.get("impact_metric") or "error_rate"),
    )
    return {"project_id": project_id, "service": service, "status": "configured"}


@app.get("/api/v1/connectors/setup")
async def get_connector_setup(authorization: str = Header(default="")) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    return _connector_setup_payload(project_id)


@app.get("/api/v1/incidents/{incident_id}")
async def get_gateway_incident(
    incident_id: str, authorization: str = Header(default="")
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    record: Dict[str, Any] | None = get_persistent_incident(incident_id, project_id)
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")
    return _with_gateway_context(record)


@app.post("/api/v1/incidents/{incident_id}/ask")
async def ask_gateway_incident(
    incident_id: str, request: Request, authorization: str = Header(default="")
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    body: Dict[str, Any] = await _request_json_with_limit(request)
    question: str = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    record: Dict[str, Any] | None = get_persistent_incident(incident_id, project_id)
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")
    result: Dict[str, Any] = await answer_question(_with_gateway_context(record), question)
    result["evidence_confidence"] = "high" if result.get("citations") else "insufficient"
    result["missing_evidence"] = [] if result.get("citations") else ["cited evidence"]
    return result


@app.post("/api/v1/connectors/github/webhook")
async def github_webhook(
    request: Request,
    authorization: str = Header(default=""),
    x_github_event: str = Header(default="push"),
    x_github_delivery: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    raw_body: bytes = await request.body()
    _verify_webhook_signature(raw_body, x_hub_signature_256, "GITHUB_WEBHOOK_SECRET")
    _record_connector_delivery(project_id, "github", x_github_delivery)
    payload: Any = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    body: Dict[str, Any] = github_event_to_body(payload, x_github_event)
    body["project_id"] = project_id
    event, chunks = normalize_event(body, project_id)
    saved: Dict[str, Any] = save_evidence_event(event, chunks)
    return {
        "event_id": saved["event_id"],
        "project_id": project_id,
        "connector": "github",
        "chunks_created": len(chunks),
        "status": "accepted",
    }


@app.post("/api/v1/connectors/github/{project_id}/webhook")
async def github_direct_webhook(
    project_id: str,
    request: Request,
    x_github_event: str = Header(default="push"),
    x_github_delivery: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
) -> Dict[str, Any]:
    _enforce_rate_limit(f"github:{project_id}")
    raw_body: bytes = await request.body()
    _verify_signature_with_secret(
        raw_body,
        x_hub_signature_256,
        _github_webhook_secret(project_id),
        "GITHUB_WEBHOOK_SECRETS or GITHUB_WEBHOOK_SECRET",
    )
    _record_connector_delivery(project_id, "github", x_github_delivery)
    payload: Any = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    body: Dict[str, Any] = github_event_to_body(payload, x_github_event)
    body["project_id"] = project_id
    event, chunks = normalize_event(body, project_id)
    saved: Dict[str, Any] = save_evidence_event(event, chunks)
    return {
        "event_id": saved["event_id"],
        "project_id": project_id,
        "connector": "github",
        "mode": "direct",
        "chunks_created": len(chunks),
        "status": "accepted",
    }


@app.post("/api/v1/connectors/supabase/webhook")
async def supabase_webhook(
    request: Request,
    authorization: str = Header(default=""),
    x_supabase_signature: str = Header(default=""),
    x_supabase_delivery: str = Header(default=""),
    x_request_id: str = Header(default=""),
) -> Dict[str, Any]:
    project_id: str = _require_project(authorization)
    raw_body: bytes = await request.body()
    _verify_webhook_signature(raw_body, x_supabase_signature, "SUPABASE_WEBHOOK_SECRET")
    _record_connector_delivery(
        project_id, "supabase", x_supabase_delivery or x_request_id
    )
    payload: Any = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON object required")
    body: Dict[str, Any] = supabase_event_to_body(payload)
    body["project_id"] = project_id
    event, chunks = normalize_event(body, project_id)
    saved: Dict[str, Any] = save_evidence_event(event, chunks)
    return {
        "event_id": saved["event_id"],
        "project_id": project_id,
        "connector": "supabase",
        "chunks_created": len(chunks),
        "status": "accepted",
    }


@app.post("/api/incidents/trigger")
async def trigger_incident(incident_data: Dict[str, Any]) -> Dict[str, Any]:
    _require_demo_mode()
    incident_id: str = str(uuid.uuid4())
    timestamp: str = incident_data.get("timestamp", datetime.now().isoformat())

    state: IncidentState = IncidentState(
        incident_id=incident_id,
        timestamp=timestamp,
        alert_description=incident_data.get("alert_description", ""),
        service=incident_data.get("service", "unknown"),
        severity=incident_data.get("severity", "unknown"),
        log_source_path=incident_data.get("logs_path", ""),
    )

    record: Dict[str, Any] = _serialize_state(dict(vars(state)))
    record["current_status"] = "investigating"
    record["created_at"] = datetime.now().isoformat()
    incident_store[incident_id] = record
    incident_order.insert(0, incident_id)

    await post_war_room(
        f"🚨 Incident opened on *{state.service}* ({state.severity.upper()}): "
        f"{state.alert_description} — agents dispatched. "
        f"Live: http://localhost:8000/incident/{incident_id}"
    )
    asyncio.create_task(_run_analysis(incident_id, state))

    return record


@app.get("/api/incidents")
async def list_incidents() -> List[Dict[str, Any]]:
    _require_demo_mode()
    return [incident_store[iid] for iid in incident_order if iid in incident_store]


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str) -> Dict[str, Any]:
    _require_demo_mode()
    if incident_id not in incident_store:
        raise HTTPException(status_code=404, detail="Incident not found")

    return incident_store[incident_id]


@app.post("/api/incidents/{incident_id}/ask")
async def ask_incident(incident_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Natural-language Q&A grounded in one incident's investigation data."""
    _require_demo_mode()
    if incident_id not in incident_store:
        raise HTTPException(status_code=404, detail="Incident not found")
    question: str = str(body.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    return await answer_question(incident_store[incident_id], question)


@app.post("/api/incidents/{incident_id}/remediation/{step_index}/decision")
async def decide_remediation(
    incident_id: str, step_index: int, body: Dict[str, Any]
) -> Dict[str, Any]:
    """Human-in-the-loop gate: no recovery action is considered actionable
    until a human explicitly approves it."""
    _require_demo_mode()
    if incident_id not in incident_store:
        raise HTTPException(status_code=404, detail="Incident not found")
    decision: str = str(body.get("decision", ""))
    if decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")
    record: Dict[str, Any] = incident_store[incident_id]
    if step_index < 0 or step_index >= len(record.get("recovery_recommendations", [])):
        raise HTTPException(status_code=404, detail="Recommendation not found")
    record.setdefault("remediation_decisions", {})[str(step_index)] = {
        "decision": decision,
        "decided_at": datetime.now().isoformat(),
    }
    return record


def _postmortem_markdown(record: Dict[str, Any]) -> str:
    rc: Dict[str, Any] = record.get("root_cause") or {}
    decisions: Dict[str, Any] = record.get("remediation_decisions", {})
    impact: Dict[str, Any] = record.get("revenue_impact_justification") or {}
    log_cache: Dict[str, Any] = record.get("log_context_cache") or {}
    lines: List[str] = [
        f"# Incident Postmortem — {record.get('service')}",
        "",
        f"- **Incident ID:** {record.get('incident_id')}",
        f"- **Date:** {record.get('timestamp')}",
        f"- **Severity:** {record.get('severity')}",
        f"- **Alert:** {record.get('alert_description')}",
        "",
        "## Executive Summary",
        "",
        record.get("executive_summary") or "N/A",
        "",
        "## Root Cause",
        "",
        f"**{rc.get('hypothesis', 'Unknown')}** (confidence: {rc.get('confidence', 0) * 100:.0f}%)",
        "",
    ]
    if rc.get("deploy_correlation"):
        lines += [f"> ⚡ {rc['deploy_correlation']}", ""]
    lines += ["### Supporting Evidence", ""]
    lines += [f"- {e}" for e in rc.get("supporting_evidence", [])]
    if rc.get("ruled_out_hypotheses"):
        lines += ["", "### Alternatives Considered & Ruled Out", ""]
        lines += [
            f"- ~~{r.get('hypothesis')}~~ — {r.get('reason')}"
            for r in rc["ruled_out_hypotheses"]
        ]
    lines += [
        "",
        "## Business Impact",
        "",
        f"- Affected users: {record.get('affected_users', 0):,}",
        f"- Estimated revenue impact: ${record.get('estimated_revenue_impact_per_minute', 0):.2f}/minute",
    ]
    if impact:
        lines += [
            f"- Verification: {impact.get('verification_status', 'unknown')} ({impact.get('confidence_level', 'unknown')} confidence)",
            (
                f"- Justification: {impact.get('affected_users', 0):,} affected users x "
                f"${impact.get('revenue_per_user_per_minute', 0):.2f}/user/min"
            ),
            (
                f"- Bounded range: ${impact.get('lower_bound_per_minute', 0):.2f}-"
                f"${impact.get('upper_bound_per_minute', 0):.2f}/minute"
            ),
            (
                f"- Limit: impact rate capped at {impact.get('limits', {}).get('impact_rate_ceiling', 1.0):.0%}; "
                f"affected users capped at {impact.get('limits', {}).get('affected_users_ceiling', 0):,}"
            ),
        ]
        if impact.get("data_gaps"):
            lines += ["- Data gaps: " + "; ".join(str(gap) for gap in impact["data_gaps"])]
    if log_cache:
        lines += [
            "",
            "## Centralized Log Context",
            "",
            f"- Logs scanned: {log_cache.get('total_logs_scanned', 0):,}",
            f"- Error context windows cached: {len(log_cache.get('error_contexts', []))}",
        ]
        for item in log_cache.get("hierarchy", []):
            lines.append(
                f"- {item.get('severity')} / {item.get('type')}: {item.get('count')} events"
            )
    lines += ["", "## Recovery Actions", ""]
    for i, rec in enumerate(record.get("recovery_recommendations", [])):
        status: str = decisions.get(str(i), {}).get("decision", "pending review")
        lines.append(f"{i + 1}. {rec} — _{status}_")
    if record.get("similar_incidents"):
        lines += ["", "## Related Past Incidents", ""]
        lines += [
            f"- Incident #{s.get('number')} on {s.get('service')} "
            f"({str(s.get('resolved_at', ''))[:10]}): {s.get('hypothesis')} — {s.get('match_reason')}"
            for s in record["similar_incidents"]
        ]
    lines += ["", "## Investigation Timeline", ""]
    for inv in record.get("agent_invocations", []):
        detail: str = inv.get("reasoning") or inv.get("hypothesis") or inv.get("action", "")
        lines.append(
            f"- `{str(inv.get('timestamp', ''))[11:19]}` **{inv.get('agent')}** — {detail}"
        )
    lines += ["", "---", "", "_Generated automatically by AI Operations Command Center_", ""]
    return "\n".join(lines)


@app.get("/api/incidents/{incident_id}/postmortem")
async def download_postmortem(incident_id: str) -> Response:
    _require_demo_mode()
    if incident_id not in incident_store:
        raise HTTPException(status_code=404, detail="Incident not found")
    markdown: str = _postmortem_markdown(incident_store[incident_id])
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="postmortem-{incident_id[:8]}.md"'
        },
    )


@app.get("/")
async def serve_dashboard() -> FileResponse:
    return FileResponse("frontend/dashboard.html", media_type="text/html")


@app.get("/styles.css")
async def serve_styles() -> FileResponse:
    return FileResponse("frontend/styles.css", media_type="text/css")


@app.get("/sdk/immune-agent.js")
async def serve_immune_agent_sdk() -> FileResponse:
    return FileResponse("frontend/immune-agent.js", media_type="application/javascript")


@app.get("/incident/{incident_id}")
async def serve_incident_detail(incident_id: str) -> FileResponse:
    return FileResponse("frontend/incident_detail.html", media_type="text/html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
