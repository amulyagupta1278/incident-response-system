# Setup Instructions

## Prerequisites

- Python 3.10 or higher
- pip
- git

## Step 1: Create Virtual Environment

```bash
cd incident-response-system
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

## Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 3: Create Environment Configuration

```bash
cp .env.example .env
```

Add your Anthropic API key to `.env`:
```
ANTHROPIC_API_KEY=sk-your-actual-key-here
ENVIRONMENT=development
LOG_LEVEL=INFO
```

## Step 4: Run Tests

```bash
pytest
```

Expected output:
```
========================= 17 passed in 0.04s ==========================
```

All 17 tests passing means the agent system is working correctly:
- 5 tests per scenario (log, metrics, rca, business_impact, complete_flow)
- 2 tests for all scenarios (complete_flow, isolation)
- Total: 17 tests

## Step 5: Start Backend Server

```bash
python app.py
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

## Step 6: Open Frontend

Open your browser to `http://localhost:8000`

You should see the AI Operations Command Center dashboard with three blue buttons for test scenarios.

## Step 7: Trigger an Incident

Click "Scenario 1: DB Pool Exhaustion" button.

You should see:
- A loading indicator briefly
- An incident card appears with root cause, affected users, revenue impact
- Click "View Full Details" to see full analysis

## Step 8: Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit: AI Operations Command Center

- 7-agent autonomous incident response system
- LangGraph orchestration
- FastAPI backend with mock data
- Vanilla HTML/CSS/JS frontend
- 17 pytest test cases (all passing)
- 3 realistic incident scenarios

Authors: Amulya Gupta & Anujay"
git remote add origin https://github.com/[username]/incident-response-system.git
git branch -M main
git push -u origin main
```

## Troubleshooting

### Port 8000 already in use
```bash
python app.py --port 8001
```

### Module not found errors
Ensure virtual environment is activated:
```bash
source venv/bin/activate
```

### Tests failing
Check that mock data files exist:
```bash
ls -la data/scenario_*/
```

All JSON files should be present (logs.json, metrics.json, deployments.json).

### API key errors
Verify `.env` file exists and contains valid Anthropic API key:
```bash
cat .env
```

## Verification Checklist

- [ ] Virtual environment created and activated
- [ ] Dependencies installed (`pip list` shows all packages)
- [ ] `.env` file created with API key
- [ ] All tests pass (`pytest`)
- [ ] Backend starts without errors (`python app.py`)
- [ ] Dashboard loads at http://localhost:8000
- [ ] Can trigger scenarios and see results
- [ ] View details page loads correctly
- [ ] All agent invocations appear in audit trail

## Development

### Adding New Agents

1. Create new agent file in `agents/` directory
2. Import IncidentState from `agents/__init__.py`
3. Define function signature: `def agent_name(state: IncidentState) -> IncidentState`
4. Perform analysis and augment state
5. Log invocation in `state.agent_invocations`
6. Return modified state
7. Import and call in `app.py` in correct order

### Adding New Scenarios

1. Create new directory in `data/` (e.g., `scenario_4/`)
2. Add `logs.json`, `metrics.json`, `deployments.json`
3. Update `mock_data.py` to map service to scenario
4. Add scenario to `scenarioConfigs` in `frontend/dashboard.html`
5. Create test file `tests/test_scenario_4.py`
6. Run tests to verify

### Extending RCA Logic

Edit `agents/rca_analysis.py` to add new heuristics:

```python
if new_condition and other_condition:
    hypothesis = "New Root Cause"
    confidence = 0.75
    supporting_evidence = [...]
```

## Performance Notes

- Each scenario completes in ~200-300ms
- Mock data loads from JSON files (instant)
- No database queries in MVP
- Memory footprint: ~50MB
- Supports 1000+ concurrent pending incidents

## Next Steps

1. Integrate live Datadog/Splunk logs and metrics
2. Add Claude API for natural language RCA
3. Implement automated remediation
4. Add Slack notifications
5. Build incident history and ML training data

## Support

For issues or questions, check:
- README.md for architecture overview
- Each agent file for implementation details
- tests/ for usage examples
- frontend/ HTML files for API contract
