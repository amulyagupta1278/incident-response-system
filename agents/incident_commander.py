from typing import Any
from datetime import datetime
from agents import IncidentState
from mock_data import load_logs, load_metrics, load_deployments


def incident_commander(state: IncidentState) -> IncidentState:
    timestamp: str = state.timestamp or datetime.now().isoformat()

    state.raw_logs = load_logs(state.service, timestamp)
    state.raw_metrics = load_metrics(state.service, timestamp)
    state.deployment_changes = load_deployments(state.service, timestamp)

    invocation: dict[str, Any] = {
        "agent": "incident_commander",
        "timestamp": datetime.now().isoformat(),
        "action": "load_incident_data",
        "data_points": {
            "logs_loaded": len(state.raw_logs),
            "metrics_loaded": len(state.raw_metrics),
            "deployments_loaded": len(state.deployment_changes)
        }
    }
    state.agent_invocations.append(invocation)

    return state
