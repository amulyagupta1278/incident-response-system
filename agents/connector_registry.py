from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Protocol

import httpx
from mock_data import load_deployments, load_logs, load_metrics, load_service_config


CONNECTORS_PATH = Path("data/connectors.json")
SECRET_KEYS = {"api_key", "token", "secret", "password", "access_key", "client_secret", "webhook_url"}

CONNECTOR_CATALOG = [
    {"type": "mcp", "name": "MCP Server", "category": "tools", "fields": ["endpoint", "auth_env"]},
    {"type": "aws_s3", "name": "AWS S3", "category": "storage", "fields": ["bucket", "region", "credential_env"]},
    {"type": "gcp_bucket", "name": "Google Cloud Storage", "category": "storage", "fields": ["bucket", "project", "credential_env"]},
    {"type": "azure_blob", "name": "Azure Blob", "category": "storage", "fields": ["container", "account", "credential_env"]},
    {"type": "log_stream", "name": "Live Log Stream", "category": "telemetry", "fields": ["endpoint", "stream_format", "auth_env"]},
    {"type": "file_upload", "name": "Managed File Upload", "category": "knowledge", "fields": ["path"]},
    {"type": "slack", "name": "Slack", "category": "collaboration", "fields": ["channel", "token_env"]},
    {"type": "google_drive", "name": "Google Drive", "category": "knowledge", "fields": ["folder_id", "credential_env"]},
    {"type": "obsidian_vault", "name": "Obsidian Vault", "category": "knowledge", "fields": ["path", "sync_mode"]},
    {"type": "memgraph", "name": "Memgraph", "category": "memory", "fields": ["uri", "username", "password_env"]},
    {"type": "qdrant", "name": "Qdrant Vector Store", "category": "knowledge", "fields": ["endpoint", "api_key_env", "collection"]},
    {"type": "sarvam", "name": "Sarvam AI Voice", "category": "voice", "fields": ["api_key_env", "voice", "languages"]},
    {"type": "elevenlabs", "name": "ElevenLabs Voice", "category": "voice", "fields": ["api_key_env", "voice_id", "languages"]},
    {"type": "teams", "name": "Microsoft Teams", "category": "collaboration", "fields": ["webhook_url_env", "project_owner", "admin", "alert_threshold_per_minute"]},
    {"type": "ollama", "name": "Ollama (Local)", "category": "ai", "fields": ["endpoint", "model"]},
    {"type": "llm_openai", "name": "OpenAI", "category": "ai", "fields": ["api_key_env", "model", "base_url", "active"]},
    {"type": "llm_gemini", "name": "Google Gemini", "category": "ai", "fields": ["api_key_env", "model", "base_url", "active"]},
    {"type": "llm_groq", "name": "Groq", "category": "ai", "fields": ["api_key_env", "model", "base_url", "active"]},
    {"type": "llm_claude", "name": "Anthropic Claude", "category": "ai", "fields": ["api_key_env", "model", "base_url", "active"]},
]


def _load() -> List[Dict[str, Any]]:
    try:
        value = json.loads(CONNECTORS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    CONNECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONNECTORS_PATH.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _safe_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: ("********" if key.lower() in SECRET_KEYS and value else value)
        for key, value in config.items()
    }


def list_connectors() -> List[Dict[str, Any]]:
    return [{**item, "config": _safe_config(item.get("config") or {})} for item in _load()]


def runtime_connectors(*connector_types: str) -> List[Dict[str, Any]]:
    """Return enabled connector records to trusted backend adapters.

    Unlike ``list_connectors`` this intentionally returns raw configuration,
    so it must never be serialized by an API handler. Credentials should still
    normally be stored as environment-variable references (``api_key_env``).
    """
    supported = set(connector_types)
    items = [item for item in _load() if item.get("enabled", True)]
    if supported:
        items = [item for item in items if item.get("type") in supported]
    return items


def upsert_connector(payload: Dict[str, Any]) -> Dict[str, Any]:
    connector_type = str(payload.get("type", "")).strip()
    supported = {item["type"] for item in CONNECTOR_CATALOG}
    if connector_type not in supported:
        raise ValueError(f"Unsupported connector type: {connector_type}")
    items = _load()
    connector_id = str(payload.get("id") or uuid.uuid4())
    existing = next((item for item in items if item.get("id") == connector_id), None)
    config = dict(payload.get("config") or {})
    if existing:
        old_config = existing.get("config") or {}
        config = {key: old_config.get(key) if value == "********" else value for key, value in config.items()}
    record = {
        "id": connector_id,
        "name": str(payload.get("name") or connector_type.replace("_", " ").title()),
        "type": connector_type,
        "enabled": bool(payload.get("enabled", True)),
        "config": config,
        "status": (existing or {}).get("status", "pending"),
        "last_heartbeat": (existing or {}).get("last_heartbeat"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    items = [record if item.get("id") == connector_id else item for item in items]
    if not existing:
        items.append(record)
    _save(items)
    return {**record, "config": _safe_config(config)}


def delete_connector(connector_id: str) -> bool:
    items = _load()
    retained = [item for item in items if item.get("id") != connector_id]
    _save(retained)
    return len(retained) != len(items)


async def heartbeat(connector_id: str) -> Dict[str, Any]:
    items = _load()
    record = next((item for item in items if item.get("id") == connector_id), None)
    if not record:
        raise KeyError(connector_id)
    config = record.get("config") or {}
    endpoint = str(config.get("endpoint") or "").strip()
    status, detail = "configured", "Configuration is present; authenticated probe is deferred to the runtime adapter."
    if not record.get("enabled"):
        status, detail = "disabled", "Connector is disabled."
    elif record.get("type") == "obsidian_vault":
        vault_path = str(config.get("path") or "").strip()
        if vault_path:
            path = Path(vault_path).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            if path.exists() and path.is_dir():
                status, detail = "online", f"Obsidian vault found at {path}."
            else:
                status, detail = "error", f"Obsidian path not found: {path}."
        else:
            status, detail = "error", "Obsidian vault path is not configured."
    elif record.get("type") == "teams":
        webhook_env = str(config.get("webhook_url_env") or "TEAMS_ALERT_WEBHOOK_URL").strip()
        webhook_url = os.getenv(webhook_env, "").strip() or str(config.get("webhook_url") or "").strip()
        if not webhook_url:
            status, detail = "error", f"Teams webhook URL not found in {webhook_env}."
        else:
            try:
                async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                    response = await client.post(
                        webhook_url,
                        json={
                            "text": (
                                "AIOC Teams connector handshake succeeded. "
                                f"Project owner: {config.get('project_owner') or 'not set'}; "
                                f"admin: {config.get('admin') or 'not set'}."
                            )
                        },
                    )
                status = "online" if response.status_code < 500 else "error"
                detail = f"Teams webhook handshake returned HTTP {response.status_code}."
            except Exception as exc:
                status, detail = "error", str(exc)[:180]
    elif record.get("type") == "memgraph":
        uri = str(config.get("uri") or os.getenv("MEMGRAPH_URI") or "bolt://127.0.0.1:7687").strip()
        try:
            from neo4j import GraphDatabase

            password_env = str(config.get("password_env") or "").strip()
            username = str(config.get("username") or os.getenv("MEMGRAPH_USERNAME") or "").strip()
            password = os.getenv(password_env, "") if password_env else os.getenv("MEMGRAPH_PASSWORD", "")
            auth = (username, password) if username else None
            driver = GraphDatabase.driver(uri, auth=auth)
            with driver:
                driver.verify_connectivity()
            status, detail = "online", f"Connected to Memgraph at {uri}."
        except Exception as exc:
            status, detail = "error", str(exc)[:180]
    elif record.get("type") == "qdrant":
        endpoint = str(config.get("endpoint") or os.getenv("QDRANT_URL", "")).strip()
        api_key_env = str(config.get("api_key_env") or "").strip()
        api_key = os.getenv(api_key_env, "").strip() if api_key_env else str(config.get("api_key") or "").strip()
        if not endpoint:
            status, detail = "error", "Qdrant endpoint is not configured."
        else:
            try:
                from qdrant_client import QdrantClient

                client = QdrantClient(url=endpoint, api_key=api_key or None, prefer_grpc=False)
                client.get_collections()
                status, detail = "online", f"Connected to Qdrant at {endpoint}."
            except ImportError:
                status, detail = "error", "qdrant-client is not installed."
            except Exception as exc:
                status, detail = "error", str(exc)[:180]
    elif endpoint:
        try:
            async with httpx.AsyncClient(timeout=4, follow_redirects=True) as client:
                response = await client.get(endpoint)
            status = "online" if response.status_code < 500 else "error"
            detail = f"Endpoint returned HTTP {response.status_code}."
        except Exception as exc:
            status, detail = "error", str(exc)[:180]
    elif not any(str(value).strip() for value in config.values()):
        status, detail = "error", "Connector configuration is incomplete."
    record["status"] = status
    record["heartbeat_detail"] = detail
    record["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
    _save(items)
    return {**record, "config": _safe_config(config)}

