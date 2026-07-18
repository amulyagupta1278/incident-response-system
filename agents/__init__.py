from dataclasses import dataclass, field
from typing import List, Dict, Any, Set


@dataclass
class IncidentState:
    incident_id: str
    timestamp: str
    alert_description: str
    service: str
    trace_id: str = ""
    severity: str = "unknown"
    lifecycle_status: str = "opened"
    agent_status: str = "initial"
    project_id: str = ""
    environment: str = "production"
    log_source_path: str = ""
    raw_logs: List[Dict[str, Any]] = field(default_factory=list)
    raw_metrics: List[Dict[str, Any]] = field(default_factory=list)
    deployment_changes: List[Dict[str, Any]] = field(default_factory=list)
    deployment_analysis: Dict[str, Any] = field(default_factory=dict)
    evidence_catalog: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    log_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    log_context_cache: Dict[str, Any] = field(default_factory=dict)
    metric_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    root_cause: Dict[str, Any] = None
    debate_rounds: List[Dict[str, Any]] = field(default_factory=list)
    affected_users: int = 0
    estimated_revenue_impact_per_minute: float = 0.0
    estimated_cost_impact_per_minute: float = 0.0
    revenue_impact_justification: Dict[str, Any] = field(default_factory=dict)
    business_risk_level: str = "unknown"
    blast_radius: Dict[str, Any] = field(default_factory=dict)
    engineering_summary: str = ""
    executive_summary: str = ""
    recovery_recommendations: List[str] = field(default_factory=list)
    recovery_plan: Dict[str, Any] = field(default_factory=dict)
    service_profile: Dict[str, Any] = field(default_factory=dict)
    ownership: Dict[str, Any] = field(default_factory=dict)
    escalation_path: List[str] = field(default_factory=list)
    runbooks: List[str] = field(default_factory=list)
    rollback_plan: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    upstream_services: List[str] = field(default_factory=list)
    context_metadata: Dict[str, Any] = field(default_factory=dict)
    retrieval_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence_citations: List[Dict[str, Any]] = field(default_factory=list)
    kg_similar_incidents: List[str] = field(default_factory=list)
    historical_impact_note: str = ""
    similar_incidents: List[Dict[str, Any]] = field(default_factory=list)
    agent_invocations: List[Dict[str, Any]] = field(default_factory=list)
    compact_contexts: List[Dict[str, Any]] = field(default_factory=list)
    review_events: List[Dict[str, Any]] = field(default_factory=list)
    remediation_policy: Dict[str, Any] = field(default_factory=dict)
    quality_gates: Dict[str, Any] = field(default_factory=dict)
    kpi_guardrails: Dict[str, Any] = field(default_factory=dict)
    stakeholder_updates: Dict[str, Any] = field(default_factory=dict)
    troubleshooting_plan: List[str] = field(default_factory=list)
    escalation_summary: str = ""
    completed_steps: Set[str] = field(default_factory=set)
    analysis_iterations: int = 0
    rca_confidence: float = 0.0
    max_iterations: int = 10
    current_status: str = "initial"
    next_action: str = ""
    span_seq: int = 0
    current_parent_span_id: str = ""
