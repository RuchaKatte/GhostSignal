"""Persistence round-trip tests for investigation results."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from ghost.api.routes_alert import Alert, _create_or_get_investigation
from ghost.ingest.bundle import IncidentBundle
from ghost.storage import db
from ghost.storage.results import get_investigation_result, save_investigation_result


@pytest.mark.asyncio
async def test_saved_bundle_and_report_are_exposed_from_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    db._engine = None
    db._async_session = None
    monkeypatch.setattr(
        db,
        "get_settings",
        lambda: SimpleNamespace(ghost_db_path=str(tmp_path / "ghost.db")),
    )
    try:
        await db.init_db()
        alert = Alert.model_validate(
            {
                "status": "firing",
                "labels": {"alertname": "HighErrorRate", "service": "gateway"},
                "startsAt": "2026-01-01T00:00:00Z",
                "fingerprint": "stable-fingerprint",
            }
        )
        first_id, first_run = await _create_or_get_investigation(alert)
        second_id, second_run = await _create_or_get_investigation(alert)
        assert first_id == second_id
        assert first_run is True
        assert second_run is False

        async with db.get_db() as session:
            await session.execute(
                text(
                    "INSERT INTO investigations "
                    "(id, alertname, service, status) VALUES (41, 'HighErrorRate', "
                    "'gateway', 'pending')"
                )
            )
            await session.commit()

        bundle = IncidentBundle(
            incident_id="41",
            alert_name="HighErrorRate",
            service="gateway",
            logs=[{"log_id": "log-1", "message": "redacted"}],
        )
        report = {
            "incident_id": "41",
            "incident_summary": "Insufficient evidence",
            "pipeline_status": "partial",
            "root_cause_hypotheses": [],
        }

        await save_investigation_result(41, bundle, report)
        result = await get_investigation_result(41)

        assert result is not None
        assert result["bundle"]["incident_id"] == "41"
        assert result["report"] == report
        async with db.get_db() as session:
            row = await session.execute(text("SELECT status FROM investigations WHERE id = 41"))
            assert row.scalar_one() == "partial"
    finally:
        if db._engine is not None:
            await db._engine.dispose()
        db._engine = None
        db._async_session = None
