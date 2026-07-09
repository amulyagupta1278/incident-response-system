from dataclasses import dataclass, field
from typing import List, Dict, Any, Set


@dataclass
class IncidentState:
    incident_id: str
    timestamp: str
    alert_description: str
    service: str
    severity: str = "unknown"
    raw_logs: List[Dict[str, Any]] = field(default_factory=list)
    raw_metrics: List[Dict[str, Any]] = field(default_factory=list)
    deployment_changes: List[Dict[str, Any]] = field(default_factory=list)
    log_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    log_context_cache: Dict[str, Any] = field(default_factory=dict)
    metric_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    root_cause: Dict[str, Any] = None
    affected_users: int = 0
    estimated_revenue_impact_per_minute: float = 0.0
    revenue_impact_justification: Dict[str, Any] = field(default_factory=dict)
    engineering_summary: str = ""
    executive_summary: str = ""
    recovery_recommendations: List[str] = field(default_factory=list)
    similar_incidents: List[Dict[str, Any]] = field(default_factory=list)
    agent_invocations: List[Dict[str, Any]] = field(default_factory=list)
    completed_steps: Set[str] = field(default_factory=set)
    analysis_iterations: int = 0
    rca_confidence: float = 0.0
    max_iterations: int = 5
    current_status: str = "initial"
    next_action: str = ""
