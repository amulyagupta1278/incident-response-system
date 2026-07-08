import json
import os
from datetime import datetime
from typing import Any, Dict

from agents import IncidentState
from agents.rca_analysis import rca_analysis


async def rca_analysis_with_claude(state: IncidentState) -> IncidentState:
    from agents.tools import call_claude_for_rca

    result: Dict[str, Any] = {}
    source: str = "heuristic_fallback"

    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            result = await call_claude_for_rca.ainvoke(
                {
                    "logs_str": json.dumps(state.log_anomalies, default=str),
                    "metrics_str": json.dumps(state.metric_anomalies, default=str),
                    "deployments_str": json.dumps(state.deployment_changes, default=str),
                }
            )
            source = "claude"
        except Exception as exc:
            print(f"[rca] Claude call failed, using heuristic fallback: {exc}")
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
            "action": "run_rca_with_claude",
            "source": source,
            "hypothesis": state.root_cause["hypothesis"],
            "confidence": state.rca_confidence,
            "iteration": state.analysis_iterations,
        }
    )
    return state
