"""GraphQL schema (SPEC §7). Shapes follow wandersync gateway/schema.py; domain is AegisVolt."""
import asyncio
import hmac
import os
from datetime import UTC, datetime
from enum import Enum
from urllib.parse import quote

import saga as orchestrator
import strawberry
from botocore.exceptions import ClientError
from common import ddb
from common.log import ENV, VERSION
from strawberry.extensions import MaxAliasesLimiter
from strawberry.types import Info

FLAGS_TABLE = os.environ.get("FLAGS_TABLE", "aegisvolt-flags-local")


@strawberry.enum
class ConnectorType(Enum):
    FAST = "FAST"
    ULTRA = "ULTRA"


@strawberry.enum
class ConnectorStatus(Enum):
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    MAINTENANCE = "MAINTENANCE"


@strawberry.enum
class SagaStatus(Enum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"
    STOPPED = "STOPPED"
    FINISHED = "FINISHED"


@strawberry.type
class Connector:
    id: strawberry.ID
    station_id: strawberry.ID
    type: ConnectorType
    power_kw: int
    status: ConnectorStatus
    saga_id: strawberry.ID | None
    note: str | None = None
    status_since: str | None = None
    fault_count: int | None = None  # consecutive hardware faults; OUT_OF_SERVICE at 3


@strawberry.type
class Station:
    id: strawberry.ID
    name: str
    city: str
    connectors: list[Connector]


@strawberry.type
class TelemetryPoint:
    station_id: strawberry.ID
    connector_id: strawberry.ID
    saga_id: strawberry.ID
    ts: str
    power_kw: float
    energy_kwh: float
    voltage: float


@strawberry.type
class ChargeSession:
    saga_id: strawberry.ID
    connector_id: strawberry.ID
    fleet_id: strawberry.ID | None
    status: str
    started_at: str
    ended_at: str | None
    kwh_requested: float
    kwh_delivered: float
    power_kw: int


@strawberry.type
class Flag:
    name: str
    enabled: bool
    description: str | None
    updated_at: str | None


@strawberry.type
class WalletTxn:
    saga_id: strawberry.ID | None
    kind: str
    amount_cents: int
    created_at: str


@strawberry.type
class Wallet:
    fleet_id: strawberry.ID
    balance_cents: int
    transactions: list[WalletTxn]


@strawberry.type
class Fleet:
    fleet_id: strawberry.ID
    balance_cents: int


@strawberry.type
class SagaStep:
    name: str
    status: str
    at: str
    detail: str | None


@strawberry.type
class Saga:
    id: strawberry.ID
    status: SagaStatus
    fleet_id: strawberry.ID
    station_id: strawberry.ID
    connector_id: strawberry.ID
    kwh: float
    amount_cents: int | None
    created_at: str
    steps: list[SagaStep]
    power_kw: int | None = None
    ends_at: str | None = None


@strawberry.type
class Pricing:
    rate_cents_per_kwh: int
    surge_multiplier: float
    surge: bool


@strawberry.type
class ServiceHealth:
    service: str
    status: str
    version: str


@strawberry.type
class Health:
    env: str
    version: str
    services: list[ServiceHealth]


@strawberry.input
class StartChargeInput:
    fleet_id: strawberry.ID
    station_id: strawberry.ID
    connector_id: strawberry.ID
    kwh: float
    idempotency_key: strawberry.ID | None = None


# ───────────────────────── mapping helpers ─────────────────────────

def to_saga(s: dict) -> Saga:
    return Saga(id=s["id"], status=SagaStatus(s["status"]), fleet_id=s["fleet_id"], station_id=s["station_id"],
                connector_id=s["connector_id"], kwh=float(s["kwh"]), amount_cents=s.get("amount_cents"),
                created_at=s["created_at"], steps=[SagaStep(**st) for st in s["steps"]],
                power_kw=s.get("power_kw"), ends_at=s.get("ends_at"))


def to_wallet(w: dict) -> Wallet:
    return Wallet(fleet_id=w["fleet_id"], balance_cents=w["balance_cents"],
                  transactions=[WalletTxn(**t) for t in w["transactions"]])


def to_flag(f: dict) -> Flag:
    return Flag(name=f["name"], enabled=bool(f["enabled"]), description=f.get("description"),
                updated_at=f.get("updated_at"))


async def get(service: str, path: str) -> dict | list:
    resp = await orchestrator.call(service, "GET", path)
    if resp.status_code != 200:
        raise ValueError(f"{service}: {resp.status_code} {resp.text[:200]}")
    return resp.json()


async def owner_call(method: str, path: str, body: dict | None = None) -> None:
    resp = await orchestrator.call("reservation", method, path, body)
    if resp.status_code != 200:
        raise ValueError(resp.json().get("detail", resp.text[:200]) if resp.headers.get("content-type", "").startswith(
            "application/json") else resp.text[:200])


def require_admin(info: Info) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "")
    given = info.context["request"].headers.get("x-admin-token", "")
    if not expected or not hmac.compare_digest(given, expected):
        raise PermissionError("FORBIDDEN: valid X-Admin-Token required")


# ───────────────────────── resolvers ─────────────────────────

@strawberry.type
class Query:
    @strawberry.field
    async def stations(self) -> list[Station]:
        return [Station(id=s["id"], name=s["name"], city=s["city"],
                        connectors=[Connector(id=c["id"], station_id=c["station_id"], type=ConnectorType(c["type"]),
                                              power_kw=c["power_kw"], status=ConnectorStatus(c["status"]),
                                              saga_id=c.get("saga_id"), note=c.get("note"),
                                              status_since=c.get("status_since"), fault_count=c.get("fault_count"))
                                              for c in s["connectors"]])
                for s in await get("reservation", "/stations")]

    @strawberry.field
    async def telemetry(self, station_id: strawberry.ID) -> list[TelemetryPoint]:
        return [TelemetryPoint(**{k: p[k] for k in TelemetryPoint.__annotations__})
                for p in await get("dispatch", f"/telemetry/{station_id}")]

    @strawberry.field
    async def flags(self) -> list[Flag]:
        items = await asyncio.to_thread(lambda: ddb.table(FLAGS_TABLE).scan(ConsistentRead=True)["Items"])
        return sorted((to_flag(ddb.plain(i)) for i in items), key=lambda f: f.name)

    @strawberry.field
    async def station_sessions(self, station_id: strawberry.ID, since: str) -> list[ChargeSession]:
        """Sessions started at the station since `since` (the UI sends its local midnight), with kWh delivered."""
        datetime.fromisoformat(since)  # reject junk before it reaches a filter
        return [ChargeSession(**p) for p in await get("dispatch", f"/energy/{station_id}?since={quote(since)}")]

    @strawberry.field
    async def pricing(self) -> Pricing:
        return Pricing(**await get("billing", "/pricing"))

    @strawberry.field
    async def fleets(self) -> list[Fleet]:
        return [Fleet(**f) for f in await get("billing", "/wallets")]

    @strawberry.field
    async def wallet(self, fleet_id: strawberry.ID) -> Wallet:
        return to_wallet(await get("billing", f"/wallets/{fleet_id}"))

    @strawberry.field
    async def sagas(self, limit: int = 20) -> list[Saga]:
        return [to_saga(s) for s in await orchestrator.recent(max(1, min(limit, 100)))]

    @strawberry.field
    async def saga(self, id: strawberry.ID) -> Saga | None:
        s = await orchestrator.load(id)
        return to_saga(s) if s else None

    @strawberry.field
    async def health(self) -> Health:
        async def one(service: str) -> ServiceHealth:
            try:
                h = await get(service, "/health")
                return ServiceHealth(service=h["service"], status=h["status"], version=h["version"])
            except Exception as exc:  # noqa: BLE001 — report, don't fail the whole query
                return ServiceHealth(service=service, status=f"down: {type(exc).__name__}", version="?")

        services = await asyncio.gather(*(one(s) for s in orchestrator.URL))
        return Health(env=ENV, version=VERSION,
                      services=[ServiceHealth(service="graphql-gateway", status="ok", version=VERSION), *services])


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def start_charge(self, input: StartChargeInput) -> Saga:
        if not 0 < input.kwh <= 100:
            raise ValueError("kwh must be in (0, 100]")
        s = await orchestrator.start(input.fleet_id, input.station_id, input.connector_id, input.kwh,
                                     input.idempotency_key)
        return to_saga(s)

    @strawberry.mutation
    async def stop_charge(self, saga_id: strawberry.ID) -> Saga:
        return to_saga(await orchestrator.stop(saga_id))

    @strawberry.mutation
    async def set_flag(self, info: Info, name: str, enabled: bool) -> Flag:
        require_admin(info)
        try:
            resp = await asyncio.to_thread(
                ddb.table(FLAGS_TABLE).update_item, Key={"name": name},
                UpdateExpression="SET enabled = :e, updated_at = :t", ConditionExpression="attribute_exists(#n)",
                ExpressionAttributeNames={"#n": "name"},
                ExpressionAttributeValues={":e": enabled, ":t": datetime.now(UTC).isoformat()},
                ReturnValues="ALL_NEW")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError(f"unknown flag {name}")
            raise
        return to_flag(ddb.plain(resp["Attributes"]))

    @strawberry.mutation
    async def connector_lifecycle(self, info: Info, station_id: strawberry.ID, connector_id: strawberry.ID,
                                  action: str, note: str = "") -> list[Station]:
        """Station owner: maintenance (from AVAILABLE or OUT_OF_SERVICE) or reopen (from MAINTENANCE)."""
        require_admin(info)
        if action not in ("maintenance", "reopen"):
            raise ValueError("action must be maintenance or reopen")
        await owner_call("POST", "/connectors/lifecycle", {"station_id": station_id, "connector_id": connector_id,
                                                          "action": action, "note": note})
        return await Query().stations()

    @strawberry.mutation
    async def add_connector(self, info: Info, station_id: strawberry.ID, type: ConnectorType) -> list[Station]:
        require_admin(info)
        await owner_call("POST", f"/stations/{station_id}/connectors", {"type": type.value})
        return await Query().stations()

    @strawberry.mutation
    async def remove_connector(self, info: Info, station_id: strawberry.ID, connector_id: strawberry.ID) -> list[Station]:
        require_admin(info)
        await owner_call("DELETE", f"/stations/{station_id}/connectors/{connector_id}")
        return await Query().stations()

    @strawberry.mutation
    async def create_fleet(self, info: Info, fleet_id: strawberry.ID, initial_cents: int = 0) -> Wallet:
        require_admin(info)
        resp = await orchestrator.call("billing", "POST", "/wallets", {"fleet_id": fleet_id, "initial_cents": initial_cents})
        if resp.status_code == 409:
            raise ValueError(f"{fleet_id} already exists")
        if resp.status_code == 422:
            raise ValueError("fleetId must look like FLEET-NAME (A-Z, 0-9, -), initialCents 0 to 1000000")
        if resp.status_code != 200:
            raise ValueError(f"billing: {resp.status_code} {resp.text[:200]}")
        return to_wallet(resp.json())

    @strawberry.mutation
    async def top_up(self, info: Info, fleet_id: strawberry.ID, amount_cents: int) -> Wallet:
        require_admin(info)
        if not 0 < amount_cents <= 1_000_000:
            raise ValueError("amountCents must be in (0, 1000000]")
        resp = await orchestrator.call("billing", "POST", "/topup", {"fleet_id": fleet_id, "amount_cents": amount_cents})
        if resp.status_code != 200:
            raise ValueError(f"billing: {resp.status_code} {resp.text[:200]}")
        return to_wallet(resp.json())


# Alias batching would fan out N service calls per request; cap it (as in wandersync).
schema = strawberry.Schema(query=Query, mutation=Mutation, extensions=[lambda: MaxAliasesLimiter(max_alias_count=10)])
