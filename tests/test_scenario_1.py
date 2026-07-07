import pytest
from agents import IncidentState
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.rca_analysis import rca_analysis
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary


@pytest.fixture
def scenario_1_state() -> IncidentState:
    return IncidentState(
        incident_id="test-scenario-1",
        timestamp="2026-07-07T14:32:15Z",
        alert_description="Database connection pool exhaustion detected",
        service="payment-api",
        severity="critical"
    )


def test_scenario_1_log_analysis(scenario_1_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_1_state)
    state = log_analysis(state)

    assert len(state.log_anomalies) > 0
    assert any(anom["type"] == "timeout" for anom in state.log_anomalies)
    assert any(anom["type"] == "connection_error" for anom in state.log_anomalies)


def test_scenario_1_metrics_analysis(scenario_1_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_1_state)
    state = log_analysis(state)
    state = metrics_analysis(state)

    assert len(state.metric_anomalies) > 0
    assert any(m["metric_name"] == "cpu_percent" for m in state.metric_anomalies)
    assert any(m["metric_name"] == "error_rate" for m in state.metric_anomalies)


def test_scenario_1_rca(scenario_1_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_1_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)

    assert state.root_cause is not None
    assert "hypothesis" in state.root_cause
    assert state.root_cause["confidence"] > 0.60


def test_scenario_1_business_impact(scenario_1_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_1_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)

    assert state.affected_users > 0
    assert state.estimated_revenue_impact_per_minute > 0


def test_scenario_1_complete_flow(scenario_1_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_1_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)
    state = executive_summary(state)

    assert len(state.log_anomalies) > 0
    assert len(state.metric_anomalies) > 0
    assert state.root_cause is not None
    assert state.root_cause["confidence"] > 0.60
    assert state.affected_users > 0
    assert len(state.engineering_summary) > 0
    assert len(state.executive_summary) > 0
    assert len(state.recovery_recommendations) > 0
    assert len(state.agent_invocations) == 6
