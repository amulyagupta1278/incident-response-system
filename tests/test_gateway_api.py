import hashlib
import hmac
import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def make_client(monkeypatch: Any, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "gateway.db"))
    monkeypatch.setenv("INGEST_API_KEYS", "project-a:key-a,project-b:key-b")
    monkeypatch.setenv("BROWSER_PUBLIC_KEYS", "project-a:browser-a,project-b:browser-b")
    monkeypatch.setenv("GATEWAY_WORKER_ENABLED", "false")
    if "ALLOWED_ORIGINS" not in os.environ:
        monkeypatch.setenv("ALLOWED_ORIGINS", "")
    if "BROWSER_ALLOWED_ORIGINS" not in os.environ:
        monkeypatch.setenv("BROWSER_ALLOWED_ORIGINS", "")
    if "CONNECTOR_SIGNATURES_REQUIRED" not in os.environ:
        monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    else:
        import app  # noqa: F401
    return TestClient(sys.modules["app"].app)


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def signed_body(payload: dict[str, Any], secret: str) -> tuple[str, str]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body.decode(), signature


def slack_signature(body: str, secret: str, timestamp: str) -> str:
    base = f"v0:{timestamp}:{body}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def test_v1_ingest_requires_project_api_key(monkeypatch: Any, tmp_path: Path) -> None:
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/events",
        json={
            "event_type": "log",
            "source": "custom",
            "service": "api",
            "payload": {"message": "ERROR timeout"},
        },
    )

    assert response.status_code == 401


def test_v1_ingest_accepts_and_redacts_event(monkeypatch: Any, tmp_path: Path) -> None:
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/events",
        headers=auth("key-a"),
        json={
            "event_type": "log",
            "source": "custom",
            "service": "api",
            "payload": {"message": "ERROR token=secret-value timeout"},
        },
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == "project-a"
    assert response.json()["chunks_created"] >= 1


def test_v1_incidents_are_project_scoped(monkeypatch: Any, tmp_path: Path) -> None:
    client = make_client(monkeypatch, tmp_path)

    created = client.post(
        "/api/v1/incidents",
        headers=auth("key-a"),
        json={
            "service": "api",
            "severity": "critical",
            "alert_description": "timeouts rising",
        },
    )

    assert created.status_code == 200
    incident_id = created.json()["incident_id"]

    assert client.get(f"/api/v1/incidents/{incident_id}", headers=auth("key-a")).status_code == 200
    assert client.get(f"/api/v1/incidents/{incident_id}", headers=auth("key-b")).status_code == 404


def test_v1_service_config_endpoint(monkeypatch: Any, tmp_path: Path) -> None:
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/service-config",
        headers=auth("key-a"),
        json={
            "service": "api",
            "total_users": 25000,
            "revenue_per_user_per_minute": 0.12,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "configured"


def test_intelligence_endpoints_require_auth_and_remain_project_scoped(
    monkeypatch: Any, tmp_path: Path
) -> None:
    client = make_client(monkeypatch, tmp_path)
    created = client.post(
        "/api/v1/incidents",
        headers=auth("key-a"),
        json={"service": "payment-api", "severity": "critical", "alert_description": "pool timeout"},
    )
    assert created.status_code == 200

    assert client.get("/api/v1/analytics").status_code == 401
    analytics = client.get("/api/v1/analytics?period=week", headers=auth("key-a"))
    graph = client.get("/api/v1/knowledge-graph", headers=auth("key-a"))
    catalog = client.get("/api/v1/connectors/catalog", headers=auth("key-a"))

    assert analytics.status_code == 200
    assert analytics.json()["project_id"] == "project-a"
    assert analytics.json()["total"] == 1
    assert graph.status_code == 200
    assert graph.json()["project_id"] == "project-a"
    assert "relational" in graph.json()
    assert catalog.status_code == 200
    assert any(item["type"] == "memgraph" for item in catalog.json()["connectors"])


def test_connector_setup_requires_server_key_and_is_project_scoped(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ops.example")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "do-not-leak")
    client = make_client(monkeypatch, tmp_path)

    missing = client.get("/api/v1/connectors/setup")
    browser = client.get("/api/v1/connectors/setup", headers=auth("browser-a"))
    project_a = client.get("/api/v1/connectors/setup", headers=auth("key-a"))
    project_b = client.get("/api/v1/connectors/setup", headers=auth("key-b"))
    payload = project_a.json()

    assert missing.status_code == 401
    assert browser.status_code == 401
    assert project_a.status_code == 200
    assert project_b.status_code == 200
    assert payload["project_id"] == "project-a"
    assert project_b.json()["project_id"] == "project-b"
    assert payload["endpoints"]["github_direct_webhook"] == (
        "https://ops.example/api/v1/connectors/github/project-a/webhook"
    )
    assert payload["github_webhook"]["url"] == (
        "https://ops.example/api/v1/connectors/github/project-a/webhook"
    )
    assert payload["endpoints"]["supabase_direct_webhook"] == (
        "https://ops.example/api/v1/connectors/supabase/project-a/webhook"
    )
    assert payload["supabase_webhook"]["url"] == (
        "https://ops.example/api/v1/connectors/supabase/project-a/webhook"
    )
    assert 'data-project-id="project-a"' in payload["browser_snippet"]
    assert "do-not-leak" not in json.dumps(payload)


def test_readiness_is_project_scoped_and_never_leaks_secrets(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ops.example")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ops-ui.example")
    monkeypatch.setenv("BROWSER_ALLOWED_ORIGINS", "https://shop.example")
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRETS", "project-a:github-secret")
    monkeypatch.setenv("SUPABASE_WEBHOOK_SECRETS", "project-a:supabase-secret")
    monkeypatch.setenv("RAW_PAYLOAD_RETENTION_DAYS", "0")
    monkeypatch.setenv("ALLOW_SQLITE_IN_PRODUCTION", "true")
    client = make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GATEWAY_WORKER_ENABLED", "true")

    missing = client.get("/api/v1/readiness")
    browser = client.get("/api/v1/readiness", headers=auth("browser-a"))
    response = client.get("/api/v1/readiness", headers=auth("key-a"))
    payload = response.json()
    serialized = json.dumps(payload)

    assert missing.status_code == 401
    assert browser.status_code == 401
    assert response.status_code == 200
    assert payload["project_id"] == "project-a"
    assert payload["status"] == "ready"
    assert payload["ai"]["provider"] == "openai"
    assert payload["connectors"]["github_direct"] is True
    assert payload["connectors"]["supabase_direct"] is True
    assert payload["security"]["demo_mode"] is False
    assert payload["missing"] == []
    assert "github-secret" not in serialized
    assert "supabase-secret" not in serialized


def test_readiness_reports_blocked_production_security_gaps(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "false")
    monkeypatch.setenv("ALLOWED_ORIGINS", "")
    monkeypatch.setenv("BROWSER_ALLOWED_ORIGINS", "")
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRETS", raising=False)
    monkeypatch.delenv("SUPABASE_WEBHOOK_SECRETS", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_WEBHOOK_SECRET", raising=False)
    client = make_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/readiness", headers=auth("key-a"))
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "blocked"
    assert "github_secret" in payload["missing"]
    assert "supabase_secret" in payload["missing"]
    assert "browser_origin_allowlist" in payload["missing"]
    assert "cors_allowlist" in payload["missing"]
    assert "demo_routes_disabled" in payload["missing"]
    assert "production_database" in payload["missing"]


def test_readiness_does_not_claim_postgres_support_without_driver(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ops-ui.example")
    monkeypatch.setenv("BROWSER_ALLOWED_ORIGINS", "https://shop.example")
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRETS", "project-a:github-secret")
    monkeypatch.setenv("SUPABASE_WEBHOOK_SECRETS", "project-a:supabase-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://ops.example/db")
    client = make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("GATEWAY_WORKER_ENABLED", "true")

    response = client.get("/api/v1/readiness", headers=auth("key-a"))
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "blocked"
    assert payload["security"]["database_backend"] == "postgres_configured_unsupported"
    assert "production_database" in payload["missing"]


def test_admin_can_provision_project_without_project_env_entries(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    client = make_client(monkeypatch, tmp_path)

    provisioned = client.post(
        "/api/v1/projects",
        headers=auth("admin-secret"),
        json={"project_id": "project-c", "name": "Project C"},
    )
    payload = provisioned.json()
    server_key = payload["credentials"]["server_api_key"]
    browser_key = payload["credentials"]["browser_public_key"]
    github_secret = payload["credentials"]["github_webhook_secret"]
    supabase_secret = payload["credentials"]["supabase_webhook_secret"]

    setup = client.get("/api/v1/connectors/setup", headers=auth(server_key))
    readiness = client.get("/api/v1/readiness", headers=auth(server_key))

    github_body, github_signature = signed_body(
        {
            "repository": {"name": "web"},
            "after": "new-project-sha",
            "commits": [{"id": "new-project-sha", "message": "project c deploy"}],
        },
        github_secret,
    )
    github = client.post(
        "/api/v1/connectors/github/project-c/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "project-c-delivery",
            "X-Hub-Signature-256": github_signature,
            "Content-Type": "application/json",
        },
        content=github_body,
    )
    supabase_body, supabase_signature = signed_body(
        {"service": "web", "type": "database_event", "message": "db healthy"},
        supabase_secret,
    )
    supabase = client.post(
        "/api/v1/connectors/supabase/project-c/webhook",
        headers={
            "X-Supabase-Delivery": "project-c-supabase",
            "X-Supabase-Signature": supabase_signature,
            "Content-Type": "application/json",
        },
        content=supabase_body,
    )
    browser = client.post(
        "/api/v1/browser/events",
        headers=auth(browser_key),
        json={
            "project_id": "project-c",
            "event_type": "browser_error",
            "service": "web",
            "message": "project c browser error",
        },
    )
    audit = client.get("/api/v1/audit", headers=auth(server_key))
    other_project_audit = client.get("/api/v1/audit", headers=auth("key-a"))
    audit_blob = json.dumps(audit.json())
    audit_types = {event["event_type"] for event in audit.json()["events"]}

    assert provisioned.status_code == 200
    assert payload["project_id"] == "project-c"
    assert setup.status_code == 200
    assert setup.json()["project_id"] == "project-c"
    assert readiness.status_code == 200
    assert readiness.json()["connectors"]["github_direct"] is True
    assert readiness.json()["connectors"]["supabase_direct"] is True
    assert readiness.json()["connectors"]["browser_sensor"] is True
    assert github.status_code == 200
    assert supabase.status_code == 200
    assert browser.status_code == 200
    assert audit.status_code == 200
    assert other_project_audit.status_code == 200
    assert audit.json()["project_id"] == "project-c"
    assert other_project_audit.json()["project_id"] == "project-a"
    assert "project_provisioned" in audit_types
    assert "connector_evidence_ingested" in audit_types
    assert "browser_evidence_ingested" in audit_types
    assert github_secret not in audit_blob
    assert supabase_secret not in audit_blob
    assert server_key not in audit_blob
    assert browser_key not in audit_blob
    assert "project-c" not in os.getenv("INGEST_API_KEYS", "")
    assert "project-c" not in os.getenv("BROWSER_PUBLIC_KEYS", "")


def test_project_provisioning_requires_admin_key(
    monkeypatch: Any, tmp_path: Path
) -> None:
    client = make_client(monkeypatch, tmp_path)

    not_configured = client.post(
        "/api/v1/projects",
        headers=auth("admin-secret"),
        json={"project_id": "project-c"},
    )
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    missing = client.post("/api/v1/projects", json={"project_id": "project-c"})
    wrong = client.post(
        "/api/v1/projects",
        headers=auth("wrong-secret"),
        json={"project_id": "project-c"},
    )

    assert not_configured.status_code == 503
    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_admin_can_rotate_project_credentials(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "admin-secret")
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    client = make_client(monkeypatch, tmp_path)

    provisioned = client.post(
        "/api/v1/projects",
        headers=auth("admin-secret"),
        json={"project_id": "project-d", "name": "Project D"},
    )
    initial = provisioned.json()["credentials"]

    server_rotation = client.post(
        "/api/v1/projects/project-d/rotate",
        headers=auth("admin-secret"),
        json={"credential_type": "server_api_key"},
    )
    new_server_key = server_rotation.json()["credentials"]["server_api_key"]
    old_server_readiness = client.get(
        "/api/v1/readiness", headers=auth(initial["server_api_key"])
    )
    new_server_readiness = client.get("/api/v1/readiness", headers=auth(new_server_key))

    browser_rotation = client.post(
        "/api/v1/projects/project-d/rotate",
        headers=auth("admin-secret"),
        json={"credential_type": "browser_public_key"},
    )
    new_browser_key = browser_rotation.json()["credentials"]["browser_public_key"]
    old_browser = client.post(
        "/api/v1/browser/events",
        headers=auth(initial["browser_public_key"]),
        json={
            "project_id": "project-d",
            "event_type": "browser_error",
            "service": "web",
            "message": "old browser key should fail",
        },
    )
    new_browser = client.post(
        "/api/v1/browser/events",
        headers=auth(new_browser_key),
        json={
            "project_id": "project-d",
            "event_type": "browser_error",
            "service": "web",
            "message": "new browser key should work",
        },
    )

    github_rotation = client.post(
        "/api/v1/projects/project-d/rotate",
        headers=auth("admin-secret"),
        json={"credential_type": "github_webhook_secret"},
    )
    new_github_secret = github_rotation.json()["credentials"]["github_webhook_secret"]
    github_payload = {
        "repository": {"name": "web"},
        "after": "rotated-sha",
        "commits": [{"id": "rotated-sha", "message": "rotated secret deploy"}],
    }
    old_github_body, old_github_signature = signed_body(
        github_payload, initial["github_webhook_secret"]
    )
    new_github_body, new_github_signature = signed_body(
        github_payload, new_github_secret
    )
    old_github = client.post(
        "/api/v1/connectors/github/project-d/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "project-d-old-github",
            "X-Hub-Signature-256": old_github_signature,
            "Content-Type": "application/json",
        },
        content=old_github_body,
    )
    new_github = client.post(
        "/api/v1/connectors/github/project-d/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "project-d-new-github",
            "X-Hub-Signature-256": new_github_signature,
            "Content-Type": "application/json",
        },
        content=new_github_body,
    )

    supabase_rotation = client.post(
        "/api/v1/projects/project-d/rotate",
        headers=auth("admin-secret"),
        json={"credential_type": "supabase_webhook_secret"},
    )
    new_supabase_secret = supabase_rotation.json()["credentials"][
        "supabase_webhook_secret"
    ]
    supabase_payload = {"service": "web", "type": "database_event", "message": "ok"}
    old_supabase_body, old_supabase_signature = signed_body(
        supabase_payload, initial["supabase_webhook_secret"]
    )
    new_supabase_body, new_supabase_signature = signed_body(
        supabase_payload, new_supabase_secret
    )
    old_supabase = client.post(
        "/api/v1/connectors/supabase/project-d/webhook",
        headers={
            "X-Supabase-Delivery": "project-d-old-supabase",
            "X-Supabase-Signature": old_supabase_signature,
            "Content-Type": "application/json",
        },
        content=old_supabase_body,
    )
    new_supabase = client.post(
        "/api/v1/connectors/supabase/project-d/webhook",
        headers={
            "X-Supabase-Delivery": "project-d-new-supabase",
            "X-Supabase-Signature": new_supabase_signature,
            "Content-Type": "application/json",
        },
        content=new_supabase_body,
    )
    audit = client.get("/api/v1/audit", headers=auth(new_server_key))
    rotation_events = [
        event
        for event in audit.json()["events"]
        if event["event_type"] == "credential_rotated"
    ]

    assert provisioned.status_code == 200
    assert server_rotation.status_code == 200
    assert old_server_readiness.status_code == 401
    assert new_server_readiness.status_code == 200
    assert browser_rotation.status_code == 200
    assert old_browser.status_code == 401
    assert new_browser.status_code == 200
    assert github_rotation.status_code == 200
    assert old_github.status_code == 401
    assert new_github.status_code == 200
    assert supabase_rotation.status_code == 200
    assert old_supabase.status_code == 401
    assert new_supabase.status_code == 200
    assert len(rotation_events) == 4
    assert all(event["details"]["revoked_previous"] is True for event in rotation_events)


def test_legacy_demo_routes_are_disabled_in_production(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_MODE", "false")
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/incidents/trigger",
        json={"service": "api", "alert_description": "demo should be closed"},
    )

    assert response.status_code == 404


def test_legacy_demo_routes_work_in_development_default(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("DEMO_MODE", raising=False)
    client = make_client(monkeypatch, tmp_path)

    response = client.get("/api/incidents")

    assert response.status_code == 200


def test_browser_key_is_write_only(monkeypatch: Any, tmp_path: Path) -> None:
    client = make_client(monkeypatch, tmp_path)

    event = client.post(
        "/api/v1/browser/events",
        headers=auth("browser-a"),
        json={
            "project_id": "project-a",
            "event_type": "browser_error",
            "service": "web",
            "message": "token=secret-value frontend exploded",
            "page_url": "https://example.com/pay?token=secret-value",
        },
    )

    assert event.status_code == 200
    assert event.json()["status"] == "accepted"
    assert client.get("/api/v1/incidents/nope", headers=auth("browser-a")).status_code == 401


def test_browser_origin_allowlist_blocks_untrusted_sites(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("BROWSER_ALLOWED_ORIGINS", "https://trusted.example")
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/browser/events",
        headers={**auth("browser-a"), "Origin": "https://evil.example"},
        json={
            "project_id": "project-a",
            "event_type": "browser_error",
            "service": "web",
            "message": "blocked origin",
        },
    )

    assert response.status_code == 403


def test_browser_origin_allowlist_accepts_trusted_site(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("BROWSER_ALLOWED_ORIGINS", "https://trusted.example")
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/v1/browser/events",
        headers={**auth("browser-a"), "Origin": "https://trusted.example"},
        json={
            "project_id": "project-a",
            "event_type": "browser_error",
            "service": "web",
            "message": "trusted origin",
        },
    )

    assert response.status_code == 200


def test_browser_error_burst_auto_creates_incident(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("BROWSER_ERROR_TRIGGER_COUNT", "2")
    client = make_client(monkeypatch, tmp_path)

    for i in range(2):
        response = client.post(
            "/api/v1/browser/events",
            headers=auth("browser-a"),
            json={
                "project_id": "project-a",
                "event_type": "api_failure",
                "service": "web",
                "message": f"checkout api failed {i}",
                "api_url": "https://api.example.com/checkout?password=secret",
                "status_code": 503,
            },
        )

    assert response.status_code == 200
    assert response.json()["incident"]["status"] == "queued"


def test_github_and_browser_events_create_evidence_edges(
    monkeypatch: Any, tmp_path: Path
) -> None:
    client = make_client(monkeypatch, tmp_path)

    github = client.post(
        "/api/v1/connectors/github/webhook",
        headers={**auth("key-a"), "X-GitHub-Event": "push"},
        json={
            "repository": {"name": "web"},
            "after": "abc123",
            "commits": [{"id": "abc123", "message": "change checkout flow"}],
        },
    )
    browser = client.post(
        "/api/v1/browser/events",
        headers=auth("browser-a"),
        json={
            "project_id": "project-a",
            "event_type": "browser_error",
            "service": "web",
            "release_sha": "abc123",
            "message": "checkout flow failed",
        },
    )

    assert github.status_code == 200
    assert browser.status_code == 200


def test_github_webhook_requires_valid_signature_when_enabled(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }

    missing = client.post(
        "/api/v1/connectors/github/webhook",
        headers={**auth("key-a"), "X-GitHub-Event": "push"},
        json=payload,
    )
    body, signature = signed_body(payload, "github-secret")
    accepted = client.post(
        "/api/v1/connectors/github/webhook",
        headers={
            **auth("key-a"),
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert missing.status_code == 401
    assert accepted.status_code == 200


def test_direct_github_webhook_accepts_valid_signature_without_bearer(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRETS", "project-a:github-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }
    body, signature = signed_body(payload, "github-secret")

    response = client.post(
        "/api/v1/connectors/github/project-a/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "direct-delivery-1",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == "project-a"
    assert response.json()["mode"] == "direct"


def test_direct_github_webhook_rejects_missing_or_invalid_signature(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRETS", "project-a:github-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }
    body, _ = signed_body(payload, "github-secret")

    missing = client.post(
        "/api/v1/connectors/github/project-a/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "missing-signature-delivery",
            "Content-Type": "application/json",
        },
        content=body,
    )
    invalid = client.post(
        "/api/v1/connectors/github/project-a/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "invalid-signature-delivery",
            "X-Hub-Signature-256": "sha256=bad",
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_direct_github_webhook_requires_existing_project_and_delivery_id(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRETS", "project-a:github-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }
    body, signature = signed_body(payload, "github-secret")

    missing_delivery = client.post(
        "/api/v1/connectors/github/project-a/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )
    unknown_project = client.post(
        "/api/v1/connectors/github/missing-project/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "unknown-project-delivery",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert missing_delivery.status_code == 400
    assert unknown_project.status_code == 404


def test_duplicate_webhook_delivery_is_rejected(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }
    body, signature = signed_body(payload, "github-secret")
    headers = {
        **auth("key-a"),
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }

    first = client.post("/api/v1/connectors/github/webhook", headers=headers, content=body)
    duplicate = client.post("/api/v1/connectors/github/webhook", headers=headers, content=body)

    assert first.status_code == 200
    assert duplicate.status_code == 409


def test_direct_github_duplicate_delivery_is_rejected(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRETS", "project-a:github-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }
    body, signature = signed_body(payload, "github-secret")
    headers = {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": "direct-delivery-dup",
        "X-Hub-Signature-256": signature,
        "Content-Type": "application/json",
    }

    first = client.post("/api/v1/connectors/github/project-a/webhook", headers=headers, content=body)
    duplicate = client.post("/api/v1/connectors/github/project-a/webhook", headers=headers, content=body)

    assert first.status_code == 200
    assert duplicate.status_code == 409


def test_webhook_delivery_replay_is_scoped_per_project(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv(
        "GITHUB_WEBHOOK_SECRETS", "project-a:github-secret-a,project-b:github-secret-b"
    )
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "repository": {"name": "web"},
        "after": "abc123",
        "commits": [{"id": "abc123", "message": "change checkout flow"}],
    }
    body_a, signature_a = signed_body(payload, "github-secret-a")
    body_b, signature_b = signed_body(payload, "github-secret-b")

    project_a = client.post(
        "/api/v1/connectors/github/project-a/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "shared-delivery-id",
            "X-Hub-Signature-256": signature_a,
            "Content-Type": "application/json",
        },
        content=body_a,
    )
    project_b = client.post(
        "/api/v1/connectors/github/project-b/webhook",
        headers={
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "shared-delivery-id",
            "X-Hub-Signature-256": signature_b,
            "Content-Type": "application/json",
        },
        content=body_b,
    )

    assert project_a.status_code == 200
    assert project_b.status_code == 200


def test_direct_supabase_webhook_accepts_valid_signature_without_bearer(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_WEBHOOK_SECRETS", "project-a:supabase-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "service": "db",
        "type": "database_event",
        "table": "orders",
        "record": {"id": 42, "status": "failed"},
    }
    body, signature = signed_body(payload, "supabase-secret")

    response = client.post(
        "/api/v1/connectors/supabase/project-a/webhook",
        headers={
            "X-Supabase-Delivery": "supabase-delivery-1",
            "X-Supabase-Signature": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 200
    assert response.json()["project_id"] == "project-a"
    assert response.json()["mode"] == "direct"


def test_direct_supabase_webhook_rejects_invalid_signature_and_duplicate(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_WEBHOOK_SECRETS", "project-a:supabase-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {
        "service": "db",
        "type": "auth_event",
        "message": "auth failures rising",
    }
    body, signature = signed_body(payload, "supabase-secret")

    invalid = client.post(
        "/api/v1/connectors/supabase/project-a/webhook",
        headers={
            "X-Supabase-Delivery": "supabase-delivery-dup",
            "X-Supabase-Signature": "sha256=bad",
            "Content-Type": "application/json",
        },
        content=body,
    )
    first = client.post(
        "/api/v1/connectors/supabase/project-a/webhook",
        headers={
            "X-Supabase-Delivery": "supabase-delivery-dup",
            "X-Supabase-Signature": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )
    duplicate = client.post(
        "/api/v1/connectors/supabase/project-a/webhook",
        headers={
            "X-Supabase-Delivery": "supabase-delivery-dup",
            "X-Supabase-Signature": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert invalid.status_code == 401
    assert first.status_code == 200
    assert duplicate.status_code == 409


def test_direct_supabase_webhook_requires_existing_project_and_delivery_id(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("CONNECTOR_SIGNATURES_REQUIRED", "true")
    monkeypatch.setenv("SUPABASE_WEBHOOK_SECRETS", "project-a:supabase-secret")
    client = make_client(monkeypatch, tmp_path)
    payload = {"service": "db", "type": "auth_event", "message": "auth failures rising"}
    body, signature = signed_body(payload, "supabase-secret")

    missing_delivery = client.post(
        "/api/v1/connectors/supabase/project-a/webhook",
        headers={
            "X-Supabase-Signature": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )
    unknown_project = client.post(
        "/api/v1/connectors/supabase/missing-project/webhook",
        headers={
            "X-Supabase-Delivery": "unknown-project-delivery",
            "X-Supabase-Signature": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert missing_delivery.status_code == 400
    assert unknown_project.status_code == 404


def test_slack_command_requires_valid_signature(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    monkeypatch.setenv("SLACK_PROJECT_ID", "project-a")
    client = make_client(monkeypatch, tmp_path)
    body = "text=status"
    timestamp = str(int(time.time()))

    missing_signature = client.post(
        "/api/slack/commands",
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=body,
    )
    invalid_signature = client.post(
        "/api/slack/commands",
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": "v0=bad",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=body,
    )

    assert missing_signature.status_code == 401
    assert invalid_signature.status_code == 401


def test_slack_command_accepts_valid_signature_and_project_scope(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    monkeypatch.setenv("SLACK_PROJECT_ID", "project-a")
    client = make_client(monkeypatch, tmp_path)
    body = "text=status"
    timestamp = str(int(time.time()))
    signature = slack_signature(body, "slack-secret", timestamp)

    response = client.post(
        "/api/slack/commands",
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": signature,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        content=body,
    )

    assert response.status_code == 200
    assert "No incidents found" in response.json()["text"]
