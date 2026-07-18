from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agents import IncidentState


@dataclass(frozen=True)
class ConfidenceBreakdown:
    score: float
    evidence_strength: float
    signal_count: int
    deploy_correlation: float
    signal_diversity: float
    anomaly_severity: float
    data_completeness: float
    alternatives_ruled_out: float
    historical_similarity: float
    llm_self_report: Optional[float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def compute_confidence(state: IncidentState, *, llm_self_report: Optional[float]) -> ConfidenceBreakdown:
    log_signals: int = len(state.log_anomalies or [])
    metric_signals: int = len(state.metric_anomalies or [])
    signal_count: int = log_signals + metric_signals

    # Evidence strength: prefer multiple independent signals; cap quickly.
    evidence_strength: float = _clamp01(signal_count / 6.0)

    has_deploy: bool = bool(state.deployment_changes)
    deploy_corr: float = 1.0 if has_deploy else 0.0

    signal_diversity: float = _clamp01(
        len(
            {
                *(a.get("type", "") for a in state.log_anomalies or []),
                *(m.get("metric_name", "") for m in state.metric_anomalies or []),
            }
        )
        / 6.0
    )
    severity_weights = {"critical": 1.0, "high": 0.8, "medium": 0.55, "low": 0.25}
    severities = [
        severity_weights.get(str(a.get("severity", "")).lower(), 0.25)
        for a in (state.log_anomalies or []) + (state.metric_anomalies or [])
    ]
    anomaly_severity: float = max(severities) if severities else 0.0
    data_completeness: float = _clamp01(
        sum(
            [
                bool(state.raw_logs),
                bool(state.raw_metrics),
                bool(state.deployment_changes) or not has_deploy,
                bool(state.evidence_catalog),
            ]
        )
        / 4.0
    )
    alternatives_ruled_out: float = _clamp01(
        len((state.root_cause or {}).get("ruled_out_hypotheses", []) or []) / 2.0
    )
    historical_similarity: float = 1.0 if state.similar_incidents else 0.0

    # LLM self-report is informative but never decisive alone.
    llm_part: float = 0.0
    if llm_self_report is not None:
        llm_part = _clamp01(llm_self_report)

    score: float = (
        0.24 * evidence_strength
        + 0.16 * signal_diversity
        + 0.14 * anomaly_severity
        + 0.12 * data_completeness
        + 0.10 * deploy_corr
        + 0.08 * alternatives_ruled_out
        + 0.06 * historical_similarity
        + 0.10 * llm_part
    )

    # Penalize when there is very little evidence, even if the LLM is confident.
    if evidence_strength < 0.34:
        score *= 0.85

    return ConfidenceBreakdown(
        score=_clamp01(score),
        evidence_strength=evidence_strength,
        signal_count=signal_count,
        deploy_correlation=deploy_corr,
        signal_diversity=signal_diversity,
        anomaly_severity=anomaly_severity,
        data_completeness=data_completeness,
        alternatives_ruled_out=alternatives_ruled_out,
        historical_similarity=historical_similarity,
        llm_self_report=llm_self_report,
    )
