from __future__ import annotations

import os
from typing import Any, Dict, List

from agents import IncidentState
from agents.telemetry import Timer, record_invocation


def run_rca_debate(state: IncidentState) -> IncidentState:
    """Run a bounded proposer/critics/judge review over the RCA.

    Critics are selected from incident signals, so the trace shows dynamic
    sub-agent participation without an unbounded autonomous loop.
    """
    max_rounds = max(1, min(int(os.getenv("RCA_DEBATE_MAX_ROUNDS", "2")), 3))
    if len(state.debate_rounds) >= max_rounds:
        state.completed_steps.add("rca_debate")
        state.current_status = "rca_debated"
        return state

    timer = Timer.begin()
    root_cause = state.root_cause or {}
    proposer_span = state.current_parent_span_id
    round_number = len(state.debate_rounds) + 1
    exchanges: List[Dict[str, Any]] = []

    evidence = list(root_cause.get("supporting_evidence") or [])
    evidence_refs = list(root_cause.get("supporting_evidence_refs") or [])
    alternatives = list(root_cause.get("ruled_out_hypotheses") or [])
    evidence_challenges: List[str] = []
    if len(evidence) < 3:
        evidence_challenges.append("Fewer than three supporting evidence claims were supplied")
    if len(evidence_refs) < min(3, len(evidence)):
        evidence_challenges.append("Some RCA claims do not resolve to raw evidence IDs")
    if len(alternatives) < 2:
        evidence_challenges.append("Fewer than two plausible alternatives were ruled out")
    if not evidence_challenges:
        evidence_challenges.append("Evidence coverage and alternative elimination are sufficient")
    evidence_verdict = "challenge" if any("Fewer" in item or "do not" in item for item in evidence_challenges) else "support"
    evidence_span = record_invocation(
        state,
        agent="evidence_critic",
        action="critique_rca_evidence",
        source="deterministic_debate",
        reasoning="; ".join(evidence_challenges),
        findings={"verdict": evidence_verdict, "challenges": evidence_challenges},
        parent_span_id=proposer_span,
        extra={"debate_round": round_number, "debate_role": "critic"},
    )
    exchanges.append({"agent": "evidence_critic", "verdict": evidence_verdict, "points": evidence_challenges})

    critic_spans = [evidence_span["span_id"]]
    needs_operations_critic = (
        state.severity.lower() == "critical"
        or state.rca_confidence < 0.85
        or bool(state.deployment_changes)
    )
    if needs_operations_critic:
        ops_points = _operations_critique(state)
        ops_verdict = "challenge" if any("missing" in point.lower() for point in ops_points) else "support"
        ops_span = record_invocation(
            state,
            agent="operations_critic",
            action="critique_operational_safety",
            source="deterministic_debate",
            reasoning="; ".join(ops_points),
            findings={"verdict": ops_verdict, "challenges": ops_points},
            parent_span_id=proposer_span,
            extra={"debate_round": round_number, "debate_role": "critic"},
        )
        critic_spans.append(ops_span["span_id"])
        exchanges.append({"agent": "operations_critic", "verdict": ops_verdict, "points": ops_points})

    challenges = [item for item in exchanges if item["verdict"] == "challenge"]
    if challenges:
        original_confidence = float(root_cause.get("confidence", state.rca_confidence) or 0.0)
        revised_confidence = max(0.0, round(original_confidence - min(0.12, 0.04 * len(challenges)), 3))
        root_cause["confidence"] = revised_confidence
        state.rca_confidence = revised_confidence
        record_invocation(
            state,
            agent="rca_reviser",
            action="revise_rca_after_critique",
            source="deterministic_debate",
            reasoning="Reduced confidence until critic challenges receive additional evidence.",
            findings={"before": original_confidence, "after": revised_confidence},
            parent_span_id=proposer_span,
            extra={"debate_round": round_number, "debate_role": "reviser"},
        )

    decision = "accepted" if not challenges else "accepted_with_caveats"
    judge = record_invocation(
        state,
        agent="debate_judge",
        action="adjudicate_rca_debate",
        source="deterministic_debate",
        reasoning=(
            "Accepted the RCA because both critics found adequate grounding and operational safety."
            if not challenges
            else "Accepted provisionally with explicit confidence reduction and critic caveats."
        ),
        findings={
            "decision": decision,
            "critic_spans": critic_spans,
            "challenge_count": len(challenges),
            "final_confidence": state.rca_confidence,
        },
        parent_span_id=proposer_span,
        latency_ms=timer.ms(),
        extra={"debate_round": round_number, "debate_role": "judge"},
    )
    debate_round = {
        "round": round_number,
        "proposer_span_id": proposer_span,
        "critic_span_ids": critic_spans,
        "judge_span_id": judge["span_id"],
        "decision": decision,
        "exchanges": exchanges,
        "final_confidence": state.rca_confidence,
    }
    state.debate_rounds.append(debate_round)
    root_cause["debate"] = list(state.debate_rounds)
    state.root_cause = root_cause
    state.completed_steps.add("rca_debate")
    state.current_status = "rca_debated"
    return state


def _operations_critique(state: IncidentState) -> List[str]:
    points: List[str] = []
    hypothesis = str((state.root_cause or {}).get("hypothesis", "")).lower()
    if state.deployment_changes and not (state.root_cause or {}).get("deploy_correlation"):
        points.append("Deployment correlation is missing despite a recent change")
    else:
        points.append("Deployment correlation was explicitly considered")
    if "cache" in hypothesis and not any(
        anomaly.get("type") == "retry_storm" for anomaly in state.log_anomalies
    ):
        points.append("Retry-storm evidence is missing for the cache-stampede hypothesis")
    else:
        points.append("The proposed failure mode matches the observed operational signals")
    return points
