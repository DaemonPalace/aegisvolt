const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });

export const money = (cents: number | null | undefined) => (cents == null ? "—" : usd.format(cents / 100));
export const kwh = (v: number) => `${v.toLocaleString("en-US", { maximumFractionDigits: 1 })}`;
export const shortId = (id: string) => id.slice(0, 8);

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** "Today 17:51" / "2026-11-21 09:14" */
export const when = (iso: string) => {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today ? `Today ${clock(iso).slice(0, 5)}` : d.toISOString().slice(0, 10);
};

/** Seconds between two ISO stamps, one decimal. */
export const secs = (from: string, to: string) => ((Date.parse(to) - Date.parse(from)) / 1000).toFixed(1);

/** Flag names are SCREAMING_SNAKE in the store; people read them as words. */
export const FLAG_LABEL: Record<string, string> = {
  ENABLE_DYNAMIC_SURGE_PRICING: "Surge pricing",
  ENABLE_FAST_CHARGE_BOOST: "ULTRA connectors (fast-charge boost)",
  SIMULATE_CHARGER_HARDWARE_FAULT: "Simulate charger hardware fault",
};

/** "4:07" left, from milliseconds; "0:00" once past. */
export const countdown = (ms: number) => {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
