"""Persistence helpers for complete investigation bundles and reports."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from ghost.ingest.bundle import IncidentBundle


async def save_investigation_result(
    investigation_id: int,
    bundle: IncidentBundle,
    report: dict[str, Any],
) -> None:
    """Persist the complete evidence bundle and structured report."""
    report_json = json.dumps(report, sort_keys=True, default=str)
    root_cause = next(
        (
            item["description"]
            for item in report.get("root_cause_hypotheses", [])
            if item.get("status") == "Established"
        ),
        None,
    )
    from ghost.storage.db import get_db

    async with get_db() as db:
        await db.execute(
            text(
                """INSERT INTO investigation_results
                   (investigation_id, bundle_json, report_json, updated_at)
                   VALUES (:investigation_id, :bundle_json, :report_json, datetime('now'))
                   ON CONFLICT(investigation_id) DO UPDATE SET
                       bundle_json = excluded.bundle_json,
                       report_json = excluded.report_json,
                       updated_at = datetime('now')"""
            ),
            {
                "investigation_id": investigation_id,
                "bundle_json": bundle.to_json(),
                "report_json": report_json,
            },
        )
        await db.execute(
            text(
                """UPDATE investigations
                   SET status = :status, summary = :summary, root_cause = :root_cause,
                       narrative = :narrative, updated_at = datetime('now')
                   WHERE id = :investigation_id"""
            ),
            {
                "status": report.get("pipeline_status", "completed"),
                "summary": report.get("incident_summary", ""),
                "root_cause": root_cause,
                "narrative": report.get("incident_summary", ""),
                "investigation_id": investigation_id,
            },
        )
        await db.commit()


async def get_investigation_result(investigation_id: int) -> dict[str, Any] | None:
    """Retrieve a saved bundle/report payload for an investigation."""
    from ghost.storage.db import get_db

    async with get_db() as db:
        result = await db.execute(
            text(
                "SELECT bundle_json, report_json, updated_at FROM investigation_results "
                "WHERE investigation_id = :investigation_id"
            ),
            {"investigation_id": investigation_id},
        )
        row = result.fetchone()
    if row is None:
        return None
    return {
        "bundle": json.loads(row.bundle_json),
        "report": json.loads(row.report_json),
        "updated_at": row.updated_at,
    }
