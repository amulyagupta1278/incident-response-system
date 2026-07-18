"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  BadgeCheck,
  Check,
  CircleAlert,
  Database,
  Download,
  FileJson,
  FileSearch,
  Gauge,
  MessageSquare,
  RefreshCw,
  SearchCheck,
  ShieldCheck,
  Target,
  Users,
  X
} from "lucide-react";
import {
  ApiError,
  askIncident,
  decideRemediation,
  getIncident,
  getTrace,
  listIncidents,
  overrideRootCause,
  postmortemUrl,
  requestMoreData,
  reviewRca
} from "@/lib/api";
import type { Incident, TraceExport } from "@/lib/types";

const tabs = ["overview", "flow", "evidence", "review", "trace"] as const;
type Tab = (typeof tabs)[number];

type FlowStepStatus = "complete" | "active" | "pending" | "blocked" | "review";

type FlowStep = {
  key: string;
  title: string;
  description: string;
  completedStep?: string;
  agents: string[];
  icon: "database" | "logs" | "metrics" | "rca" | "impact" | "summary" | "review";
};

const flowSteps: FlowStep[] = [
  {
    key: "load_data",
    title: "Load Evidence",
    description: "Fetch logs, metrics, deployments, service context, and evidence IDs.",
    completedStep: "load_data",
    agents: ["incident_commander"],
    icon: "database"
  },
  {
    key: "log_analysis",
    title: "Analyze Logs",
    description: "Detect timeout, dependency, GC, memory, and runtime error patterns.",
    completedStep: "log_analysis",
    agents: ["log_analysis"],
    icon: "logs"
  },
  {
    key: "metrics_analysis",
    title: "Analyze Metrics",
    description: "Compare incident-window metrics against baseline behavior.",
    completedStep: "metrics_analysis",
    agents: ["metrics_analysis"],
    icon: "metrics"
  },
  {
    key: "rca_analysis",
    title: "Root Cause",
    description: "Generate hypothesis, confidence breakdown, and ruled-out alternatives.",
    completedStep: "rca_analysis",
    agents: ["rca_agent", "rca_analysis", "memory"],
    icon: "rca"
  },
  {
    key: "business_impact",
    title: "Business Impact",
    description: "Estimate affected users and revenue impact from service metrics.",
    completedStep: "business_impact",
    agents: ["business_impact"],
    icon: "impact"
  },
  {
    key: "summary",
    title: "Summaries",
    description: "Generate engineering notes, executive summary, and recovery plan.",
    completedStep: "summary",
    agents: ["executive_summary"],
    icon: "summary"
  },
  {
    key: "human_review",
    title: "Human Review",
    description: "Accept/reject RCA, request more data, or approve remediation actions.",
    agents: ["human_reviewer"],
    icon: "review"
  }
];

export function IncidentDetail({ incidentId }: { incidentId: string }) {
  const router = useRouter();
  const [incident, setIncident] = useState<Incident | null>(null);
  const [trace, setTrace] = useState<TraceExport | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [busy, setBusy] = useState(false);
  const [missing, setMissing] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [chat, setChat] = useState<Array<{ role: "user" | "agent"; text: string }>>([]);

  async function refresh() {
    try {
      const nextIncident = await getIncident(incidentId);
      setIncident(nextIncident);
      setMissing(false);
      setLoadError(null);
      if (tab === "trace") {
        setTrace(await getTrace(incidentId));
      }
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 404) {
        setMissing(true);
        setIncident(null);
        setTrace(null);
        setLoadError("This incident ID is not available in the currently running backend.");
        return;
      }
      setLoadError(exc instanceof Error ? exc.message : "Failed to load incident");
    }
  }

  useEffect(() => {
    if (missing) return;
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => window.clearInterval(timer);
  }, [incidentId, tab, missing]);

  const confidence = Math.round((incident?.root_cause?.confidence || 0) * 100);
  const evidenceRefs = incident?.root_cause?.supporting_evidence_refs || [];
  const gates = incident?.quality_gates || {};
  const recommendations = incident?.recovery_recommendations || [];
  const decisions = incident?.remediation_decisions || {};
  const completedSteps = incident?.completed_steps || [];

  async function mutate(action: () => Promise<Incident>) {
    setBusy(true);
    try {
      setIncident(await action());
      if (tab === "trace") setTrace(await getTrace(incidentId));
    } finally {
      setBusy(false);
    }
  }

  async function submitQuestion() {
    const trimmed = question.trim();
    if (!trimmed) return;
    setQuestion("");
    setChat((items) => [...items, { role: "user", text: trimmed }]);
    const answer = await askIncident(incidentId, trimmed);
    setChat((items) => [...items, { role: "agent", text: `${answer.answer} (${answer.source})` }]);
  }

  const traceSpans = useMemo(() => trace?.spans || incident?.agent_invocations || [], [trace, incident]);
  const flowState = useMemo(
    () => incident ? buildFlowState(incident, traceSpans, completedSteps) : null,
    [incident, traceSpans, completedSteps]
  );

  async function openLatestIncident() {
    const incidents = await listIncidents();
    const latest = incidents[0];
    router.replace(latest ? `/incident/${latest.incident_id}` : "/");
  }

  if (missing) {
    return (
      <MissingIncidentPanel
        incidentId={incidentId}
        message={loadError}
        openLatestIncident={openLatestIncident}
      />
    );
  }

  if (!incident || !flowState) {
    return (
      <div className="empty-panel">
        <strong>Loading incident...</strong>
        {loadError && <span>{loadError}</span>}
      </div>
    );
  }

  return (
    <div className="screen-stack">
      <section className="detail-hero">
        <Link className="back-button" href="/"><ArrowLeft size={18} /> Back</Link>
        <div className="detail-title-row">
          <div>
            <span className={`severity-pill severity-${incident.severity || "unknown"}`}>{incident.severity}</span>
            <h1>{incident.service}</h1>
            <p>{incident.alert_description}</p>
          </div>
          <a className="icon-action" href={postmortemUrl(incident.incident_id)} title="Download postmortem">
            <Download size={20} />
          </a>
        </div>
        <div className="hero-metrics compact">
          <Metric label="Confidence" value={`${confidence}%`} />
          <Metric label="Users" value={(incident.affected_users || 0).toLocaleString()} />
          <Metric label="Lifecycle" value={incident.lifecycle_status || "opened"} />
        </div>
      </section>

      <div className="tab-bar" role="tablist">
        {tabs.map((item) => (
          <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
            {item}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <section className="content-section">
          <div className="rca-card">
            <div className="rca-card-header">
              <SearchCheck size={22} />
              <div>
                <p className="eyebrow">Root Cause</p>
                <h2>{incident.root_cause?.hypothesis || "Pending analysis"}</h2>
              </div>
            </div>
            <div className="confidence-track"><span style={{ width: `${confidence}%` }} /></div>
            {incident.root_cause?.deploy_correlation && <p className="callout">{incident.root_cause.deploy_correlation}</p>}
            <div className="summary-grid">
              <SummaryBlock title="Executive Summary" text={incident.executive_summary || "Pending"} />
              <SummaryBlock title="Engineering Notes" text={incident.engineering_summary || "Pending"} />
            </div>
          </div>
        </section>
      )}

      {tab === "flow" && (
        <section className="content-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">LangGraph Flow</p>
              <h2>Agent workflow map</h2>
            </div>
            <button className="ghost-button" onClick={refresh}><RefreshCw size={16} /> Refresh</button>
          </div>

          <div className="flow-summary-grid">
            <Metric label="Current" value={flowState.currentLabel} />
            <Metric label="Completed" value={`${flowState.completedCount}/${flowSteps.length}`} />
            <Metric label="Trace Spans" value={traceSpans.length} />
          </div>

          <div className="flow-map" aria-label="Agent workflow map">
            {flowState.nodes.map((node, index) => (
              <FlowNodeCard key={node.step.key} node={node} isLast={index === flowState.nodes.length - 1} />
            ))}
          </div>

          <div className="flow-inspector">
            <div>
              <p className="eyebrow">Runtime Evidence</p>
              <h3>How to read this map</h3>
              <p>
                Each node is derived from <code>completed_steps</code>, <code>current_status</code>, and the span-like
                <code> agent_invocations</code> trace. Router and memory events appear under the node they influenced.
              </p>
            </div>
            <div className="flow-legend">
              <span><i className="legend-dot complete" /> Complete</span>
              <span><i className="legend-dot active" /> Active</span>
              <span><i className="legend-dot review" /> Review</span>
              <span><i className="legend-dot pending" /> Pending</span>
            </div>
          </div>
        </section>
      )}

      {tab === "evidence" && (
        <section id="incidents" className="content-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Grounded Evidence</p>
              <h2>Claims to Raw Facts</h2>
            </div>
            <span className="count-token">{evidenceRefs.length} refs</span>
          </div>
          <div className="evidence-stack">
            {evidenceRefs.map((ref, index) => {
              const raw = ref.evidence_id ? incident.evidence_catalog?.[ref.evidence_id] : undefined;
              return (
                <article className="evidence-card" key={`${ref.evidence_id}-${index}`}>
                  <div>
                    <strong>{ref.claim}</strong>
                    <span>{ref.evidence_type}</span>
                  </div>
                  <code>{ref.evidence_id}</code>
                  <pre>{JSON.stringify(raw || {}, null, 2)}</pre>
                </article>
              );
            })}
          </div>
        </section>
      )}

      {tab === "review" && (
        <section id="review" className="content-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Human Governance</p>
              <h2>Review and Actions</h2>
            </div>
            <button className="ghost-button" onClick={refresh}><RefreshCw size={16} /> Refresh</button>
          </div>

          <div className="review-action-grid">
            <button disabled={busy} onClick={() => mutate(() => reviewRca(incidentId, "accepted", "Accepted from Next.js command UI."))}>
              <Check size={18} /> Accept RCA
            </button>
            <button disabled={busy} onClick={() => mutate(() => reviewRca(incidentId, "rejected", "Rejected from Next.js command UI."))}>
              <X size={18} /> Reject RCA
            </button>
            <button disabled={busy} onClick={() => mutate(() => reviewRca(incidentId, "evidence_insufficient", "Evidence marked insufficient from Next.js command UI."))}>
              <CircleAlert size={18} /> Evidence Insufficient
            </button>
            <button disabled={busy} onClick={() => mutate(() => requestMoreData(incidentId, "Reviewer requested another investigation pass from Next.js command UI."))}>
              <RefreshCw size={18} /> Request More Data
            </button>
            <button disabled={busy} onClick={() => {
              const hypothesis = window.prompt("Override root cause hypothesis");
              if (hypothesis) mutate(() => overrideRootCause(incidentId, hypothesis, "Override submitted from Next.js command UI."));
            }}>
              <ShieldCheck size={18} /> Override RCA
            </button>
          </div>

          <div className="quality-grid">
            {Object.entries(gates).map(([key, value]) => (
              <div className={`quality-item ${value === true ? "quality-pass" : value === false ? "quality-fail" : ""}`} key={key}>
                <strong>{key.replaceAll("_", " ")}</strong>
                <span>{String(value)}</span>
              </div>
            ))}
          </div>

          <div className="recommendation-list">
            {recommendations.map((item, index) => (
              <div className="recommendation" key={item}>
                <span>{index + 1}. {item}</span>
                {decisions[String(index)] ? (
                  <strong>{decisions[String(index)]?.decision}</strong>
                ) : (
                  <div>
                    <button onClick={() => mutate(() => decideRemediation(incidentId, index, "approved"))}>Approve</button>
                    <button onClick={() => mutate(() => decideRemediation(incidentId, index, "rejected"))}>Reject</button>
                  </div>
                )}
              </div>
            ))}
          </div>

          <ChatPanel chat={chat} question={question} setQuestion={setQuestion} submitQuestion={submitQuestion} />
        </section>
      )}

      {tab === "trace" && (
        <section id="trace" className="content-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Audit Trace</p>
              <h2>Agent Spans</h2>
            </div>
            <button className="ghost-button" onClick={() => getTrace(incidentId).then(setTrace)}>
              <FileJson size={16} /> Load Trace
            </button>
          </div>
          <div className="trace-list">
            {traceSpans.map((span, index) => (
              <article className="trace-row" key={`${span.span_id}-${index}`}>
                <span>{index + 1}</span>
                <div>
                  <strong>{span.agent}</strong>
                  <p>{span.reasoning || span.action}</p>
                  <code>{span.span_id}</code>
                </div>
                <small>{span.latency_ms ? `${span.latency_ms}ms` : span.source}</small>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function FlowNodeCard({
  node,
  isLast
}: {
  node: {
    step: FlowStep;
    status: FlowStepStatus;
    spans: Array<{
      agent?: string;
      action?: string;
      reasoning?: string;
      source?: string;
      latency_ms?: number;
      span_id?: string;
    }>;
    highlight: string;
  };
  isLast: boolean;
}) {
  const Icon = getFlowIcon(node.step.icon);
  const latestSpan = node.spans[node.spans.length - 1];
  return (
    <article className={`flow-node flow-${node.status}`}>
      <div className="flow-node-shell">
        <div className="flow-node-icon"><Icon size={19} /></div>
        <div>
          <span>{node.highlight}</span>
          <h3>{node.step.title}</h3>
          <p>{node.step.description}</p>
        </div>
      </div>

      <div className="flow-node-meta">
        <strong>{node.spans.length} span{node.spans.length === 1 ? "" : "s"}</strong>
        <span>{latestSpan?.source || latestSpan?.action || "waiting"}</span>
      </div>

      {latestSpan && (
        <div className="flow-span-callout">
          <strong>{latestSpan.agent}</strong>
          <p>{latestSpan.reasoning || latestSpan.action || "Agent completed this step."}</p>
          <small>{latestSpan.latency_ms ? `${latestSpan.latency_ms}ms` : latestSpan.span_id}</small>
        </div>
      )}

      {!isLast && <div className="flow-connector" aria-hidden="true" />}
    </article>
  );
}

function buildFlowState(
  incident: Incident,
  traceSpans: Incident["agent_invocations"],
  completedSteps: string[]
) {
  const activeKey = currentFlowKey(incident);
  const nodes = flowSteps.map((step) => {
    const spans = (traceSpans || []).filter((span) => belongsToStep(span.agent || "", span.action || "", step));
    const complete = step.completedStep ? completedSteps.includes(step.completedStep) : false;
    const status = step.key === "human_review"
      ? reviewStatus(incident)
      : activeKey === step.key && !complete
        ? "active"
        : complete
          ? "complete"
          : dependencyBlocked(step, completedSteps)
            ? "pending"
            : "pending";
    return {
      step,
      status,
      spans,
      highlight: statusLabel(status)
    };
  });
  return {
    nodes,
    completedCount: nodes.filter((node) => node.status === "complete" || node.status === "review").length,
    currentLabel: readableStatus(incident.lifecycle_status || incident.current_status || "opened")
  };
}

function belongsToStep(agent: string, action: string, step: FlowStep) {
  if (step.agents.includes(agent)) return true;
  if (agent === "router_agent" && action.includes(step.key.replace("_analysis", ""))) return true;
  return step.agents.some((stepAgent) => action.includes(stepAgent.replace("_agent", "")));
}

function currentFlowKey(incident: Incident) {
  const status = incident.current_status || "";
  if (status.includes("load")) return "load_data";
  if (status.includes("log")) return "log_analysis";
  if (status.includes("metric")) return "metrics_analysis";
  if (status.includes("rca") || status.includes("root")) return "rca_analysis";
  if (status.includes("impact")) return "business_impact";
  if (status.includes("summary") || status === "complete") return "summary";
  return "";
}

function reviewStatus(incident: Incident): FlowStepStatus {
  const lifecycle = (incident.lifecycle_status || "").toLowerCase();
  if (lifecycle.includes("review") || lifecycle.includes("approval")) return "review";
  if ((incident.review_events || []).length > 0) return "complete";
  if (incident.current_status === "complete") return "review";
  return "pending";
}

function dependencyBlocked(step: FlowStep, completedSteps: string[]) {
  const stepIndex = flowSteps.findIndex((item) => item.key === step.key);
  return flowSteps.slice(0, stepIndex).some((item) => item.completedStep && !completedSteps.includes(item.completedStep));
}

function statusLabel(status: FlowStepStatus) {
  const labels: Record<FlowStepStatus, string> = {
    complete: "Complete",
    active: "Running now",
    pending: "Pending",
    blocked: "Blocked",
    review: "Needs human review"
  };
  return labels[status];
}

function readableStatus(value: string) {
  return value.replaceAll("_", " ");
}

function getFlowIcon(icon: FlowStep["icon"]) {
  const icons = {
    database: Database,
    logs: FileSearch,
    metrics: Gauge,
    rca: Target,
    impact: Users,
    summary: BadgeCheck,
    review: ShieldCheck
  };
  return icons[icon];
}

function MissingIncidentPanel({
  incidentId,
  message,
  openLatestIncident
}: {
  incidentId: string;
  message: string | null;
  openLatestIncident: () => Promise<void>;
}) {
  return (
    <section className="missing-incident-panel">
      <div className="missing-incident-icon"><CircleAlert size={28} /></div>
      <p className="eyebrow">Incident Not Found</p>
      <h1>This investigation is no longer in backend memory.</h1>
      <p>
        {message || "The backend returned 404 for this incident."} This usually happens after restarting Uvicorn,
        because demo incidents are kept in the active Python process.
      </p>
      <code>{incidentId}</code>
      <div className="button-row">
        <Link className="ghost-button" href="/"><ArrowLeft size={16} /> Back to dashboard</Link>
        <button className="ghost-button" onClick={openLatestIncident}><RefreshCw size={16} /> Open latest incident</button>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function SummaryBlock({ title, text }: { title: string; text: string }) {
  return (
    <article className="summary-block">
      <h3>{title}</h3>
      <pre>{text}</pre>
    </article>
  );
}

function ChatPanel({
  chat,
  question,
  setQuestion,
  submitQuestion
}: {
  chat: Array<{ role: "user" | "agent"; text: string }>;
  question: string;
  setQuestion: (value: string) => void;
  submitQuestion: () => void;
}) {
  return (
    <div className="chat-panel">
      <h3><MessageSquare size={18} /> Ask the incident</h3>
      <div className="chat-log">
        {chat.map((item, index) => <p className={item.role} key={`${item.role}-${index}`}>{item.text}</p>)}
      </div>
      <div className="chat-input">
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") submitQuestion();
          }}
          placeholder="Ask about evidence, impact, deployments..."
        />
        <button onClick={submitQuestion}>Ask</button>
      </div>
    </div>
  );
}
