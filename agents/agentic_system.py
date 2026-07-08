from typing import Any, Dict

from langgraph.graph import END, StateGraph

from agents import IncidentState
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.rca_agent import rca_analysis_with_claude
from agents.request_more_data_agent import request_more_data
from agents.router_agent import route_next_action, should_request_more_data

_compiled_graph: Any = None


def _as_updates(state: IncidentState) -> Dict[str, Any]:
    return dict(vars(state))


def _route_node(state: IncidentState) -> Dict[str, Any]:
    state.next_action = route_next_action(state)
    return _as_updates(state)


def _select_next_node(state: IncidentState) -> str:
    return state.next_action


def _load_data_node(state: IncidentState) -> Dict[str, Any]:
    state = incident_commander(state)
    state.completed_steps.add("load_data")
    state.current_status = "data_loaded"
    return _as_updates(state)


def _analyze_logs_node(state: IncidentState) -> Dict[str, Any]:
    state = log_analysis(state)
    state.completed_steps.add("log_analysis")
    state.current_status = "logs_analyzed"
    return _as_updates(state)


def _analyze_metrics_node(state: IncidentState) -> Dict[str, Any]:
    state = metrics_analysis(state)
    state.completed_steps.add("metrics_analysis")
    state.current_status = "metrics_analyzed"
    return _as_updates(state)


async def _run_rca_node(state: IncidentState) -> Dict[str, Any]:
    state = await rca_analysis_with_claude(state)
    state.current_status = "rca_completed"
    return _as_updates(state)


def _request_more_data_node(state: IncidentState) -> Dict[str, Any]:
    state = request_more_data(state)
    return _as_updates(state)


def _business_impact_node(state: IncidentState) -> Dict[str, Any]:
    state = business_impact(state)
    state.completed_steps.add("business_impact")
    state.current_status = "impact_calculated"
    return _as_updates(state)


def _generate_summary_node(state: IncidentState) -> Dict[str, Any]:
    state = executive_summary(state)
    state.completed_steps.add("summary")
    state.current_status = "complete"
    return _as_updates(state)


def create_incident_analysis_graph() -> Any:
    graph: StateGraph = StateGraph(IncidentState)

    graph.add_node("route_next_action", _route_node)
    graph.add_node("load_data", _load_data_node)
    graph.add_node("analyze_logs", _analyze_logs_node)
    graph.add_node("analyze_metrics", _analyze_metrics_node)
    graph.add_node("run_rca", _run_rca_node)
    graph.add_node("request_more_data", _request_more_data_node)
    graph.add_node("calculate_business_impact", _business_impact_node)
    graph.add_node("generate_summary", _generate_summary_node)

    graph.add_conditional_edges(
        "route_next_action",
        _select_next_node,
        {
            "load_data": "load_data",
            "analyze_logs": "analyze_logs",
            "analyze_metrics": "analyze_metrics",
            "run_rca": "run_rca",
            "request_more_data": "request_more_data",
            "calculate_business_impact": "calculate_business_impact",
            "generate_summary": "generate_summary",
            "complete": END,
        },
    )

    for node in [
        "load_data",
        "analyze_logs",
        "analyze_metrics",
        "request_more_data",
        "calculate_business_impact",
    ]:
        graph.add_edge(node, "route_next_action")

    graph.add_conditional_edges(
        "run_rca",
        should_request_more_data,
        {
            "low_confidence": "request_more_data",
            "high_confidence": "route_next_action",
        },
    )

    graph.add_edge("generate_summary", END)
    graph.set_entry_point("route_next_action")

    return graph.compile()


def get_compiled_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = create_incident_analysis_graph()
    return _compiled_graph


async def run_incident_analysis(state: IncidentState) -> IncidentState:
    graph: Any = get_compiled_graph()
    result: Any = await graph.ainvoke(dict(vars(state)))
    if isinstance(result, IncidentState):
        return result
    return IncidentState(**result)
