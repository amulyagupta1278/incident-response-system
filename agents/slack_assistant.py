from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

import httpx
from fastapi import Header, HTTPException, Request


SLACK_SIGNATURE_VERSION = "v0"
SLACK_REPLAY_WINDOW_SECONDS = 60 * 5


class SlackAssistant:
    """Small Slack adapter for the incident-response workflow.

    The adapter intentionally uses Slack's HTTP primitives directly instead of a
    framework dependency so the demo remains easy to run offline. If no Slack bot
    token is configured, it returns payloads for local tests but skips Web API
    calls.
    """

    def __init__(self) -> None:
        self.signing_secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
        self.bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        self.default_channel = os.getenv("SLACK_DEFAULT_CHANNEL", "").strip()
        self.public_base_url = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
        self.enabled = bool(self.signing_secret and self.bot_token)

    def require_configured_for_outbound(self) -> bool:
        return bool(self.bot_token)

    async def verify_request(
        self,
        body: bytes,
        x_slack_signature: str = Header(default=""),
        x_slack_request_timestamp: str = Header(default=""),
    ) -> None:
        if not self.signing_secret:
            if os.getenv("SLACK_ALLOW_UNSIGNED_LOCAL", "").lower() in {"1", "true", "yes"}:
                return
            raise HTTPException(status_code=503, detail="SLACK_SIGNING_SECRET is not configured")
        try:
            timestamp = int(x_slack_request_timestamp)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail="invalid Slack timestamp") from exc
        if abs(time.time() - timestamp) > SLACK_REPLAY_WINDOW_SECONDS:
            raise HTTPException(status_code=401, detail="stale Slack request")
        basestring = b":".join(
            [
                SLACK_SIGNATURE_VERSION.encode("utf-8"),
                str(timestamp).encode("utf-8"),
                body,
            ]
        )
        digest = hmac.new(
            self.signing_secret.encode("utf-8"),
            basestring,
            hashlib.sha256,
        ).hexdigest()
        expected = f"{SLACK_SIGNATURE_VERSION}={digest}"
        if not hmac.compare_digest(expected, x_slack_signature):
            raise HTTPException(status_code=401, detail="invalid Slack signature")

    async def read_payload(self, request: Request) -> Tuple[bytes, Dict[str, Any]]:
        body = await request.body()
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            return body, json.loads(body.decode("utf-8") or "{}")
        form = parse_qs(body.decode("utf-8"))
        if "payload" in form:
            return body, json.loads(form["payload"][0])
        return body, {key: values[0] for key, values in form.items()}

    async def post_message(
        self,
        *,
        channel: str,
        text: str,
        blocks: Optional[List[Dict[str, Any]]] = None,
        thread_ts: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.bot_token:
            return {"ok": False, "skipped": "SLACK_BOT_TOKEN is not configured", "text": text}
        payload: Dict[str, Any] = {"channel": channel, "text": text}
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("ok"):
                raise HTTPException(status_code=502, detail=f"Slack post failed: {result}")
            return result

    def incident_url(self, incident_id: str) -> str:
        return f"{self.public_base_url}/incident/{incident_id}"

    def postmortem_url(self, incident_id: str) -> str:
        return f"{self.public_base_url}/api/incidents/{incident_id}/postmortem"

    def home_text(self) -> str:
        return (
            "*AIOC Slack Assistant*\n"
            "Use `/aioc trigger db|memory|timeout`, `/aioc status [incident_id]`, "
            "`/aioc ask <incident_id> <question>`, or `/aioc help`."
        )


def scenario_from_text(text: str) -> Dict[str, str]:
    normalized = text.strip().lower()
    if normalized in {"", "db", "database", "db-pool", "pool", "scenario1", "scenario-1"}:
        return {
            "timestamp": datetime.now().isoformat(),
            "service": "payment-api",
            "severity": "critical",
            "alert_description": "Database connection pool exhaustion detected",
        }
    if normalized in {"memory", "mem", "leak", "scenario2", "scenario-2"}:
        return {
            "timestamp": datetime.now().isoformat(),
            "service": "order-processor",
            "severity": "critical",
            "alert_description": "Memory leak detected - GC pause times increasing",
        }
    if normalized in {"timeout", "cascade", "cascading", "scenario3", "scenario-3"}:
        return {
            "timestamp": datetime.now().isoformat(),
            "service": "checkout-gateway",
            "severity": "critical",
            "alert_description": "Cascading failure - downstream service timeout",
        }
    return {
        "timestamp": datetime.now().isoformat(),
        "service": "unknown",
        "severity": "critical",
        "alert_description": text.strip() or "Slack-triggered incident",
    }


def summarize_incident(record: Dict[str, Any]) -> str:
    root_cause = record.get("root_cause") or {}
    confidence = root_cause.get("confidence", record.get("rca_confidence", 0.0)) or 0.0
    return (
        f"*{record.get('service', 'unknown')}* `{record.get('incident_id', '')[:8]}`\n"
        f"Status: `{record.get('current_status')}` / lifecycle `{record.get('lifecycle_status')}`\n"
        f"Alert: {record.get('alert_description', 'N/A')}\n"
        f"RCA: {root_cause.get('hypothesis', 'Pending analysis')} ({confidence * 100:.0f}% confidence)\n"
        f"Impact: {record.get('affected_users', 0):,} users, "
        f"${record.get('estimated_revenue_impact_per_minute', 0):.2f}/min"
    )


def incident_blocks(record: Dict[str, Any], assistant: SlackAssistant) -> List[Dict[str, Any]]:
    incident_id = str(record.get("incident_id", ""))
    recommendations = record.get("recovery_recommendations", [])[:3]
    fields = [
        {"type": "mrkdwn", "text": f"*Service*\n{record.get('service', 'unknown')}"},
        {"type": "mrkdwn", "text": f"*Severity*\n{record.get('severity', 'unknown')}"},
        {"type": "mrkdwn", "text": f"*Status*\n{record.get('current_status', 'unknown')}"},
        {"type": "mrkdwn", "text": f"*Lifecycle*\n{record.get('lifecycle_status', 'unknown')}"},
    ]
    blocks: List[Dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": summarize_incident(record)}},
        {"type": "section", "fields": fields},
    ]
    if recommendations:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Recommended recovery actions*\n"
                    + "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(recommendations)),
                },
            }
        )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open Command Center"},
                    "url": assistant.incident_url(incident_id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Accept RCA"},
                    "style": "primary",
                    "value": incident_id,
                    "action_id": "aioc_accept_rca",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Request More Data"},
                    "value": incident_id,
                    "action_id": "aioc_request_more_data",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Postmortem"},
                    "url": assistant.postmortem_url(incident_id),
                },
            ],
        }
    )
    return blocks


def parse_command(text: str) -> Tuple[str, str]:
    stripped = text.strip()
    if not stripped:
        return "help", ""
    command, _, rest = stripped.partition(" ")
    return command.lower(), rest.strip()
