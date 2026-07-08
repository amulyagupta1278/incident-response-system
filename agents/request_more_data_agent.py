from datetime import datetime
from typing import Any, Dict, List

from agents import IncidentState
from agents.log_analysis import log_analysis
from mock_data import load_deployments, load_logs


def request_more_data(state: IncidentState) -> IncidentState:
    """Low-confidence loop: gather deeper evidence, then send the state back
    through RCA. Re-fetches logs and deployment history, widens the log scan,
    and records exactly what extra data it decided to pull."""
    state.current_status = "requesting_deeper_analysis"

    data_requested: List[str] = []

    fresh_logs: List[Dict[str, Any]] = load_logs(state.service, state.timestamp)
    if len(fresh_logs) > len(state.raw_logs):
        data_requested.append(f"fetched {len(fresh_logs) - len(state.raw_logs)} additional log entries")
    state.raw_logs = fresh_logs or state.raw_logs

    fresh_deployments: List[Dict[str, Any]] = load_deployments(state.service, state.timestamp)
    if fresh_deployments and not state.deployment_changes:
        data_requested.append(f"fetched {len(fresh_deployments)} deployment records")
    state.deployment_changes = fresh_deployments or state.deployment_changes

    state.log_anomalies = []
    state = log_analysis(state)
    data_requested.append(f"re-ran log analysis: {len(state.log_anomalies)} anomalies")

    state.completed_steps.discard("rca_analysis")

    invocation: Dict[str, Any] = {
        "agent": "request_more_data_agent",
        "timestamp": datetime.now().isoformat(),
        "action": "request_deeper_analysis",
        "reasoning": (
            f"RCA confidence {state.rca_confidence:.2f} is below the 0.70 threshold; "
            "gathering more evidence before re-running RCA"
        ),
        "data_requested": data_requested,
        "iteration": state.analysis_iterations,
    }
    state.agent_invocations.append(invocation)

    return state
