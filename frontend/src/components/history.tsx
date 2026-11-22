import Link from "next/link";
import { outcome } from "@/components/saga-track";
import { StateBadge } from "@/components/state-badge";
import { money, shortId, when } from "@/lib/format";
import type { Saga } from "@/lib/gql";

/** Saga history: newest first. On the console it is capped with "View all"; on /saga/ it shows all 20. */
export function History({ sagas, cap, onStop, stopping }: {
  sagas: Saga[]; cap?: number; onStop: (id: string) => void; stopping: string | null;
}) {
  const rows = cap ? sagas.slice(0, cap) : sagas;
  const compensated = sagas.filter((s) => s.status === "COMPENSATED").length;
  return (
    <section className="panel" aria-labelledby="history-h">
      <header>
        <h2 id="history-h">Saga history</h2>
        <span className="meta num">
          {sagas.length} latest · {compensated} compensated
          {cap && sagas.length > cap && <> · <Link href="/saga/" className="card-link">View all</Link></>}
        </span>
      </header>
      {rows.length === 0 ? (
        <p className="empty">No charges yet in this environment. Start one from a free connector.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Saga</th>
                <th>Outcome</th>
                <th className="n hide-phone">Energy</th>
                <th className="n">Amount</th>
                <th className="n hide-phone">Started</th>
                <th className="n"><span className="hide-phone">Action</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id}>
                  <td>
                    <Link href={`/saga/?id=${s.id}`} className="card-link mono">{shortId(s.id)}</Link>
                    <span className="sub">{s.connectorId} · {s.fleetId}</span>
                  </td>
                  <td>
                    <StateBadge state={s.status} />
                    <span className="sub wrap">{outcome(s, true)}</span>
                  </td>
                  <td className="n num hide-phone">{s.kwh}<span className="unit">kWh</span></td>
                  <td className="n num">{money(s.amountCents)}</td>
                  <td className="n num hide-phone">{when(s.createdAt)}</td>
                  <td className="act">
                    {s.status === "COMPLETED" ? (
                      <button className="btn" disabled={stopping === s.id} onClick={() => onStop(s.id)}>
                        {stopping === s.id ? "Stopping…" : "Stop"}
                      </button>
                    ) : (
                      <span className="dash">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
