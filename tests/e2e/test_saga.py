"""End-to-end SAGA scenarios through GraphQL (ported from wandersync scripts/simulate_saga.py and
Taller-PatronSaga scripts/simulate.py). Each asserts final status, compensation order and DynamoDB state."""
import uuid

import pytest
from conftest import connector, start, stop, wait, wallet


def compensations(saga: dict) -> list[str]:
    return [s["name"] for s in saga["steps"] if s["status"] == "COMPENSATED"]


@pytest.mark.parametrize("name, fleet, station, cid, kwh, flags, status, undo", [
    ("happy path", "FLEET-ANDINA", "BOG-01", "BOG-01-C1", 10, {}, "COMPLETED", []),
    ("insufficient funds", "FLEET-LOWFUNDS", "BOG-01", "BOG-01-C2", 20, {}, "COMPENSATED", ["reserve"]),
    ("hardware fault", "FLEET-ANDINA", "MDE-01", "MDE-01-C1", 10, {"SIMULATE_CHARGER_HARDWARE_FAULT": True},
     "COMPENSATED", ["charge", "reserve"]),
    ("ULTRA with boost off", "FLEET-ANDINA", "MDE-01", "MDE-01-C3", 5, {"ENABLE_FAST_CHARGE_BOOST": False},
     "FAILED", []),
    ("ULTRA with boost on", "FLEET-ANDINA", "MDE-01", "MDE-01-C3", 5, {"ENABLE_FAST_CHARGE_BOOST": True},
     "COMPLETED", []),
])
def test_scenario(flag, name, fleet, station, cid, kwh, flags, status, undo):
    for f, on in flags.items():
        flag(f, on)
    before = wallet(fleet)["balanceCents"]

    saga = wait(start(fleet, station, cid, kwh)["id"])

    assert saga["status"] == status, saga
    assert compensations(saga) == undo  # reverse order of the forward steps
    after = wallet(fleet)
    if status == "COMPLETED":
        assert after["balanceCents"] == before - saga["amountCents"]
        assert connector(station, cid)["sagaId"] == saga["id"]
        assert stop(saga["id"])["status"] == "STOPPED"
    else:
        assert after["balanceCents"] == before  # charge refunded, or never taken
    assert connector(station, cid)["status"] == "AVAILABLE"
    if "charge" in undo:
        kinds = {t["kind"] for t in after["transactions"] if t["sagaId"] == saga["id"]}
        assert kinds == {"CHARGE", "REFUND"}


def test_idempotent_replay_starts_one_saga_and_charges_once():
    key = str(uuid.uuid4())
    first = start("FLEET-ANDINA", "CDMX-01", "CDMX-01-C1", 2, key)
    again = start("FLEET-ANDINA", "CDMX-01", "CDMX-01-C1", 2, key)
    assert first["id"] == again["id"] == key
    saga = wait(key)
    assert saga["status"] == "COMPLETED"
    assert [t["kind"] for t in wallet("FLEET-ANDINA")["transactions"] if t["sagaId"] == key] == ["CHARGE"]
    stop(key)
