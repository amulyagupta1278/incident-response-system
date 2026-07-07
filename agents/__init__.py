from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime


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
    metric_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    root_cause: Dict[str, Any] = None
    affected_users: int = 0
    estimated_revenue_impact_per_minute: float = 0.0
    engineering_summary: str = ""
    executive_summary: str = ""
    recovery_recommendations: List[str] = field(default_factory=list)
    agent_invocations: List[Dict[str, Any]] = field(default_factory=list)
