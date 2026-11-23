"""Shared helpers for tests that drive the running stack through GraphQL (compose locally, api_url in CI)."""
import os
import time

import httpx
import pytest

API = os.environ.get("API_URL", "http://localhost:8080/graphql")
ADMIN = os.environ.get("ADMIN_TOKEN", "local-admin")
TERMINAL = {"COMPLETED", "FAILED", "COMPENSATED", "COMPENSATION_FAILED", "STOPPED", "FINISHED"}


def gql(query: str, variables: dict | None = None, admin: bool = False) -> dict:
    resp = httpx.post(API, json={"query": query, "variables": variables or {}}, timeout=30,
                      headers={"X-Admin-Token": ADMIN} if admin else {})
    resp.raise_for_status()
    body = resp.json()
    assert "errors" not in body, body["errors"]
    return body["data"]


SAGA = "id status amountCents steps { name status detail }"


def start(fleet: str, station: str, cid: str, kwh: float, key: str | None = None) -> dict:
    return gql(f"mutation($i: StartChargeInput!) {{ startCharge(input: $i) {{ {SAGA} }} }}",
               {"i": {"fleetId": fleet, "stationId": station, "connectorId": cid, "kwh": kwh,
                      "idempotencyKey": key}})["startCharge"]


def wait(saga_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = gql(f'{{ saga(id: "{saga_id}") {{ {SAGA} }} }}')["saga"]
        if s["status"] in TERMINAL:
            return s
        time.sleep(0.5)
    raise AssertionError(f"saga {saga_id} still running after {timeout}s")


def stop(saga_id: str) -> dict:
    return gql(f'mutation {{ stopCharge(sagaId: "{saga_id}") {{ {SAGA} }} }}')["stopCharge"]


def set_flag(name: str, on: bool) -> None:
    gql(f'mutation {{ setFlag(name: "{name}", enabled: {str(on).lower()}) {{ enabled }} }}', admin=True)


def lifecycle(station: str, cid: str, action: str) -> None:
    gql(f'mutation {{ connectorLifecycle(stationId: "{station}", connectorId: "{cid}", action: "{action}", '
        f'note: "e2e") {{ id }} }}', admin=True)


def connector(station: str, cid: str) -> dict:
    st = next(s for s in gql("{ stations { id connectors { id status sagaId faultCount } } }")["stations"] if s["id"] == station)
    return next(c for c in st["connectors"] if c["id"] == cid)


def wallet(fleet: str) -> dict:
    return gql(f'{{ wallet(fleetId: "{fleet}") {{ balanceCents transactions {{ sagaId kind amountCents }} }} }}')[
        "wallet"]


@pytest.fixture
def flag():
    """Set flags for one test and always restore the dev defaults afterwards."""
    defaults = {f["name"]: f["enabled"] for f in gql("{ flags { name enabled } }")["flags"]}
    yield set_flag
    for name, on in defaults.items():
        set_flag(name, on)
