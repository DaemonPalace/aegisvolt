"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { outcome } from "@/components/saga-track";
import { countdown, money } from "@/lib/format";
import { LIVE, SAGAS, TERMINAL, progress, type Saga, type Wallet } from "@/lib/gql";
import { useFleet, useNow } from "@/lib/runtime";

const LINGER_MS = 20_000; // a finished toast stays this long, then goes by itself

/**
 * One pop-up per live charge of the fleet: it follows the saga step by step, counts down while energy flows,
 * and ends as "done charging" or "compensated". Mounted in the shell, so it runs on every tab.
 */
export function Toasts() {
  const { fleet, watched } = useFleet();
  const now = useNow();
  const { data } = useQuery<{ sagas: Saga[]; wallet: Wallet }>(SAGAS, { variables: { fleetId: fleet }, pollInterval: 2000 });
  // id → when it reached a terminal state (0 while still live); only sagas seen live (or started here) get a toast
  const [seen, setSeen] = useState<Record<string, number>>({});
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  const mine = (data?.sagas ?? []).filter((s) => s.fleetId === fleet);
  useEffect(() => {
    setSeen((prev) => {
      let next = prev;
      for (const s of mine) {
        const tracked = s.id in prev || watched.has(s.id) || LIVE.has(s.status);
        if (!tracked) continue;
        const ended = TERMINAL.has(s.status) ? prev[s.id] || Date.now() : 0;
        if (prev[s.id] !== ended) next = { ...next, [s.id]: ended };
      }
      return next;
    });
  }, [data, watched]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = mine.filter((s) => s.id in seen && !dismissed.has(s.id) && (!seen[s.id] || now - seen[s.id] < LINGER_MS));
  const refund = (id: string) => data?.wallet.transactions.find((t) => t.sagaId === id && t.kind === "REFUND")?.amountCents;
  const close = (id: string) => setDismissed((d) => new Set(d).add(id));

  return (
    <div className="toasts" role="status" aria-live="polite">
      {shown.map((s) => <Toast key={s.id} saga={s} now={now} refund={refund(s.id)} onClose={() => close(s.id)} />)}
    </div>
  );
}

function Toast({ saga: s, now, refund, onClose }: { saga: Saga; now: number; refund?: number; onClose: () => void }) {
  const done = TERMINAL.has(s.status);
  let tone = "run", title: string, body: React.ReactNode;
  switch (s.status) {
    case "STARTED": {
      const next = ["reserve", "charge", "activate"].find((n) => !s.steps.some((x) => x.name === n));
      const undoing = s.steps.some((x) => x.status === "FAILED");
      if (undoing) tone = "warn";
      title = undoing ? `Undoing the charge on ${s.connectorId}` : `Starting on ${s.connectorId}`;
      body = <Steps saga={s} next={next} />;
      break;
    }
    case "COMPLETED":
      title = `Charging on ${s.connectorId}`;
      body = (
        <>
          <span className="bar" aria-hidden="true"><i style={{ width: `${Math.round(progress(s, now) * 100)}%` }} /></span>
          <span className="num">{s.endsAt ? `${countdown(Date.parse(s.endsAt) - now)} until ${s.kwh} kWh` : `${s.kwh} kWh`} · {money(s.amountCents)} paid</span>
        </>
      );
      break;
    case "FINISHED":
      tone = "pos"; title = `Done charging on ${s.connectorId}`;
      body = `${s.kwh} kWh delivered for ${money(s.amountCents)}. The connector is free again.`;
      break;
    case "COMPENSATED":
      tone = "warn"; title = `Charge on ${s.connectorId} compensated`;
      body = <>{outcome(s)}{refund ? ` ${money(refund)} back in the wallet.` : ""}</>;
      break;
    default:
      tone = s.status === "STOPPED" ? "" : "neg";
      title = s.status === "STOPPED" ? `Stopped on ${s.connectorId}` : `Charge on ${s.connectorId} did not start`;
      body = outcome(s);
  }
  return (
    <div className={`toast ${tone}`}>
      <div className="toast-head">
        <strong>{title}</strong>
        {done && <button className="text-btn" onClick={onClose} aria-label="Dismiss">Close</button>}
      </div>
      <div className="toast-body">{body}</div>
      <Link href={`/saga/?id=${s.id}`} className="card-link">See the saga</Link>
    </div>
  );
}

function Steps({ saga, next }: { saga: Saga; next?: string }) {
  return (
    <ol className="mini-steps">
      {["reserve", "charge", "activate"].map((n) => {
        const st = saga.steps.find((x) => x.name === n);
        return <li key={n} className={st ? (st.status === "OK" ? "done" : "failed") : n === next ? "running" : ""}>{n}</li>;
      })}
    </ol>
  );
}
