"""Create the 5 tables (local only) and load seed/seed.json (mirrors infra/modules/data); or reset an env to the seed.

Default: tables that already exist are left alone, so restarting compose keeps local state.
--reset: empty every table of the env and load the seed again. Extra connectors, new fleets, deposits, sagas,
         sessions and telemetry are erased; flags go back to that env's defaults. Works on DynamoDB Local and on AWS.

Usage: DYNAMODB_ENDPOINT=http://localhost:8000 python scripts/seed_local.py [suffix] [flag-defaults-env] [--reset]
       python scripts/seed_local.py dev dev --reset      # AWS dev (operator credentials, no endpoint)
"""
import json
import os
import pathlib
import sys

import boto3

RESET = "--reset" in sys.argv
ARGS = [a for a in sys.argv[1:] if a != "--reset"]
SUFFIX = ARGS[0] if ARGS else "local"
FLAG_ENV = ARGS[1] if len(ARGS) > 1 else "dev"
SEED = json.loads((pathlib.Path(__file__).parent.parent / "seed" / "seed.json").read_text())
ddb = boto3.resource("dynamodb", region_name="us-east-1", endpoint_url=os.environ.get("DYNAMODB_ENDPOINT") or None)
TABLES = {"reservations": ["pk", "sk"], "billing": ["pk", "sk"], "dispatch": ["pk", "sk"], "sagas": ["pk", "sk"],
          "flags": ["name"]}


def create(name: str):
    full, keys = f"aegisvolt-{name}-{SUFFIX}", TABLES[name]
    if full in ddb.meta.client.list_tables()["TableNames"]:
        print(f"{full} exists, skipping")
        return None
    t = ddb.create_table(
        TableName=full, BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": k, "KeyType": kt} for k, kt in zip(keys, ("HASH", "RANGE"))],
        AttributeDefinitions=[{"AttributeName": k, "AttributeType": "S"} for k in keys])
    t.wait_until_exists()
    print(f"{full} created")
    return t


def empty(name: str):
    t, keys = ddb.Table(f"aegisvolt-{name}-{SUFFIX}"), TABLES[name]
    scan, n = {"ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(keys))),
               "ExpressionAttributeNames": {f"#k{i}": k for i, k in enumerate(keys)}}, 0
    with t.batch_writer() as batch:
        while True:
            page = t.scan(**scan)
            for item in page["Items"]:
                batch.delete_item(Key={k: item[k] for k in keys})
                n += 1
            if "LastEvaluatedKey" not in page:
                break
            scan["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    print(f"{t.name}: {n} items erased")
    return t


LOAD = {
    "reservations": lambda t: [
        t.put_item(Item=item) for s in SEED["stations"] for item in [
            {"pk": f"STATION#{s['id']}", "sk": "META", "name": s["name"], "city": s["city"]},
            *({"pk": f"STATION#{s['id']}", "sk": f"CONNECTOR#{s['id']}-{c['suffix']}", "type": c["type"],
               "power_kw": c["power_kw"], "status": "AVAILABLE"} for c in SEED["connectors"])]],
    "billing": lambda t: [t.put_item(Item={"pk": f"FLEET#{f['id']}", "sk": "WALLET", "balance_cents": f["balance_cents"]})
                          for f in SEED["fleets"]],
    "flags": lambda t: [t.put_item(Item={"name": n, "enabled": f[FLAG_ENV], "description": f["description"]})
                        for n, f in SEED["flags"].items()],
}

if RESET and SUFFIX in ("prod",) and os.environ.get("I_MEAN_PROD") != "1":
    sys.exit("refusing to reset prod; set I_MEAN_PROD=1 if you really mean it")

for name in TABLES:
    table = empty(name) if RESET else create(name)
    if table and name in LOAD:
        LOAD[name](table)
if RESET:
    print(f"{SUFFIX} is back to seed/seed.json (flags: {FLAG_ENV} defaults)")
