import json
import os
import unittest

from agents import IncidentState
from agents.business_impact import business_impact
from agents.executive_summary import executive_summary
from agents.incident_commander import incident_commander
from agents.metrics_analysis import metrics_analysis
from agents.log_analysis import log_analysis
from agents.quality import evaluate_quality_gates
from agents.rca_analysis import rca_analysis


def _load_golden() -> dict:
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "golden", "scenarios.json"), "r", encoding="utf-8") as f:
        return json.load(f)


class GoldenScenariosTest(unittest.TestCase):
    def test_golden_rca(self) -> None:
        golden = _load_golden()

        for name, spec in golden.items():
            with self.subTest(name=name):
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

                self.assertIsNotNone(state.root_cause)
                self.assertEqual(state.root_cause.get("hypothesis"), spec["expected_hypothesis"])
                self.assertGreaterEqual(float(state.root_cause.get("confidence", 0.0)), float(spec["min_confidence"]))
                self.assertLessEqual(float(state.root_cause.get("confidence", 0.0)), float(spec["max_confidence"]))
                self.assertTrue(state.quality_gates.get("overall_passed"))

                # Evidence faithfulness smoke check: do not allow empty evidence list
                evidence = (state.root_cause.get("supporting_evidence") or [])
                self.assertGreaterEqual(len(evidence), 1)
                refs = (state.root_cause.get("supporting_evidence_refs") or [])
                self.assertGreaterEqual(len(refs), 1)


if __name__ == "__main__":
    unittest.main()
