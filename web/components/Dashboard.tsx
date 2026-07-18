"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  History,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles
} from "lucide-react";
import { listIncidents, resetIncidents, triggerIncident } from "@/lib/api";
import type { Incident } from "@/lib/types";

const scenarios = [
  {
    label: "DB Pool Exhaustion",
    service: "payment-api",
    severity: "critical",
    timestamp: "2026-07-07T14:32:15Z",
    alert_description: "Database connection pool exhaustion detected"
  },
  {
    label: "Memory Leak",
    service: "order-processor",
    severity: "critical",
    timestamp: "2026-07-07T16:30:00Z",
    alert_description: "Memory leak detected - GC pause times increasing"
  },
  {
    label: "Cascading Timeout",
    service: "checkout-gateway",
    severity: "critical",
    timestamp: "2026-07-07T17:05:05Z",
    alert_description: "Cascading failure - downstream service timeout"
  }
];

type IncidentGroup = {
  signature: string;
  latest: Incident;
  incidents: Incident[];
  actionableCount: number;
};

export function Dashboard() {
  const router = useRouter();
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const sortedIncidents = useMemo(() => [...incidents].sort(compareIncidentTime), [incidents]);
  const groups = useMemo(() => groupIncidents(sortedIncidents), [sortedIncidents]);
  const actionGroups = useMemo(
    () => groups.filter((group) => group.actionableCount > 0),
    [groups]
  );
  const historyGroups = useMemo(
    () => groups.filter((group) => group.actionableCount === 0),
    [groups]
  );
  const investigating = useMemo(
    () => incidents.filter((incident) => incident.current_status !== "complete").length,
    [incidents]
  );
  const needsReview = actionGroups.filter((group) => group.latest.current_status === "complete").length;

  async function refresh() {
    try {
      setError(null);
      setIncidents(await listIncidents());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => window.clearInterval(timer);
  }, []);

  async function launch(index: number) {
    const scenario = scenarios[index];
    setTriggering(scenario.label);
    try {
      setError(null);
      const incident = await triggerIncident(scenario);
      setIncidents((items) => [incident, ...items]);
      router.push(`/incident/${incident.incident_id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to launch incident");
    } finally {
      setTriggering(null);
    }
  }

  async function clearDemoQueue() {
    if (!window.confirm("Clear all in-memory demo incidents and start fresh?")) return;
    try {
      setError(null);
      await resetIncidents();
      setIncidents([]);
      setShowHistory(false);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to reset demo queue");
    }
  }

  return (
    <div className="screen-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Operations Command</p>
          <h1>Incident queue</h1>
          <p className="hero-copy">
            Live RCA status, evidence gates, revenue impact, and report-only recovery decisions.
          </p>
        </div>
        <div className="hero-metrics" aria-label="Incident overview">
          <Metric label="Investigating" value={investigating} />
          <Metric label="Needs Review" value={needsReview} />
          <Metric label="Total" value={incidents.length} />
        </div>
      </section>

      <section className="launch-grid" aria-label="Scenarios">
        {scenarios.map((scenario, index) => (
          <button
            className="scenario-tile"
            key={scenario.label}
            onClick={() => launch(index)}
            disabled={Boolean(triggering)}
            aria-busy={triggering === scenario.label}
          >
            <span className="tile-icon"><Play size={18} /></span>
            <span className="tile-text">
              <strong>{scenario.label}</strong>
              <small>{triggering === scenario.label ? "Launching investigation..." : `${scenario.service} · launch and open`}</small>
            </span>
          </button>
        ))}
      </section>

      {error && <div className="error-panel">{error}</div>}

      <section id="incidents" className="content-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Action Queue</p>
            <h2>Incidents that need attention</h2>
          </div>
          <div className="button-row">
            <button className="ghost-button" onClick={refresh}>Refresh</button>
            {incidents.length > 0 && (
              <button className="danger-button" onClick={clearDemoQueue}>
                <RotateCcw size={16} /> Clear Demo
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="empty-panel">Loading incidents...</div>
        ) : actionGroups.length === 0 ? (
          <div className="empty-panel">
            <Sparkles size={24} />
            <strong>No open action items.</strong>
            <span>Launch a scenario to start an investigation, or open history for completed runs.</span>
          </div>
        ) : (
          <div className="incident-list">
            {actionGroups.map((group) => (
              <IncidentCard key={group.signature} group={group} />
            ))}
          </div>
        )}
      </section>

      {historyGroups.length > 0 && (
        <section className="content-section muted-section">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Run History</p>
              <h2>{historyGroups.length} completed incident pattern{historyGroups.length === 1 ? "" : "s"}</h2>
            </div>
            <button className="ghost-button" onClick={() => setShowHistory((value) => !value)}>
              <History size={16} /> {showHistory ? "Hide" : "Show"} History
            </button>
          </div>
          {showHistory ? (
            <div className="incident-list compact-list">
              {historyGroups.map((group) => (
                <IncidentCard key={group.signature} group={group} compact />
              ))}
            </div>
          ) : (
            <div className="archive-summary">
              Historical duplicate runs are archived here so the command queue stays readable.
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function IncidentCard({ group, compact = false }: { group: IncidentGroup; compact?: boolean }) {
  const incident = group.latest;
  const confidence = Math.round((incident.root_cause?.confidence || incident.rca_confidence || 0) * 100);
  const complete = incident.current_status === "complete";
  const statusLabel = getStatusLabel(incident);
  const hiddenRuns = group.incidents.length - 1;

  return (
    <Link className={`incident-card ${compact ? "compact-card" : ""} ${isActionable(incident) ? "actionable-card" : ""}`} href={`/incident/${incident.incident_id}`}>
      <div className="incident-card-top">
        <div>
          <div className="pill-row">
            <span className={`severity-pill severity-${incident.severity || "unknown"}`}>{incident.severity || "unknown"}</span>
            <span className="status-pill">{statusLabel}</span>
            {hiddenRuns > 0 && <span className="hidden-count">{hiddenRuns} older run{hiddenRuns === 1 ? "" : "s"} hidden</span>}
          </div>
          <h3>{incident.service}</h3>
          <p>{incident.alert_description}</p>
        </div>
        <span className="open-arrow"><ArrowRight size={20} /></span>
      </div>
      {!compact && (
        <div className="incident-progress">
          <span style={{ width: `${confidence}%` }} />
        </div>
      )}
      <div className="incident-card-bottom">
        <span><Clock3 size={15} /> {incident.current_status}</span>
        <span>{confidence}% confidence</span>
        <span>{complete ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />} {incident.lifecycle_status}</span>
        <span><ShieldCheck size={15} /> {incident.quality_gates?.overall_passed ? "gates pass" : "reviewing"}</span>
      </div>
      {!compact && (
        <div className="card-meta">
          <span>{group.incidents.length} total run{group.incidents.length === 1 ? "" : "s"} for this signal</span>
          <strong>Open investigation</strong>
        </div>
      )}
    </Link>
  );
}

function groupIncidents(items: Incident[]): IncidentGroup[] {
  const groups = new Map<string, IncidentGroup>();
  for (const incident of items) {
    const signature = [
      incident.service || "unknown-service",
      incident.alert_description || "unknown-alert"
    ].join("::");
    const existing = groups.get(signature);
    if (!existing) {
      groups.set(signature, {
        signature,
        latest: incident,
        incidents: [incident],
        actionableCount: isActionable(incident) ? 1 : 0
      });
      continue;
    }
    existing.incidents.push(incident);
    if (compareIncidentTime(incident, existing.latest) < 0) {
      existing.latest = incident;
    }
    if (isActionable(incident)) {
      existing.actionableCount += 1;
    }
  }
  return [...groups.values()].sort((left, right) => compareIncidentTime(left.latest, right.latest));
}

function isActionable(incident: Incident) {
  if (incident.current_status !== "complete") return true;
  const lifecycle = (incident.lifecycle_status || "").toLowerCase();
  if (
    [
      "reviewing",
      "investigating",
      "needs_review",
      "needs_human_review",
      "review_requested_more_data"
    ].includes(lifecycle)
  ) return true;
  if (incident.quality_gates?.overall_passed === false) return true;
  return false;
}

function getStatusLabel(incident: Incident) {
  if (incident.current_status !== "complete") return "Agents running";
  if (isActionable(incident)) return "Human review needed";
  return "Reviewed";
}

function compareIncidentTime(left: Incident, right: Incident) {
  return timestampOf(right) - timestampOf(left);
}

function timestampOf(incident: Incident) {
  return Date.parse(incident.created_at || incident.timestamp || "") || 0;
}
