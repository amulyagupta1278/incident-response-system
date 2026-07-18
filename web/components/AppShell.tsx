"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, Bell, BrainCircuit, ClipboardCheck, Home, Radar } from "lucide-react";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const onIncident = pathname.startsWith("/incident/");

  return (
    <div className="app-frame">
      <header className="topbar">
        <Link href="/" className="brand" aria-label="AI Operations Command home">
          <span className="brand-mark">
            <Radar size={20} />
          </span>
          <span>
            <strong>AIOC</strong>
            <small>Incident Command</small>
          </span>
        </Link>
        <nav className="desktop-nav" aria-label="Primary">
          <Link className={pathname === "/" ? "active" : ""} href="/">Incidents</Link>
          <Link className={pathname.startsWith("/intelligence") ? "active" : ""} href="/intelligence">Intelligence</Link>
        </nav>
        <div className="topbar-status">
          <span className="live-dot" />
          <span>Live Ops</span>
        </div>
      </header>

      <main className="app-main">{children}</main>

      <nav className="mobile-nav" aria-label="Primary">
        <Link className={!onIncident ? "active" : ""} href="/">
          <Home size={20} />
          <span>Home</span>
        </Link>
        <a href="#incidents">
          <Bell size={20} />
          <span>Incidents</span>
        </a>
        <a href="#review">
          <ClipboardCheck size={20} />
          <span>Review</span>
        </a>
        <a href="#trace">
          <Activity size={20} />
          <span>Trace</span>
        </a>
        <Link className={pathname.startsWith("/intelligence") ? "active" : ""} href="/intelligence">
          <BrainCircuit size={20} />
          <span>Intel</span>
        </Link>
      </nav>
    </div>
  );
}
