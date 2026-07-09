import json
from datetime import datetime
from typing import Any, Dict

from agents import IncidentState
from agents.llm import complete_json, get_model, llm_available, llm_strict_mode
from agents.memory import find_similar_incidents
from agents.rca_analysis import rca_analysis

RCA_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "confidence": {"type": "number"},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
        "ruled_out_hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["hypothesis", "reason"],
                "additionalProperties": False,
            },
        },
        "deploy_correlation": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": [
        "hypothesis",
        "confidence",
        "supporting_evidence",
        "ruled_out_hypotheses",
        "deploy_correlation",
        "reasoning",
    ],
    "additionalProperties": False,
}


async def rca_analysis_with_llm(state: IncidentState) -> IncidentState:
    """Root cause analysis: reasons over evidence with the configured LLM
    (OpenAI); falls back to heuristic pattern matching only when no key is
    configured, or when strict mode is disabled."""
    result: Dict[str, Any] = {}
    source: str = "heuristic_fallback"

    if llm_available():
        prompt: str = (
            "You are a senior site reliability engineer performing root cause "
            "analysis on a production incident.\n\n"
            f"Alert: {state.alert_description}\n"
            f"Service: {state.service} (severity: {state.severity})\n"
            f"Incident started at: {state.timestamp}\n\n"
            f"Log anomalies:\n{json.dumps(state.log_anomalies, default=str)}\n\n"
            f"Metric anomalies:\n{json.dumps(state.metric_anomalies, default=str)}\n\n"
            f"Deployment changes:\n{json.dumps(state.deployment_changes, default=str)}\n\n"
            "Determine the most likely root cause. Return:\n"
            "- hypothesis: a short root-cause title\n"
            "- confidence: calibrated between 0 and 1\n"
            "- supporting_evidence: 3-5 items, each citing an EXACT log message, "
            "metric name with baseline->current values, or deployment change from "
            "the data above. Never cite evidence that is not literally present.\n"
            "- ruled_out_hypotheses: 2 plausible alternative causes you considered "
            "and dismissed, each with the specific data point that rules it out\n"
            "- deploy_correlation: if a deployment timestamp precedes the incident "
            "start, one sentence like 'Incident began N minutes after deployment "
            "vX.Y.Z which <relevant change>'; empty string if no deployment is "
            "plausibly related\n"
            "- reasoning: one sentence describing how you weighed the evidence"
        )
        try:
            result = await complete_json(
                system="You are an expert SRE. Ground every claim in the provided evidence.",
                prompt=prompt,
                schema=RCA_SCHEMA,
                schema_name="root_cause_analysis",
            )
            source = f"llm:{get_model()}"
        except Exception as exc:
            if llm_strict_mode():
                raise RuntimeError(
                    f"LLM RCA failed in strict mode: {exc}"
                ) from exc
            print(f"[rca] LLM call failed, using heuristic fallback: {exc}")
            result = {}

    if result:
        confidence: float = min(max(float(result.get("confidence", 0.5)), 0.0), 1.0)
        state.root_cause = {
            "hypothesis": result.get("hypothesis", "Unknown"),
            "confidence": confidence,
            "supporting_evidence": result.get("supporting_evidence", []),
            "ruled_out_hypotheses": result.get("ruled_out_hypotheses", []),
            "deploy_correlation": result.get("deploy_correlation", ""),
        }
    else:
        state = rca_analysis(state)

    state.rca_confidence = float(state.root_cause.get("confidence", 0.0))
    state.completed_steps.add("rca_analysis")

    state.similar_incidents = find_similar_incidents(state)
    if state.similar_incidents:
        top: Dict[str, Any] = state.similar_incidents[0]
        state.agent_invocations.append(
            {
                "agent": "memory",
                "timestamp": datetime.now().isoformat(),
                "action": "recall_similar_incidents",
                "source": "memory",
                "reasoning": (
                    f"This matches incident #{top.get('number')} on {top.get('service')} "
                    f"from {str(top.get('resolved_at', ''))[:10]} — same pattern: "
                    f"{top.get('hypothesis')} ({top.get('match_reason')})"
                ),
                "iteration": state.analysis_iterations,
            }
        )

    state.agent_invocations.append(
        {
            "agent": "rca_agent",
            "timestamp": datetime.now().isoformat(),
            "action": "run_rca",
            "source": source,
            "hypothesis": state.root_cause["hypothesis"],
            "confidence": state.rca_confidence,
            "reasoning": result.get("reasoning", "")
            or f"Pattern-matched evidence against known failure signatures; best fit '{state.root_cause['hypothesis']}'",
            "iteration": state.analysis_iterations,
        }
    )
    return state
