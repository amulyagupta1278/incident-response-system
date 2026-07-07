import pytest
from agents import IncidentState
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.rca_analysis import rca_analysis
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary


@pytest.fixture
def scenario_2_state() -> IncidentState:
    return IncidentState(
        incident_id="test-scenario-2",
        timestamp="2026-07-07T16:30:00Z",
        alert_description="Memory leak detected - GC pause times increasing",
        service="order-processor",
        severity="critical"
    )


def test_scenario_2_log_analysis(scenario_2_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_2_state)
    state = log_analysis(state)

    assert len(state.log_anomalies) > 0
    assert any(anom["type"] == "gc_pause" for anom in state.log_anomalies)


def test_scenario_2_metrics_analysis(scenario_2_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_2_state)
    state = log_analysis(state)
    state = metrics_analysis(state)

    assert len(state.metric_anomalies) > 0
    assert any(m["metric_name"] == "memory_mb" for m in state.metric_anomalies)


def test_scenario_2_rca(scenario_2_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_2_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)

    assert state.root_cause is not None
    assert "hypothesis" in state.root_cause
    assert state.root_cause["confidence"] > 0.50


def test_scenario_2_business_impact(scenario_2_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_2_state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)

    assert state.affected_users > 0
    assert state.estimated_revenue_impact_per_minute > 0


def test_scenario_2_complete_flow(scenario_2_state: IncidentState) -> None:
    state: IncidentState = incident_commander(scenario_2_state)
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
