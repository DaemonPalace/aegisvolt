"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { StateBadge } from "@/components/state-badge";
import { clock, countdown, kwh as fmtKwh, money } from "@/lib/format";
import { CHARGE, STOP_CHARGE, TELEMETRY, progress, type Point, type Saga, type Station } from "@/lib/gql";
import { useNow } from "@/lib/runtime";

/** The connectors this fleet holds right now: progress and countdown per row; a row opens to metadata and telemetry. */
export function FleetSessions({ sagas, stations }: { sagas: Saga[]; stations: Station[] }) {
  const now = useNow();
  const [open, setOpen] = useState<string | null>(null);
  const [stop] = useMutation(STOP_CHARGE, { refetchQueries: [CHARGE] });
  const [stopping, setStopping] = useState<string | null>(null);
  const name = (id: string) => stations.find((s) => s.id === id)?.name ?? id;

  return (
    <section className="panel" aria-labelledby="fleet-h">
      <header>
        <h2 id="fleet-h">Your fleet&apos;s connectors</h2>
        <span className="meta num">{sagas.length} in use</span>
      </header>
      {sagas.length === 0 ? (
        <p className="empty">None of your vehicles is on a connector. Start a charge above.</p>
      ) : (
        <ul className="sessions">
          {sagas.map((s) => {
            const p = progress(s, now);
            const left = s.endsAt ? Date.parse(s.endsAt) - now : null;
            const expanded = open === s.id;
            return (
              <li key={s.id} className={expanded ? "open" : undefined}>
                <button className="session-head" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : s.id)}>
                  <span className="who">
                    <strong className="mono">{s.connectorId}</strong>
                    <span className="sub">{name(s.stationId)}{s.powerKw ? ` · ${s.powerKw} kW` : ""}</span>
                  </span>
                  <span className="bar" aria-hidden="true"><i style={{ width: `${Math.round(p * 100)}%` }} /></span>
                  <span className="left num">
                    {s.status === "STARTED" ? "Starting…" : left != null ? <>{countdown(left)}<span className="unit">left</span></> : "—"}
                  </span>
                  <StateBadge state={s.status} />
                  <span className="chev" aria-hidden="true">{expanded ? "−" : "+"}</span>
                </button>
                {expanded && (
                  <div className="session-body">
                    <SessionDetail saga={s} stationName={name(s.stationId)} progress={p} />
                    <div className="session-actions">
                      <Link href={`/saga/?id=${s.id}`} className="card-link">Open the saga</Link>
                      {s.status === "COMPLETED" && (
                        <button className="btn" disabled={stopping === s.id}
                          onClick={async () => { setStopping(s.id); try { await stop({ variables: { sagaId: s.id } }); } finally { setStopping(null); } }}>
                          {stopping === s.id ? "Stopping…" : "Stop charging now"}
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Polls telemetry only while open: each poll also makes the dispatch service sample its live sessions. */
function SessionDetail({ saga, stationName, progress: p }: { saga: Saga; stationName: string; progress: number }) {
  const { data } = useQuery<{ telemetry: Point[] }>(TELEMETRY, { variables: { stationId: saga.stationId }, pollInterval: 2000 });
  const points = (data?.telemetry ?? []).filter((t) => t.sagaId === saga.id);
  const last = points[0];
  const avg = points.length ? points.reduce((a, t) => a + t.powerKw, 0) / points.length : null;
  const activated = saga.steps.find((x) => x.name === "activate" && x.status === "OK")?.at;

  return (
    <div className="session-grid">
      <div>
        <h3>Metrics</h3>
        <dl className="facts">
          <div><dt>Delivered</dt><dd className="num">{last ? fmtKwh(last.energyKwh) : "0"} / {saga.kwh}<span className="unit">kWh</span></dd></div>
          <div><dt>Progress</dt><dd className="num">{Math.round(p * 100)}%</dd></div>
          <div><dt>Power now</dt><dd className="num">{last ? last.powerKw : "—"}<span className="unit">kW</span></dd></div>
          <div><dt>Average power</dt><dd className="num">{avg ? avg.toFixed(1) : "—"}<span className="unit">kW</span></dd></div>
          <div><dt>Voltage</dt><dd className="num">{last ? last.voltage : "—"}<span className="unit">V</span></dd></div>
        </dl>
      </div>
      <div>
        <h3>Session</h3>
        <dl className="facts">
          <div><dt>Station</dt><dd>{stationName}</dd></div>
          <div><dt>Rated power</dt><dd className="num">{saga.powerKw ?? "—"}<span className="unit">kW</span></dd></div>
          <div><dt>Paid</dt><dd className="num">{money(saga.amountCents)}</dd></div>
          <div><dt>Energy on since</dt><dd className="num">{activated ? clock(activated) : "—"}</dd></div>
          <div><dt>Full at</dt><dd className="num">{saga.endsAt ? clock(saga.endsAt) : "—"}</dd></div>
          <div><dt>Saga</dt><dd className="mono">{saga.id.slice(0, 8)}</dd></div>
        </dl>
      </div>
      <div className="span-all">
        <h3>Telemetry <span className="meta">latest samples, every 2 s</span></h3>
        {points.length === 0 ? (
          <p className="meta">{saga.status === "STARTED" ? "Waiting for the charger to switch on." : "Waiting for the first sample."}</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Time</th><th className="n">Power</th><th className="n">Energy</th><th className="n">Voltage</th></tr></thead>
              <tbody>
                {points.slice(0, 8).map((t) => (
                  <tr key={t.ts}>
                    <td className="num">{clock(t.ts)}</td>
                    <td className="n num">{t.powerKw}<span className="unit">kW</span></td>
                    <td className="n num">{fmtKwh(t.energyKwh)}<span className="unit">kWh</span></td>
                    <td className="n num">{t.voltage}<span className="unit">V</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
