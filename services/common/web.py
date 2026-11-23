"""FastAPI app factory shared by every service (ported from wandersync services/common/web.py).

Adds: DynamoDB health check, X-Correlation-ID → contextvar → every log line, X-Version header (canary gate).
"""
import logging
import os
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from common import ddb
from common.log import ENV, VERSION, correlation_id, event, setup


def make_app(service: str) -> FastAPI:
    setup(service)
    app = FastAPI(title=service, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health(response: Response):
        # Healthy = this service can reach its own table.
        try:
            ddb.client().describe_table(TableName=os.environ["TABLE_NAME"])
        except Exception as exc:  # noqa: BLE001 — any failure means unhealthy
            response.status_code = 503
            return {"status": "db_unavailable", "service": service, "env": ENV, "version": VERSION,
                    "error": type(exc).__name__}
        return {"status": "ok", "service": service, "env": ENV, "version": VERSION}

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        # level=ERROR + version=canary is what the CanaryErrors metric filter counts.
        event("http.error", f"{type(exc).__name__}: {exc}", logging.ERROR)
        return JSONResponse({"error": "INTERNAL"}, status_code=500)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        cid = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        token = correlation_id.set(cid)
        try:
            response = await call_next(request)
        finally:
            correlation_id.reset(token)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Version"] = VERSION
        return response

    return app
