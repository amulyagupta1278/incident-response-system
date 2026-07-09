import json
import os
from typing import Any, List, Dict
from agents.log_ingestion import load_log_file


def load_logs(service: str, timestamp: str, source_path: str = "") -> List[Dict[str, Any]]:
    live_path: str = source_path or os.getenv("LIVE_LOGS_PATH", "") or _find_live_log_path(service)
    if live_path:
        return load_log_file(live_path, service)

    scenario_dir: str = _find_scenario_dir(service)
    if not scenario_dir:
        return []

    logs_path: str = os.path.join(scenario_dir, "logs.json")
    if os.path.exists(logs_path):
        with open(logs_path, "r") as f:
            return json.load(f)
    return []


def load_metrics(service: str, timestamp: str) -> List[Dict[str, Any]]:
    scenario_dir: str = _find_scenario_dir(service)
    if not scenario_dir:
        return []

    metrics_path: str = os.path.join(scenario_dir, "metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path, "r") as f:
            return json.load(f)
    return []


def load_deployments(service: str, timestamp: str) -> List[Dict[str, Any]]:
    scenario_dir: str = _find_scenario_dir(service)
    if not scenario_dir:
        return []

    deployments_path: str = os.path.join(scenario_dir, "deployments.json")
    if os.path.exists(deployments_path):
        with open(deployments_path, "r") as f:
            return json.load(f)
    return []


def load_service_config() -> Dict[str, Any]:
    return {
        "payment-api": {
            "total_users": 14000,
            "revenue_per_user_per_minute": 0.05
        },
        "order-processor": {
            "total_users": 50000,
            "revenue_per_user_per_minute": 0.03
        },
        "checkout-gateway": {
            "total_users": 30000,
            "revenue_per_user_per_minute": 0.04
        }
    }


def _find_scenario_dir(service: str) -> str:
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    data_dir: str = os.path.join(base_dir, "data")

    service_to_scenario: Dict[str, str] = {
        "payment-api": "scenario_1",
        "order-processor": "scenario_2",
        "checkout-gateway": "scenario_3"
    }

    scenario: str = service_to_scenario.get(service)
    if not scenario:
        return ""

    scenario_path: str = os.path.join(data_dir, scenario)
    if os.path.isdir(scenario_path):
        return scenario_path

    return ""


def _find_live_log_path(service: str) -> str:
    base_dir: str = os.path.dirname(os.path.abspath(__file__))
    live_dir: str = os.path.join(base_dir, "data", "live_logs")
    safe_service: str = service.replace("/", "_").replace(" ", "_")
    for ext in ("json", "jsonl", "ndjson", "log", "txt"):
        candidate: str = os.path.join(live_dir, f"{safe_service}.{ext}")
        if os.path.exists(candidate):
            return candidate
    return ""
