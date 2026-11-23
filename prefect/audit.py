"""SAGA consistency rules (SPEC §13), kept free of Prefect and AWS so they are unit-tested on plain dicts.

Rewritten from wandersync scripts/reconcile.sql: there it was SQL joins across service schemas, here it is the same
cross-checks over the rows of the sagas, billing and reservations tables.
"""
from datetime import datetime, timedelta

ORPHAN_AFTER = timedelta(minutes=5)
FINISH_GRACE = timedelta(minutes=5)  # a charge past its endsAt by more than this lost its finish timer
NO_END_LIMIT = timedelta(hours=1)  # charges from before endsAt existed never finish by themselves


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def classify(saga: dict, now: datetime) -> str:
    """CHARGING, FINISHED, STOPPED, COMPENSATED, FAILED, or ORPHAN (needs a human or the recovery sweep)."""
    status = saga["status"]
    if status == "STARTED":
        return "ORPHAN" if now - _ts(saga["created_at"]) > ORPHAN_AFTER else "RUNNING"
    if status == "COMPENSATION_FAILED":
        return "ORPHAN"
    if status == "COMPLETED":
        ends = saga.get("ends_at")
        if not ends:
            return "ORPHAN" if now - _ts(saga["created_at"]) > NO_END_LIMIT else "CHARGING"
        return "ORPHAN" if now - _ts(ends) > FINISH_GRACE else "CHARGING"
    return status  # FINISHED, STOPPED, COMPENSATED, FAILED


def check(sagas: list[dict], txns: list[dict], connectors: list[dict], now: datetime) -> dict:
    """Counts per class plus every inconsistency found across the three services' tables.

    sagas: rows of the sagas table; txns: billing TXN rows (saga_id, kind); connectors: reservation CONNECTOR rows
    (id, status, saga_id).
    """
    kinds: dict[str, set] = {}
    for t in txns:
        if t.get("saga_id"):
            kinds.setdefault(t["saga_id"], set()).add(t["kind"])
    holder = {c["saga_id"]: c for c in connectors if c.get("saga_id")}
    by_id = {s["id"]: s for s in sagas}

    counts: dict[str, int] = {}
    orphans, problems = [], []
    faulted = [c["id"] for c in connectors if c["status"] == "OUT_OF_SERVICE"]
    for s in sagas:
        cls = classify(s, now)
        counts[cls] = counts.get(cls, 0) + 1
        sid, held = s["id"], holder.get(s["id"])
        if cls == "ORPHAN":
            orphans.append({"saga": sid, "status": s["status"], "connector": s["connector_id"],
                            "why": "STARTED > 5 min" if s["status"] == "STARTED" else
                            "compensation gave up" if s["status"] == "COMPENSATION_FAILED" else
                            "past endsAt, never finished" if s.get("ends_at") else "no endsAt, open > 1 h"})
        if s["status"] == "COMPENSATED" and "CHARGE" in kinds.get(sid, set()) and "REFUND" not in kinds.get(sid, set()):
            problems.append({"saga": sid, "problem": "compensated but the charge was never refunded"})
        if s["status"] in ("COMPENSATED", "FAILED", "FINISHED", "STOPPED") and held:
            problems.append({"saga": sid, "problem": f"{s['status']} but still holds {held['id']}"})
        if s["status"] == "COMPLETED" and not held:
            problems.append({"saga": sid, "problem": f"charging but {s['connector_id']} is not held by it"})
        if s["status"] in ("COMPLETED", "FINISHED", "STOPPED") and "CHARGE" not in kinds.get(sid, set()):
            problems.append({"saga": sid, "problem": f"{s['status']} without a CHARGE in the wallet"})
    for sid, c in holder.items():
        if sid not in by_id:
            problems.append({"saga": sid, "problem": f"{c['id']} held by a saga that does not exist"})
    return {"total": len(sagas), "counts": counts, "orphans": orphans, "problems": problems,
            "out_of_service": faulted}


def markdown(env: str, report: dict) -> str:
    lines = [f"# SAGA audit: {env}", "", f"{report['total']} sagas checked.", "", "| Class | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in sorted(report["counts"].items())]
    if report["out_of_service"]:
        closed = ", ".join(report["out_of_service"])
        lines += ["", f"Connectors closed after a hardware fault (expected, awaiting maintenance): {closed}"]
    for title, rows, cols in (("Orphans", report["orphans"], ("saga", "status", "connector", "why")),
                              ("Inconsistencies", report["problems"], ("saga", "problem"))):
        lines += ["", f"## {title}: {len(rows)}"]
        if rows:
            lines += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            lines += ["| " + " | ".join(str(r[c]) for c in cols) + " |" for r in rows]
    return "\n".join(lines)
