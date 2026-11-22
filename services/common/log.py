"""JSON logging: one line per event, tagged with env/service/version/correlation_id (SPEC §8)."""
import logging
import os
import sys
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

ENV = os.environ.get("ENV", "local")
VERSION = os.environ.get("VERSION", "stable")
IMAGE_TAG = os.environ.get("IMAGE_TAG", "dev")

log = logging.getLogger("aegisvolt")


class _Context(logging.Filter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record):
        record.env, record.service, record.version = ENV, self.service, VERSION
        record.image_tag, record.correlation_id = IMAGE_TAG, correlation_id.get()
        if not hasattr(record, "event"):
            record.event = "log"
        return True


def setup(service: str) -> None:
    if log.handlers:
        return
    formatter = JsonFormatter(
        "%(asctime)s %(levelname)s %(env)s %(service)s %(version)s %(image_tag)s %(correlation_id)s %(event)s %(message)s",
        rename_fields={"asctime": "ts", "levelname": "level", "message": "msg"},
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if os.environ.get("CLOUDWATCH_LOGS") == "true":
        import watchtower

        handlers.append(watchtower.CloudWatchLogHandler(
            log_group_name=f"/aegisvolt/{ENV}/{service}",
            log_stream_name=os.environ.get("HOSTNAME", service),  # k8s sets HOSTNAME = pod name
            create_log_group=False,  # Terraform owns the group (retention, metric filters)
        ))
    for h in handlers:
        h.setFormatter(formatter)
        h.addFilter(_Context(service))
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False


def event(name: str, msg: str, level: int = logging.INFO, **fields) -> None:
    log.log(level, msg, extra={"event": name, **fields})
