import Link from "next/link";
import { money, shortId, when } from "@/lib/format";
import type { Wallet } from "@/lib/gql";

/** Fleet wallet: the balance the charge step debits, and its latest movements (refunds are compensations). */
export function WalletPanel({ wallet, fleets, fleet, onFleet }: {
  wallet: Wallet | undefined; fleets: string[]; fleet: string; onFleet: (f: string) => void;
}) {
  const txns = wallet?.transactions.slice(0, 8) ?? [];
  return (
    <section className="panel" aria-labelledby="wallet-h">
      <header>
        <h2 id="wallet-h">Fleet wallet</h2>
        <select className="input" style={{ width: "auto", height: 32 }} value={fleet} onChange={(e) => onFleet(e.target.value)} aria-label="Fleet">
          {fleets.map((f) => <option key={f}>{f}</option>)}
        </select>
      </header>
      <div className="balance">
        <span className="meta">Balance</span>
        <span className="kpi-value num">{wallet ? money(wallet.balanceCents) : <span className="skeleton" style={{ height: 28, width: 120 }} />}</span>
      </div>
      <div className="rows">
        {wallet && txns.length === 0 && <p className="row meta">No movements yet.</p>}
        {txns.map((t, i) => (
          <div className="row" key={`${t.sagaId}-${t.kind}-${i}`}>
            <span className="grow">
              {t.kind === "REFUND" ? "Refund" : t.kind === "CHARGE" ? "Charge" : t.kind === "TOPUP" ? "Top-up" : t.kind}
              <span className="sub">
                {t.sagaId ? <Link href={`/saga/?id=${t.sagaId}`} className="mono">{shortId(t.sagaId)}</Link> : "Manual"} · {when(t.createdAt)}
              </span>
            </span>
            <span className="n num" style={{ color: t.kind === "CHARGE" ? "var(--text)" : "var(--positive)" }}>
              {t.kind === "CHARGE" ? "−" : "+"}{money(t.amountCents)}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
