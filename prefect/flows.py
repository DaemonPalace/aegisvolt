"""Prefect observability for AegisVolt (SPEC §13). Prefect observes; it never runs the SAGA.

env_health  every 5 min: per env, GraphQL `health` (with latency) and describe_table on the 5 tables.
saga_audit  every 5 min: per env, reads the sagas, billing and reservations tables and cross-checks them (audit.py);
            the run FAILS when it finds orphans or inconsistencies, so the Prefect dashboard turns red.

Donor patterns: create_*_artifact + serve(cron=...) from wandersync ingestion/flows/ingest.py; task retries and a
quality gate that raises, from Taller-DASK flows/clean_pipeline.py.

Usage (prefect/.venv, see prefect/README.md):
  python prefect/flows.py run [env ...]   # one run of each flow now
  python prefect/flows.py serve           # both flows on a */5 cron, until stopped
Envs come from prefect/envs.json; an env without api_url is reported as "not deployed".
AWS envs use the default credential chain (e.g. AWS_PROFILE=aegisvolt); "local" uses DynamoDB Local.
"""
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3
import httpx
from audit import check, markdown
from prefect.artifacts import create_markdown_artifact, create_table_artifact

from prefect import flow, get_run_logger, serve, task

ENVS: dict[str, dict] = json.loads((Path(__file__).parent / "envs.json").read_text())
TABLES = ("reservations", "billing", "dispatch", "sagas", "flags")
CRON = "*/5 * * * *"


def dynamodb(env: str):
    cfg = ENVS[env]
    return boto3.resource("dynamodb", region_name=cfg.get("region", "us-east-1"),
                          endpoint_url=cfg.get("dynamodb_endpoint"),
                          **({"aws_access_key_id": "local", "aws_secret_access_key": "local"}
                             if cfg.get("dynamodb_endpoint") else {}))


def scan_all(table, **kw) -> list[dict]:
    items, page = [], table.scan(**kw)
    items += page["Items"]
    while "LastEvaluatedKey" in page:
        page = table.scan(ExclusiveStartKey=page["LastEvaluatedKey"], **kw)
        items += page["Items"]
    return items


# ───────────────────────── env_health ─────────────────────────

@task(task_run_name="health-{env}", retries=2, retry_delay_seconds=5)
def probe(env: str) -> dict:
    cfg = ENVS[env]
    if not cfg.get("api_url"):
        return {"env": env, "api": "not deployed", "latency_ms": None, "services_ok": "-", "tables_active": "-"}
    t0 = time.perf_counter()
    try:
        body = httpx.post(cfg["api_url"], json={"query": "{ health { services { service status } } }"}, timeout=15).json()
        services = body["data"]["health"]["services"]
        api, ok = "ok", sum(s["status"] == "ok" for s in services)
        services_ok = f"{ok}/{len(services)}"
        if ok < len(services):
            api = "degraded"
    except Exception as exc:  # noqa: BLE001 — a down env is a result, not a crash
        api, services_ok = f"down: {type(exc).__name__}", "0/4"
    latency = round((time.perf_counter() - t0) * 1000)
    client = dynamodb(env).meta.client
    active = 0
    for name in TABLES:
        try:
            active += client.describe_table(TableName=f"aegisvolt-{name}-{cfg['suffix']}")["Table"]["TableStatus"] == "ACTIVE"
        except Exception as exc:  # noqa: BLE001 — a missing table is counted, not raised
            get_run_logger().warning("%s: table %s: %s", env, name, type(exc).__name__)
    return {"env": env, "api": api, "latency_ms": latency, "services_ok": services_ok,
            "tables_active": f"{active}/{len(TABLES)}"}


@flow(name="env_health", log_prints=True)
def env_health(envs: list[str] | None = None) -> list[dict]:
    rows = [f.result() for f in [probe.submit(e) for e in envs or list(ENVS)]]
    create_table_artifact(key="env-health", table=rows, description=f"AegisVolt env health at {datetime.now(UTC):%H:%M} UTC")
    bad = [r["env"] for r in rows if r["api"] not in ("ok", "not deployed") or r["tables_active"] not in ("5/5", "-")]
    for r in rows:
        print(r)
    if bad:
        raise RuntimeError(f"unhealthy: {', '.join(bad)}")  # quality gate: the run goes red
    return rows


# ───────────────────────── saga_audit ─────────────────────────

@task(task_run_name="audit-{env}", retries=2, retry_delay_seconds=5)
def audit_env(env: str) -> dict:
    cfg = ENVS[env]
    if not cfg.get("api_url"):
        return {"env": env, "skipped": True}
    db, sfx = dynamodb(env), cfg["suffix"]
    sagas = [{k: v for k, v in i.items() if k not in ("pk", "sk")}
             for i in scan_all(db.Table(f"aegisvolt-sagas-{sfx}")) if i["sk"] == "META"]
    txns = [{"saga_id": i.get("saga_id"), "kind": i["kind"]}
            for i in scan_all(db.Table(f"aegisvolt-billing-{sfx}")) if i["sk"].startswith("TXN#")]
    connectors = [{"id": i["sk"].removeprefix("CONNECTOR#"), "status": i["status"], "saga_id": i.get("saga_id")}
                  for i in scan_all(db.Table(f"aegisvolt-reservations-{sfx}")) if i["sk"].startswith("CONNECTOR#")]
    report = check(sagas, txns, connectors, datetime.now(UTC))
    create_markdown_artifact(key=f"saga-audit-{env}", markdown=markdown(env, report),
                             description=f"SAGA audit {env}: {len(report['orphans'])} orphans, "
                                         f"{len(report['problems'])} inconsistencies")
    get_run_logger().info("%s: %s, %d orphans, %d inconsistencies", env, report["counts"],
                          len(report["orphans"]), len(report["problems"]))
    return {"env": env, **report}


@flow(name="saga_audit", log_prints=True)
def saga_audit(envs: list[str] | None = None) -> list[dict]:
    reports = [f.result() for f in [audit_env.submit(e) for e in envs or list(ENVS)]]
    done = [r for r in reports if not r.get("skipped")]
    create_table_artifact(key="saga-audit-summary", table=[
        {"env": r["env"], "sagas": r["total"], **r["counts"], "orphans": len(r["orphans"]),
         "inconsistencies": len(r["problems"]), "out_of_service": len(r["out_of_service"])} for r in done],
        description="SAGA audit per env")
    bad = [f"{r['env']} ({len(r['orphans'])} orphans, {len(r['problems'])} inconsistencies)"
           for r in done if r["orphans"] or r["problems"]]
    if bad:
        raise RuntimeError(f"SAGA audit failed: {'; '.join(bad)}")
    return reports


if __name__ == "__main__":
    mode, envs = (sys.argv[1] if len(sys.argv) > 1 else "run"), sys.argv[2:] or None
    if mode == "serve":
        serve(env_health.to_deployment(name="scheduled", cron=CRON),
              saga_audit.to_deployment(name="scheduled", cron=CRON))
    else:
        for f in (env_health, saga_audit):
            try:
                f(envs)
            except Exception as exc:  # noqa: BLE001 — report and go on to the next flow
                print(f"{f.name}: {exc}")
