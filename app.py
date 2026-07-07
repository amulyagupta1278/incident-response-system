from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import uuid
from datetime import datetime
from typing import Any, Dict
from agents import IncidentState
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.rca_analysis import rca_analysis
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary

app: FastAPI = FastAPI(title="AI Operations Command Center")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

incident_store: Dict[str, Dict[str, Any]] = {}


def build_incident_response_graph() -> Any:
    return None


@app.get("/api/health")
async def health_check() -> Dict[str, str]:
    return {"status": "healthy"}


@app.post("/api/incidents/trigger")
async def trigger_incident(incident_data: Dict[str, Any]) -> Dict[str, Any]:
    incident_id: str = str(uuid.uuid4())
    timestamp: str = incident_data.get("timestamp", datetime.now().isoformat())

    state: IncidentState = IncidentState(
        incident_id=incident_id,
        timestamp=timestamp,
        alert_description=incident_data.get("alert_description", ""),
        service=incident_data.get("service", ""),
        severity=incident_data.get("severity", "unknown")
    )

    state = incident_commander(state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)
    state = executive_summary(state)

    result: Dict[str, Any] = {
        "incident_id": state.incident_id,
        "timestamp": state.timestamp,
        "alert_description": state.alert_description,
        "service": state.service,
        "severity": state.severity,
        "log_anomalies": state.log_anomalies,
        "metric_anomalies": state.metric_anomalies,
        "root_cause": state.root_cause,
        "affected_users": state.affected_users,
        "estimated_revenue_impact_per_minute": state.estimated_revenue_impact_per_minute,
        "engineering_summary": state.engineering_summary,
        "executive_summary": state.executive_summary,
        "recovery_recommendations": state.recovery_recommendations,
        "agent_invocations": state.agent_invocations
    }

    incident_store[incident_id] = result

    return result


@app.get("/api/incidents/{incident_id}")
async def get_incident(incident_id: str) -> Dict[str, Any]:
    if incident_id not in incident_store:
        raise HTTPException(status_code=404, detail="Incident not found")

    return incident_store[incident_id]


@app.get("/")
async def serve_dashboard() -> FileResponse:
    return FileResponse("frontend/dashboard.html", media_type="text/html")


@app.get("/incident/{incident_id}")
async def serve_incident_detail(incident_id: str) -> FileResponse:
    return FileResponse("frontend/incident_detail.html", media_type="text/html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
