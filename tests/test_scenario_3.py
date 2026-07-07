import pytest
from agents import IncidentState
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.rca_analysis import rca_analysis
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary


@pytest.fixture
def scenario_3_state() -> IncidentState:
    return IncidentState(
        incident_id="test-scenario-3",
        timestamp="2026-07-07T17:05:05Z",
        alert_description="Cascading failure - downstream service timeout",
        service="checkout-gateway",
        severity="critical"
    )


def test_scenario_3_log_analysis(scenario_3_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_3_state)
    state = log_analysis(state)

    assert len(state.log_anomalies) > 0
    assert any(anom["type"] == "timeout" for anom in state.log_anomalies)


def test_scenario_3_metrics_analysis(scenario_3_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_3_state)
    state = log_analysis(state)
    state = metrics_analysis(state)

    assert len(state.metric_anomalies) > 0
    assert any(m["metric_name"] == "error_rate" for m in state.metric_anomalies)
    assert any(m["metric_name"] == "latency_ms" for m in state.metric_anomalies)


def test_scenario_3_rca(scenario_3_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_3_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)

    assert state.root_cause is not None
    assert "hypothesis" in state.root_cause
    assert state.root_cause["confidence"] > 0.50


def test_scenario_3_business_impact(scenario_3_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_3_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)

    assert state.affected_users > 0
    assert state.estimated_revenue_impact_per_minute > 0


def test_scenario_3_complete_flow(scenario_3_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_3_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)
    state = executive_summary(state)

    assert len(state.log_anomalies) > 0
    assert len(state.metric_anomalies) > 0
    assert state.root_cause is not None
    assert state.affected_users > 0
    assert len(state.engineering_summary) > 0
    assert len(state.executive_summary) > 0
    assert len(state.recovery_recommendations) > 0
    assert len(state.agent_invocations) == 6
