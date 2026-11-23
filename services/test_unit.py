"""Unit tests: all 4 services in-process on moto DynamoDB. The orchestrator's HTTP client is mounted
straight onto the participants' ASGI apps, so the real saga code runs with no network and no Prefect."""
import asyncio
import importlib.util
import os
import pathlib
import runpy
import sys

import httpx
import pytest
from moto import mock_aws

ROOT = pathlib.Path(__file__).parent.parent
os.environ.update(AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test", AWS_REGION="us-east-1",
                  AWS_DEFAULT_REGION="us-east-1", FLAGS_TABLE="aegisvolt-flags-local",
                  SAGAS_TABLE="aegisvolt-sagas-local", ADMIN_TOKEN="t0ken", SAGA_STEP_DELAY_SECONDS="0")
os.environ.pop("DYNAMODB_ENDPOINT", None)
sys.path.insert(0, str(ROOT / "services" / "gateway"))


def load(service: str, table: str):
    os.environ["TABLE_NAME"] = f"aegisvolt-{table}-local"
    spec = importlib.util.spec_from_file_location(f"{service}_app", ROOT / "services" / service / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def svc():
    with mock_aws():
        sys.argv = ["seed", "local", "dev"]
        runpy.run_path(str(ROOT / "scripts" / "seed_local.py"))
        mods = {"reservation": load("reservation", "reservations"), "billing": load("billing", "billing"),
                "dispatch": load("dispatch", "dispatch"), "gateway": load("gateway", "sagas")}
        import saga

        saga.COMPENSATION_BACKOFF = [0] * 5
        mods["saga"] = saga
        wire(mods)
        yield mods


def wire(svc, broken: dict | None = None):
    """Mount every participant; `broken` maps 'service/path' → exception or status code to fake a failure."""
    broken = broken or {}

    class Faulty(httpx.AsyncBaseTransport):
        def __init__(self, name, app):
            self.name, self.inner = name, httpx.ASGITransport(app=app)

        async def handle_async_request(self, request):
            fault = broken.get(f"{self.name}{request.url.path}")
            if isinstance(fault, Exception):
                raise fault
            if isinstance(fault, int):
                return httpx.Response(fault, json={"error": "injected"})
            return await self.inner.handle_async_request(request)

    svc["saga"].http = httpx.AsyncClient(mounts={
        f"http://{n}": Faulty(n, svc[n].app) for n in ("reservation", "billing", "dispatch")})


def flag(name: str, on: bool):
    from common import ddb

    ddb.table("aegisvolt-flags-local").update_item(Key={"name": name}, UpdateExpression="SET enabled = :e",
                                                    ExpressionAttributeValues={":e": on})


def connector(svc, station: str, cid: str) -> dict:
    st = next(s for s in asyncio.run(get(svc, "reservation", "/stations")) if s["id"] == station)
    return next(c for c in st["connectors"] if c["id"] == cid)


async def get(svc, service, path):
    return (await svc["saga"].call(service, "GET", path)).json()


def balance(svc, fleet: str) -> int:
    return asyncio.run(get(svc, "billing", f"/wallets/{fleet}"))["balance_cents"]


def run_saga(svc, fleet, station, cid, kwh, broken=None) -> dict:
    async def go():
        wire(svc, broken)
        s = await svc["saga"].start(fleet, station, cid, kwh)
        while (await svc["saga"].load(s["id"]))["status"] == "STARTED":
            await asyncio.sleep(0.01)
        return await svc["saga"].load(s["id"])
    try:
        return asyncio.run(go())
    finally:
        wire(svc)  # healthy wiring for the assertions that follow


def compensated(s: dict) -> list[str]:
    return [st["name"] for st in s["steps"] if st["status"] == "COMPENSATED"]


# ───────────────────────── tests ─────────────────────────

def test_price_rounds_up_and_applies_surge(svc):
    assert svc["billing"].price(10, False) == 250
    assert svc["billing"].price(10, True) == 375
    assert svc["billing"].price(0.1, True) == 4  # 3.75 → 4


def test_happy_path_then_stop(svc):
    before = balance(svc, "FLEET-ANDINA")
    s = run_saga(svc, "FLEET-ANDINA", "BOG-01", "BOG-01-C1", 10)
    assert s["status"] == "COMPLETED", s
    assert [st["name"] for st in s["steps"]] == ["reserve", "charge", "activate"]
    assert s["amount_cents"] == 375 and balance(svc, "FLEET-ANDINA") == before - 375
    assert connector(svc, "BOG-01", "BOG-01-C1")["saga_id"] == s["id"]

    wire(svc)
    stopped = asyncio.run(svc["saga"].stop(s["id"]))
    assert stopped["status"] == "STOPPED"
    assert connector(svc, "BOG-01", "BOG-01-C1")["status"] == "AVAILABLE"


def test_low_funds_releases_connector(svc):
    s = run_saga(svc, "FLEET-LOWFUNDS", "BOG-01", "BOG-01-C2", 20)  # 750 cents > 500
    assert s["status"] == "COMPENSATED"
    assert compensated(s) == ["reserve"]
    assert balance(svc, "FLEET-LOWFUNDS") == 500
    assert connector(svc, "BOG-01", "BOG-01-C2")["status"] == "AVAILABLE"


def test_hardware_fault_refunds_then_releases_and_replay_changes_nothing(svc):
    before = balance(svc, "FLEET-ANDINA")
    flag("SIMULATE_CHARGER_HARDWARE_FAULT", True)
    try:
        s = run_saga(svc, "FLEET-ANDINA", "MDE-01", "MDE-01-C1", 10)
    finally:
        flag("SIMULATE_CHARGER_HARDWARE_FAULT", False)
    assert s["status"] == "COMPENSATED"
    assert compensated(s) == ["charge", "reserve"]  # reverse order
    kinds = [t["kind"] for t in asyncio.run(get(svc, "billing", "/wallets/FLEET-ANDINA"))["transactions"]]
    assert "REFUND" in kinds
    assert balance(svc, "FLEET-ANDINA") == before

    # Replay both compensations: nothing moves.
    body = {"saga_id": s["id"], "fleet_id": "FLEET-ANDINA", "station_id": "MDE-01", "connector_id": "MDE-01-C1"}
    wire(svc)
    for service, path in (("billing", "/refund"), ("reservation", "/release")):
        assert asyncio.run(svc["saga"].call(service, "POST", path, body)).status_code == 200
    assert balance(svc, "FLEET-ANDINA") == before

    # Stage 1 on top of the SAGA: the fault is counted, the connector stays AVAILABLE.
    assert s["steps"][-1]["name"] == "fault" and "fault 1/3" in s["steps"][-1]["detail"]
    c = connector(svc, "MDE-01", "MDE-01-C1")
    assert c["status"] == "AVAILABLE" and c["fault_count"] == 1
    # Replaying the fault record (same saga_id) counts nothing more.
    again = life(svc, "MDE-01-C1", "fault", saga_id=s["id"]).json()
    assert again["replay"] is True and again["fault_count"] == 1
    assert connector(svc, "MDE-01", "MDE-01-C1")["fault_count"] == 1
    life(svc, "MDE-01-C1", "healthy")  # leave it clean for the next test


def life(svc, cid: str, action: str, **extra) -> httpx.Response:
    return asyncio.run(svc["saga"].call("reservation", "POST", "/connectors/lifecycle", {
        "station_id": cid.rsplit("-C", 1)[0], "connector_id": cid, "action": action, "note": "", **extra}))


def faulty_saga(svc, cid: str) -> dict:
    flag("SIMULATE_CHARGER_HARDWARE_FAULT", True)
    try:
        return run_saga(svc, "FLEET-ANDINA", cid.rsplit("-C", 1)[0], cid, 1)
    finally:
        flag("SIMULATE_CHARGER_HARDWARE_FAULT", False)


def test_three_consecutive_faults_close_then_owner_reopen_resets(svc):
    for n in (1, 2, 3):
        s = faulty_saga(svc, "MDE-01-C1")
        assert s["status"] == "COMPENSATED" and f"fault {n}/3" in s["steps"][-1]["detail"]
        assert connector(svc, "MDE-01", "MDE-01-C1")["status"] == ("OUT_OF_SERVICE" if n == 3 else "AVAILABLE")
    assert life(svc, "MDE-01-C1", "reopen").status_code == 409  # must go through maintenance first
    assert life(svc, "MDE-01-C1", "maintenance", note="swap the board").json()["status"] == "MAINTENANCE"
    assert connector(svc, "MDE-01", "MDE-01-C1")["note"] == "swap the board"
    assert life(svc, "MDE-01-C1", "maintenance").json()["replay"] is True
    assert life(svc, "MDE-01-C1", "reopen").json()["status"] == "AVAILABLE"
    c = connector(svc, "MDE-01", "MDE-01-C1")
    assert c["status"] == "AVAILABLE" and c["fault_count"] == 0


def test_successful_charge_resets_the_fault_count(svc):
    assert "fault 1/3" in faulty_saga(svc, "BOG-01-C3")["steps"][-1]["detail"]
    s = run_saga(svc, "FLEET-ANDINA", "BOG-01", "BOG-01-C3", 1)
    assert s["status"] == "COMPLETED" and s["steps"][-1]["name"] == "healthy"
    assert connector(svc, "BOG-01", "BOG-01-C3")["fault_count"] == 0
    wire(svc)
    asyncio.run(svc["saga"].stop(s["id"]))


def test_unreachable_charger_closes_at_once(svc):
    s = run_saga(svc, "FLEET-ANDINA", "CDMX-01", "CDMX-01-C2", 1,
                 broken={"dispatch/activate": httpx.ConnectError("down")})
    assert s["status"] == "COMPENSATED" and compensated(s) == ["charge", "reserve"]
    assert "unreachable" in s["steps"][-1]["detail"]
    assert connector(svc, "CDMX-01", "CDMX-01-C2")["status"] == "OUT_OF_SERVICE"
    life(svc, "CDMX-01-C2", "maintenance")
    life(svc, "CDMX-01-C2", "reopen")
    assert life(svc, "CDMX-01-C2", "unreachable", saga_id=s["id"]).json()["status"] == "AVAILABLE"  # replay: no re-close


def test_ultra_needs_boost_flag(svc):
    flag("ENABLE_FAST_CHARGE_BOOST", False)
    try:
        s = run_saga(svc, "FLEET-ANDINA", "CDMX-01", "CDMX-01-C3", 5)
    finally:
        flag("ENABLE_FAST_CHARGE_BOOST", True)
    assert s["status"] == "FAILED" and compensated(s) == []  # step 1 refused: nothing to undo


def test_timeout_compensates_the_unknown_step_too(svc):
    s = run_saga(svc, "FLEET-ANDINA", "MDE-01", "MDE-01-C2", 1,
                 broken={"dispatch/activate": httpx.ReadTimeout("slow")})
    assert s["status"] == "COMPENSATED"
    assert compensated(s) == ["activate", "charge", "reserve"]
    assert connector(svc, "MDE-01", "MDE-01-C2")["status"] == "OUT_OF_SERVICE"  # no answer = unreachable: closed at once


def test_compensation_that_never_succeeds(svc):
    s = run_saga(svc, "FLEET-ANDINA", "CDMX-01", "CDMX-01-C1", 1,
                 broken={"dispatch/activate": 503, "billing/refund": 500})
    assert s["status"] == "COMPENSATION_FAILED"
    assert compensated(s) == ["reserve"]  # the other compensations still ran


def test_participants_are_idempotent_and_tombstoned(svc):
    wire(svc)
    call = lambda svc_, path, body: asyncio.run(svc["saga"].call(svc_, "POST", path, body))
    hold = {"saga_id": "s-tomb", "station_id": "CDMX-01", "connector_id": "CDMX-01-C2"}
    assert call("reservation", "/release", hold).json()["result"] == "tombstone"
    assert call("reservation", "/reserve", hold).status_code == 410  # late reserve can't orphan the connector

    before = balance(svc, "FLEET-ANDINA")
    charge = {"saga_id": "s-dup", "fleet_id": "FLEET-ANDINA", "kwh": 2}
    first, again = call("billing", "/charge", charge).json(), call("billing", "/charge", charge).json()
    assert first["amount_cents"] == again["amount_cents"] and again["replay"] is True
    assert balance(svc, "FLEET-ANDINA") == before - first["amount_cents"]


def test_graphql_admin_guard_and_idempotency_key(svc):
    wire(svc)

    async def gql(query, headers=None):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=svc["gateway"].app), base_url="http://gw") as c:
            return (await c.post("/graphql", json={"query": query}, headers=headers or {})).json()

    async def go():
        denied = await gql('mutation { setFlag(name: "ENABLE_FAST_CHARGE_BOOST", enabled: true) { enabled } }')
        allowed = await gql('mutation { setFlag(name: "ENABLE_FAST_CHARGE_BOOST", enabled: true) { enabled } }',
                            {"X-Admin-Token": "t0ken"})
        start = ('mutation { startCharge(input: {fleetId: "FLEET-ANDINA", stationId: "BOG-01", connectorId: "BOG-01-C2",'
                 ' kwh: 1, idempotencyKey: "same-key"}) { id } }')
        a, b = await gql(start), await gql(start)
        await asyncio.gather(*svc["saga"]._running)
        stations = await gql("{ stations { id connectors { id status } } }")
        return denied, allowed, a, b, stations

    denied, allowed, a, b, stations = asyncio.run(go())
    assert "FORBIDDEN" in denied["errors"][0]["message"]
    assert allowed["data"]["setFlag"]["enabled"] is True
    assert a["data"]["startCharge"]["id"] == b["data"]["startCharge"]["id"] == "same-key"
    assert len(stations["data"]["stations"]) == 3


def test_charge_finishes_by_itself_at_ends_at(svc):
    async def go():
        wire(svc)
        s = await svc["saga"].start("FLEET-ANDINA", "CDMX-01", "CDMX-01-C2", 0.01)  # ~24 ms at 150 kW ×10
        for _ in range(500):
            s = await svc["saga"].load(s["id"])
            if s["status"] not in ("STARTED", "COMPLETED"):
                return s
            await asyncio.sleep(0.01)
        return s

    s = asyncio.run(go())
    assert s["status"] == "FINISHED", s
    assert s["power_kw"] == 150 and s["ends_at"]
    assert [st["name"] for st in s["steps"]] == ["reserve", "charge", "activate", "stop", "release"]
    assert connector(svc, "CDMX-01", "CDMX-01-C2")["status"] == "AVAILABLE"


def test_owner_adds_and_removes_connectors_and_reads_energy(svc):
    wire(svc)
    call = lambda method, path, body=None: asyncio.run(svc["saga"].call("reservation", method, path, body))
    added = call("POST", "/stations/BOG-01/connectors", {"type": "ULTRA"}).json()
    assert added == {"id": "BOG-01-C4", "type": "ULTRA", "power_kw": 350, "status": "AVAILABLE"}

    s = run_saga(svc, "FLEET-ANDINA", "BOG-01", "BOG-01-C4", 3)
    assert s["status"] == "COMPLETED" and s["power_kw"] == 350
    assert call("DELETE", "/stations/BOG-01/connectors/BOG-01-C4").status_code == 409  # a car is plugged in

    wire(svc)
    asyncio.run(svc["saga"].stop(s["id"]))
    sessions = asyncio.run(get(svc, "dispatch", f"/energy/BOG-01?since={s['created_at'][:10]}"))
    mine = next(x for x in sessions if x["saga_id"] == s["id"])
    assert mine["fleet_id"] == "FLEET-ANDINA" and mine["status"] == "STOPPED"
    assert 0 <= mine["kwh_delivered"] < 3  # stopped early: only part of the 3 kWh went in

    assert call("DELETE", "/stations/BOG-01/connectors/BOG-01-C4").json() == {"removed": "BOG-01-C4"}
    assert all(c["id"] != "BOG-01-C4" for st in asyncio.run(get(svc, "reservation", "/stations")) for c in st["connectors"])


def test_new_fleet_gets_a_wallet_and_can_charge(svc):
    wire(svc)
    call = lambda method, path, body=None: asyncio.run(svc["saga"].call("billing", method, path, body))
    assert call("POST", "/wallets", {"fleet_id": "fleet lowercase"}).status_code == 422
    made = call("POST", "/wallets", {"fleet_id": "FLEET-NORTE", "initial_cents": 2000}).json()
    assert made["balance_cents"] == 2000 and [t["kind"] for t in made["transactions"]] == ["TOPUP"]
    assert call("POST", "/wallets", {"fleet_id": "FLEET-NORTE"}).status_code == 409
    assert "FLEET-NORTE" in [f["fleet_id"] for f in call("GET", "/wallets").json()]
    s = run_saga(svc, "FLEET-NORTE", "BOG-01", "BOG-01-C3", 4)  # 4 kWh × 25 × 1.5 = 150 cents
    assert s["status"] == "COMPLETED" and balance(svc, "FLEET-NORTE") == 1850
