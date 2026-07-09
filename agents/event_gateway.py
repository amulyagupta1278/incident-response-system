import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Tuple


ALLOWED_EVENT_TYPES: set[str] = {
    "alert",
    "log",
    "metric",
    "deployment",
    "github_push",
    "github_pr",
    "supabase_event",
    "app_error",
    "business_metric",
}

SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,}]+"),
    re.compile(r"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9_\-\.]+"),
]


def normalize_event(body: Dict[str, Any], project_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    event_type: str = str(body.get("event_type", "")).strip()
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"event_type must be one of {sorted(ALLOWED_EVENT_TYPES)}")

    service: str = str(body.get("service", "")).strip() or "unknown-service"
    payload: Dict[str, Any] = _as_dict(body.get("payload", {}))
    redacted_payload: Dict[str, Any] = redact(payload)
    event: Dict[str, Any] = {
        "event_id": str(body.get("event_id") or uuid.uuid4()),
        "project_id": project_id,
        "source": str(body.get("source") or "custom"),
        "event_type": event_type,
        "service": service,
        "environment": str(body.get("environment") or "production"),
        "timestamp": str(body.get("timestamp") or datetime.now().isoformat()),
        "payload": redacted_payload,
    }
    if _raw_retention_enabled():
        event["raw_payload"] = redacted_payload
    return event, _chunks_for_event(event)


def github_event_to_body(payload: Dict[str, Any], event_name: str) -> Dict[str, Any]:
    repo: Dict[str, Any] = _as_dict(payload.get("repository", {}))
    commits: list[Any] = payload.get("commits") if isinstance(payload.get("commits"), list) else []
    return {
        "event_type": "github_pr" if event_name == "pull_request" else "github_push",
        "source": "github",
        "service": repo.get("name") or repo.get("full_name") or "github-repo",
        "environment": "production",
        "timestamp": datetime.now().isoformat(),
        "payload": {
            "repository": repo.get("full_name") or repo.get("name"),
            "ref": payload.get("ref"),
            "before": payload.get("before"),
            "after": payload.get("after"),
            "commits": [
                {
                    "id": commit.get("id"),
                    "message": commit.get("message"),
                    "timestamp": commit.get("timestamp"),
                    "url": commit.get("url"),
                }
                for commit in commits[:10]
                if isinstance(commit, dict)
            ],
            "action": payload.get("action"),
            "pull_request": _as_dict(payload.get("pull_request", {})).get("html_url"),
            "sender": _as_dict(payload.get("sender", {})).get("login"),
        },
    }


def supabase_event_to_body(payload: Dict[str, Any]) -> Dict[str, Any]:
    record: Dict[str, Any] = _as_dict(payload.get("record", payload))
    return {
        "event_type": "supabase_event",
        "source": "supabase",
        "service": str(payload.get("service") or payload.get("table") or "supabase"),
        "environment": str(payload.get("environment") or "production"),
        "timestamp": str(payload.get("timestamp") or datetime.now().isoformat()),
        "payload": {
            "type": payload.get("type") or payload.get("eventType") or "database_event",
            "table": payload.get("table"),
            "schema": payload.get("schema"),
            "record": record,
            "message": payload.get("message") or json.dumps(record, default=str)[:1000],
        },
    }


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text: str = value
        for pattern in SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text
    return value


def _chunks_for_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload_text: str = json.dumps(event["payload"], default=str, ensure_ascii=False)[:4000]
    label: str = f"{event['source']} {event['event_type']} for {event['service']}"
    chunks: List[Dict[str, Any]] = [
        {
            "source_type": event["event_type"],
            "label": label,
            "text": payload_text,
        }
    ]
    payload: Dict[str, Any] = event["payload"]
    if event["event_type"] in {"log", "app_error", "supabase_event"}:
        message: str = str(payload.get("message") or payload.get("msg") or payload_text)
        chunks.append(
            {
                "source_type": "runtime_evidence",
                "label": f"Runtime evidence: {event['service']}",
                "text": message[:4000],
            }
        )
    if event["event_type"] in {"github_push", "github_pr", "deployment"}:
        chunks.append(
            {
                "source_type": "change_evidence",
                "label": f"Change evidence: {event['service']}",
                "text": payload_text,
            }
        )
    return chunks


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _raw_retention_enabled() -> bool:
    try:
        return int(os.getenv("RAW_PAYLOAD_RETENTION_DAYS", "0")) > 0
    except ValueError:
        return False
