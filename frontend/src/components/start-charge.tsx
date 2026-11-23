"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation } from "@apollo/client/react";
import { SagaTrack, outcome } from "@/components/saga-track";
import { money } from "@/lib/format";
import { CONSOLE, START_CHARGE, type Connector, type Saga, type Station } from "@/lib/gql";

/** Start charge: triggers the SAGA (reserve → charge → activate) and follows the newest saga on its track. */
export function StartCharge({ stations, boost, connector, onConnector, fleet, onFleet, fleets, latest, balanceCents }: {
  stations: Station[]; boost: boolean; connector: string; onConnector: (id: string) => void;
  fleet: string; onFleet: (f: string) => void; fleets: string[]; latest: Saga | undefined; balanceCents: number;
}) {
  const [kwh, setKwh] = useState("10");
  const [error, setError] = useState("");
  const [start, { loading }] = useMutation<{ startCharge: Saga }>(START_CHARGE, { refetchQueries: [CONSOLE] });
  const usable = (c: Connector) => c.status === "AVAILABLE" && (c.type !== "ULTRA" || boost);
  const value = Number(kwh);
  const valid = connector && value > 0 && value <= 100;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const station = stations.find((s) => s.connectors.some((c) => c.id === connector));
    try {
      await start({ variables: { input: { fleetId: fleet, stationId: station?.id, connectorId: connector, kwh: value, idempotencyKey: newKey() } } });
      onConnector("");
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <section className="panel" aria-labelledby="start-h">
      <header>
        <h2 id="start-h">Start a charge</h2>
      </header>
      <form className="charge" onSubmit={submit}>
        <div className="field">
          <label htmlFor="connector">Connector</label>
          <select id="connector" className="input" value={connector} onChange={(e) => onConnector(e.target.value)}>
            <option value="">Pick a free connector</option>
            {stations.map((s) => (
              <optgroup key={s.id} label={`${s.name} · ${s.city}`}>
                {s.connectors.map((c) => (
                  <option key={c.id} value={c.id} disabled={!usable(c)}>
                    {c.id} · {c.type === "ULTRA" ? "Ultra" : "Fast"} {c.powerKw} kW{!usable(c) ? (c.status !== "AVAILABLE" ? " · in use" : " · boost off") : ""}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <div className="grid" style={{ gap: 16 }}>
          <div className="field" style={{ gridColumn: "span 7" }}>
            <label htmlFor="fleet">Fleet</label>
            <select id="fleet" className="input" value={fleet} onChange={(e) => onFleet(e.target.value)}>
              {fleets.map((f) => <option key={f}>{f}</option>)}
            </select>
            <span className="hint num">Balance {money(balanceCents)}</span>
          </div>
          <div className="field" style={{ gridColumn: "span 5" }}>
            <label htmlFor="kwh">Energy, kWh</label>
            <input id="kwh" className="input num" inputMode="decimal" value={kwh} onChange={(e) => setKwh(e.target.value)} />
          </div>
        </div>
        {kwh && !(value > 0 && value <= 100) && <p className="error">Energy must be more than 0 and at most 100 kWh.</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn primary block" disabled={!valid || loading}>{loading ? "Starting…" : "Start charge"}</button>
      </form>
      <hr className="divider" />
      {latest ? (
        <>
          <div className="track-head">
            <h3 className="name" style={{ fontSize: 14 }}>Latest saga</h3>
            <Link href={`/saga/?id=${latest.id}`} className="card-link">Open {latest.id.slice(0, 8)}</Link>
          </div>
          <SagaTrack saga={latest} />
          <p className="meta" style={{ marginTop: 12 }}>{outcome(latest)}</p>
        </>
      ) : (
        <p className="empty">The track of the next charge appears here.</p>
      )}
    </section>
  );
}

// crypto.randomUUID exists only in secure contexts; the S3 website is plain HTTP. getRandomValues works on both.
// The key becomes the saga id, so keep the uuid4 shape the gateway uses for its own ids.
function newKey(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
