"use client";

import { useQuery } from "@apollo/client/react";
import { ChargeWizard } from "@/components/charge-wizard";
import { Deposit, NewFleet } from "@/components/deposit";
import { FlagsPanel } from "@/components/flags-panel";
import { FleetSessions } from "@/components/fleet-sessions";
import { money } from "@/lib/format";
import { CHARGE, LIVE, type Flag, type Pricing, type Saga, type Station, type Wallet } from "@/lib/gql";
import { useConfig, useFleet } from "@/lib/runtime";

type Data = { stations: Station[]; flags: Flag[]; pricing: Pricing; sagas: Saga[]; wallet: Wallet };

export default function ChargePage() {
  const { env } = useConfig();
  const { fleet } = useFleet();
  const { data, previousData, error } = useQuery<Data>(CHARGE, { variables: { fleetId: fleet }, pollInterval: 2000 });
  const d = data ?? previousData;

  const on = (name: string) => d?.flags.find((f) => f.name === name)?.enabled ?? false;
  const live = (d?.sagas ?? []).filter((s) => s.fleetId === fleet && LIVE.has(s.status));

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Charge</h1>
          <p className="sub">
            Pick a station, a connector and the energy; the price follows the amount and surge pricing.
            {on("SIMULATE_CHARGER_HARDWARE_FAULT") && <> The hardware fault simulation is <strong>on</strong> in {env}: activation will fail and the charge will be refunded.</>}
          </p>
        </div>
        <div className="actions balance-chip">
          <span className="meta">{fleet} balance</span>
          <span className="kpi-value num">{d ? money(d.wallet.balanceCents) : "—"}</span>
        </div>
      </div>
      {error && !d && <p className="notice">Couldn&apos;t reach the {env} API ({error.message}). Retrying every 2 s.</p>}
      {d && (
        <div className="grid">
          <div className="span-8 stack">
            <ChargeWizard stations={d.stations} sagas={d.sagas} boost={on("ENABLE_FAST_CHARGE_BOOST")} pricing={d.pricing} balanceCents={d.wallet.balanceCents} />
            <FleetSessions sagas={live} stations={d.stations} />
          </div>
          <div className="span-4 stack side">
            <Deposit wallet={d.wallet} />
            <NewFleet />
            <FlagsPanel flags={d.flags} />
          </div>
        </div>
      )}
    </>
  );
}
