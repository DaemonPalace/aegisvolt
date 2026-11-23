// Ported from Taller-PatronSaga state-badge.tsx: one pill per state, relabelled for saga and connector states.
const STYLES: Record<string, { cls: string; label: string }> = {
  STARTED: { cls: "run", label: "Running" },
  COMPLETED: { cls: "pos", label: "Charging" },
  STOPPED: { cls: "", label: "Stopped" },
  FAILED: { cls: "neg", label: "Rejected" },
  COMPENSATED: { cls: "warn", label: "Compensated" },
  COMPENSATION_FAILED: { cls: "neg", label: "Compensation failed" },
  AVAILABLE: { cls: "pos", label: "Available" },
  RESERVED: { cls: "run", label: "In use" },
  OK: { cls: "pos", label: "Done" },
};

export function StateBadge({ state }: { state: string }) {
  const s = STYLES[state] ?? { cls: "", label: state };
  return <span className={`pill ${s.cls}`}>{s.label}</span>;
}
