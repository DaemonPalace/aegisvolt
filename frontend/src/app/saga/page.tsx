"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useMutation, useQuery } from "@apollo/client/react";
import { History } from "@/components/history";
import { FORWARD, SagaTrack, outcome } from "@/components/saga-track";
import { StateBadge } from "@/components/state-badge";
import { WalletPanel } from "@/components/wallet";
import { clock, money, secs, when } from "@/lib/format";
import { CONSOLE, FLEETS, SAGA, SAGA_WALLET, STOP_CHARGE, TERMINAL, type Saga, type Wallet } from "@/lib/gql";
import { useConfig } from "@/lib/runtime";

// Ported from wandersync reservas/[id]/page.tsx. Static export can't serve dynamic routes, so the id is ?id=.
export default function SagaPage() {
  return (
    <Suspense>
      <SagaRoute />
    </Suspense>
  );
}

function SagaRoute() {
  const id = useSearchParams().get("id");
  return id ? <SagaDetail id={id} /> : <SagaList />;
}

function SagaList() {
  const [fleet, setFleet] = useState(FLEETS[0]);
  const { data, error } = useQuery<{ sagas: Saga[]; wallet: Wallet }>(CONSOLE, { variables: { fleetId: fleet }, pollInterval: 2000 });
  const [stop] = useMutation(STOP_CHARGE, { refetchQueries: [CONSOLE] });
  const [stopping, setStopping] = useState<string | null>(null);
  const sagas = data?.sagas ?? [];
  return (
    <>
      <div className="title-row">
        <div>
          <h1>Sagas</h1>
          <p className="sub">The 20 latest &ldquo;reserve &amp; activate charge&rdquo; sagas, newest first. Open one to see its steps and compensations.</p>
        </div>
      </div>
      {error && <p className="notice">Couldn&apos;t load sagas ({error.message}).</p>}
      <div className="grid">
        <div className="span-8">
          <History sagas={sagas} onStop={async (sid) => { setStopping(sid); try { await stop({ variables: { sagaId: sid } }); } finally { setStopping(null); } }} stopping={stopping} />
        </div>
        <div className="span-4">
          <WalletPanel wallet={data?.wallet} fleets={FLEETS} fleet={fleet} onFleet={setFleet} />
        </div>
      </div>
    </>
  );
}

function SagaDetail({ id }: { id: string }) {
  const { env } = useConfig();
  const [done, setDone] = useState(false);
  const { data, error, loading } = useQuery<{ saga: Saga | null }>(SAGA, {
    variables: { id }, pollInterval: done ? 0 : 2000, fetchPolicy: "network-only", notifyOnNetworkStatusChange: false,
  });
  const saga = data?.saga;
  if (saga && TERMINAL.has(saga.status) && saga.status !== "COMPLETED" && !done) setDone(true); // stop polling at a terminal state
  const { data: w } = useQuery<{ wallet: Wallet }>(SAGA_WALLET, { variables: { fleetId: saga?.fleetId ?? "" }, skip: !saga, pollInterval: done ? 0 : 2000 });
  const [stop, { loading: stopping }] = useMutation(STOP_CHARGE, { refetchQueries: [SAGA] });

  if (loading && !saga) return <p className="meta">Loading saga {id.slice(0, 8)}…</p>;
  if (error && !saga) return <p className="notice">Couldn&apos;t load saga {id.slice(0, 8)} ({error.message}).</p>;
  if (!saga) {
    return (
      <div className="title-row">
        <div>
          <h1>Saga not found</h1>
          <p className="sub">No saga <span className="mono">{id}</span> in {env}. <Link href="/saga/" className="card-link">See all sagas</Link></p>
        </div>
      </div>
    );
  }

  const moves = (w?.wallet.transactions ?? []).filter((t) => t.sagaId === saga.id);
  const net = moves.reduce((sum, t) => sum + (t.kind === "CHARGE" ? -t.amountCents : t.amountCents), 0);
  const label = (name: string) => FORWARD.find((f) => f.name === name)?.label ?? name.charAt(0).toUpperCase() + name.slice(1);

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Saga {saga.id.slice(0, 8)} <StateBadge state={saga.status} /></h1>
          <p className="sub">{outcome(saga)}</p>
        </div>
        <div className="actions">
          {saga.status === "COMPLETED" && (
            <button className="btn primary" disabled={stopping} onClick={() => stop({ variables: { sagaId: saga.id } })}>
              {stopping ? "Stopping…" : "Stop charge"}
            </button>
          )}
        </div>
      </div>
      <div className="grid">
        <section className="panel span-12" aria-labelledby="track-h">
          <header>
            <h2 id="track-h">Reserve &amp; activate charge</h2>
            <span className="meta num">{saga.connectorId} · {saga.fleetId} · {saga.kwh} kWh · started {when(saga.createdAt)}</span>
          </header>
          <SagaTrack saga={saga} big />
          <div className="legend">
            <span><i /> Forward step</span>
            <span><i className="back" /> Compensation, newest step undone first</span>
          </div>
        </section>
      </div>
      <div className="grid">
        <section className="panel span-8" aria-labelledby="log-h">
          <header>
            <h2 id="log-h">Step log</h2>
            <span className="meta num">{saga.steps.length} entries</span>
          </header>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>Step</th><th>Result</th><th className="n">At</th><th className="n hide-phone">After start</th><th className="hide-phone">Service reply</th></tr>
              </thead>
              <tbody>
                {saga.steps.map((s, i) => (
                  <tr key={`${s.name}-${s.status}-${i}`}>
                    <td>{s.status === "COMPENSATED" ? FORWARD.find((f) => f.name === s.name)?.undo ?? label(s.name) : label(s.name)}</td>
                    <td><span className={`pill ${s.status === "OK" ? "pos" : s.status === "FAILED" ? "neg" : s.status === "COMPENSATED" ? "warn" : ""}`}>{s.status === "OK" ? "Done" : s.status === "FAILED" ? "Failed" : s.status === "COMPENSATED" ? "Compensated" : s.status}</span></td>
                    <td className="n num">{clock(s.at)}</td>
                    <td className="n num hide-phone">+{secs(saga.createdAt, s.at)}<span className="unit">s</span></td>
                    <td className="mono hide-phone" style={{ whiteSpace: "normal", color: "var(--muted)" }}>{s.detail ?? "—"}</td>
                  </tr>
                ))}
                {saga.steps.length === 0 && <tr><td colSpan={5} className="meta">No step has run yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
        <div className="span-4 stack">
          <section className="panel" aria-labelledby="money-h">
            <header>
              <h2 id="money-h">Wallet movements</h2>
              <span className="meta">{saga.fleetId}</span>
            </header>
            <div className="rows">
              {moves.length === 0 && <p className="row meta">This saga moved no money.</p>}
              {moves.map((t, i) => (
                <div className="row" key={`${t.kind}-${i}`}>
                  <span className="grow">{t.kind === "REFUND" ? "Refund" : "Charge"}<span className="sub num">{clock(t.createdAt)}</span></span>
                  <span className="n num">{t.kind === "CHARGE" ? "−" : "+"}{money(t.amountCents)}</span>
                </div>
              ))}
              {moves.length > 0 && (
                <div className="row"><strong className="grow">Net for the fleet</strong><strong className="n num">{net < 0 ? "−" : net > 0 ? "+" : ""}{money(Math.abs(net))}</strong></div>
              )}
            </div>
          </section>
          <section className="panel" aria-labelledby="trace-h">
            <header><h2 id="trace-h">Correlation ID</h2></header>
            <p className="mono" style={{ wordBreak: "break-all" }}>{saga.id}</p>
            <p className="meta" style={{ marginTop: 8 }}>Every service logs this ID. Search it in CloudWatch Logs Insights across /aegisvolt/{env}/*.</p>
            <button className="btn" style={{ marginTop: 12 }} onClick={() => navigator.clipboard.writeText(saga.id)}>Copy ID</button>
          </section>
        </div>
      </div>
    </>
  );
}
