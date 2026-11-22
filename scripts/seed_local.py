"""Local only: create the 5 tables in DynamoDB Local and load seed/seed.json (mirrors infra/modules/data).

Tables that already exist are left alone, so restarting compose keeps local state.
Usage: DYNAMODB_ENDPOINT=http://localhost:8000 python scripts/seed_local.py [suffix] [flag-defaults-env]
"""
import json
import os
import pathlib
import sys

import boto3

SUFFIX = sys.argv[1] if len(sys.argv) > 1 else "local"
FLAG_ENV = sys.argv[2] if len(sys.argv) > 2 else "dev"
SEED = json.loads((pathlib.Path(__file__).parent.parent / "seed" / "seed.json").read_text())
ddb = boto3.resource("dynamodb", region_name="us-east-1", endpoint_url=os.environ.get("DYNAMODB_ENDPOINT") or None)


def create(name: str, keys: list[str]):
    full = f"aegisvolt-{name}-{SUFFIX}"
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


if res := create("reservations", ["pk", "sk"]):
    for s in SEED["stations"]:
        res.put_item(Item={"pk": f"STATION#{s['id']}", "sk": "META", "name": s["name"], "city": s["city"]})
        for c in SEED["connectors"]:
            res.put_item(Item={"pk": f"STATION#{s['id']}", "sk": f"CONNECTOR#{s['id']}-{c['suffix']}",
                               "type": c["type"], "power_kw": c["power_kw"], "status": "AVAILABLE"})
if bil := create("billing", ["pk", "sk"]):
    for f in SEED["fleets"]:
        bil.put_item(Item={"pk": f"FLEET#{f['id']}", "sk": "WALLET", "balance_cents": f["balance_cents"]})
create("dispatch", ["pk", "sk"])
create("sagas", ["pk", "sk"])
if flg := create("flags", ["name"]):
    for name, f in SEED["flags"].items():
        flg.put_item(Item={"name": name, "enabled": f[FLAG_ENV], "description": f["description"]})
