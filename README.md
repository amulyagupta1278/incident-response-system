# AI Operations Command Center

Autonomous Multi-Agent Incident Response System

> See **[SUBMISSION.md](SUBMISSION.md)** for a project overview and key differentiators, **[ARCHITECTURE.md](ARCHITECTURE.md)** for the technical deep-dive and diagrams, and **[WORKFLOW.md](WORKFLOW.md)** for a step-by-step system walkthrough.

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

Powered by LangGraph orchestration with LLM reasoning from **OpenAI (ChatGPT / GPT-4o)**. The router, RCA, and report-writing agents all reason with the LLM; every agent has a deterministic fallback so the system also runs fully offline in heuristic mode.

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
# paste your OPENAI_API_KEY into .env

pytest
python app.py
```

Then open http://localhost:8000 in a browser.

## Demo

1. Click "Scenario 1: DB Pool Exhaustion" button
2. Wait for analysis to complete (~2 seconds)
3. View incident card with root cause confidence, affected users, revenue impact
4. Click "View Full Details" to see engineering summary, evidence, and recovery recommendations

## Real Logs

The app now prefers real logs before demo scenarios. Supported formats:

- `.json` array, or object with `logs`, `records`, `events`, or `data`
- `.jsonl` / `.ndjson`
- plain `.log` / `.txt` project logs
- common Apache-style access logs; HTTP 5xx lines become `ERROR`

Use one of these paths:

```bash
# Per incident
curl -s -X POST http://127.0.0.1:8011/api/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-07-07T14:32:15Z",
    "service": "my-service",
    "alert_description": "Live log analysis",
    "severity": "critical",
    "logs_path": "data/live_logs/my-service.log"
  }'

# Or global source for every trigger
LIVE_LOGS_PATH=data/live_logs/my-service.log python -m uvicorn app:app --host 127.0.0.1 --port 8011
```

Auto-discovery also works when a file exists at:

```text
data/live_logs/<service>.json
data/live_logs/<service>.jsonl
data/live_logs/<service>.ndjson
data/live_logs/<service>.log
data/live_logs/<service>.txt
```

Kaggle workflow:

```bash
# Requires Kaggle credentials at ~/.kaggle/kaggle.json
mkdir -p data/kaggle data/live_logs
kaggle datasets download -d <owner>/<dataset> -p data/kaggle --unzip
cp data/kaggle/<log-file>.log data/live_logs/my-service.log
```

Large Kaggle/project logs should stay out of git. `data/live_logs/` and `data/kaggle/` are ignored.

## Railway Deployment

This project is Railway-first for production because the backend runs FastAPI plus long-running agent jobs. `railway.toml` starts the app with:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port $PORT
```

Set these Railway variables:

```text
OPENAI_API_KEY=<server-side key>
OPENAI_MODEL=gpt-4o
LLM_STRICT_MODE=true
ALLOWED_ORIGINS=https://your-nextjs-app.vercel.app,https://your-railway-domain.up.railway.app
BROWSER_ALLOWED_ORIGINS=https://your-nextjs-app.vercel.app
PUBLIC_BASE_URL=https://your-railway-domain.up.railway.app
ADMIN_API_KEY=<strong-admin-provisioning-key>
INGEST_API_KEYS=demo-project:<replace-with-strong-key>
BROWSER_PUBLIC_KEYS=demo-project:<replace-with-public-browser-key>
APP_ENV=production
DEMO_MODE=false
GATEWAY_WORKER_ENABLED=true
RAW_PAYLOAD_RETENTION_DAYS=0
CONNECTOR_SIGNATURES_REQUIRED=true
GITHUB_WEBHOOK_SECRETS=demo-project:<github-webhook-secret>
GITHUB_WEBHOOK_SECRET=<github-webhook-secret>
SUPABASE_WEBHOOK_SECRETS=demo-project:<supabase-webhook-secret>
SUPABASE_WEBHOOK_SECRET=<supabase-webhook-secret>
```

For one demo project, env keys are fine. For many real projects, use admin provisioning instead of adding every project to `.env`.

Next.js can be a separate frontend. Keep AI/RAG/OpenAI and webhook secrets on this Railway backend, then call `/api/v1/*` from Next.js with a server-side bearer key.

Security model:

- Server endpoints require `Authorization: Bearer <project-api-key>`.
- Browser ingest uses a separate public write-only browser key and origin allowlist.
- GitHub and Supabase webhooks should send HMAC SHA-256 signatures in production.
- Connector delivery IDs are stored to reject replayed webhook deliveries.
- All incident reads and Ask calls are project-scoped.
- Legacy demo routes `/api/incidents/*` are disabled in production unless `DEMO_MODE=true`.

## Universal Ingest API

Connect any app, GitHub repo, Supabase project, or backend by sending evidence:

Create a new project without editing `.env`:

```bash
curl -s -X POST "$PUBLIC_BASE_URL/api/v1/projects" \
  -H "Authorization: Bearer <admin-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "checkout-prod",
    "name": "Checkout Production"
  }'
```

The response returns one-time credentials: server API key, browser public key, GitHub webhook secret, Supabase webhook secret, and project-scoped setup URLs. Store those values in the connected app/GitHub/Supabase secret manager. The normal project `.env` does not need `checkout-prod` entries after this.

Rotate any project credential without editing `.env`:

```bash
curl -s -X POST "$PUBLIC_BASE_URL/api/v1/projects/checkout-prod/rotate" \
  -H "Authorization: Bearer <admin-api-key>" \
  -H "Content-Type: application/json" \
  -d '{"credential_type": "github_webhook_secret"}'
```

Supported `credential_type` values: `server_api_key`, `browser_public_key`, `github_webhook_secret`, `supabase_webhook_secret`. Server/browser rotations revoke old keys. Webhook rotations replace stored connector secrets, so old signatures stop working.

```bash
curl -s -X POST "$PUBLIC_BASE_URL/api/v1/events" \
  -H "Authorization: Bearer <project-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "log",
    "source": "custom",
    "service": "checkout-api",
    "environment": "production",
    "payload": {"level": "ERROR", "message": "checkout timeout"}
  }'
```

Then create incident job:

```bash
curl -s -X POST "$PUBLIC_BASE_URL/api/v1/incidents" \
  -H "Authorization: Bearer <project-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "checkout-api",
    "severity": "critical",
    "alert_description": "checkout timeout spike"
  }'
```

Useful production endpoints:

- `POST /api/v1/events` - universal evidence ingest
- `POST /api/v1/projects` - admin-only project provisioning
- `POST /api/v1/projects/{project_id}/rotate` - admin-only credential rotation
- `POST /api/v1/service-config` - project revenue/user config
- `GET /api/v1/connectors/setup` - project-scoped copy-paste connector setup
- `GET /api/v1/readiness` - project-scoped runtime readiness and security audit
- `GET /api/v1/audit` - project-scoped audit trail for admin, ingest, connector, and Ask actions
- `POST /api/v1/incidents` - queue incident analysis
- `GET /api/v1/incidents/{incident_id}` - persistent incident state
- `POST /api/v1/incidents/{incident_id}/ask` - cited RAG answer
- `POST /api/v1/connectors/github/{project_id}/webhook` - direct GitHub webhook evidence
- `POST /api/v1/connectors/github/webhook` - bearer-auth GitHub relay evidence
- `POST /api/v1/connectors/supabase/{project_id}/webhook` - direct Supabase webhook evidence
- `POST /api/v1/connectors/supabase/webhook` - bearer-auth Supabase relay evidence

Connector setup helper:

```bash
curl -s "$PUBLIC_BASE_URL/api/v1/connectors/setup" \
  -H "Authorization: Bearer <project-api-key>"
```

This returns project-scoped endpoint URLs, browser snippet, webhook header contract, and production security checklist. It never returns stored secret values.

Runtime readiness helper:

```bash
curl -s "$PUBLIC_BASE_URL/api/v1/readiness" \
  -H "Authorization: Bearer <project-api-key>"
```

This returns `ready`, `degraded`, or `blocked` plus non-secret checks for OpenAI mode, direct GitHub/Supabase secrets, browser origin allowlist, demo-route status, payload limit, rate limit, and raw payload retention.

Audit trail helper:

```bash
curl -s "$PUBLIC_BASE_URL/api/v1/audit?limit=50" \
  -H "Authorization: Bearer <project-api-key>"
```

Audit events are project-scoped and redact secret-like fields. They record project provisioning, credential rotation, evidence ingestion, connector webhooks, auto-created incidents, business config changes, and Ask responses with citation counts.

For GitHub UI, set Payload URL to `/api/v1/connectors/github/{project_id}/webhook`, content type to `application/json`, Secret to the matching project secret from `GITHUB_WEBHOOK_SECRETS`, and select push/pull request/deployment/release events as needed.

For Supabase or Supabase-adjacent emitters, send signed JSON to `/api/v1/connectors/supabase/{project_id}/webhook` with `X-Supabase-Signature: sha256=<hmac>` and a stable `X-Supabase-Delivery` or `X-Request-ID`. Use `SUPABASE_WEBHOOK_SECRETS=project-id:secret` for project-specific secrets.

## Hackathon Acceptance Smoke

Run one deterministic end-to-end demo without external services:

```bash
python scripts/hackathon_acceptance_smoke.py
```

It proves:

- project-scoped connector setup
- server API key required
- browser key is write-only
- browser origin allowlist blocks untrusted sites
- GitHub webhook HMAC is required
- duplicate webhook delivery is rejected
- GitHub deploy evidence links to browser/API failure burst
- incident auto-creates with evidence chunks and graph edges
- Ask answer returns citations even in offline retrieval-fallback mode

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
- **Confidence**: ~90%
- **Affected Users**: 9,520
- **Revenue Impact**: $476/minute
- **Key Indicators**: Connection timeout errors, CPU spike 23% → 78%, error_rate 0.1% → 68%
- **Impact Verification**: `verified_estimate` when service config and error_rate metric are present; report includes formula, bounds, sources, and data gaps.

### Scenario 2: Memory Leak
- **Root Cause**: Code regression causing memory not to be released
- **Confidence**: 72%
- **Affected Users**: 7,500
- **Revenue Impact**: $225/minute
- **Key Indicators**: GC pause times increasing, memory 500MB → 2000MB (no deployment)

### Scenario 3: Cascading Failure
- **Root Cause**: Timeout calling downstream payment-api service
- **Confidence**: 80%
- **Affected Users**: 25,500
- **Revenue Impact**: $1,020/minute
- **Key Indicators**: Timeout errors, latency spike 55ms → 8000ms, error_rate spike to 85%

## Tech Stack

- **Python 3.10+** - Core language
- **LangGraph** - Agent orchestration
- **FastAPI** - REST backend (analysis runs as a background task; the UI polls live progress)
- **OpenAI (gpt-4o)** - LLM reasoning, configured via `OPENAI_API_KEY` in `.env`
- **Pydantic** - Data validation
- **pytest** - Testing
- **Vanilla HTML/CSS/JavaScript** - Frontend (no build step)

## API Endpoints

- `POST /api/incidents/trigger` - Start incident analysis (returns immediately with `current_status: "investigating"`; agents run in the background)
  ```json
  {
    "timestamp": "2026-07-07T14:32:15Z",
    "service": "payment-api",
    "alert_description": "Connection pool exhaustion",
    "severity": "critical",
    "logs_path": "data/live_logs/payment-api.log"
  }
  ```

- `GET /api/incidents` - List all incidents (newest first)
- `GET /api/incidents/{incident_id}` - Poll live analysis state / retrieve completed analysis
- `POST /api/incidents/{incident_id}/ask` - RAG Q&A over incident evidence. Returns `answer`, `source`, `citations`, and `retrieved_chunks`.
- `GET /api/config` - Active LLM provider and model (`heuristic` when no key is set)
- `GET /api/graph` - Mermaid rendering of the agent graph
- `GET /api/health` - Health check
- `GET /` - Dashboard HTML
- `GET /incident/{incident_id}` - Incident detail HTML

## Next Steps

- Implement live Datadog/Splunk integration
- Add Codex-style code-change analysis for deployment diffs
- Create automated remediation playbooks
- Build Slack/PagerDuty notification system
- Implement multi-step recovery automation
- Add historical incident search and learning

## License

MIT

## Authors

Amulya Gupta & Anujay
