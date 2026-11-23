"use client";

import { Fragment, useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { TokenForm } from "@/components/flags-panel";
import { StateBadge } from "@/components/state-badge";
import { clock, kwh, when } from "@/lib/format";
import { ADD_CONNECTOR, LIFECYCLE, OWNER, REMOVE_CONNECTOR, type Connector, type Point, type Session, type Station } from "@/lib/gql";
import { adminToken, useConfig, useNow } from "@/lib/runtime";

type Data = { stations: Station[]; stationSessions: Session[]; telemetry: Point[] };
const NO_FLEET = "Not recorded (older sessions)";
const STATION_KEY = "aegisvolt.station";
const SESSION_LABEL: Record<string, string> = { ACTIVE: "Charging", STOPPED: "Ended", FAILED: "Hardware fault" };

// Local midnight, so "today" means the owner's day, not UTC's.
const midnight = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.toISOString(); };

/** Station owner: which bays work, who is on them, how much energy went out today, and the maintenance loop. */
export default function StationOwner() {
  const { env } = useConfig();
  const [stationId, setStationIdState] = useState(() => (typeof window === "undefined" ? "" : localStorage.getItem(STATION_KEY) ?? ""));
  const [since] = useState(midnight);
  const [token, setToken] = useState(adminToken);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");
  const now = useNow();

  const { data, previousData, error: loadError } = useQuery<Data>(OWNER, { variables: { stationId: stationId || "BOG-01", since }, pollInterval: 2000 });
  const d = data ?? previousData;
  const ctx = { context: { headers: { "X-Admin-Token": token } }, refetchQueries: [OWNER] };
  const [lifecycle, { loading: l1 }] = useMutation(LIFECYCLE, ctx);
  const [add, { loading: l2 }] = useMutation(ADD_CONNECTOR, ctx);
  const [remove, { loading: l3 }] = useMutation(REMOVE_CONNECTOR, ctx);
  const busy = l1 || l2 || l3;

  const station = d?.stations.find((s) => s.id === (stationId || "BOG-01")) ?? d?.stations[0];
  const setStation = (id: string) => { localStorage.setItem(STATION_KEY, id); setStationIdState(id); setOpen(null); };
  const run = async (fn: () => Promise<unknown>) => {
    setError("");
    try { await fn(); } catch (e) {
      const m = (e as Error).message;
      setError(m.includes("FORBIDDEN") ? "The admin token was refused. Paste the one for this environment." : m);
    }
  };

  if (!d || !station) return loadError ? <p className="notice">Couldn&apos;t reach the {env} API ({loadError.message}).</p> : <p className="meta">Loading…</p>;

  const sessions = d.stationSessions;
  const byConnector = (id: string) => sessions.filter((s) => s.connectorId === id);
  const live = (c: Connector) => d.telemetry.find((t) => t.connectorId === c.id && t.sagaId === c.sagaId && c.status === "RESERVED");
  const drawing = station.connectors.reduce((sum, c) => sum + (live(c)?.powerKw ?? 0), 0);
  const today = sessions.reduce((sum, s) => sum + s.kwhDelivered, 0);
  const capacity = station.connectors.filter((c) => c.status === "AVAILABLE" || c.status === "RESERVED");
  const down = station.connectors.filter((c) => c.status === "OUT_OF_SERVICE");

  return (
    <>
      <div className="title-row">
        <div>
          <h1>{station.name}</h1>
          <p className="sub">{station.city} · <span className="mono">{station.id}</span> · today since {clock(since).slice(0, 5)}, refreshed every 2 s</p>
        </div>
        <div className="actions">
          <select className="input" style={{ width: "auto" }} value={station.id} onChange={(e) => setStation(e.target.value)} aria-label="Station">
            {d.stations.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.id}</option>)}
          </select>
        </div>
      </div>

      <div className="kpis">
        <div><span className="meta">Drawing now</span><span className="kpi-value num">{drawing.toFixed(1)}<span className="unit">kW</span></span></div>
        <div><span className="meta">Energy today</span><span className="kpi-value num">{kwh(today)}<span className="unit">kWh</span></span></div>
        <div><span className="meta">Sessions today</span><span className="kpi-value num">{sessions.length}</span></div>
        <div><span className="meta">Connectors in service</span><span className="kpi-value num">{capacity.length} of {station.connectors.length}</span></div>
      </div>

      {down.map((c) => (
        <p className="notice alert" key={c.id}>
          <strong className="mono">{c.id}</strong> is out of service ({c.note ?? "fault"}{c.statusSince ? `, since ${clock(c.statusSince)}` : ""}). Fleets can&apos;t book it until it goes through maintenance and you reopen it.
        </p>
      ))}

      <div className="grid">
        <section className="panel span-12" aria-labelledby="conn-h">
          <header>
            <h2 id="conn-h">Connectors</h2>
            <span className="meta num">{station.connectors.filter((c) => c.status === "RESERVED").length} in use · {down.length} out of service</span>
          </header>
          <div className="table-wrap">
            <table className="expandable">
              <thead>
                <tr>
                  <th>Connector</th><th>Status</th><th>Fleet</th>
                  <th className="n">Power now</th><th className="n hide-phone">Last session</th><th className="n">Today</th><th className="n"><span className="hide-phone">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {station.connectors.map((c) => {
                  const mine = byConnector(c.id);
                  const current = c.sagaId ? sessions.find((s) => s.sagaId === c.sagaId) : undefined;
                  const p = live(c);
                  const expanded = open === c.id;
                  return (
                    <Fragment key={c.id}>
                      <tr className={expanded ? "current" : undefined} onClick={() => setOpen(expanded ? null : c.id)}>
                        <td><span className="mono">{c.id}</span><span className="sub">{c.type === "ULTRA" ? "UltraFast" : "Fast"} · {c.powerKw} kW</span></td>
                        <td>
                          <StateBadge state={c.status} />
                          {c.note && c.status !== "AVAILABLE" && <span className="sub wrap">{c.note}</span>}
                          {c.status === "AVAILABLE" && !!c.faultCount && <span className="sub wrap">{c.faultCount} of 3 faults</span>}
                        </td>
                        <td>{c.status === "RESERVED" ? <strong>{current?.fleetId ?? "—"}</strong> : <span className="dash">—</span>}</td>
                        <td className="n num">{p ? <>{p.powerKw}<span className="unit">kW</span></> : <span className="dash">—</span>}</td>
                        <td className="n num hide-phone">{mine[0] ? <>{kwh(mine[0].kwhDelivered)}<span className="unit">kWh</span></> : <span className="dash">—</span>}</td>
                        <td className="n num">{kwh(mine.reduce((a, s) => a + s.kwhDelivered, 0))}<span className="unit">kWh</span></td>
                        <td className="act" onClick={(e) => e.stopPropagation()}>
                          <Actions c={c} disabled={busy || !token}
                            onMaintenance={() => setOpen(c.id)}
                            onReopen={() => run(() => lifecycle({ variables: { stationId: station.id, connectorId: c.id, action: "reopen", note: "" } }))}
                            onRemove={() => confirm(`Remove ${c.id} from ${station.name}?`) && run(() => remove({ variables: { stationId: station.id, connectorId: c.id } }))} />
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="flow-row">
                          <td colSpan={7}>
                            <ConnectorDetail c={c} sessions={mine} now={now} disabled={busy || !token}
                              onMaintenance={(note) => run(() => lifecycle({ variables: { stationId: station.id, connectorId: c.id, action: "maintenance", note } }))} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {error && <p className="error" role="alert" style={{ marginTop: 12 }}>{error}</p>}
        </section>
      </div>

      <div className="grid">
        <section className="panel span-8" aria-labelledby="sess-h">
          <header>
            <h2 id="sess-h">Sessions today</h2>
            <span className="meta num">{kwh(today)} kWh delivered of {kwh(sessions.reduce((a, s) => a + s.kwhRequested, 0))} requested</span>
          </header>
          <SessionTable sessions={sessions} now={now} showConnector />
        </section>
        <div className="span-4 stack side">
          <section className="panel" aria-labelledby="manage-h">
            <header><h2 id="manage-h">Manage the station</h2></header>
            <p className="lead">Add a bay, or remove one nobody is charging on. Maintenance and reopening are on each connector.</p>
            <div className="token">
              <button className="btn" disabled={busy || !token} onClick={() => run(() => add({ variables: { stationId: station.id, type: "FAST" } }))}>Add Fast 150 kW</button>
              <button className="btn" disabled={busy || !token} onClick={() => run(() => add({ variables: { stationId: station.id, type: "ULTRA" } }))}>Add UltraFast 350 kW</button>
            </div>
            <TokenForm token={token} onToken={setToken} what="manage the station" />
          </section>
          <section className="panel" aria-labelledby="fleets-h">
            <header><h2 id="fleets-h">Energy by fleet today</h2></header>
            <div className="rows">
              {sessions.length === 0 && <p className="row meta">No sessions yet today.</p>}
              {Object.entries(sessions.reduce<Record<string, number>>((m, s) => ({ ...m, [s.fleetId ?? NO_FLEET]: (m[s.fleetId ?? NO_FLEET] ?? 0) + s.kwhDelivered }), {}))
                .sort((a, b) => b[1] - a[1])
                .map(([fleet, total]) => (
                  <div className="row" key={fleet}><span className="grow">{fleet}</span><span className="n num">{kwh(total)}<span className="unit">kWh</span></span></div>
                ))}
            </div>
          </section>
        </div>
      </div>
    </>
  );
}

function Actions({ c, disabled, onMaintenance, onReopen, onRemove }: {
  c: Connector; disabled: boolean; onMaintenance: () => void; onReopen: () => void; onRemove: () => void;
}) {
  if (c.status === "RESERVED") return <span className="meta">Charging</span>;
  return (
    <span className="act-group">
      {c.status === "MAINTENANCE" ? (
        <button className="btn primary" disabled={disabled} onClick={onReopen}>Reopen</button>
      ) : (
        <button className={`btn${c.status === "OUT_OF_SERVICE" ? " primary" : ""}`} disabled={disabled} onClick={onMaintenance}>
          {c.status === "OUT_OF_SERVICE" ? "Request maintenance" : "Maintenance"}
        </button>
      )}
      <button className="text-btn" disabled={disabled} onClick={onRemove}>Remove</button>
    </span>
  );
}

function ConnectorDetail({ c, sessions, now, disabled, onMaintenance }: {
  c: Connector; sessions: Session[]; now: number; disabled: boolean; onMaintenance: (note: string) => void;
}) {
  const [note, setNote] = useState(c.status === "OUT_OF_SERVICE" ? `Inspect charger: ${c.note ?? "hardware fault"}` : "");
  const canRequest = c.status === "AVAILABLE" || c.status === "OUT_OF_SERVICE";
  return (
    <div className="flow">
      {canRequest && (
        <form className="maint" onSubmit={(e) => { e.preventDefault(); onMaintenance(note || "Scheduled maintenance"); }}>
          <label htmlFor={`note-${c.id}`}>Maintenance request for <span className="mono">{c.id}</span></label>
          <div className="token">
            <input id={`note-${c.id}`} className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What should the technician do?" maxLength={300} />
            <button className="btn primary" disabled={disabled}>Send to maintenance</button>
          </div>
          <span className="meta">The connector stays closed to fleets until you reopen it after the work.</span>
        </form>
      )}
      {c.status === "MAINTENANCE" && (
        <p className="meta">Under maintenance{c.statusSince ? ` since ${when(c.statusSince)}` : ""}: {c.note}. Reopen it once the technician is done.</p>
      )}
      <h3 style={{ margin: "16px 0 8px" }}>Sessions today on this connector</h3>
      <SessionTable sessions={sessions} now={now} />
    </div>
  );
}

function SessionTable({ sessions, now, showConnector }: { sessions: Session[]; now: number; showConnector?: boolean }) {
  if (sessions.length === 0) return <p className="empty">No sessions today.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Started</th>{showConnector && <th>Connector</th>}<th>Fleet</th><th>Result</th>
            <th className="n">Delivered</th><th className="n hide-phone">Requested</th><th className="n hide-phone">Duration</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => {
            const end = s.endedAt ? Date.parse(s.endedAt) : s.status === "ACTIVE" ? now : Date.parse(s.startedAt);
            const secs = Math.max(0, Math.round((end - Date.parse(s.startedAt)) / 1000));
            return (
              <tr key={s.sagaId}>
                <td className="num">{clock(s.startedAt)}<span className="sub mono">{s.sagaId.slice(0, 8)}</span></td>
                {showConnector && <td className="mono">{s.connectorId}</td>}
                <td>{s.fleetId ?? <span className="dash">—</span>}</td>
                <td><span className={`pill ${s.status === "ACTIVE" ? "run" : s.status === "FAILED" ? "neg" : ""}`}>{SESSION_LABEL[s.status] ?? s.status}</span></td>
                <td className="n num">{kwh(s.kwhDelivered)}<span className="unit">kWh</span></td>
                <td className="n num hide-phone">{kwh(s.kwhRequested)}<span className="unit">kWh</span></td>
                <td className="n num hide-phone">{Math.floor(secs / 60)}:{String(secs % 60).padStart(2, "0")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
