"""
Investigations route - list and retrieve past investigations.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from ghost.storage.db import get_db
from ghost.storage.results import get_investigation_result

log = structlog.get_logger(service="ghost.investigations")
router = APIRouter(prefix="/api", tags=["investigations"])


@router.get("/investigations")
async def list_investigations(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    async with get_db() as db:
        rows = await db.execute(
            text("SELECT * FROM investigations ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        )
        return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/investigations/{inv_id}")
async def get_investigation(inv_id: int) -> dict[str, Any]:
    async with get_db() as db:
        row = await db.execute(
            text("SELECT * FROM investigations WHERE id = :id"),
            {"id": inv_id},
        )
        result = row.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Investigation not found")
        investigation = dict(result._mapping)
    saved_result = await get_investigation_result(inv_id)
    if saved_result is not None:
        investigation["investigation_result"] = saved_result
    return investigation


@router.get("/investigations/{inv_id}/evidence")
async def get_investigation_evidence(
    inv_id: int,
    evidence_id: str | None = Query(default=None, min_length=1),
) -> dict[str, Any]:
    """Return redacted bundle evidence, optionally narrowed to one evidence ID."""
    saved_result = await get_investigation_result(inv_id)
    if saved_result is None:
        raise HTTPException(status_code=404, detail="Investigation evidence not found")

    bundle = saved_result["bundle"]
    evidence = (
        [{"kind": "log", **item} for item in bundle.get("logs", [])]
        + [{"kind": "metric", **item} for item in bundle.get("metrics", [])]
        + [{"kind": "anomaly", **item} for item in bundle.get("anomalies", [])]
    )
    if evidence_id is not None:
        evidence = [
            item for item in evidence if evidence_id in {item.get("log_id"), item.get("metric_id")}
        ]
        if not evidence:
            raise HTTPException(status_code=404, detail="Evidence ID not found")
    return {"investigation_id": inv_id, "evidence": evidence}


@router.get("/ofrenda")
async def list_ofrenda(limit: int = Query(default=50, ge=1, le=100)) -> list[dict[str, Any]]:
    """List completed and partial investigations for the incident memory page."""
    async with get_db() as db:
        rows = await db.execute(
            text(
                """SELECT i.id, i.alertname, i.service, i.severity, i.summary,
                          i.root_cause, i.alert_time, i.updated_at, r.report_json
                   FROM investigations AS i
                   JOIN investigation_results AS r ON r.investigation_id = i.id
                   WHERE i.status IN ('completed', 'partial')
                   ORDER BY i.updated_at DESC LIMIT :limit"""
            ),
            {"limit": limit},
        )
        entries = [dict(row._mapping) for row in rows.fetchall()]
    for entry in entries:
        entry["report"] = json.loads(entry.pop("report_json"))
    return entries
