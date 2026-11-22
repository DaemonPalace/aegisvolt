"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { FlagsPanel } from "@/components/flags-panel";
import { History } from "@/components/history";
import { outcome } from "@/components/saga-track";
import { StartCharge } from "@/components/start-charge";
import { Stations } from "@/components/stations";
import { clock } from "@/lib/format";
import { CONSOLE, FLEETS, STOP_CHARGE, type Flag, type Health, type Saga, type Station, type Wallet } from "@/lib/gql";
import { useConfig } from "@/lib/runtime";

type Data = { health: Health; stations: Station[]; flags: Flag[]; sagas: Saga[]; wallet: Wallet };

export default function Console() {
  const { env } = useConfig();
  const [fleet, setFleet] = useState(FLEETS[0]);
  const [connector, setConnector] = useState("");
  const [stopping, setStopping] = useState<string | null>(null);
  const { data, previousData, error, refetch } = useQuery<Data>(CONSOLE, { variables: { fleetId: fleet }, pollInterval: 2000 });
  const [stop] = useMutation(STOP_CHARGE, { refetchQueries: [CONSOLE] });
  const d = data ?? previousData;
  const [stamp, setStamp] = useState("");
  useEffect(() => { if (data) setStamp(new Date().toISOString()); }, [data]);

  const boost = d?.flags.find((f) => f.name === "ENABLE_FAST_CHARGE_BOOST")?.enabled ?? false;
  const fault = d?.flags.find((f) => f.name === "SIMULATE_CHARGER_HARDWARE_FAULT")?.enabled ?? false;
  const latest = d?.sagas[0];

  const onStop = async (id: string) => {
    setStopping(id);
    try { await stop({ variables: { sagaId: id } }); } finally { setStopping(null); }
  };

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Charging console</h1>
          <p className="sub">
            {latest ? <><strong>Last charge:</strong> {outcome(latest)}</> : "No charges yet in this environment."}
            {fault && <> The hardware fault simulation is <strong>on</strong> in {env}: activation will fail and the saga will compensate.</>}
          </p>
        </div>
        <div className="actions">
          <Freshness ok={!error} stamp={stamp} onRetry={() => refetch()} />
        </div>
      </div>
      {error && !d && <p className="notice">Couldn&apos;t reach the {env} API ({error.message}). Retrying every 2 s.</p>}
      {d && (
        <>
          <div className="grid">
            <div className="span-8 stack">
              <Stations stations={d.stations} boost={boost} selected={connector} onPick={(c) => setConnector(c.id)} />
              <History sagas={d.sagas} cap={6} onStop={onStop} stopping={stopping} />
            </div>
            <div className="span-4 stack side">
              <StartCharge stations={d.stations} boost={boost} connector={connector} onConnector={setConnector}
                fleet={fleet} onFleet={setFleet} fleets={FLEETS} latest={latest} balanceCents={d.wallet.balanceCents} />
              <FlagsPanel flags={d.flags} />
            </div>
          </div>
        </>
      )}
    </>
  );
}

/** The time the last poll answered; on an error the old values stay and a Retry appears. */
function Freshness({ ok, stamp, onRetry }: { ok: boolean; stamp: string; onRetry: () => void }) {
  return (
    <>
      {stamp && <span className="stamp num">Updated {clock(stamp)} · every 2 s</span>}
      {!ok && <button className="btn" onClick={onRetry}>Retry</button>}
    </>
  );
}
