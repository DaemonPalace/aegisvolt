"""billing-wallet-service — SAGA step 2 (charge) and compensation C2 (refund).

Ported from Taller-PatronSaga account-service core.py: Postgres ledger + idempotency records
become one DynamoDB TransactWriteItems (balance update + TXN marker), SPEC §5.
"""
import math
import os
from datetime import UTC, datetime
from decimal import Decimal

from botocore.exceptions import ClientError
from common import ddb
from common.log import event
from common.web import make_app
from fastapi import HTTPException
from pydantic import BaseModel, Field

app = make_app("billing-wallet-service")
TABLE = os.environ.get("TABLE_NAME", "aegisvolt-billing-local")
RATE_CENTS_PER_KWH = 25
SURGE = Decimal("1.5")


class Charge(BaseModel):
    saga_id: str
    fleet_id: str
    kwh: float = Field(gt=0, le=100)


class Refund(BaseModel):
    saga_id: str
    fleet_id: str


class NewFleet(BaseModel):
    fleet_id: str = Field(pattern=r"^FLEET-[A-Z0-9-]{2,30}$")
    initial_cents: int = Field(default=0, ge=0, le=1_000_000)


class TopUp(BaseModel):
    fleet_id: str
    amount_cents: int = Field(gt=0, le=1_000_000)


def now() -> str:
    return datetime.now(UTC).isoformat()


def wallet_key(fleet_id: str) -> dict:
    return {"pk": f"FLEET#{fleet_id}", "sk": "WALLET"}


def txn_key(fleet_id: str, saga_id: str, kind: str) -> dict:
    return {"pk": f"FLEET#{fleet_id}", "sk": f"TXN#{saga_id}#{kind}"}


def price(kwh: float, surge: bool) -> int:
    return math.ceil(Decimal(str(kwh)) * RATE_CENTS_PER_KWH * (SURGE if surge else 1))


@app.get("/pricing")
def pricing():
    # The UI quotes price(kwh, surge) from these, so the rate lives only here.
    return {"rate_cents_per_kwh": RATE_CENTS_PER_KWH, "surge_multiplier": float(SURGE),
            "surge": ddb.flag("ENABLE_DYNAMIC_SURGE_PRICING")}


@app.get("/wallets")
def wallets():
    # ponytail: Scan for the WALLET rows; a GSI on sk if fleets reach the thousands.
    items = ddb.plain(ddb.table(TABLE).scan(FilterExpression="sk = :w", ExpressionAttributeValues={":w": "WALLET"})["Items"])
    return sorted(({"fleet_id": i["pk"].removeprefix("FLEET#"), "balance_cents": i["balance_cents"]} for i in items),
                  key=lambda f: f["fleet_id"])


@app.post("/wallets")
def create_wallet(body: NewFleet):
    try:
        ddb.table(TABLE).put_item(Item={**wallet_key(body.fleet_id), "balance_cents": 0},
                                  ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(409, "FLEET_EXISTS")
        raise
    event("billing.fleet_created", f"{body.fleet_id} created")
    if body.initial_cents:
        return topup(TopUp(fleet_id=body.fleet_id, amount_cents=body.initial_cents))  # recorded as a deposit
    return wallet(body.fleet_id)


@app.get("/wallets/{fleet_id}")
def wallet(fleet_id: str):
    items = ddb.plain(ddb.table(TABLE).query(
        KeyConditionExpression="pk = :pk", ExpressionAttributeValues={":pk": f"FLEET#{fleet_id}"},
        ConsistentRead=True)["Items"])
    w = next((i for i in items if i["sk"] == "WALLET"), None)
    if not w:
        raise HTTPException(404, "WALLET_NOT_FOUND")
    txns = [{"saga_id": i.get("saga_id"), "kind": i["kind"], "amount_cents": i["amount_cents"],
             "created_at": i["created_at"]} for i in items if i["sk"].startswith("TXN#")]
    txns.sort(key=lambda t: t["created_at"], reverse=True)
    return {"fleet_id": fleet_id, "balance_cents": w["balance_cents"], "transactions": txns}


@app.post("/charge")
def charge(body: Charge):
    surge = ddb.flag("ENABLE_DYNAMIC_SURGE_PRICING")
    amount = price(body.kwh, surge)
    try:
        ddb.client().transact_write_items(TransactItems=[
            {"Update": {"TableName": TABLE, "Key": wallet_key(body.fleet_id),
                        "ConditionExpression": "balance_cents >= :a",
                        "UpdateExpression": "SET balance_cents = balance_cents - :a",
                        "ExpressionAttributeValues": {":a": amount}}},
            {"Put": {"TableName": TABLE, "ConditionExpression": "attribute_not_exists(pk)",
                     "Item": {**txn_key(body.fleet_id, body.saga_id, "CHARGE"), "kind": "CHARGE",
                              "saga_id": body.saga_id, "amount_cents": amount, "surge": surge,
                              "created_at": now()}}},
        ])
    except ClientError as exc:
        low_funds, replay = ddb.cancel_reasons(exc)
        if replay:  # idempotent replay: return what the first call charged
            first = ddb.table(TABLE).get_item(Key=txn_key(body.fleet_id, body.saga_id, "CHARGE"),
                                              ConsistentRead=True)["Item"]
            return {"amount_cents": int(first["amount_cents"]), "surge": first["surge"], "replay": True}
        if low_funds:
            raise HTTPException(402, "INSUFFICIENT_FUNDS")
        raise
    event("billing.charged", f"charged {amount} cents", amount_cents=amount, surge=surge)
    return {"amount_cents": amount, "surge": surge, "replay": False}


@app.post("/refund")
def refund(body: Refund):
    charged = ddb.table(TABLE).get_item(Key=txn_key(body.fleet_id, body.saga_id, "CHARGE"),
                                        ConsistentRead=True).get("Item")
    if not charged:  # charge never landed (or was refused): nothing to give back
        return {"refunded_cents": 0}
    amount = int(charged["amount_cents"])
    try:
        ddb.client().transact_write_items(TransactItems=[
            {"ConditionCheck": {"TableName": TABLE, "Key": txn_key(body.fleet_id, body.saga_id, "CHARGE"),
                                "ConditionExpression": "attribute_exists(pk)"}},
            {"Put": {"TableName": TABLE, "ConditionExpression": "attribute_not_exists(pk)",
                     "Item": {**txn_key(body.fleet_id, body.saga_id, "REFUND"), "kind": "REFUND",
                              "saga_id": body.saga_id, "amount_cents": amount, "created_at": now()}}},
            {"Update": {"TableName": TABLE, "Key": wallet_key(body.fleet_id),
                        "UpdateExpression": "SET balance_cents = balance_cents + :a",
                        "ExpressionAttributeValues": {":a": amount}}},
        ])
    except ClientError as exc:
        _, replay, _ = ddb.cancel_reasons(exc)
        if replay:
            return {"refunded_cents": 0, "replay": True}
        raise
    event("billing.refunded", f"refunded {amount} cents", amount_cents=amount)
    return {"refunded_cents": amount}


@app.post("/topup")
def topup(body: TopUp):
    ts = now()
    try:
        ddb.client().transact_write_items(TransactItems=[
            {"Update": {"TableName": TABLE, "Key": wallet_key(body.fleet_id),
                        "ConditionExpression": "attribute_exists(pk)",
                        "UpdateExpression": "SET balance_cents = balance_cents + :a",
                        "ExpressionAttributeValues": {":a": body.amount_cents}}},
            {"Put": {"TableName": TABLE, "Item": {"pk": f"FLEET#{body.fleet_id}", "sk": f"TXN#TOPUP#{ts}",
                                                  "kind": "TOPUP", "amount_cents": body.amount_cents,
                                                  "created_at": ts}}},
        ])
    except ClientError as exc:
        if any(ddb.cancel_reasons(exc)):
            raise HTTPException(404, "WALLET_NOT_FOUND")
        raise
    return wallet(body.fleet_id)
