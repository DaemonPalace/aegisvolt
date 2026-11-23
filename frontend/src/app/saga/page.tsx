"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, Suspense, useState } from "react";
import { useQuery } from "@apollo/client/react";
import { SagaFlow } from "@/components/saga-flow";
import { outcome } from "@/components/saga-track";
import { StateBadge } from "@/components/state-badge";
import { money, shortId, when } from "@/lib/format";
import { SAGA, SAGAS, SAGA_WALLET, TERMINAL, type Saga, type Wallet } from "@/lib/gql";
import { useConfig, useFleet } from "@/lib/runtime";

// Static export can't serve dynamic routes, so one saga is ?id=.
export default function TransactionsPage() {
  return (
    <Suspense>
      <Route />
    </Suspense>
  );
}

function Route() {
  const id = useSearchParams().get("id");
  return id ? <SagaDetail id={id} /> : <Transactions />;
}

/** The fleet's charges, newest first; a row opens to its SAGA orchestration (finished or compensated). */
function Transactions() {
  const { fleet } = useFleet();
  const [open, setOpen] = useState<string | null>(null);
  const { data, error } = useQuery<{ sagas: Saga[]; wallet: Wallet }>(SAGAS, { variables: { fleetId: fleet }, pollInterval: 2000 });
  const sagas = (data?.sagas ?? []).filter((s) => s.fleetId === fleet);
  const count = (st: string) => sagas.filter((s) => s.status === st).length;

  return (
    <>
      <div className="title-row">
        <div>
          <h1>Transactions</h1>
          <p className="sub">Every charge of {fleet}, newest first. Open one to see how the saga ran: each step, each compensation, and the money it moved.</p>
        </div>
        <div className="actions balance-chip">
          <span className="meta">Balance</span>
          <span className="kpi-value num">{data ? money(data.wallet.balanceCents) : "—"}</span>
        </div>
      </div>
      {error && <p className="notice">Couldn&apos;t load transactions ({error.message}).</p>}
      <section className="panel" aria-labelledby="tx-h">
        <header>
          <h2 id="tx-h">Charges</h2>
          <span className="meta num">{sagas.length} total · {count("FINISHED")} charged · {count("COMPENSATED")} compensated · {count("FAILED")} rejected</span>
        </header>
        {sagas.length === 0 ? (
          <p className="empty">{data ? "No charges yet for this fleet." : "Loading…"}</p>
        ) : (
          <div className="table-wrap">
            <table className="expandable">
              <thead>
                <tr>
                  <th>Charge</th>
                  <th>Outcome</th>
                  <th className="n hide-phone">Energy</th>
                  <th className="n">Amount</th>
                  <th className="n hide-phone">Started</th>
                  <th className="n"><span className="hide-phone">Flow</span></th>
                </tr>
              </thead>
              <tbody>
                {sagas.map((s) => {
                  const expanded = open === s.id;
                  return (
                    <Fragment key={s.id}>
                      <tr className={expanded ? "current" : undefined} onClick={() => setOpen(expanded ? null : s.id)}>
                        <td>
                          <span className="mono">{s.connectorId}</span>
                          <span className="sub mono">{shortId(s.id)}</span>
                        </td>
                        <td>
                          <StateBadge state={s.status} />
                          <span className="sub wrap">{outcome(s, true)}</span>
                        </td>
                        <td className="n num hide-phone">{s.kwh}<span className="unit">kWh</span></td>
                        <td className="n num">{money(s.amountCents)}</td>
                        <td className="n num hide-phone">{when(s.createdAt)}</td>
                        <td className="act">
                          <button className="btn" aria-expanded={expanded} onClick={(e) => { e.stopPropagation(); setOpen(expanded ? null : s.id); }}>
                            {expanded ? "Hide" : "Show"}
                          </button>
                        </td>
                      </tr>
                      {expanded && (
                        <tr className="flow-row">
                          <td colSpan={6}>
                            <p className="meta">{outcome(s)} <Link href={`/saga/?id=${s.id}`} className="card-link">Open on its own page</Link></p>
                            <SagaFlow saga={s} moves={(data?.wallet.transactions ?? []).filter((t) => t.sagaId === s.id)} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
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
  if (saga && TERMINAL.has(saga.status) && !done) setDone(true); // stop polling at a terminal state
  const { data: w } = useQuery<{ wallet: Wallet }>(SAGA_WALLET, { variables: { fleetId: saga?.fleetId ?? "" }, skip: !saga, pollInterval: done ? 0 : 2000 });

  if (loading && !saga) return <p className="meta">Loading saga {id.slice(0, 8)}…</p>;
  if (error && !saga) return <p className="notice">Couldn&apos;t load saga {id.slice(0, 8)} ({error.message}).</p>;
  if (!saga) {
    return (
      <div className="title-row">
        <div>
          <h1>Saga not found</h1>
          <p className="sub">No saga <span className="mono">{id}</span> in {env}. <Link href="/saga/" className="card-link">See all transactions</Link></p>
        </div>
      </div>
    );
  }
  return (
    <>
      <div className="title-row">
        <div>
          <h1>Saga {saga.id.slice(0, 8)} <StateBadge state={saga.status} /></h1>
          <p className="sub">{outcome(saga)} <Link href="/saga/" className="card-link">All transactions</Link></p>
        </div>
      </div>
      <section className="panel" aria-labelledby="track-h">
        <header>
          <h2 id="track-h">Reserve &amp; activate charge</h2>
          <span className="meta num">{saga.connectorId} · {saga.fleetId} · {saga.kwh} kWh · started {when(saga.createdAt)}</span>
        </header>
        <SagaFlow saga={saga} moves={(w?.wallet.transactions ?? []).filter((t) => t.sagaId === saga.id)} big />
      </section>
    </>
  );
}
