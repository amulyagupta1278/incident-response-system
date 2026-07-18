from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from agents import IncidentState
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary
from agents.incident_commander import incident_commander
from agents.log_analysis import log_analysis
from agents.metrics_analysis import metrics_analysis
from agents.quality import evaluate_quality_gates
from agents.rca_analysis import rca_analysis


@dataclass
class EvalResult:
    scenario: str
    passed: bool
    score: float
    checks: Dict[str, bool]
    details: Dict[str, Any]


def _load_golden() -> Dict[str, Any]:
    path = Path(__file__).parent / "golden" / "scenarios.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _run_scenario(name: str, spec: Dict[str, Any]) -> EvalResult:
    state = IncidentState(
        incident_id=f"eval-{name}",
        timestamp=spec["timestamp"],
        alert_description=spec["alert_description"],
        service=spec["service"],
        severity="critical",
    )
    state.trace_id = state.incident_id
    state = incident_commander(state)
    state = log_analysis(state)
    state = metrics_analysis(state)
    state = rca_analysis(state)
    state = business_impact(state)
    state = executive_summary(state)
    state.quality_gates = evaluate_quality_gates(state)

    root_cause = state.root_cause or {}
    evidence_text = " ".join(root_cause.get("supporting_evidence", []))
    ruled_out_text = " ".join(
        f"{item.get('hypothesis', '')} {item.get('reason', '')}"
        for item in root_cause.get("ruled_out_hypotheses", [])
    )
    confidence = float(root_cause.get("confidence", 0.0))
    checks = {
        "correctness": root_cause.get("hypothesis") == spec["expected_hypothesis"],
        "groundedness": bool(root_cause.get("supporting_evidence_refs")),
        "quality_gates": bool(state.quality_gates.get("overall_passed")),
        "confidence_band": spec["min_confidence"] <= confidence <= spec["max_confidence"],
        "required_evidence": all(
            term.lower() in evidence_text.lower()
            for term in spec.get("required_evidence_terms", [])
        ),
        "ruled_out": all(
            term.lower() in ruled_out_text.lower()
            for term in spec.get("required_ruled_out_terms", [])
        ),
        "actionability": len(state.recovery_recommendations) >= 3,
    }
    score = sum(1 for ok in checks.values() if ok) / len(checks)
    return EvalResult(
        scenario=name,
        passed=all(checks.values()),
        score=score,
        checks=checks,
        details={
            "hypothesis": root_cause.get("hypothesis"),
            "confidence": confidence,
            "quality_gates": state.quality_gates,
        },
    )


def run_evals() -> List[EvalResult]:
    return [_run_scenario(name, spec) for name, spec in _load_golden().items()]


def _print_results(results: List[EvalResult]) -> None:
    payload = {
        "passed": all(r.passed for r in results),
        "score": round(sum(r.score for r in results) / max(len(results), 1), 3),
        "results": [
            {
                "scenario": r.scenario,
                "passed": r.passed,
                "score": round(r.score, 3),
                "checks": r.checks,
                "details": r.details,
            }
            for r in results
        ],
    }
    print(json.dumps(payload, indent=2))


def _run_pytest() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run trust-quality incident evals.")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--pytest", action="store_true")
    args = parser.parse_args()

    rounds = max(1, args.max_rounds if args.recursive else 1)
    last_results: List[EvalResult] = []
    pytest_ok = True
    for round_index in range(1, rounds + 1):
        print(f"validation_round={round_index}")
        last_results = run_evals()
        _print_results(last_results)
        pytest_ok = _run_pytest() if args.pytest else True
        if all(r.passed for r in last_results) and pytest_ok:
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
