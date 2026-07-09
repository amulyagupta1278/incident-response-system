import json
from pathlib import Path

from agents import IncidentState
from agents.incident_commander import incident_commander
from agents.log_ingestion import load_log_file


def test_load_jsonl_logs(tmp_path: Path) -> None:
    source: Path = tmp_path / "payment-api.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-07-07T14:32:16Z",
                        "level": "ERROR",
                        "service": "payment-api",
                        "message": "Connection timeout while acquiring database connection from pool",
                    }
                ),
                json.dumps(
                    {
                        "time": "2026-07-07T14:32:17Z",
                        "severity": "WARN",
                        "msg": "Connection pool wait time increasing",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    logs = load_log_file(source, "payment-api")

    assert len(logs) == 2
    assert logs[0]["level"] == "ERROR"
    assert logs[0]["error_type"] == "timeout"
    assert logs[1]["level"] == "WARNING"


def test_load_plain_project_logs(tmp_path: Path) -> None:
    source: Path = tmp_path / "app.log"
    source.write_text(
        "\n".join(
            [
                "2026-07-07T14:32:15Z INFO request started",
                "2026-07-07T14:32:16Z ERROR connection pool exhausted",
                '10.0.0.1 - - [07/Jul/2026:14:32:17 +0000] "GET /checkout HTTP/1.1" 503 42',
            ]
        ),
        encoding="utf-8",
    )

    logs = load_log_file(source, "checkout-gateway")

    assert len(logs) == 3
    assert logs[1]["level"] == "ERROR"
    assert logs[1]["error_type"] == "connection_error"
    assert logs[2]["level"] == "ERROR"


def test_incident_commander_uses_explicit_log_source(tmp_path: Path) -> None:
    source: Path = tmp_path / "live.log"
    source.write_text("2026-07-07T14:32:16Z ERROR timeout talking to database\n", encoding="utf-8")
    state = IncidentState(
        incident_id="live-logs",
        timestamp="2026-07-07T14:32:16Z",
        alert_description="Live log test",
        service="unknown-service",
        severity="critical",
        log_source_path=str(source),
    )

    state = incident_commander(state)

    assert len(state.raw_logs) == 1
    assert state.raw_logs[0]["error_type"] == "timeout"
    assert state.agent_invocations[0]["data_points"]["log_source"] == str(source)
