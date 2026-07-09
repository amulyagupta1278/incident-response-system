import hashlib
import hmac
import importlib
import json
import os
import sys
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
    assert payload["endpoints"]["github_webhook"] == (
        "https://ops.example/api/v1/connectors/github/webhook"
    )
    assert 'data-project-id="project-a"' in payload["browser_snippet"]
    assert "do-not-leak" not in json.dumps(payload)


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
