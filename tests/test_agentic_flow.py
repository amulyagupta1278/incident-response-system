import pytest

from agents import IncidentState
from agents.agentic_system import create_incident_analysis_graph, run_incident_analysis
from agents.request_more_data_agent import request_more_data
from agents.router_agent import route_next_action, should_request_more_data


def make_state(**overrides: object) -> IncidentState:
    defaults: dict = {
        "incident_id": "test-incident",
        "timestamp": "2026-07-07T14:32:15Z",
        "alert_description": "Test alert",
        "service": "payment-api",
        "severity": "critical",
    }
    defaults.update(overrides)
    return IncidentState(**defaults)


def test_router_decides_next_action() -> None:
    state: IncidentState = make_state(incident_id="test1")
    action: str = route_next_action(state)
    assert action == "load_data"


def test_router_with_completed_steps() -> None:
    state: IncidentState = make_state(
        incident_id="test2",
        raw_logs=[{"message": "log1"}],
        completed_steps={"load_data"},
    )
    action: str = route_next_action(state)
    assert action == "analyze_logs"


def test_router_routes_to_metrics_after_logs() -> None:
    state: IncidentState = make_state(
        incident_id="test3",
        completed_steps={"load_data", "log_analysis"},
    )
    action: str = route_next_action(state)
    assert action == "analyze_metrics"


def test_router_requests_more_data_on_low_confidence() -> None:
    state: IncidentState = make_state(
        incident_id="test4",
        completed_steps={"load_data", "log_analysis", "metrics_analysis", "rca_analysis"},
        rca_confidence=0.5,
        analysis_iterations=2,
    )
    action: str = route_next_action(state)
    assert action == "request_more_data"


def test_router_completes_after_all_steps() -> None:
    state: IncidentState = make_state(
        incident_id="test5",
        completed_steps={
            "load_data",
            "log_analysis",
            "metrics_analysis",
            "rca_analysis",
            "business_impact",
            "summary",
        },
        rca_confidence=0.85,
    )
    action: str = route_next_action(state)
    assert action == "complete"


def test_should_request_more_data_low_confidence() -> None:
    state: IncidentState = make_state(
        incident_id="test6",
        rca_confidence=0.62,
        analysis_iterations=2,
        max_iterations=5,
    )
    result: str = should_request_more_data(state)
    assert result == "low_confidence"


def test_should_request_more_data_high_confidence() -> None:
    state: IncidentState = make_state(
        incident_id="test7",
        rca_confidence=0.85,
        analysis_iterations=2,
    )
    result: str = should_request_more_data(state)
    assert result == "high_confidence"


def test_should_request_more_data_respects_max_iterations() -> None:
    state: IncidentState = make_state(
        incident_id="test8",
        rca_confidence=0.4,
        analysis_iterations=5,
        max_iterations=5,
    )
    result: str = should_request_more_data(state)
    assert result == "high_confidence"


def test_analysis_iterations_increment() -> None:
    state: IncidentState = make_state(incident_id="test9")
    initial_iterations: int = state.analysis_iterations
    route_next_action(state)
    assert state.analysis_iterations > initial_iterations


def test_request_more_data_resets_rca_step() -> None:
    state: IncidentState = make_state(
        incident_id="test10",
        completed_steps={"load_data", "log_analysis", "metrics_analysis", "rca_analysis"},
    )
    state = request_more_data(state)
    assert "rca_analysis" not in state.completed_steps
    assert state.current_status == "requesting_deeper_analysis"
    assert any(
        inv["agent"] == "request_more_data_agent" for inv in state.agent_invocations
    )


def test_agentic_graph_compiles() -> None:
    graph: object = create_incident_analysis_graph()
    assert graph is not None


def test_graph_visualization_renders() -> None:
    graph: object = create_incident_analysis_graph()
    mermaid: str = graph.get_graph().draw_mermaid()
    assert "route_next_action" in mermaid
    assert "run_rca" in mermaid


@pytest.mark.asyncio
async def test_agentic_graph_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    state: IncidentState = make_state(incident_id="test-e2e")
    result: IncidentState = await run_incident_analysis(state)

    assert result.analysis_iterations > 1
    assert result.root_cause is not None
    assert result.rca_confidence > 0.6
    assert result.affected_users > 0
    assert len(result.engineering_summary) > 0
    assert len(result.executive_summary) > 0
    assert result.current_status == "complete"
    assert "summary" in result.completed_steps


@pytest.mark.asyncio
async def test_agentic_graph_loops_on_low_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    state: IncidentState = make_state(
        incident_id="test-loop",
        service="unknown-service",
    )
    result: IncidentState = await run_incident_analysis(state)

    assert result.rca_confidence < 0.7
    assert any(
        inv["agent"] == "request_more_data_agent" for inv in result.agent_invocations
    )
    assert result.current_status == "complete"
