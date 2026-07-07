import pytest
from agents import IncidentState
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.rca_analysis import rca_analysis
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary


def test_all_scenarios_complete_flow() -> None:
    scenarios: list[tuple[str, str, str, str]] = [
        ("test-scenario-1", "2026-07-07T14:32:15Z", "Database connection pool exhaustion detected", "payment-api"),
        ("test-scenario-2", "2026-07-07T16:30:00Z", "Memory leak detected - GC pause times increasing", "order-processor"),
        ("test-scenario-3", "2026-07-07T17:05:05Z", "Cascading failure - downstream service timeout", "checkout-gateway"),
    ]

    for incident_id, timestamp, alert_description, service in scenarios:
        state: IncidentState = IncidentState(
            incident_id=incident_id,
            timestamp=timestamp,
            alert_description=alert_description,
            service=service,
            severity="critical"
        )

        state = incident_commander(state)
        state = log_analysis(state)
        state = metrics_analysis(state)
        state = rca_analysis(state)
        state = business_impact(state)
        state = executive_summary(state)

        assert state.incident_id == incident_id
        assert state.service == service
        assert len(state.log_anomalies) > 0
        assert len(state.metric_anomalies) > 0
        assert state.root_cause is not None
        assert state.root_cause["confidence"] > 0
        assert state.affected_users > 0
        assert state.estimated_revenue_impact_per_minute > 0
        assert len(state.engineering_summary) > 0
        assert len(state.executive_summary) > 0
        assert len(state.recovery_recommendations) > 0
        assert len(state.agent_invocations) == 6


def test_scenario_isolation() -> None:
    state1: IncidentState = IncidentState(
        incident_id="test-1",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Test 1",
        service="payment-api",
        severity="critical"
    )

    state2: IncidentState = IncidentState(
        incident_id="test-2",
        timestamp="2026-07-07T16:30:00Z",
        alert_description="Test 2",
        service="order-processor",
        severity="critical"
    )

    state1 = incident_commander(state1)
    state2 = incident_commander(state2)

    assert state1.incident_id != state2.incident_id
    assert state1.service != state2.service
    assert len(state1.raw_logs) != len(state2.raw_logs)
