# Master Workflow

The end-to-end life of one incident, from alert to postmortem — what happens, in what order, in which file. For system design rationale, see [ARCHITECTURE.md](ARCHITECTURE.md). For a project overview, see [SUBMISSION.md](SUBMISSION.md).

## 0. Setup (once)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional: paste OPENAI_API_KEY for LLM reasoning
pytest                       # 3 scenarios + LLM-layer tests should pass
python app.py                # http://localhost:8000
```

No API key → system runs entirely on deterministic heuristics. This is the same code path every LLM-backed agent falls back to on any API failure, so it applies equally in production and in local runs.

## 1. Incident Trigger

**Actor:** dashboard button click, or `POST /api/incidents/trigger` directly.

```json
{"timestamp": "2026-07-07T14:32:15Z", "service": "payment-api",
 "alert_description": "Connection pool exhaustion", "severity": "critical"}
```

`app.py::trigger_incident`:
1. Mints `incident_id`, builds a fresh `IncidentState`.
2. Stores an initial record with `current_status: "investigating"` and returns it **immediately** (sub-100ms) — the caller never blocks on analysis.
3. Posts to the war room ("🚨 Incident opened... agents dispatched") if `WAR_ROOM_WEBHOOK_URL` is set.
4. Fires `asyncio.create_task(_run_analysis(...))` — analysis runs in the background from here on.

## 2. Autonomous Investigation (background)

`_run_analysis` streams the compiled LangGraph, writing the incident record to the in-memory store after **every** node transition — this is what allows the dashboard to render partial progress instead of a single result once everything finishes.

| Step | Node | What happens | State written |
|---|---|---|---|
| 1 | `route_next_action` | Router picks `load_data` (only legal first move) | `next_action` |
| 2 | `load_data` | Fetches logs/metrics/deployments for the service from the matching scenario | `raw_logs`, `raw_metrics`, `deployment_changes` |
| 3 | `analyze_logs` | Scans for timeout / connection-pool / GC-pause patterns | `log_anomalies` |
| 4 | `analyze_metrics` | Diffs incident-window metrics vs. baseline, flags >50% swings | `metric_anomalies` |
| 5 | `run_rca` | LLM (or heuristic) forms a hypothesis with confidence, cited evidence, ruled-out alternatives, deploy correlation | `root_cause`, `rca_confidence` |
| 5a | *(conditional)* `request_more_data` | **Only if confidence < 0.70** and iterations remain: re-fetches deeper data, re-runs log analysis, sends control back to `run_rca` | `raw_logs` (refreshed), loop continues |
| 6 | `calculate_business_impact` | Converts affected-user error rate into revenue-per-minute using per-service config | `affected_users`, `estimated_revenue_impact_per_minute` |
| 7 | `generate_summary` | Deterministic engineering/exec summary, then LLM rewrite grounded in the finished analysis; default recovery recommendations are hypothesis-specific | `engineering_summary`, `executive_summary`, `recovery_recommendations` |
| — | *(side effect during step 5)* | `find_similar_incidents` checks `data/incident_memory.json` for matching past root causes / anomaly signatures | `similar_incidents` |
| — | *(side effect, first time root_cause appears)* | War-room post: "🔍 Root cause identified... (N% confidence)" | — |
| 8 | `complete` | Graph ends; `record_incident` persists this investigation into memory for future matching | — |
| — | *(final side effect)* | War-room post: "✅ Investigation complete... N users affected, $X/min. Full report: /incident/{id}" | — |

Every node also appends a structured entry to `agent_invocations` (`agent`, `timestamp`, `action`, `source` — Codex runtime source such as `llm:gpt-4o` / `heuristic_fallback` / `deterministic` / `guardrail`, `reasoning`). This list **is** the audit trail rendered in the UI and the postmortem export.

## 3. Live Observation (client side)

`incident_detail.html` polls `GET /api/incidents/{id}` every second. Each poll can show:

- **Status banner** cycling through `data_loaded → logs_analyzed → metrics_analyzed → rca_completed → (requesting_deeper_analysis → rca_completed, if looped) → impact_calculated → complete`
- **Root Cause Analysis** panel — hypothesis, confidence meter, evidence list, ruled-out alternatives, deploy-correlation callout, as soon as `run_rca` completes (before impact/summary even start)
- **Seen Before** panel — populated the moment memory finds a match
- **Agent Reasoning & Audit Trail** — every invocation so far, growing live

This means an observer watching the dashboard sees partial results appear progressively, not a single report at the end.

## 4. Interactive Q&A

`POST /api/incidents/{id}/ask` with `{"question": "..."}`. `agents/qa.py::answer_question` uses `agents/rag.py` to build incident evidence chunks, retrieve the most relevant chunks with Codex/OpenAI embeddings, and answer only from those retrieved chunks with citations. If the retrieved evidence is insufficient, the answer must say what is missing. With no LLM configured, it falls back to keyword-routed heuristic answers (root cause / impact / deployments / logs / metrics / similar incidents / recommendations). Available the instant an incident exists, even mid-investigation — ask "what's the status?" while agents are still working.

## 5. Human Approval Gate

Each recovery recommendation renders with **Approve** / **Reject** buttons. `POST /api/incidents/{id}/remediation/{step}/decision` records the decision with a timestamp; nothing is auto-executed. This is the one place the system deliberately refuses to be autonomous — investigation is fully agentic, action is always human-gated.

## 6. Postmortem Export

`GET /api/incidents/{id}/postmortem` renders a complete Markdown postmortem on demand: executive summary, root cause with confidence, supporting evidence, ruled-out alternatives, business impact, recovery actions annotated with their approval status, related past incidents, and the full investigation timeline (`agent_invocations`, formatted as a bulleted, timestamped log). One document that starts as a live dashboard and ends as something you'd paste into a real incident channel.

## Walkthrough (~3 minutes)

1. **Verify the graph is generated, not drawn** — open `/api/graph`; the Mermaid diagram is rendered live from the compiled `StateGraph`.
2. **Trigger Scenario 1** (DB pool exhaustion) from the dashboard. The status banner moves through stages in real time.
3. **Open the incident detail page while it's still running** — root cause and evidence appear before the rest of the analysis finishes, showing the live-streaming behavior described above.
4. **Trigger Scenario 2** (memory leak, no recent deployment) for comparison — same six agents, a structurally different conclusion (no deploy correlation, different recovery playbook), showing the reasoning is data-driven rather than fixed per scenario.
5. **Ask a question** in the chat box — e.g. "why do we think this is a memory leak and not a deployment?" — and get an answer grounded in that incident's own evidence.
6. **Reject one recommendation, approve another** — this exercises the human-approval gate; download the postmortem and confirm the decisions are stamped into the document.
7. **Unset `OPENAI_API_KEY` and restart**, then re-run a scenario — identical structure with heuristic reasoning, confirming the fallback path works independently of the LLM.

## Failure Modes — Handled, Not Hoped Around

| Failure | Behavior |
|---|---|
| No `OPENAI_API_KEY` set | Every LLM-backed agent silently uses its heuristic path; `GET /api/config` reports `llm_provider: "heuristic"` |
| Codex/OpenAI call raises (timeout, rate limit, bad JSON) | Caught per-agent, logged to console, falls back to heuristic result — investigation never halts |
| LLM router picks an action outside the legal set | Guardrail overrides with the deterministic choice; logged with `source: "guardrail"` for visibility, not silently swallowed |
| RCA confidence stays low after `max_iterations` | Loop exits anyway (bounded), proceeds with best-available hypothesis rather than hanging forever |
| War-room webhook unreachable | `post_war_room` catches and logs; never raises into the investigation |
| Unknown `incident_id` on any endpoint | `404` with a clear detail message |
