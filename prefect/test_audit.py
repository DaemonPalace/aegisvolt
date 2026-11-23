"""Unit tests for the SAGA audit rules (pure: no Prefect, no AWS)."""
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit import check, classify, markdown

NOW = datetime(2026, 11, 23, 18, 0, tzinfo=UTC)


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def saga(sid, status, minutes=1, ends=None, connector="BOG-01-C1"):
    return {"id": sid, "status": status, "created_at": ago(minutes), "ends_at": ends, "connector_id": connector}


def test_classify():
    assert classify(saga("a", "STARTED", 1), NOW) == "RUNNING"
    assert classify(saga("a", "STARTED", 6), NOW) == "ORPHAN"
    assert classify(saga("a", "COMPENSATION_FAILED"), NOW) == "ORPHAN"
    assert classify(saga("a", "COMPLETED", ends=ago(-1)), NOW) == "CHARGING"
    assert classify(saga("a", "COMPLETED", ends=ago(10)), NOW) == "ORPHAN"  # finish timer lost
    assert classify(saga("a", "FINISHED"), NOW) == "FINISHED"
    assert classify(saga("a", "COMPLETED", 30), NOW) == "CHARGING"  # no endsAt yet (older session)
    assert classify(saga("a", "COMPLETED", 90), NOW) == "ORPHAN"  # no endsAt and open over an hour


def test_consistent_world_has_no_problems():
    sagas = [saga("ok", "COMPLETED", ends=ago(-1)), saga("done", "FINISHED", connector="BOG-01-C2"),
             saga("undo", "COMPENSATED", connector="MDE-01-C1"), saga("rej", "FAILED", connector="MDE-01-C3")]
    txns = [{"saga_id": "ok", "kind": "CHARGE"}, {"saga_id": "done", "kind": "CHARGE"},
            {"saga_id": "undo", "kind": "CHARGE"}, {"saga_id": "undo", "kind": "REFUND"}, {"saga_id": None, "kind": "TOPUP"}]
    connectors = [{"id": "BOG-01-C1", "status": "RESERVED", "saga_id": "ok"},
                  {"id": "BOG-01-C2", "status": "AVAILABLE"},
                  {"id": "MDE-01-C1", "status": "OUT_OF_SERVICE"}]  # closed by the fault: expected, not a problem
    r = check(sagas, txns, connectors, NOW)
    assert r["problems"] == [] and r["orphans"] == []
    assert r["counts"] == {"CHARGING": 1, "FINISHED": 1, "COMPENSATED": 1, "FAILED": 1}
    assert r["out_of_service"] == ["MDE-01-C1"]


def test_every_inconsistency_is_reported():
    sagas = [saga("norefund", "COMPENSATED"), saga("stillheld", "FINISHED", connector="BOG-01-C2"),
             saga("nothold", "COMPLETED", ends=ago(-1), connector="BOG-01-C3"), saga("stuck", "STARTED", 30)]
    txns = [{"saga_id": "norefund", "kind": "CHARGE"}, {"saga_id": "stillheld", "kind": "CHARGE"},
            {"saga_id": "nothold", "kind": "CHARGE"}]
    connectors = [{"id": "BOG-01-C2", "status": "RESERVED", "saga_id": "stillheld"},
                  {"id": "CDMX-01-C1", "status": "RESERVED", "saga_id": "ghost"}]
    r = check(sagas, txns, connectors, NOW)
    found = {(p["saga"], p["problem"].split()[0]) for p in r["problems"]}
    assert found == {("norefund", "compensated"), ("stillheld", "FINISHED"), ("nothold", "charging"), ("ghost", "CDMX-01-C1")}
    assert [o["saga"] for o in r["orphans"]] == ["stuck"]
    md = markdown("dev", r)
    assert "## Orphans: 1" in md and "## Inconsistencies: 4" in md
