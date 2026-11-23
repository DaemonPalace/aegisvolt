"use client";

import { useState } from "react";
import { useMutation } from "@apollo/client/react";
import { money } from "@/lib/format";
import { CHARGE, START_CHARGE, quote, type Connector, type Pricing, type Saga, type Station } from "@/lib/gql";
import { useFleet } from "@/lib/runtime";

const KIND = { FAST: "Fast", ULTRA: "UltraFast" } as const;

/** Station → connector → energy → start. Starting triggers the SAGA; the toast takes it from there. */
export function ChargeWizard({ stations, sagas, boost, pricing, balanceCents }: {
  stations: Station[]; sagas: Saga[]; boost: boolean; pricing: Pricing; balanceCents: number;
}) {
  const { fleet, watch } = useFleet();
  const [stationId, setStationId] = useState("");
  const [connectorId, setConnectorId] = useState("");
  const [kwh, setKwh] = useState(10);
  const [error, setError] = useState("");
  const [start, { loading }] = useMutation<{ startCharge: Saga }>(START_CHARGE, { refetchQueries: [CHARGE] });

  const station = stations.find((s) => s.id === stationId);
  const connector = station?.connectors.find((c) => c.id === connectorId);
  const ours = (c: Connector) => !!c.sagaId && sagas.some((s) => s.id === c.sagaId && s.fleetId === fleet);
  const usable = (c: Connector) => c.status === "AVAILABLE" && (c.type !== "ULTRA" || boost);
  const price = quote(kwh, pricing);
  const short = price > balanceCents;
  // A connector taken between polls stays selected but greys out; the button follows.
  const ready = connector && usable(connector) && kwh > 0 && kwh <= 100;

  const pickStation = (id: string) => { setStationId(id); setConnectorId(""); setError(""); };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!station || !connector) return;
    setError("");
    try {
      const { data } = await start({ variables: { input: { fleetId: fleet, stationId: station.id, connectorId: connector.id, kwh, idempotencyKey: newKey() } } });
      if (data) watch(data.startCharge.id);
      setConnectorId("");
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <section className="panel" aria-labelledby="wizard-h">
      <header>
        <h2 id="wizard-h">Start a charge</h2>
        <span className="meta">Charging as {fleet}</span>
      </header>
      <form className="wizard" onSubmit={submit}>
        <fieldset>
          <legend><span className="step-no">1</span>Choose a station</legend>
          <div className="choices three">
            {stations.map((s) => {
              const free = s.connectors.filter(usable).length;
              const ultra = s.connectors.some((c) => c.type === "ULTRA" && usable(c));
              const mine = s.connectors.filter(ours).length;
              return (
                <button type="button" key={s.id} className="choice" aria-pressed={stationId === s.id} onClick={() => pickStation(s.id)}>
                  <strong>{s.name}</strong>
                  <span className="sub">{s.city} · <span className="mono">{s.id}</span></span>
                  <span className="tags">
                    <span className={`pill ${free ? "pos" : ""}`}>{free} of {s.connectors.length} free</span>
                    {ultra && <span className="pill run">UltraFast free</span>}
                    {mine > 0 && <span className="pill warn">{mine} yours</span>}
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>

        <fieldset disabled={!station}>
          <legend><span className="step-no">2</span>Choose a connector</legend>
          {!station ? (
            <p className="empty">Pick a station first.</p>
          ) : (
            <div className="choices three">
              {station.connectors.map((c) => {
                const locked = c.type === "ULTRA" && !boost;
                const why = c.status === "OUT_OF_SERVICE" ? "Out of service" : c.status === "MAINTENANCE" ? "Under maintenance"
                  : c.status !== "AVAILABLE" ? (ours(c) ? "Charging your fleet" : "In use") : locked ? "Locked: fast-charge boost is off" : "Available";
                return (
                  <button type="button" key={c.id} className={`choice${c.type === "ULTRA" ? " ultra" : ""}`} aria-pressed={connectorId === c.id}
                    disabled={!usable(c)} onClick={() => setConnectorId(c.id)}>
                    <strong>{KIND[c.type]} <span className="num">{c.powerKw} kW</span></strong>
                    <span className="sub mono">{c.id}</span>
                    <span className="tags"><span className={`pill ${usable(c) ? "pos" : ours(c) ? "warn" : c.status === "OUT_OF_SERVICE" ? "neg" : ""}`}>{why}</span></span>
                  </button>
                );
              })}
            </div>
          )}
        </fieldset>

        <fieldset disabled={!connector}>
          <legend><span className="step-no">3</span>Choose the energy</legend>
          <div className="energy">
            <input type="range" min={1} max={100} step={1} value={kwh} onChange={(e) => setKwh(Number(e.target.value))} aria-label="Energy in kWh" />
            <label className="kwh-box">
              <input className="input num" type="number" min={1} max={100} step={1} value={kwh}
                onChange={(e) => setKwh(Math.min(100, Math.max(1, Number(e.target.value) || 1)))} aria-label="Energy, kWh" />
              <span className="unit">kWh</span>
            </label>
          </div>
          <dl className="quote">
            <div><dt>Rate</dt><dd className="num">{money(pricing.rateCentsPerKwh)} / kWh</dd></div>
            <div>
              <dt>Surge pricing</dt>
              <dd className="num">{pricing.surge ? <span className="pill warn">On · ×{pricing.surgeMultiplier}</span> : <span className="pill">Off · ×1</span>}</dd>
            </div>
            <div className="total"><dt>Price for {kwh} kWh</dt><dd className="num">{money(price)}</dd></div>
            <div><dt>Balance after</dt><dd className={`num${short ? " neg" : ""}`}>{short ? "Not enough funds" : money(balanceCents - price)}</dd></div>
          </dl>
          {short && <p className="notice">The wallet has {money(balanceCents)}. The charge step will be refused and the saga will release the connector.</p>}
        </fieldset>

        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn primary block" disabled={!ready || loading}>
          {loading ? "Starting…" : ready ? `Charge ${kwh} kWh on ${connector.id} for ${money(price)}` : "Choose a station and a connector"}
        </button>
      </form>
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
