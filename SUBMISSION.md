# AI Operations Command Center — Project Overview

**Autonomous multi-agent incident response.** Six specialized agents, orchestrated by a real LangGraph state machine, investigate a production incident end-to-end — logs, metrics, deployments, root cause, business impact, executive reporting — and know when their own answer isn't good enough yet.

## The Problem

When a production alert fires, a human on-call engineer spends the first 15–30 minutes doing the same mechanical thing every time: pull logs, pull metrics, check what deployed recently, cross-reference by eye, guess a hypothesis, argue about confidence, and only then start writing the incident summary leadership actually reads. That triage work is high-volume, evidence-driven, and pattern-based — exactly the kind of task an agentic system should own, while the *decision to act* stays with a human.

## What Was Built

- **6 specialized agents** (Incident Commander, Log Analysis, Metrics Analysis, RCA, Business Impact, Executive Summary) plus a **Router agent** that decides what happens next, orchestrated by **LangGraph** as a genuine conditional state machine — not a fixed pipeline.
- **A confidence-gated retry loop**: if the RCA agent isn't confident (< 70%), the system autonomously fetches deeper evidence and re-investigates, up to a bounded number of iterations. This is the difference between "call an LLM once" and an actual agent that knows when to keep working.
- **Evidence-graded root cause analysis**: every claim must cite an exact log line, metric delta, or deployment change — no unfounded assertions. The model must also name alternative hypotheses it *ruled out* and why, and explicitly reason about deployment-timing correlation.
- **Persistent incident memory**: every resolved incident is remembered and matched against new ones by root cause and anomaly signature — the system says "this looks like incident #3 from last week" instead of investigating from zero every time.
- **Human-in-the-loop approval gates**: the system investigates autonomously end-to-end, but every recovery action requires an explicit human Approve/Reject before it's considered actionable. Full audit trail, stamped into the exported postmortem.
- **Fully graceful LLM degradation**: every LLM-backed agent has a deterministic heuristic fallback that produces structurally identical output. The system runs correctly with **zero API keys** — this is the same code path a production deployment would fall back to during an LLM provider outage.
- **Live streaming UX**: the dashboard polls a running LangGraph stream, so partial results (root cause, then impact, then summary) appear progressively while agents are still working, rather than a single static report at the end.
- **Grounded natural-language Q&A** and **one-click Markdown postmortem export**, both built directly on the same incident record, with no separate data pipeline.

## Key Differentiators

A common approach to automated incident response is either a single LLM prompt wrapped in a UI, or a fixed pipeline that always runs the same steps regardless of what it finds. This system is neither.

| | Common approach | This system |
|---|---|---|
| Control flow | Linear script, same steps every run | LangGraph state machine with conditional routing and a bounded retry loop |
| When confidence is low | Returns the low-confidence answer anyway | Autonomously gathers more evidence and re-investigates (bounded by a max-iteration limit) |
| Evidence | Model generates a plausible-sounding cause | Must cite exact log/metric/deploy data; must name and rule out alternative hypotheses |
| LLM unavailable | System fails or degrades silently | Deterministic heuristic fallback — identical output structure, fully offline-capable |
| Memory | None — every incident starts from zero | Persists and matches past incidents by root cause and anomaly signature |
| Automation boundary | Model output is treated as final | Investigation is autonomous; every recovery action requires explicit human approval before being actionable |
| Observability | Final answer only | Full live-streaming audit trail with per-step reasoning and source (`llm:gpt-4o` / `heuristic` / `guardrail`) |

## Architecture at a Glance

```
Alert → Router (LangGraph) ⇄ [Load Data → Log Analysis + Metrics Analysis → RCA]
                                          │
                              confidence < 0.7? → Request More Data → back to RCA
                                          │
                              confidence OK → Business Impact → Executive Summary → Done
```

Full diagrams, state machine, and design rationale: **[ARCHITECTURE.md](ARCHITECTURE.md)**
Step-by-step system behavior and walkthrough: **[WORKFLOW.md](WORKFLOW.md)**

## Results (3 realistic scenarios, verified by the pytest suite)

| Scenario | Root Cause | Confidence | Affected Users | Revenue Impact |
|---|---|---|---|---|
| DB Pool Exhaustion | Pool size reduced 50→30 in recent deploy | 85% | 1,400 | $700/min |
| Memory Leak | Code regression, memory not released | 72% | 5,000 | $1,500/min |
| Cascading Failure | Timeout calling downstream payment-api | 80% | 3,000 | $1,200/min |

## Try It

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # optional — paste OPENAI_API_KEY for LLM reasoning
pytest                     # all scenarios pass, LLM layer included
python app.py               # open http://localhost:8000
```

Click a scenario button, then open the incident while it's still running — watch the root cause, evidence, and confidence appear live before the full report finishes generating.

## Tech Stack

Python · LangGraph · FastAPI (async streaming background task) · OpenAI `gpt-4o` (strict JSON schema mode) · typed state dataclass · pytest · vanilla HTML/CSS/JS (no build step required).

## Roadmap

- Live Datadog/Splunk/PagerDuty ingestion in place of scenario fixtures
- Embedding-based incident memory (swap-in ready — `find_similar_incidents` already isolates the matching interface)
- Automated remediation execution for pre-approved, low-risk playbooks
- Multi-service blast-radius correlation (cascading incidents across services)

## Team

Amulya Gupta & Anujay
