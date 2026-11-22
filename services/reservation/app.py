"""station-reservation-service — SAGA step 1 (reserve) and compensation C1 (release).

Ported from wandersync services/common/participant.py (SQL → DynamoDB conditional writes):
- reserve and release are idempotent per saga_id (marker items SAGA#<id>/RESERVE and /RELEASE).
- release before (or instead of) reserve leaves a RELEASE tombstone, and a later reserve for that
  saga is refused (410): a late, timed-out reserve can't leave an orphan booking.
- a connector is held by at most one saga: conditional update on status = AVAILABLE.
"""
import os
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from common import ddb
from common.log import event
from common.web import make_app
from fastapi import HTTPException
from pydantic import BaseModel

app = make_app("station-reservation-service")
TABLE = os.environ.get("TABLE_NAME", "aegisvolt-reservations-local")


class Hold(BaseModel):
    saga_id: str
    station_id: str
    connector_id: str


def now() -> str:
    return datetime.now(UTC).isoformat()


def connector_key(body: Hold) -> dict:
    return {"pk": f"STATION#{body.station_id}", "sk": f"CONNECTOR#{body.connector_id}"}


@app.get("/stations")
def stations():
    # ponytail: Scan of a ~12-item table; Query per station if the catalogue grows.
    items = ddb.plain(ddb.table(TABLE).scan()["Items"])
    out: dict[str, dict] = {}
    for it in items:
        if not it["pk"].startswith("STATION#"):
            continue
        sid = it["pk"].removeprefix("STATION#")
        st = out.setdefault(sid, {"id": sid, "connectors": []})
        if it["sk"] == "META":
            st.update(name=it["name"], city=it["city"])
        else:
            st["connectors"].append({
                "id": it["sk"].removeprefix("CONNECTOR#"), "station_id": sid, "type": it["type"],
                "power_kw": it["power_kw"], "status": it["status"], "saga_id": it.get("saga_id"),
            })
    for st in out.values():
        st["connectors"].sort(key=lambda c: c["id"])
    return sorted(out.values(), key=lambda s: s["id"])


@app.post("/reserve")
def reserve(body: Hold):
    conn = ddb.table(TABLE).get_item(Key=connector_key(body), ConsistentRead=True).get("Item")
    if not conn:
        raise HTTPException(404, "CONNECTOR_NOT_FOUND")
    if conn["type"] == "ULTRA" and not ddb.flag("ENABLE_FAST_CHARGE_BOOST"):
        raise HTTPException(403, "ULTRA_REQUIRES_FAST_CHARGE_BOOST")

    marker = {"pk": f"SAGA#{body.saga_id}"}
    try:
        ddb.client().transact_write_items(TransactItems=[
            {"ConditionCheck": {"TableName": TABLE, "Key": {**marker, "sk": "RELEASE"},
                                "ConditionExpression": "attribute_not_exists(pk)"}},
            {"Put": {"TableName": TABLE, "ConditionExpression": "attribute_not_exists(pk)",
                     "Item": {**marker, "sk": "RESERVE", "connector_id": body.connector_id, "at": now()}}},
            {"Update": {"TableName": TABLE, "Key": connector_key(body),
                        "ConditionExpression": "#s = :free",
                        "UpdateExpression": "SET #s = :held, saga_id = :sid, updated_at = :now",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":free": "AVAILABLE", ":held": "RESERVED",
                                                      ":sid": body.saga_id, ":now": now()}}},
        ])
    except ClientError as exc:
        released, replay, taken = ddb.cancel_reasons(exc)
        if released:
            raise HTTPException(410, "ALREADY_RELEASED")
        if replay:
            return {"status": "RESERVED", "replay": True}
        if taken:
            raise HTTPException(409, "CONNECTOR_NOT_AVAILABLE")
        raise
    event("reservation.reserved", f"{body.connector_id} reserved")
    return {"status": "RESERVED", "replay": False}


@app.post("/release")
def release(body: Hold):
    marker = {"pk": f"SAGA#{body.saga_id}", "sk": "RELEASE", "connector_id": body.connector_id, "at": now()}
    put_marker = {"TableName": TABLE, "Item": marker, "ConditionExpression": "attribute_not_exists(pk)"}
    try:
        ddb.client().transact_write_items(TransactItems=[
            {"Put": put_marker},
            {"Update": {"TableName": TABLE, "Key": connector_key(body),
                        "ConditionExpression": "saga_id = :sid",
                        "UpdateExpression": "SET #s = :free, updated_at = :now REMOVE saga_id",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":sid": body.saga_id, ":free": "AVAILABLE", ":now": now()}}},
        ])
    except ClientError as exc:
        replay, not_holder = ddb.cancel_reasons(exc)
        if replay:
            return {"status": "AVAILABLE", "result": "already_released"}
        if not not_holder:
            raise
        # Not held by this saga (reserve never landed, or was refused): leave the tombstone only.
        try:
            ddb.client().put_item(**put_marker)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
        return {"status": "AVAILABLE", "result": "tombstone"}
    event("reservation.released", f"{body.connector_id} released")
    return {"status": "AVAILABLE", "result": "released"}
