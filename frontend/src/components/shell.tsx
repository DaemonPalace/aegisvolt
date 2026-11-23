"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { Toasts } from "@/components/toasts";
import { FLEETS, HEALTH, type Health } from "@/lib/gql";
import { useConfig, useFleet } from "@/lib/runtime";

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
  { href: "/", label: "Charge", icon: <path d="M3 4h7v7H3zM14 4h7v4h-7zM14 12h7v8h-7zM3 15h7v5H3z" /> },
  { href: "/saga/", label: "Transactions", icon: <path d="M4 7h11M11 3l4 4-4 4M20 17H9M13 13l-4 4 4 4" /> },
  { href: "/station/", label: "Station owner", icon: <path d="M5 21V5a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v16M3 21h14M15 9h2a2 2 0 0 1 2 2v5a1 1 0 0 0 2 0V8l-3-3M8 8h4" /> },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const { env } = useConfig();
  const { fleet, setFleet } = useFleet();
  const path = usePathname();
  const owner = path.startsWith("/station"); // the station owner has no fleet: no fleet picker, no charge pop-ups
  const [open, setOpen] = useState(false);
  const { data } = useQuery<{ health: Health }>(HEALTH, { pollInterval: 5000 });
  const fleets = useQuery<{ fleets: { fleetId: string }[] }>(FLEETS, { pollInterval: 10000 }).data?.fleets.map((f) => f.fleetId) ?? [];
  // The remembered fleet can vanish (seed reset, other env): fall back to the first one that exists.
  useEffect(() => { if (fleets.length && !fleets.includes(fleet)) setFleet(fleets[0]); }, [fleets.join(), fleet]); // eslint-disable-line react-hooks/exhaustive-deps
  const services = data?.health.services ?? [];
  const ok = services.filter((s) => s.status === "ok").length;
  const gateway = services.find((s) => s.service === "graphql-gateway");
  const page = NAV.find((n) => (n.href === "/" ? path === "/" : path.startsWith(n.href)))?.label ?? "Charge";

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
        {!owner && <label className="scope fleet">
          <span className="label">Fleet</span>
          <select value={fleet} onChange={(e) => setFleet(e.target.value)} aria-label="Fleet">
            {fleets.map((f) => <option key={f}>{f}</option>)}
          </select>
        </label>}
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
      {!owner && <Toasts />}
    </div>
  );
}
