import { FORWARD, SagaTrack } from "@/components/saga-track";
import { clock, money, secs } from "@/lib/format";
import type { Saga, Txn } from "@/lib/gql";

const CLOSE: Record<string, string> = { stop: "Switch charger off", release: "Release connector" };
const label = (name: string, status: string) =>
  status === "COMPENSATED" ? FORWARD.find((f) => f.name === name)?.undo ?? name
    : FORWARD.find((f) => f.name === name)?.label ?? CLOSE[name] ?? name;
const RESULT: Record<string, [string, string]> = { OK: ["pos", "Done"], FAILED: ["neg", "Failed"], COMPENSATED: ["warn", "Compensated"] };

/** The orchestration of one saga: the track, every step the orchestrator recorded, and the money it moved. */
export function SagaFlow({ saga, moves, big }: { saga: Saga; moves: Txn[]; big?: boolean }) {
  const net = moves.reduce((sum, t) => sum + (t.kind === "CHARGE" ? -t.amountCents : t.amountCents), 0);
  return (
    <div className="flow">
      <SagaTrack saga={saga} big={big} />
      <div className="legend">
        <span><i /> Forward step</span>
        <span><i className="back" /> Compensation, newest step undone first</span>
      </div>
      <div className="flow-grid">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Step</th><th>Result</th><th className="n">At</th><th className="n hide-phone">After start</th><th className="hide-phone">Service reply</th></tr>
            </thead>
            <tbody>
              {saga.steps.map((s, i) => {
                const [cls, text] = RESULT[s.status] ?? ["", s.status];
                return (
                  <tr key={`${s.name}-${s.status}-${i}`}>
                    <td>{label(s.name, s.status)}</td>
                    <td><span className={`pill ${cls}`}>{text}</span></td>
                    <td className="n num">{clock(s.at)}</td>
                    <td className="n num hide-phone">+{secs(saga.createdAt, s.at)}<span className="unit">s</span></td>
                    <td className="mono hide-phone detail">{s.detail ?? "—"}</td>
                  </tr>
                );
              })}
              {saga.steps.length === 0 && <tr><td colSpan={5} className="meta">No step has run yet.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="rows money">
          <h3>Wallet movements</h3>
          {moves.length === 0 && <p className="row meta">This saga moved no money.</p>}
          {moves.map((t, i) => (
            <div className="row" key={`${t.kind}-${i}`}>
              <span className="grow">{t.kind === "REFUND" ? "Refund (compensation)" : "Charge"}<span className="sub num">{clock(t.createdAt)}</span></span>
              <span className="n num">{t.kind === "CHARGE" ? "−" : "+"}{money(t.amountCents)}</span>
            </div>
          ))}
          {moves.length > 0 && (
            <div className="row"><strong className="grow">Net for the fleet</strong><strong className="n num">{net < 0 ? "−" : net > 0 ? "+" : ""}{money(Math.abs(net))}</strong></div>
          )}
          <p className="meta corr">Correlation ID <span className="mono">{saga.id}</span></p>
        </div>
      </div>
    </div>
  );
}
