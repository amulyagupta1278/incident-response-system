# Open-Source Production Variants

This project should stay provider-neutral. The current mock provider remains
the default, while production adapters can target these open-source systems.

| Capability | Open-source default | Adapter intent |
|---|---|---|
| Traces and telemetry standard | OpenTelemetry | Export agent spans and ingest service traces |
| Metrics | Prometheus | Query time-series metrics and alert context |
| Alerts and routing | Alertmanager | Receive grouped incidents and silences |
| Logs | Grafana Loki | Query logs by service, labels, and incident windows |
| Dashboards | Grafana | Link incident evidence to operational dashboards |
| War-room chat | Mattermost | Threaded incident updates and review actions |
| Async team chat | Zulip | Topic-based incident streams |
| Decentralized chat | Matrix | Sovereign incident-room integration |
| Identity | Keycloak | SSO, auth, groups, and reviewer identity |
| Policy | Open Policy Agent | Review/remediation authorization decisions |
| Durable workflows | Temporal | Requeue investigation and review workflows |
| Persistent storage | PostgreSQL | Durable incidents, traces, evidence, and reviews |
| AI/eval tracking | MLflow | Track eval runs and LLM/heuristic comparisons |

Phase one only adds local interfaces and normalized evidence shapes. Concrete
network adapters should be added after evidence, lifecycle, trace, and eval
contracts are stable.

## Implemented adapter status

- `TELEMETRY_PROVIDER=oss` enables HTTP adapters for Prometheus metrics, Loki logs, GitLab deployment records, and Alertmanager deployment-like alert signals.
- `WAR_ROOM_PROVIDER=mattermost|zulip|matrix` enables open-source war-room posting, with the original Slack/Discord webhook path preserved.
- `KEYCLOAK_JWKS_URL` enables Keycloak/JWKS bearer-token validation for mutating API routes.
- `OPA_URL` enables Open Policy Agent authorization checks for review and remediation actions.
- Reviewer-requested reinvestigation now starts a new analysis task and writes a durable job ledger to `data/reinvestigation_jobs.json`.
