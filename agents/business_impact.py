from typing import Any
from datetime import datetime
from agents import IncidentState
from mock_data import load_service_config


def business_impact(state: IncidentState) -> IncidentState:
    service_config: dict[str, Any] = load_service_config()

    service_data: dict[str, Any] = service_config.get(state.service, {
        "total_users": 10000,
        "revenue_per_user_per_minute": 0.5
    })

    total_users: int = service_data.get("total_users", 10000)
    revenue_per_user_per_minute: float = service_data.get("revenue_per_user_per_minute", 0.5)

    error_rate_metric: Any = next(
        (m for m in state.metric_anomalies if m["metric_name"] == "error_rate"),
        None
    )

    if error_rate_metric:
        error_rate: float = error_rate_metric.get("current", 0.0)
        state.affected_users = int(total_users * error_rate)
    else:
        state.affected_users = int(total_users * 0.1)

    state.estimated_revenue_impact_per_minute = (
        state.affected_users * revenue_per_user_per_minute
    )

    invocation: dict[str, Any] = {
        "agent": "business_impact",
        "timestamp": datetime.now().isoformat(),
        "action": "calculate_business_impact",
        "findings": {
            "affected_users": state.affected_users,
            "revenue_impact_per_minute": state.estimated_revenue_impact_per_minute,
            "total_service_users": total_users
        }
    }
    state.agent_invocations.append(invocation)

    return state
