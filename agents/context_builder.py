from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

SERVICE_CATALOG: Dict[str, Dict[str, Any]] = {
    "payment-api": {
        "display_name": "Payment API",
        "owner": {"team": "Payments Platform", "primary": "payments-oncall", "slack": "#payments-war-room", "pagerduty": "pd-payments"},
        "environment": {"tier": "production", "region": "us-east-1", "cluster": "prod-core", "namespace": "payments"},
        "dependencies": ["postgres-primary", "redis-session", "fraud-api", "checkout-gateway"],
        "upstream_services": ["checkout-gateway", "mobile-app", "web-checkout"],
        "runbooks": ["D_DRIVE_RUNBOOK.md", "data/knowledge/payment-db-pool-runbook.md"],
        "escalation_path": ["payments-oncall", "db-platform-oncall", "incident-commander"],
        "rollback": {"strategy": "rollback latest payment-api deployment", "safety_check": "db_pool_usage < 80% for 10m"},
    },
    "order-processor": {
        "display_name": "Order Processor",
        "owner": {"team": "Order Fulfillment", "primary": "orders-oncall", "slack": "#orders-incidents", "pagerduty": "pd-orders"},
        "environment": {"tier": "production", "region": "us-east-1", "cluster": "prod-workers", "namespace": "orders"},
        "dependencies": ["orders-db", "kafka-orders", "inventory-api"],
        "upstream_services": ["payment-api", "checkout-gateway"],
        "runbooks": ["data/knowledge/order-processor-memory-leak-runbook.md"],
        "escalation_path": ["orders-oncall", "jvm-platform-oncall", "incident-commander"],
        "rollback": {"strategy": "restart leaking worker pool and roll back last image", "safety_check": "gc_pause_p95 < 500ms"},
    },
    "checkout-gateway": {
        "display_name": "Checkout Gateway",
        "owner": {"team": "Checkout Experience", "primary": "checkout-oncall", "slack": "#checkout-war-room", "pagerduty": "pd-checkout"},
        "environment": {"tier": "production", "region": "us-east-1", "cluster": "prod-edge", "namespace": "checkout"},
        "dependencies": ["payment-api", "inventory-api", "tax-api", "shipping-api"],
        "upstream_services": ["web-checkout", "mobile-app"],
        "runbooks": ["data/knowledge/checkout-timeout-runbook.md"],
        "escalation_path": ["checkout-oncall", "platform-network-oncall", "incident-commander"],
        "rollback": {"strategy": "disable retry storm feature flag and revert timeout config", "safety_check": "5xx_rate < 1%"},
    },
    "catalog-api": {
        "display_name": "Catalog API",
        "owner": {"team": "Catalog", "primary": "catalog-oncall", "slack": "#catalog-incidents", "pagerduty": "pd-catalog"},
        "environment": {"tier": "production", "region": "us-east-1", "cluster": "prod-core", "namespace": "catalog"},
        "dependencies": ["catalog-db", "search-api", "cdn"],
        "upstream_services": ["web-store", "mobile-app"],
        "runbooks": ["data/knowledge/catalog-runbook.md"],
        "escalation_path": ["catalog-oncall", "search-oncall", "incident-commander"],
        "rollback": {"strategy": "roll back catalog schema migration", "safety_check": "catalog_error_rate < 1%"},
    },
    "search-api": {
        "display_name": "Search API",
        "owner": {"team": "Search Platform", "primary": "search-oncall", "slack": "#search-incidents", "pagerduty": "pd-search"},
        "environment": {"tier": "production", "region": "us-east-1", "cluster": "prod-search", "namespace": "search"},
        "dependencies": ["elasticsearch", "redis-cache", "catalog-api"],
        "upstream_services": ["web-store", "mobile-app", "recommendations"],
        "runbooks": ["data/knowledge/search-cache-stampede-runbook.md"],
        "escalation_path": ["search-oncall", "cache-platform-oncall", "incident-commander"],
        "rollback": {"strategy": "enable cache coalescing and rollback query expansion deploy", "safety_check": "p95_latency < 400ms"},
    },
}


def service_profile(service: str) -> Dict[str, Any]:
    profile = dict(SERVICE_CATALOG.get(service, {}))
    if not profile:
        profile = {
            "display_name": service or "Unknown Service",
            "owner": {"team": "Unknown", "primary": "platform-oncall", "slack": "#incident-command", "pagerduty": "pd-platform"},
            "environment": {"tier": "production", "region": "unknown", "cluster": "unknown", "namespace": service or "unknown"},
            "dependencies": [],
            "upstream_services": [],
            "runbooks": [],
            "escalation_path": ["platform-oncall", "incident-commander"],
            "rollback": {"strategy": "manual review required", "safety_check": "human approval required"},
        }
    profile["service"] = service
    profile["normalized_at"] = datetime.now(timezone.utc).isoformat()
    return profile


def _business_criticality(record: Dict[str, Any]) -> str:
    severity = str(record.get("severity", "")).lower()
    tier = str(record.get("environment", {}).get("tier", "")).lower()
    if severity == "critical" or (severity == "high" and tier == "production"):
        return "critical"
    if severity == "high":
        return "high"
    if severity == "medium":
        return "medium"
    return "low"


def _normalize_change_item(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    return {"description": str(item)}


def _extract_configuration_changes(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    changes = record.get("configuration_changes") or []
    normalized: List[Dict[str, Any]] = []
    if isinstance(changes, list):
        normalized.extend(_normalize_change_item(item) for item in changes)
    elif changes:
        normalized.append(_normalize_change_item(changes))

    for deployment in record.get("deployment_changes", []):
        description = ""
        if isinstance(deployment.get("changes"), list):
            description = " ".join(str(item) for item in deployment.get("changes", []))
        else:
            description = str(deployment.get("changes", ""))
        if "config" in description.lower() or "feature flag" in description.lower() or "setting" in description.lower():
            normalized.append(
                {
                    "timestamp": deployment.get("timestamp"),
                    "source": deployment.get("source"),
                    "description": description,
                }
            )
    return normalized


def _deployment_history(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    history = []
    for deployment in record.get("deployment_changes", []):
        history.append(
            {
                "timestamp": deployment.get("timestamp"),
                "version": deployment.get("version"),
                "changes": deployment.get("changes") or [],
                "source": deployment.get("source"),
            }
        )
    return history


def enrich_record(record: Dict[str, Any]) -> Dict[str, Any]:
    profile = service_profile(str(record.get("service") or "unknown"))
    record["service_profile"] = profile
    record["ownership"] = profile.get("owner", {})
    record["environment"] = profile.get("environment", {})
    record["dependencies"] = profile.get("dependencies", [])
    record["upstream_services"] = profile.get("upstream_services", [])
    record["runbooks"] = profile.get("runbooks", [])
    record["escalation_path"] = profile.get("escalation_path", [])
    record["rollback_plan"] = profile.get("rollback", {})
    record["blast_radius"] = {
        "services": [*record["upstream_services"], *record["dependencies"]],
        "estimated_scope": "high" if len(record["upstream_services"]) + len(record["dependencies"]) >= 5 else "medium" if record["upstream_services"] or record["dependencies"] else "low",
        "reason": "Derived from service catalog dependencies and upstream consumers.",
    }
    record["deployment_history"] = _deployment_history(record)
    record["configuration_changes"] = _extract_configuration_changes(record)
    record["business_risk_level"] = record.get("business_risk_level") or _business_criticality(record)
    record["business_criticality"] = record.get("business_criticality") or record["business_risk_level"]
    record["related_incidents"] = record.get("similar_incidents", [])
    record["source_connector_id"] = record.get("source_connector_id")
    return record


def normalize_incident_context(record: Dict[str, Any]) -> Dict[str, Any]:
    enriched = enrich_record(dict(record))
    return {
        "incident_id": enriched.get("incident_id"),
        "service": enriched.get("service") or "unknown",
        "severity": enriched.get("severity") or "unknown",
        "alert_description": enriched.get("alert_description") or "",
        "ownership": enriched.get("ownership") or {},
        "environment": enriched.get("environment") or {},
        "dependencies": enriched.get("dependencies") or [],
        "upstream_services": enriched.get("upstream_services") or [],
        "runbooks": enriched.get("runbooks") or [],
        "escalation_path": enriched.get("escalation_path") or [],
        "rollback_plan": enriched.get("rollback_plan") or {},
        "blast_radius": enriched.get("blast_radius") or {},
        "deployment_history": enriched.get("deployment_history") or [],
        "configuration_changes": enriched.get("configuration_changes") or [],
        "business_criticality": enriched.get("business_criticality") or "unknown",
        "business_risk_level": enriched.get("business_risk_level") or "unknown",
        "runbooks": enriched.get("runbooks") or [],
        "related_incidents": enriched.get("related_incidents") or [],
        "source_connector_id": enriched.get("source_connector_id"),
        "service_profile": enriched.get("service_profile") or {},
        "normalized_at": enriched.get("service_profile", {}).get("normalized_at"),
        "metadata_source": "context_builder",
    }


def build_incident_context(record: Dict[str, Any]) -> Dict[str, Any]:
    return normalize_incident_context(record)


def topology_for_service(service: str) -> Dict[str, Any]:
    profile = service_profile(service)
    return {
        "service": service,
        "owner": profile.get("owner", {}),
        "environment": profile.get("environment", {}),
        "dependencies": profile.get("dependencies", []),
        "upstream_services": profile.get("upstream_services", []),
        "runbooks": profile.get("runbooks", []),
        "escalation_path": profile.get("escalation_path", []),
        "rollback": profile.get("rollback", {}),
        "blast_radius": {
            "services": [*profile.get("upstream_services", []), *profile.get("dependencies", [])],
            "estimated_scope": "high" if len(profile.get("upstream_services", [])) + len(profile.get("dependencies", [])) >= 5 else "medium" if profile.get("dependencies") or profile.get("upstream_services") else "low",
            "reason": "Derived from the operational service catalog.",
        },
    }

