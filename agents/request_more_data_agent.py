from datetime import datetime
from typing import Any, Dict

from agents import IncidentState
from agents.log_analysis import log_analysis


def request_more_data(state: IncidentState) -> IncidentState:
    state.current_status = "requesting_deeper_analysis"

    state.log_anomalies = []
    state = log_analysis(state)

    state.completed_steps.discard("rca_analysis")

    invocation: Dict[str, Any] = {
        "agent": "request_more_data_agent",
        "timestamp": datetime.now().isoformat(),
        "action": "request_deeper_analysis",
        "iteration": state.analysis_iterations,
    }
    state.agent_invocations.append(invocation)

    return state
