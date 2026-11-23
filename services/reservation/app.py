"""station-reservation-service — SAGA step 1 (reserve) and compensation C1 (release).

Ported from wandersync services/common/participant.py (SQL → DynamoDB conditional writes):
- reserve and release are idempotent per saga_id (marker items SAGA#<id>/RESERVE and /RELEASE).
- release before (or instead of) reserve leaves a RELEASE tombstone, and a later reserve for that
  saga is refused (410): a late, timed-out reserve can't leave an orphan booking.
- a connector is held by at most one saga: conditional update on status = AVAILABLE.
- hardware faults are two-staged: each one is counted once per saga_id (marker SAGA#<id>/FAULT) and the connector
  stays AVAILABLE; it closes OUT_OF_SERVICE at FAULT_THRESHOLD consecutive faults, or at once if the charger is
  unreachable. reopen and a successful activation ("healthy") reset the count.
- station owner lifecycle: AVAILABLE → OUT_OF_SERVICE (faults) → MAINTENANCE → AVAILABLE (reopen);
  connectors can be added or removed while nobody is charging on them.
"""
import os
from datetime import UTC, datetime

from botocore.exceptions import ClientError
from common import ddb
from common.log import event
from common.web import make_app
from fastapi import HTTPException
from pydantic import BaseModel, Field

app = make_app("station-reservation-service")
TABLE = os.environ.get("TABLE_NAME", "aegisvolt-reservations-local")


class Hold(BaseModel):
    saga_id: str
    station_id: str
    connector_id: str


class Lifecycle(BaseModel):
    station_id: str
    connector_id: str
    action: str  # fault | unreachable | healthy (from the SAGA) | maintenance | reopen (owner)
    note: str = Field(default="", max_length=300)
    saga_id: str | None = None  # fault / unreachable: the idempotency key


class NewConnector(BaseModel):
    type: str = Field(pattern="^(FAST|ULTRA)$")


POWER = {"FAST": 150, "ULTRA": 350}
FAULT_THRESHOLD = 3  # consecutive hardware faults that close a connector
# action → (statuses it may start from, status it leaves). RESERVED is never a source: a car is plugged in.
TRANSITIONS = {
    "maintenance": (("AVAILABLE", "OUT_OF_SERVICE"), "MAINTENANCE"),
    "reopen": (("MAINTENANCE",), "AVAILABLE"),
}


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
                "note": it.get("note"), "status_since": it.get("updated_at"),
                "fault_count": it.get("fault_count", 0),
            })
    for st in out.values():
        st["connectors"].sort(key=lambda c: (len(c["id"]), c["id"]))  # C2 before C10
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
            return {"status": "RESERVED", "replay": True, "power_kw": int(conn["power_kw"]),
                    "fault_count": int(conn.get("fault_count", 0))}
        if taken:
            raise HTTPException(409, "CONNECTOR_NOT_AVAILABLE")
        raise
    event("reservation.reserved", f"{body.connector_id} reserved at {body.station_id} ({int(conn['power_kw'])} kW)")
    return {"status": "RESERVED", "replay": False, "power_kw": int(conn["power_kw"]),
            "fault_count": int(conn.get("fault_count", 0))}


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
    event("reservation.released", f"{body.connector_id} released at {body.station_id}, back to AVAILABLE")
    return {"status": "AVAILABLE", "result": "released"}


def record_fault(body: Lifecycle, key: dict) -> dict:
    """Stage 1: count the fault once per saga; the connector stays AVAILABLE. Stage 2: close it OUT_OF_SERVICE
    at FAULT_THRESHOLD consecutive faults, or at once when the charger did not answer at all (unreachable)."""
    if not body.saga_id:
        raise HTTPException(422, "SAGA_ID_REQUIRED")
    replay = False
    try:
        ddb.client().transact_write_items(TransactItems=[
            {"Put": {"TableName": TABLE, "ConditionExpression": "attribute_not_exists(pk)",
                     "Item": {"pk": f"SAGA#{body.saga_id}", "sk": "FAULT", "connector_id": body.connector_id,
                              "action": body.action, "at": now()}}},
            {"Update": {"TableName": TABLE, "Key": key,
                        "ConditionExpression": "attribute_exists(pk) AND #s = :free",
                        "UpdateExpression": "SET fault_count = if_not_exists(fault_count, :zero) + :one, "
                                            "last_fault_at = :now",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {":free": "AVAILABLE", ":zero": 0, ":one": 1, ":now": now()}}},
        ])
    except ClientError as exc:
        replay, busy = ddb.cancel_reasons(exc)
        if not (replay or busy):
            raise
        # replay: already counted. busy: not AVAILABLE (the owner closed it) or gone: nothing to count.
    # A replay never re-closes for "unreachable" (the owner may have reopened it since); the threshold check is safe
    # to repeat (reopen removes the count) and finishes a close that crashed after the count landed.
    by_count = replay or body.action == "fault"
    values = {":free": "AVAILABLE", ":out": "OUT_OF_SERVICE", ":now": now(),
              ":note": body.note or ("charger unreachable" if body.action == "unreachable"
                                     else f"{FAULT_THRESHOLD} consecutive hardware faults")}
    if by_count:
        values[":max"] = FAULT_THRESHOLD
    try:
        ddb.table(TABLE).update_item(Key=key, UpdateExpression="SET #s = :out, note = :note, updated_at = :now",
                                     ConditionExpression="#s = :free" + (" AND fault_count >= :max" if by_count else ""),
                                     ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues=values)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    current = ddb.table(TABLE).get_item(Key=key, ConsistentRead=True).get("Item")
    if not current:
        raise HTTPException(404, "CONNECTOR_NOT_FOUND")
    out = {"status": current["status"], "fault_count": int(current.get("fault_count", 0)),
           "threshold": FAULT_THRESHOLD, "replay": replay}
    event(f"reservation.{body.action}",
          f"{body.connector_id}: fault {out['fault_count']}/{FAULT_THRESHOLD}, {out['status']}")
    return out


@app.post("/connectors/lifecycle")
def lifecycle(body: Lifecycle):
    key = {"pk": f"STATION#{body.station_id}", "sk": f"CONNECTOR#{body.connector_id}"}
    if body.action in ("fault", "unreachable"):
        return record_fault(body, key)
    if body.action == "healthy":  # the charger just activated: its fault streak is over (status unchanged)
        try:
            ddb.table(TABLE).update_item(Key=key, UpdateExpression="REMOVE fault_count, last_fault_at",
                                         ConditionExpression="attribute_exists(pk)")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise HTTPException(404, "CONNECTOR_NOT_FOUND")
            raise
        return {"fault_count": 0}
    if body.action not in TRANSITIONS:
        raise HTTPException(422, f"UNKNOWN_ACTION {body.action}")
    sources, target = TRANSITIONS[body.action]
    names = {"#s": "status"}
    values = {":t": target, ":now": now(), **{f":s{i}": s for i, s in enumerate(sources)}}
    allowed = " OR ".join(f"#s = :s{i}" for i in range(len(sources)))
    if target == "AVAILABLE":
        update = "SET #s = :t, updated_at = :now REMOVE note, fault_count, last_fault_at"  # reopen: fresh start
    else:
        update, values[":note"] = "SET #s = :t, updated_at = :now, note = :note", body.note or body.action
    try:
        ddb.table(TABLE).update_item(Key=key, UpdateExpression=update, ExpressionAttributeNames=names,
                                     ExpressionAttributeValues=values,
                                     ConditionExpression=f"attribute_exists(pk) AND ({allowed})")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        current = ddb.table(TABLE).get_item(Key=key, ConsistentRead=True).get("Item")
        if not current:
            raise HTTPException(404, "CONNECTOR_NOT_FOUND")
        if current["status"] == target:  # idempotent replay
            return {"status": target, "replay": True}
        raise HTTPException(409, f"CANNOT_{body.action.upper()}_FROM_{current['status']}")
    event(f"reservation.{body.action}", f"{body.connector_id} → {target}: {body.note}")
    return {"status": target, "replay": False}


@app.post("/stations/{station_id}/connectors")
def add_connector(station_id: str, body: NewConnector):
    t = ddb.table(TABLE)
    if not t.get_item(Key={"pk": f"STATION#{station_id}", "sk": "META"}).get("Item"):
        raise HTTPException(404, "STATION_NOT_FOUND")
    taken = t.query(KeyConditionExpression="pk = :pk AND begins_with(sk, :c)",
                    ExpressionAttributeValues={":pk": f"STATION#{station_id}", ":c": "CONNECTOR#"})["Items"]
    n = max((int(i["sk"].rsplit("-C", 1)[1]) for i in taken), default=0) + 1
    cid = f"{station_id}-C{n}"
    try:
        t.put_item(Item={"pk": f"STATION#{station_id}", "sk": f"CONNECTOR#{cid}", "type": body.type,
                         "power_kw": POWER[body.type], "status": "AVAILABLE", "updated_at": now()},
                   ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "CONNECTOR_ID_RACE_RETRY")
        raise
    event("reservation.connector_added", f"{cid} ({body.type}) added")
    return {"id": cid, "type": body.type, "power_kw": POWER[body.type], "status": "AVAILABLE"}


@app.delete("/stations/{station_id}/connectors/{connector_id}")
def remove_connector(station_id: str, connector_id: str):
    try:
        ddb.table(TABLE).delete_item(Key={"pk": f"STATION#{station_id}", "sk": f"CONNECTOR#{connector_id}"},
                                     ConditionExpression="attribute_exists(pk) AND #s <> :held",
                                     ExpressionAttributeNames={"#s": "status"},
                                     ExpressionAttributeValues={":held": "RESERVED"})
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "CONNECTOR_IN_USE_OR_MISSING")
        raise
    event("reservation.connector_removed", f"{connector_id} removed")
    return {"removed": connector_id}
