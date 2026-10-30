"""
Load generator - produces steady synthetic traffic against the gateway.
Sends orders at a configurable rate and logs outcomes.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
import uuid
import structlog

import httpx

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
log = structlog.get_logger(service="loadgen")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8001")
REQUESTS_PER_SECOND = float(os.getenv("RPS", "2"))
ITEMS = ["widget", "gadget", "doohickey", "thingamajig", "whatsit", "gizmo"]

stats = {"success": 0, "error": 0, "total": 0}


async def send_order(client: httpx.AsyncClient) -> None:
    request_id = str(uuid.uuid4())[:8]
    item = random.choice(ITEMS)
    start = time.perf_counter()
    try:
        resp = await client.post(
            f"{GATEWAY_URL}/order",
            json={"item": item, "quantity": random.randint(1, 10)},
            headers={"X-Request-ID": request_id},
            timeout=10.0,
        )
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        stats["total"] += 1
        if resp.status_code < 400:
            stats["success"] += 1
            log.info("order_sent", item=item, status=resp.status_code,
                     duration_ms=duration_ms, request_id=request_id)
        else:
            stats["error"] += 1
            log.warning("order_error", item=item, status=resp.status_code,
                        duration_ms=duration_ms, request_id=request_id)
    except Exception as exc:
        stats["error"] += 1
        stats["total"] += 1
        log.error("order_failed", item=item, error=str(exc), request_id=request_id)


async def stats_reporter():
    while True:
        await asyncio.sleep(30)
        total = stats["total"]
        error_rate = (stats["error"] / total * 100) if total else 0
        log.info("loadgen_stats", total=total, success=stats["success"],
                 errors=stats["error"], error_rate_pct=round(error_rate, 1))


async def main():
    log.info("loadgen_starting", rps=REQUESTS_PER_SECOND, gateway=GATEWAY_URL)
    interval = 1.0 / REQUESTS_PER_SECOND

    # Wait for gateway to be ready
    await asyncio.sleep(5)
    for attempt in range(20):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{GATEWAY_URL}/health", timeout=2.0)
                if resp.status_code == 200:
                    log.info("gateway_ready", attempt=attempt)
                    break
        except Exception:
            pass
        await asyncio.sleep(3)

    async with httpx.AsyncClient() as client:
        reporter = asyncio.create_task(stats_reporter())
        try:
            while True:
                await send_order(client)
                await asyncio.sleep(interval)
        finally:
            reporter.cancel()


if __name__ == "__main__":
    asyncio.run(main())
