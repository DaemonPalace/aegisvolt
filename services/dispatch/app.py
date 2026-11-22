"""energy-dispatch-service — SAGA step 3 (activate), stop, and simulated telemetry.

Failure injection (ported from wandersync participant.py chaos) is driven by the
SIMULATE_CHARGER_HARDWARE_FAULT flag instead of a request field.
"""
import os
import random
from datetime import UTC, datetime

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


class Activate(BaseModel):
    saga_id: str
    station_id: str
    connector_id: str
    kwh: float = Field(gt=0, le=100)


class Stop(BaseModel):
    saga_id: str


def now() -> str:
    return datetime.now(UTC).isoformat()


def session_key(saga_id: str) -> dict:
    return {"pk": f"SESSION#{saga_id}", "sk": "META"}


def answer(status: str):
    if status == "ACTIVE":
        return {"status": "ACTIVE"}
    if status == "FAILED":
        return JSONResponse({"error": "CHARGER_HARDWARE_FAULT"}, status_code=503)
    raise HTTPException(409, f"SESSION_{status}")  # STOPPED: a stop (compensation) got here first


@app.post("/activate")
def activate(body: Activate):
    status = "FAILED" if ddb.flag("SIMULATE_CHARGER_HARDWARE_FAULT") else "ACTIVE"
    try:
        ddb.table(TABLE).put_item(
            Item={**session_key(body.saga_id), "status": status, "station_id": body.station_id,
                  "connector_id": body.connector_id, "kwh": str(body.kwh), "started_at": now()},
            ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        # Idempotent replay: the first outcome stands.
        status = ddb.table(TABLE).get_item(Key=session_key(body.saga_id), ConsistentRead=True)["Item"]["status"]
        return answer(status)
    if status == "FAILED":
        event("dispatch.fault", f"hardware fault on {body.connector_id}", level=30)  # WARNING: a simulated fault must not fail the canary gate
    else:
        event("dispatch.activated", f"{body.connector_id} charging {body.kwh} kWh")
    return answer(status)


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
        power = round(random.uniform(90, 150), 1)
        hours = (datetime.now(UTC) - started).total_seconds() / 3600
        t.put_item(Item=ddb.json_safe({
            "pk": f"STATION#{station_id}", "sk": f"TELEMETRY#{ts}#{s['connector_id']}",
            "saga_id": s["pk"].removeprefix("SESSION#"), "connector_id": s["connector_id"], "ts": ts,
            "power_kw": power, "energy_kwh": round(min(float(s["kwh"]), power * hours), 3),
            "voltage": round(random.uniform(395, 405), 1)}))
    points = t.query(KeyConditionExpression=Key("pk").eq(f"STATION#{station_id}") & Key("sk").begins_with("TELEMETRY#"),
                     ScanIndexForward=False, Limit=20)["Items"]
    return [{**ddb.plain(p), "station_id": station_id} for p in points]
