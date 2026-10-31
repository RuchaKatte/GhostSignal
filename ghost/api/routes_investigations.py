"""
Investigations route - list and retrieve past investigations.
"""

from __future__ import annotations

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
