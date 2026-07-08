import json
from datetime import datetime
from typing import Any, Dict, List

from agents import IncidentState
from agents.llm import complete_json, get_model, llm_available

ROUTER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["action", "reasoning"],
    "additionalProperties": False,
}

ACTION_DESCRIPTIONS: Dict[str, str] = {
    "load_data": "Fetch raw logs, metrics, and deployment history for the incident service",
    "analyze_logs": "Scan raw logs for error patterns (timeouts, connection errors, GC pauses)",
    "analyze_metrics": "Compare baseline vs incident metrics to find spikes",
    "run_rca": "Synthesize all evidence into a root cause hypothesis with confidence",
    "request_more_data": "Confidence is low - gather deeper evidence and re-run RCA",
    "calculate_business_impact": "Quantify affected users and revenue impact",
    "generate_summary": "Produce engineering and executive summaries",
    "complete": "All analysis steps are done - finish the investigation",
}


def valid_next_actions(state: IncidentState) -> List[str]:
    """Compute the set of actions that are currently legal given the state.

    This is the guardrail: the LLM router chooses among these, and the
    deterministic router picks the first one.
    """
    if "load_data" not in state.completed_steps:
        return ["load_data"]

    candidates: List[str] = []
    logs_done: bool = "log_analysis" in state.completed_steps
    metrics_done: bool = "metrics_analysis" in state.completed_steps
    rca_done: bool = "rca_analysis" in state.completed_steps

    if not logs_done:
        candidates.append("analyze_logs")
    if not metrics_done:
        candidates.append("analyze_metrics")
    if logs_done and metrics_done and not rca_done:
        candidates.append("run_rca")
    if rca_done:
        if state.rca_confidence < 0.7 and state.analysis_iterations < state.max_iterations:
            candidates.append("request_more_data")
        elif "business_impact" not in state.completed_steps:
            candidates.append("calculate_business_impact")
        elif "summary" not in state.completed_steps:
            candidates.append("generate_summary")
        else:
            candidates.append("complete")
    return candidates


def route_next_action(state: IncidentState) -> str:
    """Deterministic router: dependency-ordered checklist (also LLM fallback)."""
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


async def route_next_action_agentic(state: IncidentState) -> str:
    """LLM-powered router: reasons over the incident state and decides what
    to do next, constrained to the set of currently-valid actions. Falls
    back to the deterministic router when no LLM is configured or the LLM
    returns an invalid action.
    """
    candidates: List[str] = valid_next_actions(state)

    if not llm_available() or len(candidates) == 1:
        decision: str = route_next_action(state)
        reasoning: str = (
            "Single valid next step given completed work"
            if len(candidates) == 1
            else "Deterministic checklist routing (no LLM configured)"
        )
        _record_routing(state, decision, reasoning, source="deterministic")
        return decision

    state.analysis_iterations += 1

    prompt: str = (
        "You are the orchestrator of a multi-agent incident response system. "
        "Decide the single next action.\n\n"
        f"Incident: {state.alert_description} on service '{state.service}' "
        f"(severity: {state.severity})\n"
        f"Completed steps: {sorted(state.completed_steps) or 'none'}\n"
        f"Iteration: {state.analysis_iterations} of max {state.max_iterations}\n"
        f"RCA confidence so far: {state.rca_confidence:.2f}\n"
        f"Log anomalies found: {len(state.log_anomalies)}\n"
        f"Metric anomalies found: {len(state.metric_anomalies)}\n\n"
        "Valid actions right now:\n"
        + "\n".join(f"- {a}: {ACTION_DESCRIPTIONS[a]}" for a in candidates)
        + "\n\nChoose exactly one action from the valid list and explain why in one sentence."
    )

    try:
        result: Dict[str, Any] = await complete_json(
            system="You are an autonomous SRE incident commander. Respond with JSON.",
            prompt=prompt,
            schema=ROUTER_SCHEMA,
            schema_name="routing_decision",
        )
        decision = str(result.get("action", ""))
        reasoning = str(result.get("reasoning", ""))
        if decision not in candidates:
            print(f"[router] LLM chose invalid action '{decision}', using deterministic fallback")
            decision = candidates[0]
            reasoning = f"LLM chose an invalid action; guardrail selected '{decision}'"
            source = "guardrail"
        else:
            source = f"llm:{get_model()}"
    except Exception as exc:
        print(f"[router] LLM routing failed, using deterministic fallback: {exc}")
        decision = candidates[0]
        reasoning = "LLM routing failed; deterministic fallback"
        source = "deterministic"

    print(
        f"[router] iteration={state.analysis_iterations} "
        f"confidence={state.rca_confidence:.2f} decision={decision} ({source})"
    )
    _record_routing(state, decision, reasoning, source=source)
    return decision


def _record_routing(state: IncidentState, decision: str, reasoning: str, source: str) -> None:
    state.agent_invocations.append(
        {
            "agent": "router_agent",
            "timestamp": datetime.now().isoformat(),
            "action": f"route:{decision}",
            "source": source,
            "reasoning": reasoning,
            "iteration": state.analysis_iterations,
        }
    )


def should_request_more_data(state: IncidentState) -> str:
    if state.rca_confidence < 0.7 and state.analysis_iterations < state.max_iterations:
        return "low_confidence"
    return "high_confidence"
