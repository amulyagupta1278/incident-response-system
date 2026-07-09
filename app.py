import asyncio
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

load_dotenv()

from agents import IncidentState
from agents.agentic_system import get_compiled_graph
from agents.llm import get_model, get_provider, get_timeout_seconds, llm_strict_mode
from agents.memory import record_incident
from agents.notify import post_war_room, war_room_configured
from agents.qa import answer_question

app: FastAPI = FastAPI(title="AI Operations Command Center")


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


@app.post("/api/incidents/trigger")
async def trigger_incident(incident_data: Dict[str, Any]) -> Dict[str, Any]:
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
    return [incident_store[iid] for iid in incident_order if iid in incident_store]


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str) -> Dict[str, Any]:
    if incident_id not in incident_store:
        raise HTTPException(status_code=404, detail="Incident not found")

    return incident_store[incident_id]


@app.post("/api/incidents/{incident_id}/ask")
async def ask_incident(incident_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Natural-language Q&A grounded in one incident's investigation data."""
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


@app.get("/incident/{incident_id}")
async def serve_incident_detail(incident_id: str) -> FileResponse:
    return FileResponse("frontend/incident_detail.html", media_type="text/html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
