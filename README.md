# AI Operations Command Center

Autonomous Multi-Agent Incident Response System

## Problem

Enterprise incident response is slow and manual. Engineers spend hours sifting through logs, metrics, and deployment data to determine what went wrong and why.

## Solution

The AI Operations Command Center is a multi-agent system that orchestrates autonomous investigation across logs, metrics, and deployments. Six specialized agents work in concert to:

1. **Incident Commander** - Load incident data from multiple sources
2. **Log Analysis** - Detect error patterns and anomalies
3. **Metrics Analysis** - Identify metric spikes and correlations
4. **RCA Analysis** - Synthesize findings into root cause hypotheses
5. **Business Impact** - Calculate affected users and revenue impact
6. **Executive Summary** - Generate engineering and executive reports

Powered by LangGraph orchestration and Claude API for intelligent synthesis.

## Architecture

```
START → Incident Commander → Log Analysis + Metrics Analysis → 
RCA Analysis → Business Impact → Executive Summary → END
```

Each agent:
- Takes an IncidentState as input
- Performs specialized analysis
- Augments the state with findings
- Logs invocation details for audit trail
- Returns enriched state to next agent

## Quick Start

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

pytest
python app.py
```

Then open http://localhost:8000 in a browser.

## Demo

1. Click "Scenario 1: DB Pool Exhaustion" button
2. Wait for analysis to complete (~2 seconds)
3. View incident card with root cause confidence, affected users, revenue impact
4. Click "View Full Details" to see engineering summary, evidence, and recovery recommendations

## Project Structure

```
incident-response-system/
├── agents/                          # Multi-agent system
│   ├── __init__.py                 # IncidentState dataclass
│   ├── incident_commander.py       # Data loader & orchestrator
│   ├── log_analysis.py             # Error pattern detection
│   ├── metrics_analysis.py         # Metric spike detection
│   ├── rca_analysis.py             # Root cause hypothesis
│   ├── business_impact.py          # User & revenue impact
│   └── executive_summary.py        # Report generation
├── data/                           # Realistic mock scenarios
│   ├── scenario_1/                # DB Pool (50 → 30 connection limit)
│   ├── scenario_2/                # Memory Leak (500MB → 2000MB)
│   └── scenario_3/                # Cascading Failure (timeout cascade)
├── frontend/                       # Vanilla HTML/CSS/JS
│   ├── dashboard.html             # Incident list & triggers
│   ├── incident_detail.html       # Full analysis view
│   └── styles.css                 # Professional UI
├── tests/                         # pytest suite
│   ├── test_scenario_1.py
│   ├── test_scenario_2.py
│   ├── test_scenario_3.py
│   └── test_all_scenarios.py
├── app.py                         # FastAPI backend
├── mock_data.py                   # Data loader
├── requirements.txt               # Dependencies
├── .env.example                   # Configuration template
├── README.md                      # This file
└── SETUP_INSTRUCTIONS.md         # Detailed setup guide
```

## Testing

```bash
pytest                    # Run all tests
pytest -v               # Verbose output
pytest tests/test_scenario_1.py  # Single scenario
```

All three scenarios pass with:
- Log anomalies detected > 0
- Metric anomalies detected > 0
- Root cause confidence > 0.60
- Affected users > 0
- Complete audit trail

## Results

### Scenario 1: Database Connection Pool Exhaustion
- **Root Cause**: Pool size reduced from 50 to 30 in recent deployment
- **Confidence**: 85%
- **Affected Users**: 1,400
- **Revenue Impact**: $700/minute
- **Key Indicators**: Connection timeout errors, CPU spike 23% → 78%, error_rate 0.1% → 68%

### Scenario 2: Memory Leak
- **Root Cause**: Code regression causing memory not to be released
- **Confidence**: 72%
- **Affected Users**: 5,000
- **Revenue Impact**: $1,500/minute
- **Key Indicators**: GC pause times increasing, memory 500MB → 2000MB (no deployment)

### Scenario 3: Cascading Failure
- **Root Cause**: Timeout calling downstream payment-api service
- **Confidence**: 80%
- **Affected Users**: 3,000
- **Revenue Impact**: $1,200/minute
- **Key Indicators**: Timeout errors, latency spike 55ms → 8000ms, error_rate spike to 85%

## Tech Stack

- **Python 3.10+** - Core language
- **LangGraph 0.0.33** - Agent orchestration
- **FastAPI 0.104.1** - REST backend
- **Claude 3.5 Sonnet** - AI synthesis (API key in .env)
- **Pydantic 2.4.2** - Data validation
- **pytest 7.4.3** - Testing
- **Vanilla HTML/CSS/JavaScript** - Frontend (no build step)

## API Endpoints

- `POST /api/incidents/trigger` - Trigger incident analysis
  ```json
  {
    "timestamp": "2026-07-07T14:32:15Z",
    "service": "payment-api",
    "alert_description": "Connection pool exhaustion",
    "severity": "critical"
  }
  ```

- `GET /api/incidents/{incident_id}` - Retrieve completed analysis
- `GET /api/health` - Health check
- `GET /` - Dashboard HTML
- `GET /incident/{incident_id}` - Incident detail HTML

## Next Steps

- Implement live Datadog/Splunk integration
- Add Claude API integration for natural language RCA generation
- Create automated remediation playbooks
- Build Slack/PagerDuty notification system
- Implement multi-step recovery automation
- Add historical incident search and learning

## License

MIT

## Authors

Amulya Gupta & Anujay
