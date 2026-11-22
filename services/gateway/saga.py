"""SAGA "Reserve & Activate Charge", orchestrated here in the gateway (SPEC §6).

Plain-async port of wandersync services/orders-billing/saga.py with the Prefect decorators removed:
charging never depends on Prefect. The step trail lives in the sagas table (UI + Prefect audit read it).

Forward:      reserve → charge → activate → COMPLETED
Compensation: completed steps in reverse (plus the failed one on timeout) → COMPENSATED
"""
import asyncio
import copy
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError
from common import ddb
from common.log import correlation_id, event

SAGAS_TABLE = os.environ.get("SAGAS_TABLE", "aegisvolt-sagas-local")
STEP_DELAY = float(os.environ.get("SAGA_STEP_DELAY_SECONDS", "1.5"))
CLIENT_TIMEOUT = float(os.environ.get("SAGA_CLIENT_TIMEOUT_SECONDS", "5"))
URL = {
    "reservation": os.environ.get("RESERVATION_URL", "http://reservation"),
    "billing": os.environ.get("BILLING_URL", "http://billing"),
    "dispatch": os.environ.get("DISPATCH_URL", "http://dispatch"),
}
COMPENSATION_BACKOFF = [1, 2, 4, 8, 16]  # ADR-06: 5 retries, then COMPENSATION_FAILED
RECOVER_AFTER = timedelta(seconds=60)
TERMINAL = {"COMPLETED", "FAILED", "COMPENSATED", "COMPENSATION_FAILED", "STOPPED"}

# (step, service, forward path, compensation path)
STEPS = [
    ("reserve", "reservation", "/reserve", "/release"),
    ("charge", "billing", "/charge", "/refund"),
    ("activate", "dispatch", "/activate", "/stop"),
]

http: httpx.AsyncClient | None = None  # tests swap in a client mounted on the ASGI apps
_running: set[asyncio.Task] = set()


class StepRejected(Exception):
    """Business rejection (4xx), or a transient error that survived its retry: the step did not happen."""


class StepTimeout(Exception):
    """No answer in time — the outcome is unknown, so the step is compensated too (ADR-05)."""


def now() -> str:
    return datetime.now(UTC).isoformat()


# ───────────────────────── HTTP ─────────────────────────

async def call(service: str, method: str, path: str, body: dict | None = None) -> httpx.Response:
    global http
    if http is None:
        http = httpx.AsyncClient(timeout=CLIENT_TIMEOUT)
    return await http.request(method, URL[service] + path, json=body,
                              headers={"X-Correlation-ID": correlation_id.get()})


async def forward(service: str, path: str, body: dict) -> dict:
    for attempt in (1, 2):  # one retry, on transport errors / 5xx only
        try:
            resp = await call(service, "POST", path, body)
        except httpx.TimeoutException:
            raise StepTimeout(f"{service}{path}: no answer after {CLIENT_TIMEOUT:g}s")
        except httpx.TransportError as exc:
            if attempt == 2:
                raise StepRejected(f"{service} unreachable: {exc!r}")
            continue
        if resp.status_code >= 500 and attempt == 1:
            continue
        if resp.status_code == 200:
            return resp.json()
        raise StepRejected(f"{service}{path} {resp.status_code}: {resp.text[:200]}")


async def compensating(service: str, path: str, body: dict) -> dict:
    for delay in [*COMPENSATION_BACKOFF, None]:
        try:
            resp = await call(service, "POST", path, body)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            if delay is None:
                raise
            event("saga.compensation_retry", f"{service}{path} failed ({exc!r}), retry in {delay}s", logging.WARNING)
            await asyncio.sleep(delay)


# ───────────────────────── persistence ─────────────────────────

def _table():
    return ddb.table(SAGAS_TABLE)


async def save(saga: dict) -> None:
    saga["updated_at"] = now()
    item = ddb.json_safe({"pk": f"SAGA#{saga['id']}", "sk": "META", **saga})
    await asyncio.to_thread(_table().put_item, Item=item)


def _from_item(item: dict) -> dict:
    return {k: v for k, v in ddb.plain(item).items() if k not in ("pk", "sk")}


async def load(saga_id: str) -> dict | None:
    resp = await asyncio.to_thread(_table().get_item, Key={"pk": f"SAGA#{saga_id}", "sk": "META"},
                                   ConsistentRead=True)
    return _from_item(resp["Item"]) if "Item" in resp else None


async def recent(limit: int) -> list[dict]:
    # ponytail: Scan + sort in the app; GSI on created_at past demo scale (SPEC §4).
    items = await asyncio.to_thread(lambda: _table().scan(ConsistentRead=True)["Items"])
    sagas = sorted((_from_item(i) for i in items), key=lambda s: s["created_at"], reverse=True)
    return sagas[:limit]


async def record(saga: dict, name: str, status: str, detail: str) -> None:
    saga["steps"].append({"name": name, "status": status, "at": now(), "detail": detail[:300]})
    await save(saga)


def body(saga: dict) -> dict:
    # Every participant takes the fields it needs from the same body (pydantic ignores the rest).
    return {k: saga[k] for k in ("fleet_id", "station_id", "connector_id", "kwh")} | {"saga_id": saga["id"]}


# ───────────────────────── orchestration ─────────────────────────

async def start(fleet_id: str, station_id: str, connector_id: str, kwh: float,
                idempotency_key: str | None = None) -> dict:
    saga = {"id": idempotency_key or str(uuid.uuid4()), "status": "STARTED", "fleet_id": fleet_id,
            "station_id": station_id, "connector_id": connector_id, "kwh": kwh, "amount_cents": None,
            "created_at": now(), "updated_at": now(), "steps": []}
    try:
        await asyncio.to_thread(_table().put_item,
                                Item=ddb.json_safe({"pk": f"SAGA#{saga['id']}", "sk": "META", **saga}),
                                ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        return await load(saga["id"])  # same idempotencyKey: return the existing saga, start nothing
    task = asyncio.create_task(run(copy.deepcopy(saga)))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return saga


async def run(saga: dict) -> str:
    correlation_id.set(saga["id"])  # the sagaId is the correlation id for every downstream call and log line
    event("saga.started", f"{saga['fleet_id']} on {saga['connector_id']} for {saga['kwh']} kWh")
    done: list[tuple] = []
    try:
        for step in STEPS:
            name, service, path, _ = step
            await asyncio.sleep(STEP_DELAY)  # lets the UI show each transition
            try:
                result = await forward(service, path, body(saga))
            except (StepRejected, StepTimeout) as exc:
                await record(saga, name, "FAILED", str(exc))
                event("saga.step.failed", f"{name}: {exc}", logging.WARNING, step=name)
                undo = ([step] if isinstance(exc, StepTimeout) else []) + done[::-1]
                if not undo:
                    saga["status"] = "FAILED"
                    await save(saga)
                    return saga["status"]
                return await compensate(saga, undo)
            if name == "charge":
                saga["amount_cents"] = result["amount_cents"]
            await record(saga, name, "OK", str(result))
            event("saga.step.ok", f"{name} ok", step=name)
            done.append(step)
        saga["status"] = "COMPLETED"
        await save(saga)
        event("saga.completed", "charging session active")
        return saga["status"]
    except Exception as exc:  # DynamoDB down etc.: stays STARTED, startup recovery compensates it
        event("saga.crashed", f"{type(exc).__name__}: {exc}", logging.ERROR)
        raise


async def compensate(saga: dict, steps: list[tuple]) -> str:
    event("saga.compensating", f"undoing {[s[0] for s in steps]}")
    ok = True
    for name, service, _, path in steps:
        await asyncio.sleep(STEP_DELAY)
        try:
            result = await compensating(service, path, body(saga))
            await record(saga, name, "COMPENSATED", f"{path} {result}")
        except httpx.HTTPError as exc:
            ok = False
            await record(saga, name, "FAILED", f"{path} gave up: {exc!r}")
    saga["status"] = "COMPENSATED" if ok else "COMPENSATION_FAILED"
    await save(saga)
    if ok:
        event("saga.compensated", "all completed steps undone")
    else:
        event("saga.compensation_failed", "compensation exhausted its retries", logging.ERROR)
    return saga["status"]


async def stop(saga_id: str) -> dict:
    saga = await load(saga_id)
    if not saga:
        raise ValueError("SAGA_NOT_FOUND")
    if saga["status"] != "COMPLETED":
        raise ValueError(f"SAGA_NOT_ACTIVE: status is {saga['status']}")
    correlation_id.set(saga_id)
    for name, service, path in (("stop", "dispatch", "/stop"), ("release", "reservation", "/release")):
        result = await compensating(service, path, body(saga))
        await record(saga, name, "OK", str(result))
    saga["status"] = "STOPPED"
    await save(saga)
    event("saga.stopped", "charging session stopped")
    return saga


async def recover() -> None:
    """Crash recovery (ported from wandersync orders-billing startup): every compensation is idempotent and
    tombstone-safe, so compensating all steps is correct whatever had finished."""
    cutoff = (datetime.now(UTC) - RECOVER_AFTER).isoformat()
    items = await asyncio.to_thread(
        lambda: _table().scan(FilterExpression=Attr("status").eq("STARTED") & Attr("created_at").lt(cutoff))["Items"])
    for item in items:
        saga = _from_item(item)
        correlation_id.set(saga["id"])
        event("saga.recovering", "STARTED for over 60 s: orchestrator died mid-saga", logging.WARNING)
        await compensate(saga, STEPS[::-1])
