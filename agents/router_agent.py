from agents import IncidentState


def route_next_action(state: IncidentState) -> str:
    state.analysis_iterations += 1

    if "load_data" not in state.completed_steps:
        decision: str = "load_data"
    elif "log_analysis" not in state.completed_steps:
        decision = "analyze_logs"
    elif "metrics_analysis" not in state.completed_steps:
        decision = "analyze_metrics"
    elif "rca_analysis" not in state.completed_steps:
        decision = "run_rca"
    elif state.rca_confidence < 0.7 and state.analysis_iterations < state.max_iterations:
        decision = "request_more_data"
    elif "business_impact" not in state.completed_steps:
        decision = "calculate_business_impact"
    elif "summary" not in state.completed_steps:
        decision = "generate_summary"
    else:
        decision = "complete"

    print(
        f"[router] iteration={state.analysis_iterations} "
        f"confidence={state.rca_confidence:.2f} decision={decision}"
    )
    return decision


def should_request_more_data(state: IncidentState) -> str:
    if state.rca_confidence < 0.7 and state.analysis_iterations < state.max_iterations:
        return "low_confidence"
    return "high_confidence"
