"""energy-dispatch-service — SAGA step 3 (activate), stop, and simulated telemetry.

Failure injection (ported from wandersync participant.py chaos) is driven by the
SIMULATE_CHARGER_HARDWARE_FAULT flag instead of a request field.
"""
import os
import random
from datetime import UTC, datetime, timedelta

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from common import ddb
from common.log import event
from common.web import make_app
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = make_app("energy-dispatch-service")
TABLE = os.environ.get("TABLE_NAME", "aegisvolt-dispatch-local")
# Simulated clock: a 10 kWh charge at 150 kW takes 240 s for real, 24 s at the default ×10 (demo-friendly).
CHARGE_SPEEDUP = float(os.environ.get("CHARGE_SPEEDUP", "10"))


class Activate(BaseModel):
    saga_id: str
    station_id: str
    connector_id: str
    kwh: float = Field(gt=0, le=100)
    power_kw: int = Field(default=150, gt=0)
    fleet_id: str = ""


class Stop(BaseModel):
    saga_id: str


def now() -> str:
    return datetime.now(UTC).isoformat()


def session_key(saga_id: str) -> dict:
    return {"pk": f"SESSION#{saga_id}", "sk": "META"}


def answer(status: str, ends_at: str | None = None):
    if status == "ACTIVE":
        return {"status": "ACTIVE", "ends_at": ends_at}
    if status == "FAILED":
        return JSONResponse({"error": "CHARGER_HARDWARE_FAULT"}, status_code=503)
    raise HTTPException(409, f"SESSION_{status}")  # STOPPED: a stop (compensation) got here first


@app.post("/activate")
def activate(body: Activate):
    status = "FAILED" if ddb.flag("SIMULATE_CHARGER_HARDWARE_FAULT") else "ACTIVE"
    started = datetime.now(UTC)
    ends_at = (started + timedelta(hours=body.kwh / body.power_kw / CHARGE_SPEEDUP)).isoformat()
    try:
        ddb.table(TABLE).put_item(
            Item={**session_key(body.saga_id), "status": status, "station_id": body.station_id,
                  "connector_id": body.connector_id, "kwh": str(body.kwh), "power_kw": body.power_kw,
                  "fleet_id": body.fleet_id, "started_at": started.isoformat(), "ends_at": ends_at},
            ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Idempotent replay: the first outcome stands.
        first = ddb.table(TABLE).get_item(Key=session_key(body.saga_id), ConsistentRead=True)["Item"]
        return answer(first["status"], first.get("ends_at"))
    if status == "FAILED":
        event("dispatch.fault", f"hardware fault on {body.connector_id}", level=30)  # WARNING: a simulated fault must not fail the canary gate
    else:
        event("dispatch.activated", f"{body.connector_id} charging {body.kwh} kWh until {ends_at}")
    return answer(status, ends_at)


@app.post("/stop")
def stop(body: Stop):
    # Upsert: a stop that arrives before its activate leaves a STOPPED tombstone (activate then answers 409).
    ddb.table(TABLE).update_item(
        Key=session_key(body.saga_id), UpdateExpression="SET #s = :s, stopped_at = :now",
        ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "STOPPED", ":now": now()})
    event("dispatch.stopped", "session stopped")
    return {"status": "STOPPED"}


@app.get("/telemetry/{station_id}")
def telemetry(station_id: str):
    t = ddb.table(TABLE)
    # ponytail: Scan for active sessions; a GSI on (station_id, status) if sessions pile up.
    active = t.scan(FilterExpression=Attr("pk").begins_with("SESSION#") & Attr("station_id").eq(station_id)
                    & Attr("status").eq("ACTIVE"))["Items"]
    ts = now()
    for s in active:
        started = datetime.fromisoformat(s["started_at"])
        rated = float(s.get("power_kw", 150))
        # Energy follows the same simulated clock as ends_at, so the UI countdown and the kWh meter agree.
        total = (datetime.fromisoformat(s["ends_at"]) - started).total_seconds() if "ends_at" in s else 0
        done = min(1.0, (datetime.now(UTC) - started).total_seconds() / total) if total > 0 else 0.0
        t.put_item(Item=ddb.json_safe({
            "pk": f"STATION#{station_id}", "sk": f"TELEMETRY#{ts}#{s['connector_id']}",
            "saga_id": s["pk"].removeprefix("SESSION#"), "connector_id": s["connector_id"], "ts": ts,
            "power_kw": round(rated * random.uniform(0.88, 0.99), 1),
            "energy_kwh": round(float(s["kwh"]) * done, 3),
            "voltage": round(random.uniform(395, 405), 1)}))
    points = t.query(KeyConditionExpression=Key("pk").eq(f"STATION#{station_id}") & Key("sk").begins_with("TELEMETRY#"),
                     ScanIndexForward=False, Limit=20)["Items"]
    return [{**ddb.plain(p), "station_id": station_id} for p in points]


def delivered(s: dict, at: datetime) -> float:
    """kWh a session has put in by `at`, on the same clock as ends_at (linear ramp; full when it ends)."""
    if s["status"] == "FAILED":
        return 0.0
    kwh = float(s["kwh"])
    if "ends_at" not in s:
        return kwh if s["status"] == "STOPPED" else 0.0  # sessions from before ends_at existed
    start, end = datetime.fromisoformat(s["started_at"]), datetime.fromisoformat(s["ends_at"])
    until = datetime.fromisoformat(s["stopped_at"]) if s.get("stopped_at") else at
    total = (end - start).total_seconds()
    return round(kwh * min(1.0, max(0.0, (until - start).total_seconds() / total)), 3) if total > 0 else kwh


@app.get("/energy/{station_id}")
def energy(station_id: str, since: str):
    """Every session at the station that started at or after `since`, with the energy it delivered."""
    # ponytail: Scan with a filter; a GSI on (station_id, started_at) when sessions grow.
    items = ddb.plain(ddb.table(TABLE).scan(
        FilterExpression=Attr("pk").begins_with("SESSION#") & Attr("station_id").eq(station_id)
        & Attr("started_at").gte(since))["Items"])
    at = datetime.now(UTC)
    return sorted(({"saga_id": s["pk"].removeprefix("SESSION#"), "connector_id": s["connector_id"],
                    "fleet_id": s.get("fleet_id") or None, "status": s["status"], "started_at": s["started_at"],
                    "ended_at": s.get("stopped_at"), "kwh_requested": float(s["kwh"]),
                    "kwh_delivered": delivered(s, at), "power_kw": int(s.get("power_kw", 150))}
                   for s in items), key=lambda x: x["started_at"], reverse=True)
