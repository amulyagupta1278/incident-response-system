import hashlib
import hmac
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parent.parent


def _set_demo_env(db_path: str) -> None:
    os.environ["DATABASE_PATH"] = db_path
    os.environ["PUBLIC_BASE_URL"] = "https://ops-demo.example"
    os.environ["APP_ENV"] = "production"
    os.environ["GATEWAY_WORKER_ENABLED"] = "false"
    os.environ["CONNECTOR_SIGNATURES_REQUIRED"] = "true"
    os.environ["DEMO_MODE"] = "false"
    os.environ["GITHUB_WEBHOOK_SECRETS"] = "hackathon-project:demo-github-secret"
    os.environ["GITHUB_WEBHOOK_SECRET"] = "demo-github-secret"
    os.environ["SUPABASE_WEBHOOK_SECRET"] = "demo-supabase-secret"
    os.environ["INGEST_API_KEYS"] = "hackathon-project:hackathon-server-key"
    os.environ["BROWSER_PUBLIC_KEYS"] = "hackathon-project:hackathon-browser-key"
    os.environ["BROWSER_ALLOWED_ORIGINS"] = "https://shop.example"
    os.environ["ALLOWED_ORIGINS"] = "https://judges.example"
    os.environ["BROWSER_ERROR_TRIGGER_COUNT"] = "2"
    os.environ["RAW_PAYLOAD_RETENTION_DAYS"] = "0"
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["LLM_STRICT_MODE"] = "false"


def _client() -> TestClient:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    else:
        import app  # noqa: F401
    return TestClient(sys.modules["app"].app)


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _signed_body(payload: dict[str, Any], secret: str) -> tuple[str, str]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body.decode(), signature


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _set_demo_env(str(Path(tmp) / "gateway.db"))
        client = _client()

        setup = client.get(
            "/api/v1/connectors/setup",
            headers=_auth("hackathon-server-key"),
        )
        _expect(setup.status_code == 200, "connector setup should require valid server key")
        _expect(
            setup.json()["project_id"] == "hackathon-project",
            "connector setup should be project scoped",
        )

        service_config = client.post(
            "/api/v1/service-config",
            headers=_auth("hackathon-server-key"),
            json={
                "service": "web",
                "total_users": 25000,
                "revenue_per_user_per_minute": 0.08,
                "impact_metric": "error_rate",
            },
        )
        _expect(service_config.status_code == 200, "service config should save")

        github_payload = {
            "repository": {"name": "web"},
            "after": "demo-sha-123",
            "commits": [
                {
                    "id": "demo-sha-123",
                    "message": "checkout route deploy",
                    "modified": ["app/checkout/page.tsx", "lib/payments.ts"],
                }
            ],
        }
        body, signature = _signed_body(github_payload, "demo-github-secret")
        github_headers = {
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-demo-1",
            "X-Hub-Signature-256": signature,
        }
        github = client.post(
            "/api/v1/connectors/github/hackathon-project/webhook",
            headers=github_headers,
            content=body,
        )
        replay = client.post(
            "/api/v1/connectors/github/hackathon-project/webhook",
            headers=github_headers,
            content=body,
        )
        _expect(github.status_code == 200, "signed GitHub webhook should ingest")
        _expect(replay.status_code == 409, "duplicate GitHub delivery should be rejected")

        evil_browser = client.post(
            "/api/v1/browser/events",
            headers={**_auth("hackathon-browser-key"), "Origin": "https://evil.example"},
            json={
                "project_id": "hackathon-project",
                "event_type": "browser_error",
                "service": "web",
                "message": "blocked origin",
            },
        )
        _expect(evil_browser.status_code == 403, "untrusted browser origin should be blocked")

        incident = None
        for index in range(2):
            browser = client.post(
                "/api/v1/browser/events",
                headers={**_auth("hackathon-browser-key"), "Origin": "https://shop.example"},
                json={
                    "project_id": "hackathon-project",
                    "event_type": "api_failure",
                    "service": "web",
                    "environment": "production",
                    "release_sha": "demo-sha-123",
                    "message": f"checkout failed after deploy #{index}",
                    "api_url": "https://api.shop.example/checkout?token=secret-value",
                    "status_code": 503,
                    "route": "/checkout",
                },
            )
            _expect(browser.status_code == 200, "trusted browser event should ingest")
            incident = browser.json().get("incident") or incident

        _expect(incident is not None, "browser failure burst should auto-create incident")
        incident_id = incident["incident_id"]
        fetched = client.get(
            f"/api/v1/incidents/{incident_id}",
            headers=_auth("hackathon-server-key"),
        )
        _expect(fetched.status_code == 200, "project server key should read incident")
        record = fetched.json()
        _expect(
            len(record.get("external_evidence_chunks", [])) >= 3,
            "incident should include external evidence chunks",
        )
        _expect(
            any(
                edge.get("edge_type") in {"same_release", "deployment_precedes_error"}
                for edge in record.get("evidence_edges", [])
            ),
            "incident should include deploy-to-error evidence graph edge",
        )
        ask = client.post(
            f"/api/v1/incidents/{incident_id}/ask",
            headers=_auth("hackathon-server-key"),
            json={"question": "Should we rollback this deployment?"},
        )
        _expect(ask.status_code == 200, "Ask endpoint should answer incident question")
        _expect(ask.json().get("citations"), "Ask answer should include citations")

        print("Hackathon acceptance smoke passed")
        print(f"project={record['project_id']} incident={incident_id}")
        print(f"evidence_chunks={len(record.get('external_evidence_chunks', []))}")
        print(f"evidence_edges={len(record.get('evidence_edges', []))}")
        print(f"ask_citations={len(ask.json().get('citations', []))}")
        print("security=server-key browser-write-only hmac replay-block origin-allowlist")


if __name__ == "__main__":
    main()
