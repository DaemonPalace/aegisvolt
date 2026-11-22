import { clock, money } from "@/lib/format";
import type { Saga } from "@/lib/gql";

// The forward steps of "Reserve & activate charge" (SPEC §6) and what undoing each one means.
export const FORWARD = [
  { name: "reserve", label: "Reserve", what: "Connector held", undo: "Release connector" },
  { name: "charge", label: "Charge", what: "Wallet debited", undo: "Refund wallet" },
  { name: "activate", label: "Activate", what: "Dispatch on", undo: "Deactivate dispatch" },
];

/** One sentence on how a saga ended, built from its own steps. */
export function outcome(s: Saga, short = false): string {
  const failed = s.steps.find((x) => x.status === "FAILED");
  const undone = s.steps.filter((x) => x.status === "COMPENSATED").map((x) => FORWARD.find((f) => f.name === x.name)?.undo.toLowerCase() ?? x.name);
  const where = `${s.connectorId} for ${s.fleetId}`;
  if (short) return shortOutcome(s, failed?.name, undone);
  switch (s.status) {
    case "STARTED": {
      const next = FORWARD.find((f) => !s.steps.some((x) => x.name === f.name));
      return `Running on ${where}: ${next ? next.label.toLowerCase() : "finishing"}…`;
    }
    case "COMPLETED":
      return `Charging on ${where}: ${s.kwh} kWh, ${money(s.amountCents)} debited.`;
    case "STOPPED":
      return `Charged ${s.kwh} kWh on ${where} for ${money(s.amountCents)}, then stopped.`;
    case "FAILED":
      return `Rejected on ${where} at ${failed?.name ?? "the first step"}; nothing had to be undone.`;
    case "COMPENSATED":
      return `${cap(failed?.name ?? "A step")} failed on ${where}; ${undone.join(", then ")}.`;
    case "COMPENSATION_FAILED":
      return `${cap(failed?.name ?? "A step")} failed on ${where} and the compensation did not finish.`;
    default:
      return s.status;
  }
}
function shortOutcome(s: Saga, failed: string | undefined, undone: string[]): string {
  if (s.status === "STARTED") return "In progress";
  if (s.status === "COMPLETED") return "Energy flowing";
  if (s.status === "STOPPED") return "Charged, then stopped";
  if (s.status === "FAILED") return `Rejected at ${failed ?? "first step"}`;
  if (s.status === "COMPENSATED") return `${cap(failed ?? "a step")} failed; ${undone.join(", ")}`;
  return `${cap(failed ?? "a step")} failed; compensation incomplete`;
}
const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);

/** The signature element: forward steps on the top lane, compensations returning right-to-left below. */
export function SagaTrack({ saga, big }: { saga: Saga; big?: boolean }) {
  const byName = (name: string, status?: string) => saga.steps.find((x) => x.name === name && (!status || x.status === status));
  const firstPending = FORWARD.findIndex((f) => !byName(f.name, "OK") && !byName(f.name, "FAILED"));
  const comps = saga.steps.filter((x) => x.status === "COMPENSATED");
  const stop = byName("stop");

  return (
    <div className={`track${big ? " big" : ""}`} role="img" aria-label={outcome(saga)}>
      {FORWARD.map((f, i) => {
        const ok = byName(f.name, "OK");
        const bad = byName(f.name, "FAILED");
        const cls = ok ? "done" : bad ? "failed" : saga.status === "STARTED" && i === firstPending ? "running" : "";
        const at = ok ?? bad;
        return (
          <div className={`step ${cls}`} key={f.name}>
            <span className="node" />
            <span className="name">{f.label}</span>
            <span className="what">{bad ? "Failed" : f.what}</span>
            <span className="at num">{at ? clock(at.at) : cls === "running" ? "Now" : "—"}</span>
          </div>
        );
      })}
      {comps.length > 0 && (
        <div className="back" aria-hidden="true">
          {FORWARD.map((f) => {
            const c = comps.find((x) => x.name === f.name);
            if (!c) return <div key={f.name} />;
            // the rightmost undone step is undone first; the dashed lane runs from it back to the left
            const isFirst = f.name === [...FORWARD].reverse().find((ff) => comps.some((x) => x.name === ff.name))?.name;
            return (
              <div className={`undo${isFirst ? " first" : ""}`} key={f.name}>
                <span className="node" />
                <span className="arrow" />
                <span className="name">{f.undo}</span>
                <span className="at num">{clock(c.at)}</span>
              </div>
            );
          })}
        </div>
      )}
      {stop && <p className="none">Stopped at {clock(stop.at)}; connector released.</p>}
    </div>
  );
}
