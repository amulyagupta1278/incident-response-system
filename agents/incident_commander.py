from typing import Any
from datetime import datetime
from agents import IncidentState
from mock_data import load_logs, load_metrics, load_deployments


def incident_commander(state: IncidentState) -> IncidentState:
    timestamp: str = state.timestamp or datetime.now().isoformat()

    if not state.raw_logs:
        state.raw_logs = load_logs(state.service, timestamp, state.log_source_path)
    if not state.raw_metrics:
        state.raw_metrics = load_metrics(state.service, timestamp)
    if not state.deployment_changes:
        state.deployment_changes = load_deployments(state.service, timestamp)

    invocation: dict[str, Any] = {
        "agent": "incident_commander",
        "timestamp": datetime.now().isoformat(),
        "action": "load_incident_data",
        "data_points": {
            "logs_loaded": len(state.raw_logs),
            "metrics_loaded": len(state.raw_metrics),
            "deployments_loaded": len(state.deployment_changes),
            "log_source": state.log_source_path or "auto"
        }
    }
    state.agent_invocations.append(invocation)

    return state
