"""
Orders API - manages order lifecycle, reads/writes to Postgres.
Intentionally has a connection pool that can be leaked/exhausted.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

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
log = structlog.get_logger(service="orders-api")

# ── Prometheus metrics ─────────────────────────────────────────────────────────
REQUEST_COUNT = Counter("orders_requests_total", "Total requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram(
    "orders_request_duration_seconds", "Request latency", ["endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
)
DB_POOL_SIZE = Gauge("orders_db_pool_size", "DB pool max connections")
DB_POOL_IN_USE = Gauge("orders_db_pool_in_use", "DB pool connections currently in use")
DB_ERRORS = Counter("orders_db_errors_total", "DB errors", ["type"])
ORDER_COUNT = Counter("orders_created_total", "Orders created")

POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER','ghost')}:{os.getenv('POSTGRES_PASSWORD','ghostpass')}"
    f"@{os.getenv('POSTGRES_HOST','postgres')}:{os.getenv('POSTGRES_PORT','5432')}"
    f"/{os.getenv('POSTGRES_DB','ghostdemo')}"
)

POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
_pool: asyncpg.Pool | None = None
_pool_max_current = POOL_MAX
_held_connections: list[asyncpg.Connection] = []  # chaos: leaked connections
_chaos: dict[str, Any] = {}


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=POSTGRES_DSN,
            min_size=2,
            max_size=_pool_max_current,
            command_timeout=10,
        )
        DB_POOL_SIZE.set(_pool_max_current)
        log.info("db_pool_created", max_size=_pool_max_current)
    return _pool


async def _update_pool_gauge():
    """Periodically update pool in-use gauge."""
    while True:
        if _pool:
            try:
                used = _pool.get_size() - _pool.get_idle_size()
                DB_POOL_IN_USE.set(used)
            except Exception:
                pass
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    task = asyncio.create_task(_update_pool_gauge())
    log.info("orders_api_starting", pool_max=POOL_MAX)
    yield
    task.cancel()
    if _pool:
        await _pool.close()
    log.info("orders_api_stopping")


app = FastAPI(title="Ghost Demo Orders API", lifespan=lifespan)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration = time.perf_counter() - start
        REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
        REQUEST_LATENCY.labels(request.url.path).observe(duration)
        if response.status_code >= 500:
            log.error("request_error", path=request.url.path, status=response.status_code,
                      duration_ms=round(duration * 1000, 2), request_id=request_id)
        else:
            log.info("request_handled", path=request.url.path, status=response.status_code,
                     duration_ms=round(duration * 1000, 2), request_id=request_id)
        return response
    except Exception as exc:
        log.error("request_failed", path=request.url.path, error=str(exc), request_id=request_id)
        raise


@app.get("/health")
async def health():
    pool = await get_pool()
    return {"status": "ok", "service": "orders-api", "pool_size": pool.get_size()}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/orders")
async def create_order(request: Request):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    body = await request.json()

    # Chaos: simulate pool exhaustion by holding connections
    if _chaos.get("leak_connections") and len(_held_connections) < _chaos.get("max_leak", 8):
        try:
            pool = await get_pool()
            conn = await pool.acquire(timeout=0.1)
            _held_connections.append(conn)
            log.warning("connection_leaked", held=len(_held_connections),
                        pool_max=_pool_max_current, request_id=request_id)
        except Exception:
            pass

    pool = await get_pool()
    try:
        async with pool.acquire(timeout=_chaos.get("pool_timeout", 5.0)) as conn:
            order_id = str(uuid.uuid4())[:8]
            await conn.execute(
                "INSERT INTO orders (id, item, status, request_id) VALUES ($1, $2, $3, $4)",
                order_id, body.get("item", "unknown"), "pending", request_id,
            )
            ORDER_COUNT.inc()
            log.info("order_created", order_id=order_id, item=body.get("item"),
                     request_id=request_id)
            return {"order_id": order_id, "status": "pending"}
    except asyncpg.TooManyConnectionsError:
        DB_ERRORS.labels("too_many_connections").inc()
        log.error("db_pool_exhausted", pool_size=pool.get_size(),
                  in_use=pool.get_size() - pool.get_idle_size(), request_id=request_id)
        raise HTTPException(status_code=503, detail="Database pool exhausted")
    except asyncio.TimeoutError:
        DB_ERRORS.labels("pool_timeout").inc()
        log.error("db_pool_timeout", held_connections=len(_held_connections),
                  pool_max=_pool_max_current, request_id=request_id)
        raise HTTPException(status_code=503, detail="Database pool timeout - pool exhausted")
    except Exception as exc:
        DB_ERRORS.labels("query_error").inc()
        log.error("db_error", error=str(exc), request_id=request_id)
        raise HTTPException(status_code=500, detail="Database error")


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    pool = await get_pool()
    async with pool.acquire(timeout=5.0) as conn:
        row = await conn.fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
        if not row:
            raise HTTPException(status_code=404, detail="Order not found")
        return dict(row)


# Chaos control
@app.post("/_chaos/set")
async def set_chaos(request: Request):
    global _chaos, _pool, _pool_max_current
    data = await request.json()
    _chaos = data

    # bad_deploy: reduce pool size
    if "pool_max" in data:
        new_max = data["pool_max"]
        log.warning("pool_size_changing", old=_pool_max_current, new=new_max)
        _pool_max_current = new_max
        DB_POOL_SIZE.set(new_max)
        # close and recreate pool with new limits
        if _pool:
            await _pool.close()
            _pool = None
        await get_pool()

    log.warning("chaos_applied", config=_chaos)
    return {"ok": True}


@app.post("/_chaos/clear")
async def clear_chaos():
    global _chaos, _pool, _pool_max_current, _held_connections
    _chaos = {}
    _pool_max_current = POOL_MAX

    # Release leaked connections
    for conn in _held_connections:
        try:
            pool = await get_pool()
            await pool.release(conn)
        except Exception:
            pass
    _held_connections.clear()

    # Recreate pool
    if _pool:
        await _pool.close()
        _pool = None
    await get_pool()
    log.info("chaos_cleared", pool_max=POOL_MAX)
    return {"ok": True}


@app.post("/_events/deploy")
async def record_deploy(request: Request):
    """Record a deploy event (used by chaos injector)."""
    data = await request.json()
    log.warning("deploy_event", **data)
    return {"ok": True}
