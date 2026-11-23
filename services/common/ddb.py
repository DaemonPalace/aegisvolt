"""DynamoDB access shared by every service: table handles, feature flags, Decimal → JSON."""
import os
from decimal import Decimal
from functools import cache

import boto3
from botocore.exceptions import ClientError


@cache
def resource():
    return boto3.resource(
        "dynamodb",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        endpoint_url=os.environ.get("DYNAMODB_ENDPOINT") or None,  # DynamoDB Local in compose
    )


def table(name: str):
    return resource().Table(name)


def client():
    # The resource's client accepts plain Python values (no {"S": ...} wrappers).
    return resource().meta.client


def flag(name: str) -> bool:
    """Read on every request, no cache: a toggle applies to the very next call (SPEC §3)."""
    item = table(os.environ["FLAGS_TABLE"]).get_item(Key={"name": name}, ConsistentRead=True).get("Item")
    return bool(item and item.get("enabled"))


def cancel_reasons(exc: ClientError) -> list[bool]:
    """TransactionCanceledException → one bool per TransactItem: True = that item's condition failed."""
    if exc.response["Error"]["Code"] != "TransactionCanceledException":
        raise exc
    return [r.get("Code") == "ConditionalCheckFailed" for r in exc.response.get("CancellationReasons", [])]


def plain(value):
    """DynamoDB returns numbers as Decimal; JSON wants int/float."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def json_safe(value):
    """boto3 rejects float; store it as Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value
