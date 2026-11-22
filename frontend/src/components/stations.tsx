import Link from "next/link";
import { StateBadge } from "@/components/state-badge";
import { shortId } from "@/lib/format";
import type { Connector, Station } from "@/lib/gql";

/** Stations panel: the three sites side by side, each with its bays; a free bay can be picked for the next charge. */
export function Stations({ stations, boost, selected, onPick }: {
  stations: Station[]; boost: boolean; selected: string; onPick: (c: Connector) => void;
}) {
  const all = stations.flatMap((s) => s.connectors);
  const free = all.filter((c) => c.status === "AVAILABLE").length;
  return (
    <section className="panel" aria-labelledby="stations-h">
      <header>
        <h2 id="stations-h">Stations</h2>
        <span className="meta num">{free} of {all.length} connectors free{!boost && " · Ultra locked, boost off"}</span>
      </header>
      <div className="sites">
        {stations.map((s) => (
          <div className="site" key={s.id}>
            <h3>{s.name}<span className="sub">{s.city} · {s.id}</span></h3>
            <ul className="bays">
              {s.connectors.map((c) => {
                const locked = c.type === "ULTRA" && !boost;
                const busy = c.status !== "AVAILABLE";
                return (
                  <li key={c.id} className={selected === c.id ? "current" : undefined}>
                    <span className="mono">{c.id}</span>
                    <StateBadge state={c.status} />
                    <span className="spec num">{c.type === "ULTRA" ? "Ultra" : "Fast"} · {c.powerKw} kW</span>
                    {c.sagaId ? (
                      <Link href={`/saga/?id=${c.sagaId}`} className="card-link mono">{shortId(c.sagaId)}</Link>
                    ) : selected === c.id ? (
                      <span className="spec">Selected</span>
                    ) : (
                      <button className="text-btn" disabled={busy || locked} onClick={() => onPick(c)}
                        title={locked ? "ULTRA needs the fast-charge boost flag" : undefined}>
                        {locked ? "Boost off" : "Charge here"}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
