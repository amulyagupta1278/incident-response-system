import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

load_dotenv()

from agents import IncidentState
from agents.agentic_system import get_compiled_graph
from agents.llm import get_model, get_provider

app: FastAPI = FastAPI(title="AI Operations Command Center")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
        "analysis_iterations": values.get("analysis_iterations", 0),
        "rca_confidence": values.get("rca_confidence", 0.0),
        "current_status": values.get("current_status", "initial"),
        "completed_steps": sorted(completed) if completed else [],
        "log_anomalies": values.get("log_anomalies", []),
        "metric_anomalies": values.get("metric_anomalies", []),
        "deployment_changes": values.get("deployment_changes", []),
        "root_cause": values.get("root_cause"),
        "affected_users": values.get("affected_users", 0),
        "estimated_revenue_impact_per_minute": values.get(
            "estimated_revenue_impact_per_minute", 0.0
        ),
        "engineering_summary": values.get("engineering_summary", ""),
        "executive_summary": values.get("executive_summary", ""),
        "recovery_recommendations": values.get("recovery_recommendations", []),
        "agent_invocations": values.get("agent_invocations", []),
    }


async def _run_analysis(incident_id: str, state: IncidentState) -> None:
    """Stream the agent graph, updating the store after every node so the
    dashboard can render live agent activity."""
    graph: Any = get_compiled_graph()
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

        if incident_store[incident_id].get("current_status") != "complete":
            incident_store[incident_id]["current_status"] = "complete"
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
        "agentic": True,
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
    )

    record: Dict[str, Any] = _serialize_state(dict(vars(state)))
    record["current_status"] = "investigating"
    record["created_at"] = datetime.now().isoformat()
    incident_store[incident_id] = record
    incident_order.insert(0, incident_id)

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
