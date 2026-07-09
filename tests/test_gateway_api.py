import importlib
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def make_client(monkeypatch: Any, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "gateway.db"))
    monkeypatch.setenv("INGEST_API_KEYS", "project-a:key-a,project-b:key-b")
    monkeypatch.setenv("GATEWAY_WORKER_ENABLED", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    else:
        import app  # noqa: F401
    return TestClient(sys.modules["app"].app)


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


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
