"""
Gateway service - entry point for all incoming requests.
Routes to orders-api and surfaces errors from downstream services.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ── Structured logging ─────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="gateway")

# ── Prometheus metrics ─────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "gateway_requests_total", "Total requests", ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "gateway_request_duration_seconds",
    "Request latency",
    ["endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)
ACTIVE_REQUESTS = Gauge("gateway_active_requests", "Active requests in flight")
ERROR_RATE = Counter("gateway_errors_total", "Total errors", ["type"])

ORDERS_URL = os.getenv("ORDERS_API_URL", "http://orders-api:8002")
PAYMENTS_URL = os.getenv("PAYMENTS_WORKER_URL", "http://payments-worker:8003")

# Chaos state (injected via env or internal flag)
_chaos: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("gateway_starting", version="1.0.0")
    yield
    log.info("gateway_stopping")


app = FastAPI(title="Ghost Demo Gateway", lifespan=lifespan)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    start = time.perf_counter()
    ACTIVE_REQUESTS.inc()
    try:
        response = await call_next(request)
        duration = time.perf_counter() - start
        REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
        REQUEST_LATENCY.labels(request.url.path).observe(duration)
        log.info(
            "request_handled",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(duration * 1000, 2),
            request_id=request_id,
        )
        return response
    except Exception as exc:
        ERROR_RATE.labels("unhandled").inc()
        log.error("request_failed", path=request.url.path, error=str(exc), request_id=request_id)
        raise
    finally:
        ACTIVE_REQUESTS.dec()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "gateway"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/order")
async def create_order(request: Request):
    """Create an order by calling orders-api, then trigger payment."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    body = await request.json()

    # Apply slow_downstream chaos
    extra_delay = _chaos.get("extra_delay_ms", 0)
    if extra_delay:
        await asyncio.sleep(extra_delay / 1000)

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{ORDERS_URL}/orders",
                json=body,
                headers={"X-Request-ID": request_id},
            )
            resp.raise_for_status()
            order = resp.json()
        except httpx.HTTPStatusError as exc:
            ERROR_RATE.labels("upstream_5xx").inc()
            log.error(
                "orders_api_error",
                status=exc.response.status_code,
                request_id=request_id,
            )
            return Response(content='{"error":"orders service failed"}', status_code=502)
        except httpx.RequestError as exc:
            ERROR_RATE.labels("connection_error").inc()
            log.error("orders_api_unreachable", error=str(exc), request_id=request_id)
            return Response(content='{"error":"orders service unavailable"}', status_code=503)

    return {"order": order, "request_id": request_id}


@app.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(
                f"{ORDERS_URL}/orders/{order_id}",
                headers={"X-Request-ID": request_id},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            ERROR_RATE.labels("get_order_error").inc()
            log.error("get_order_failed", order_id=order_id, error=str(exc), request_id=request_id)
            return Response(status_code=502)


# Chaos control (used by injector)
@app.post("/_chaos/set")
async def set_chaos(request: Request):
    global _chaos
    _chaos = await request.json()
    log.warning("chaos_applied", config=_chaos)
    return {"ok": True}


@app.post("/_chaos/clear")
async def clear_chaos():
    global _chaos
    _chaos = {}
    log.info("chaos_cleared")
    return {"ok": True}
