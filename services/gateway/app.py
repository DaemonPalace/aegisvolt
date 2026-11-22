"""graphql-gateway — the only entry point (API Gateway → VPC Link → NLB → this pod) and the SAGA orchestrator.

Ported from wandersync services/gateway/app.py: GraphQLRouter, alias limit, CORS, security headers.
Sessions removed; admin mutations check X-Admin-Token (schema.require_admin).
"""
import asyncio
import os
from contextlib import asynccontextmanager

import saga
from common.web import make_app
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from schema import schema
from strawberry.fastapi import GraphQLRouter


@asynccontextmanager
async def lifespan(_app):
    recovery = asyncio.create_task(saga.recover())  # crash recovery, off the startup path
    yield
    recovery.cancel()
    if saga.http:
        await saga.http.aclose()


app = make_app("graphql-gateway")
app.router.lifespan_context = lifespan
app.include_router(GraphQLRouter(schema), prefix="/graphql")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                             "Referrer-Policy": "no-referrer"})
    return response


# Added last = outermost: answers the OPTIONS preflight that API Gateway proxies through.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Admin-Token", "X-Correlation-ID"],
    expose_headers=["X-Version", "X-Correlation-ID"],
)
