"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@apollo/client/react";
import { HEALTH, type Health } from "@/lib/gql";
import { useConfig } from "@/lib/runtime";

const SERVICE_SHORT: Record<string, string> = {
  "graphql-gateway": "Gateway",
  "station-reservation-service": "Reservation",
  "billing-wallet-service": "Billing",
  "energy-dispatch-service": "Dispatch",
};

/** The mark: a charging plug face (two pins over a ground pin) in the accent. */
function Mark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="none" stroke="var(--accent)" strokeWidth="2" />
      <circle cx="8.5" cy="10" r="1.8" fill="var(--accent)" />
      <circle cx="15.5" cy="10" r="1.8" fill="var(--accent)" />
      <circle cx="12" cy="15.5" r="1.8" fill="var(--accent)" />
    </svg>
  );
}

const NAV = [
  { href: "/", label: "Console", icon: <path d="M3 4h7v7H3zM14 4h7v4h-7zM14 12h7v8h-7zM3 15h7v5H3z" /> },
  { href: "/saga/", label: "Sagas", icon: <path d="M4 7h11M11 3l4 4-4 4M20 17H9M13 13l-4 4 4 4" /> },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const { env } = useConfig();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const { data } = useQuery<{ health: Health }>(HEALTH, { pollInterval: 5000 });
  const services = data?.health.services ?? [];
  const ok = services.filter((s) => s.status === "ok").length;
  const gateway = services.find((s) => s.service === "graphql-gateway");
  const page = NAV.find((n) => (n.href === "/" ? path === "/" : path.startsWith(n.href)))?.label ?? "Console";

  return (
    <div className="app">
      <aside className={`sidebar${open ? " open" : ""}`} aria-label="Navigation">
        <div className="brand">
          <Mark />
          AegisVolt
          <button className="btn menu-toggle" style={{ marginLeft: "auto" }} onClick={() => setOpen(false)} aria-label="Close menu">
            Close
          </button>
        </div>
        <div className="scope">
          <span className="label">Environment</span>
          <strong>{env}</strong>
        </div>
        <nav>
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className="nav-item" aria-current={page === n.label ? "page" : undefined} onClick={() => setOpen(false)}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                {n.icon}
              </svg>
              {n.label}
            </Link>
          ))}
        </nav>
        <div className="bottom">
          <h2>Services in {env}</h2>
          {services.length === 0 && <span className="stamp">Checking…</span>}
          {services.map((s) => (
            <div className="svc" key={s.service}>
              <span className={`dot${s.status === "ok" ? "" : " bad"}`} aria-hidden="true" />
              {SERVICE_SHORT[s.service] ?? s.service}
              <span className="v">{s.status === "ok" ? s.version : "down"}</span>
            </div>
          ))}
        </div>
      </aside>
      <header className="topbar">
        <button className="btn menu-toggle" onClick={() => setOpen(true)} aria-label="Open menu">
          Menu
        </button>
        <div className="crumbs">
          <span className="hide-phone">AegisVolt</span>
          <span className="hide-phone">{env}</span>
          <span>{page}</span>
        </div>
        <div className="right">
          <span className="ver">Gateway {gateway ? gateway.version : "—"}</span>
          <span className={`pill ${services.length && ok === services.length ? "pos" : services.length ? "neg" : ""}`}>
            {services.length ? `${ok} of ${services.length} services up` : "Checking services"}
          </span>
        </div>
      </header>
      <main className="sheet">{children}</main>
    </div>
  );
}
