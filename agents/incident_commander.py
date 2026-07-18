from typing import Any
from datetime import datetime
from agents import IncidentState
from agents.evidence import attach_evidence_ids, build_evidence_catalog
from agents.context_builder import topology_for_service
from mock_data import load_logs, load_metrics, load_deployments


def incident_commander(state: IncidentState) -> IncidentState:
    timestamp: str = state.timestamp or datetime.now().isoformat()

    if not state.raw_logs:
        state.raw_logs = load_logs(state.service, timestamp, state.log_source_path)
    if not state.raw_metrics:
        state.raw_metrics = load_metrics(state.service, timestamp)
    if not state.deployment_changes:
        state.deployment_changes = load_deployments(state.service, timestamp)

    attach_evidence_ids(
        state.service,
        state.raw_logs,
        state.raw_metrics,
        state.deployment_changes,
    )
    state.evidence_catalog = build_evidence_catalog(
        state.raw_logs,
        state.raw_metrics,
        state.deployment_changes,
    )

    topology = topology_for_service(state.service)
    state.service_profile = topology
    state.ownership = topology.get("owner", {})
    state.dependencies = topology.get("dependencies", [])
    state.upstream_services = topology.get("upstream_services", [])
    state.runbooks = topology.get("runbooks", [])
    state.escalation_path = topology.get("escalation_path", [])
    state.rollback_plan = topology.get("rollback", {})
    state.blast_radius = topology.get("blast_radius", {})
    state.context_metadata = {"environment": topology.get("environment", {}), "source": "service_catalog"}

    invocation: dict[str, Any] = {
        "agent": "incident_commander",
        "timestamp": datetime.now().isoformat(),
        "action": "load_incident_data",
        "data_points": {
            "logs_loaded": len(state.raw_logs),
            "metrics_loaded": len(state.raw_metrics),
            "deployments_loaded": len(state.deployment_changes),
            "log_source": state.log_source_path or "auto"
        }
    }
    state.agent_invocations.append(invocation)

    return state
