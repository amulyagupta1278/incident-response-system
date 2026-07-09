import json
from typing import Any, Dict, List

from langchain_core.tools import tool

from mock_data import load_deployments, load_logs, load_metrics, load_service_config

RCA_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "confidence": {"type": "number"},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hypothesis", "confidence", "supporting_evidence"],
    "additionalProperties": False,
}


@tool
def fetch_logs(service: str, timestamp: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch recent log entries for a service around the incident timestamp."""
    return load_logs(service, timestamp)[:limit]


@tool
def fetch_metrics(service: str, timestamp: str = "") -> List[Dict[str, Any]]:
    """Fetch baseline and incident window metrics for a service."""
    return load_metrics(service, timestamp)


@tool
def fetch_deployments(service: str, timestamp: str = "") -> List[Dict[str, Any]]:
    """Fetch recent deployment changes for a service."""
    return load_deployments(service, timestamp)


@tool
def get_service_config(service: str) -> Dict[str, Any]:
    """Fetch business configuration such as user count and revenue rate for a service."""
    return load_service_config().get(service, {})


@tool
async def call_llm_for_rca(
    logs_str: str, metrics_str: str, deployments_str: str
) -> Dict[str, Any]:
    """Ask the configured LLM (OpenAI) to reason over incident
    evidence and return a root cause hypothesis."""
    from agents.llm import complete_json

    prompt: str = (
        "You are a senior site reliability engineer performing root cause analysis "
        "on a production incident.\n\n"
        f"Log anomalies:\n{logs_str}\n\n"
        f"Metric anomalies:\n{metrics_str}\n\n"
        f"Deployment changes:\n{deployments_str}\n\n"
        "Determine the most likely root cause. Return a short hypothesis title, "
        "a calibrated confidence between 0 and 1, and three to five pieces of "
        "supporting evidence grounded in the data above."
    )
    return await complete_json(
        system="You are an expert SRE. Ground every claim in the provided evidence.",
        prompt=prompt,
        schema=RCA_SCHEMA,
        schema_name="root_cause_analysis",
    )


