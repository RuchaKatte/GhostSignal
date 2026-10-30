"""
Ghost in the Logs - Main FastAPI application.
Entry point that wires together all routes and middleware.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ghost.api.routes_alert import router as alert_router
from ghost.api.routes_investigations import router as inv_router
from ghost.config import get_settings
from ghost.storage.db import init_db

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
log = structlog.get_logger(service="ghost")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info("ghost_starting", version="0.1.0", db=settings.ghost_db_path)
    await init_db()
    log.info("ghost_ready", host=settings.ghost_host, port=settings.ghost_port)
    yield
    log.info("ghost_stopping")


app = FastAPI(
    title="Ghost in the Logs",
    description="AI incident investigator powered by local LLMs",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(alert_router)
app.include_router(inv_router)


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "ghost"}


# ── Metrics (Prometheus) ───────────────────────────────────────────────────────
@app.get("/metrics")
async def metrics():
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ── Static UI ──────────────────────────────────────────────────────────────────
UI_DIR = os.path.join(os.path.dirname(__file__), "../../ui")
if os.path.isdir(UI_DIR):
    app.mount("/static", StaticFiles(directory=os.path.join(UI_DIR, "static")), name="static")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(UI_DIR, "index.html"))

    @app.get("/investigation/{inv_id}")
    async def investigation_page(inv_id: str):
        return FileResponse(os.path.join(UI_DIR, "investigation.html"))

    @app.get("/ofrenda")
    async def ofrenda_page():
        return FileResponse(os.path.join(UI_DIR, "ofrenda.html"))
