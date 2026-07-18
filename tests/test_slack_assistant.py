from agents.slack_assistant import (
    SlackAssistant,
    incident_blocks,
    parse_command,
    scenario_from_text,
    summarize_incident,
)


def test_parse_command_defaults_to_help() -> None:
    assert parse_command("") == ("help", "")
    assert parse_command("trigger db") == ("trigger", "db")
    assert parse_command("ask abc123 what happened?") == ("ask", "abc123 what happened?")


def test_scenario_from_text_maps_demo_scenarios() -> None:
    assert scenario_from_text("db")["service"] == "payment-api"
    assert scenario_from_text("memory")["service"] == "order-processor"
    assert scenario_from_text("timeout")["service"] == "checkout-gateway"


def test_summarize_incident_handles_pending_rca() -> None:
    summary = summarize_incident(
        {
            "incident_id": "abcdef123456",
            "service": "payment-api",
            "current_status": "investigating",
            "lifecycle_status": "investigating",
            "alert_description": "Database connection pool exhaustion detected",
        }
    )
    assert "payment-api" in summary
    assert "Pending analysis" in summary


def test_incident_blocks_include_governance_actions() -> None:
    assistant = SlackAssistant()
    blocks = incident_blocks(
        {
            "incident_id": "abcdef123456",
            "service": "payment-api",
            "severity": "critical",
            "current_status": "complete",
            "lifecycle_status": "needs_human_review",
            "alert_description": "Database connection pool exhaustion detected",
            "root_cause": {"hypothesis": "Pool size reduced", "confidence": 0.85},
            "recovery_recommendations": ["Restore pool size", "Restart workers"],
        },
        assistant,
    )
    action_ids = {
        element.get("action_id")
        for block in blocks
        for element in block.get("elements", [])
    }
    assert "aioc_accept_rca" in action_ids
    assert "aioc_request_more_data" in action_ids
