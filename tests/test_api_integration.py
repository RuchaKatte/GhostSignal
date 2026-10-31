"""Tests for alert validation, background queuing, and investigation report exposure."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError

from ghost.api import routes_alert, routes_investigations
from ghost.ingest.bundle import IncidentBundle


def _payload() -> routes_alert.AlertmanagerPayload:
    return routes_alert.AlertmanagerPayload.model_validate(
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighErrorRate",
                        "severity": "warning",
                        "service": "gateway",
                    },
                    "annotations": {"summary": "Error rate is high"},
                    "startsAt": "2026-01-01T00:00:00Z",
                    "endsAt": "0001-01-01T00:00:00Z",
                    "fingerprint": "stable-alert-fingerprint",
                }
            ],
        }
    )


class FakeResult:
    def __init__(self, *, row: Any = None, rowcount: int = 0) -> None:
        self.row = row
        self.rowcount = rowcount
        self.lastrowid = None

    def fetchone(self) -> Any:
        return self.row


class FakeDB:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement: Any, _params: dict[str, Any] | None = None) -> FakeResult:
        sql = str(statement)
        self.statements.append(sql)
        if sql.startswith("SELECT id, status"):
            return FakeResult()
        if sql.startswith("INSERT OR IGNORE"):
            return FakeResult(rowcount=1)
        if sql.startswith("SELECT id FROM"):
            return FakeResult(row=type("Row", (), {"id": 17})())
        return FakeResult(rowcount=1)

    async def commit(self) -> None:
        return None


class FakeDBContext:
    def __init__(self, db: FakeDB) -> None:
        self.db = db

    async def __aenter__(self) -> FakeDB:
        return self.db

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_alert_is_validated_and_queued_without_blocking_request() -> None:
    payload = _payload()
    tasks = BackgroundTasks()

    result = await routes_alert.receive_alert(payload, tasks)

    assert result["accepted"] is True
    assert result["firing_count"] == 1
    assert len(tasks.tasks) == 1


def test_invalid_alert_status_or_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError):
        routes_alert.Alert.model_validate(
            {
                "status": "unknown",
                "labels": {"alertname": "BadAlert"},
                "startsAt": "2026-01-01T00:00:00Z",
            }
        )
    with pytest.raises(ValidationError):
        routes_alert.Alert.model_validate(
            {
                "status": "firing",
                "labels": {"alertname": "BadAlert"},
                "startsAt": "2026-01-01T00:00:00",
            }
        )


@pytest.mark.asyncio
async def test_alert_pipeline_collects_and_starts_investigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeDB()
    monkeypatch.setattr(routes_alert, "get_db", lambda: FakeDBContext(db))
    bundle = IncidentBundle(
        incident_id="17",
        alert_name="HighErrorRate",
        service="gateway",
        window_start="2026-01-01T00:00:00Z",
        window_end="2026-01-01T00:10:00Z",
    )
    collection: dict[str, Any] = {}

    async def collect(**kwargs: Any) -> IncidentBundle:
        collection.update(kwargs)
        return bundle

    class FakeOrchestrator:
        async def investigate(
            self, supplied_bundle: IncidentBundle, *, investigation_id: int
        ) -> dict[str, Any]:
            assert supplied_bundle is bundle
            assert investigation_id == 17
            return {"pipeline_status": "completed"}

    monkeypatch.setattr(routes_alert, "collect_incident_bundle", collect)
    monkeypatch.setattr("ghost.agents.orchestrator.InvestigationOrchestrator", FakeOrchestrator)

    await routes_alert._process_alert(_payload().alerts[0])

    assert collection["incident_id"] == "17"
    assert collection["service"] == "gateway"
    assert collection["window_start"].tzinfo == UTC
    assert collection["window_end"] <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_existing_investigation_endpoint_includes_saved_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RowResult:
        def fetchone(self) -> Any:
            return type("Row", (), {"_mapping": {"id": 9, "status": "completed"}})()

    class ReadDB:
        async def execute(self, *_args: Any, **_kwargs: Any) -> RowResult:
            return RowResult()

    monkeypatch.setattr(routes_investigations, "get_db", lambda: FakeDBContext(ReadDB()))

    async def get_saved_result(_investigation_id: int) -> dict[str, Any]:
        return {"report": {"incident_id": "9"}}

    monkeypatch.setattr(routes_investigations, "get_investigation_result", get_saved_result)

    result = await routes_investigations.get_investigation(9)

    assert result["id"] == 9
    assert result["investigation_result"]["report"]["incident_id"] == "9"
