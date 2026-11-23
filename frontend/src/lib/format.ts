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

const SERVICES = ["station-reservation-service", "billing-wallet-service", "energy-dispatch-service", "graphql-gateway"];

/** The Logs Insights query that traces one saga across the 4 services (same as scripts/trace.sh). */
export const traceQuery = (sagaId: string) =>
  `fields @timestamp, service, version, image_tag, level, event, msg\n| filter correlation_id = "${sagaId}"\n| sort @timestamp asc`;

/** Deep link to CloudWatch Logs Insights with that query and the env's 4 log groups, last 24 h, preloaded. */
export function traceUrl(env: string, sagaId: string): string {
  // The console's hash format: encodeURIComponent with % written as *, values prefixed by ', lists as (~'a~'b).
  const enc = (s: string) => encodeURIComponent(s).replace(/'/g, "%27").replace(/%/g, "*");
  const groups = SERVICES.map((s) => `~'${enc(`/aegisvolt/${env}/${s}`)}`).join("");
  const detail = `~(end~0~start~-86400~timeType~'RELATIVE~unit~'seconds~editorString~'${enc(traceQuery(sagaId))}~source~(${groups}))`;
  return `https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:logs-insights$3FqueryDetail$3D${detail}`;
}
