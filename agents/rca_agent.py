import json
from datetime import datetime
from typing import Any, Dict

from agents import IncidentState
from agents.llm import complete_json, get_model, llm_available
from agents.rca_analysis import rca_analysis

RCA_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "confidence": {"type": "number"},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hypothesis", "confidence", "supporting_evidence"],
    "additionalProperties": False,
}


async def rca_analysis_with_llm(state: IncidentState) -> IncidentState:
    """Root cause analysis: reasons over evidence with the configured LLM
    (OpenAI or Anthropic); falls back to heuristic pattern matching when no
    key is configured or the call fails."""
    result: Dict[str, Any] = {}
    source: str = "heuristic_fallback"

    if llm_available():
        prompt: str = (
            "You are a senior site reliability engineer performing root cause "
            "analysis on a production incident.\n\n"
            f"Alert: {state.alert_description}\n"
            f"Service: {state.service} (severity: {state.severity})\n\n"
            f"Log anomalies:\n{json.dumps(state.log_anomalies, default=str)}\n\n"
            f"Metric anomalies:\n{json.dumps(state.metric_anomalies, default=str)}\n\n"
            f"Deployment changes:\n{json.dumps(state.deployment_changes, default=str)}\n\n"
            "Determine the most likely root cause. Return a short hypothesis "
            "title, a calibrated confidence between 0 and 1, and three to five "
            "pieces of supporting evidence grounded in the data above."
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
            print(f"[rca] LLM call failed, using heuristic fallback: {exc}")
            result = {}

    if result:
        confidence: float = min(max(float(result.get("confidence", 0.5)), 0.0), 1.0)
        state.root_cause = {
            "hypothesis": result.get("hypothesis", "Unknown"),
            "confidence": confidence,
            "supporting_evidence": result.get("supporting_evidence", []),
        }
    else:
        state = rca_analysis(state)

    state.rca_confidence = float(state.root_cause.get("confidence", 0.0))
    state.completed_steps.add("rca_analysis")

    state.agent_invocations.append(
        {
            "agent": "rca_agent",
            "timestamp": datetime.now().isoformat(),
            "action": "run_rca",
            "source": source,
            "hypothesis": state.root_cause["hypothesis"],
            "confidence": state.rca_confidence,
            "iteration": state.analysis_iterations,
        }
    )
    return state


# Backwards-compatible alias (previous name)
rca_analysis_with_claude = rca_analysis_with_llm
