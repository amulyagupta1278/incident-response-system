"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { BarChart3, BrainCircuit, Database, Network, Search, Send } from "lucide-react";
import { askAssistant, getAnalytics, getKnowledgeGraph, searchKnowledge } from "@/lib/api";
import type { IncidentAnalytics, KnowledgeGraphResponse, KnowledgeSearchResponse } from "@/lib/types";

export function IntelligenceConsole() {
  const [period, setPeriod] = useState<"day" | "week" | "month">("week");
  const [analytics, setAnalytics] = useState<IncidentAnalytics | null>(null);
  const [graph, setGraph] = useState<KnowledgeGraphResponse | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<KnowledgeSearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getAnalytics(period).then(setAnalytics).catch((e) => setError(String(e)));
  }, [period]);
  useEffect(() => {
    getKnowledgeGraph().then(setGraph).catch((e) => setError(String(e)));
  }, []);

  async function chat(event: FormEvent) {
    event.preventDefault();
    if (!question.trim()) return;
    setBusy(true); setError("");
    try { setAnswer((await askAssistant(question)).answer); }
    catch (e) { setError(e instanceof Error ? e.message : "Assistant failed"); }
    finally { setBusy(false); }
  }

  async function lookup(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    setBusy(true); setError("");
    try { setSearch(await searchKnowledge(query)); }
    catch (e) { setError(e instanceof Error ? e.message : "Search failed"); }
    finally { setBusy(false); }
  }

  const nodeTypes = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const node of graph?.relational.nodes || []) counts[node.type] = (counts[node.type] || 0) + 1;
    return counts;
  }, [graph]);

  return <div className="screen-stack">
    <section className="hero-panel intelligence-hero">
      <div><p className="eyebrow">Unified Operations Intelligence</p><h1>Evidence, graph memory, analytics, and incident copilot.</h1><p className="hero-copy">AIOC investigation depth running behind project-scoped gateway security.</p></div>
      <BrainCircuit size={58} />
    </section>
    {error && <div className="error-panel">{error}</div>}

    <section className="intelligence-grid">
      <article className="content-section intel-card">
        <div className="intel-title"><BarChart3 /><div><p className="eyebrow">Analytics</p><h2>Incident patterns</h2></div></div>
        <div className="period-tabs">{(["day", "week", "month"] as const).map(p => <button className={period === p ? "active" : ""} onClick={() => setPeriod(p)} key={p}>{p}</button>)}</div>
        <div className="intel-metrics"><Metric label="Incidents" value={analytics?.total || 0}/><Metric label="Active" value={analytics?.active || 0}/><Metric label="Impact/min" value={`$${analytics?.impact_per_minute || 0}`}/></div>
        <div className="chip-list">{analytics?.service_buckets.slice(0, 6).map(item => <span key={item.label}>{item.label} · {item.count}</span>)}</div>
      </article>

      <article className="content-section intel-card">
        <div className="intel-title"><Network /><div><p className="eyebrow">Knowledge Graph</p><h2>Operational relationships</h2></div></div>
        <div className="intel-metrics"><Metric label="Nodes" value={graph?.relational.nodes.length || 0}/><Metric label="Edges" value={graph?.relational.edges.length || 0}/><Metric label="Types" value={Object.keys(nodeTypes).length}/></div>
        <div className="chip-list">{Object.entries(nodeTypes).map(([type, count]) => <span key={type}>{type} · {count}</span>)}</div>
        <div className="graph-preview">{graph?.relational.edges.slice(0, 6).map((edge, index) => <div key={`${edge.source}-${index}`}><b>{edge.source}</b><span>{edge.relation}</span><b>{edge.target}</b></div>)}</div>
      </article>
    </section>

    <section className="intelligence-grid">
      <article className="content-section intel-card">
        <div className="intel-title"><BrainCircuit /><div><p className="eyebrow">Jarvis Copilot</p><h2>Ask across latest incident</h2></div></div>
        <form className="intel-form" onSubmit={chat}><textarea value={question} onChange={e => setQuestion(e.target.value)} placeholder="Why did latency spike, and what should we do?"/><button disabled={busy}><Send size={16}/> Ask</button></form>
        {answer && <div className="answer-panel">{answer}</div>}
      </article>
      <article className="content-section intel-card">
        <div className="intel-title"><Database /><div><p className="eyebrow">Hybrid RAG</p><h2>Search operational knowledge</h2></div></div>
        <form className="intel-form compact-form" onSubmit={lookup}><input value={query} onChange={e => setQuery(e.target.value)} placeholder="cache stampede runbook"/><button disabled={busy}><Search size={16}/> Search</button></form>
        <div className="search-results">{search?.results.map(hit => <article key={hit.chunk_id}><strong>{hit.title}</strong><small>{hit.kind} · score {hit.score.toFixed(2)}</small><p>{hit.content.slice(0, 240)}</p></article>)}</div>
      </article>
    </section>
  </div>;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}
