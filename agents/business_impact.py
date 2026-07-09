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
    default_impact_rate: float = service_data.get("default_impact_rate", 0.1)

    error_rate_metric: Any = next(
        (m for m in state.metric_anomalies if m["metric_name"] == "error_rate"),
        None
    )

    if error_rate_metric:
        impact_rate: float = float(error_rate_metric.get("current", 0.0))
        impact_source: str = "current error_rate metric"
    else:
        impact_rate = default_impact_rate
        impact_source = "service default impact rate"

    bounded_impact_rate: float = min(max(impact_rate, 0.0), 1.0)
    state.affected_users = min(total_users, int(total_users * bounded_impact_rate))

    state.estimated_revenue_impact_per_minute = (
        state.affected_users * revenue_per_user_per_minute
    )
    lower_bound_rate: float = max(0.0, bounded_impact_rate * 0.8)
    upper_bound_rate: float = min(1.0, bounded_impact_rate * 1.2)
    lower_bound_users: int = int(total_users * lower_bound_rate)
    upper_bound_users: int = int(total_users * upper_bound_rate)
    state.revenue_impact_justification = {
        "formula": "affected_users * revenue_per_user_per_minute",
        "affected_users_formula": "total_service_users * bounded_impact_rate",
        "total_service_users": total_users,
        "observed_impact_rate": impact_rate,
        "bounded_impact_rate": bounded_impact_rate,
        "impact_rate_source": impact_source,
        "revenue_per_user_per_minute": revenue_per_user_per_minute,
        "affected_users": state.affected_users,
        "revenue_impact_per_minute": state.estimated_revenue_impact_per_minute,
        "lower_bound_per_minute": lower_bound_users * revenue_per_user_per_minute,
        "upper_bound_per_minute": upper_bound_users * revenue_per_user_per_minute,
        "lower_bound_users": lower_bound_users,
        "upper_bound_users": upper_bound_users,
        "limits": {
            "impact_rate_floor": 0.0,
            "impact_rate_ceiling": 1.0,
            "affected_users_ceiling": total_users,
        },
    }

    invocation: dict[str, Any] = {
        "agent": "business_impact",
        "timestamp": datetime.now().isoformat(),
        "action": "calculate_business_impact",
        "reasoning": (
            f"{state.affected_users:,} affected users = {total_users:,} users * "
            f"{bounded_impact_rate:.1%} bounded impact rate from {impact_source}; "
            f"revenue impact = {state.affected_users:,} * "
            f"${revenue_per_user_per_minute:.2f}/user/min"
        ),
        "findings": {
            "affected_users": state.affected_users,
            "revenue_impact_per_minute": state.estimated_revenue_impact_per_minute,
            "total_service_users": total_users,
            "impact_rate": bounded_impact_rate,
            "lower_bound_per_minute": state.revenue_impact_justification["lower_bound_per_minute"],
            "upper_bound_per_minute": state.revenue_impact_justification["upper_bound_per_minute"],
        }
    }
    state.agent_invocations.append(invocation)

    return state
