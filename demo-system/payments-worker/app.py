"""
Payments Worker - processes payment events asynchronously.
Simulates memory accumulation and slow downstream calls.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog
from fastapi import FastAPI, Request, Response
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
log = structlog.get_logger(service="payments-worker")

PAYMENTS_PROCESSED = Counter("payments_processed_total", "Payments processed", ["status"])
PAYMENT_LATENCY = Histogram(
    "payment_processing_seconds", "Payment processing time",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
)
MEMORY_USED = Gauge("payments_memory_bytes", "Simulated memory used by worker")
QUEUE_DEPTH = Gauge("payments_queue_depth", "Simulated payment queue depth")

POSTGRES_DSN = (
    f"postgresql://{os.getenv('POSTGRES_USER','ghost')}:{os.getenv('POSTGRES_PASSWORD','ghostpass')}"
    f"@{os.getenv('POSTGRES_HOST','postgres')}:{os.getenv('POSTGRES_PORT','5432')}"
    f"/{os.getenv('POSTGRES_DB','ghostdemo')}"
)

_pool: asyncpg.Pool | None = None
_chaos: dict[str, Any] = {}
_memory_hog: list = []  # chaos: grows list to simulate leak


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = await asyncpg.create_pool(dsn=POSTGRES_DSN, min_size=1, max_size=5)
    task = asyncio.create_task(_process_queue())
    log.info("payments_worker_starting")
    yield
    task.cancel()
    await _pool.close()
    log.info("payments_worker_stopping")


app = FastAPI(title="Ghost Demo Payments Worker", lifespan=lifespan)


async def _process_queue():
    """Background task: simulate processing payment events."""
    while True:
        depth = random.randint(0, 5)
        QUEUE_DEPTH.set(depth)

        # memory leak chaos
        if _chaos.get("memory_leak"):
            chunk = " " * 10240  # 10KB per tick
            _memory_hog.append(chunk)
            mem_mb = len(_memory_hog) * 10 / 1024
            MEMORY_USED.set(len(_memory_hog) * 10240)
            if len(_memory_hog) % 50 == 0:
                log.warning("memory_usage_high", memory_mb=round(mem_mb, 1),
                            leaked_chunks=len(_memory_hog))

        await asyncio.sleep(2)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "payments-worker"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/payments/process")
async def process_payment(request: Request):
    """Process a payment - called internally."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    body = await request.json()

    start = time.perf_counter()
    extra_delay = _chaos.get("extra_delay_ms", 0)

    try:
        if extra_delay:
            log.warning("slow_downstream_active", delay_ms=extra_delay, request_id=request_id)
            await asyncio.sleep(extra_delay / 1000)

        # Simulate payment processing
        await asyncio.sleep(random.uniform(0.01, 0.05))

        if random.random() < _chaos.get("error_rate", 0.01):
            raise ValueError("Payment gateway timeout")

        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO payments (id, order_id, amount, status) VALUES ($1, $2, $3, $4)",
                str(uuid.uuid4())[:8], body.get("order_id", "unknown"),
                body.get("amount", 0.0), "completed",
            )

        duration = time.perf_counter() - start
        PAYMENT_LATENCY.observe(duration)
        PAYMENTS_PROCESSED.labels("success").inc()
        log.info("payment_processed", order_id=body.get("order_id"),
                 duration_ms=round(duration * 1000, 2), request_id=request_id)
        return {"status": "completed", "request_id": request_id}

    except Exception as exc:
        duration = time.perf_counter() - start
        PAYMENTS_PROCESSED.labels("failed").inc()
        log.error("payment_failed", error=str(exc), order_id=body.get("order_id"),
                  duration_ms=round(duration * 1000, 2), request_id=request_id)
        return {"status": "failed", "error": str(exc)}, 500


@app.post("/_chaos/set")
async def set_chaos(request: Request):
    global _chaos
    _chaos = await request.json()
    log.warning("chaos_applied", config=_chaos)
    return {"ok": True}


@app.post("/_chaos/clear")
async def clear_chaos():
    global _chaos, _memory_hog
    _chaos = {}
    _memory_hog.clear()
    MEMORY_USED.set(0)
    log.info("chaos_cleared")
    return {"ok": True}
