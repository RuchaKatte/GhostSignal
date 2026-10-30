"""
Investigations route - list and retrieve past investigations.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter
from sqlalchemy import text

from ghost.storage.db import get_db

log = structlog.get_logger(service="ghost.investigations")
router = APIRouter(prefix="/api", tags=["investigations"])


@router.get("/investigations")
async def list_investigations(limit: int = 20) -> list[dict[str, Any]]:
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
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Investigation not found")
        return dict(result._mapping)
