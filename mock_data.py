import json
import os
from typing import Any, List, Dict


def load_logs(service: str, timestamp: str) -> List[Dict[str, Any]]:
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
