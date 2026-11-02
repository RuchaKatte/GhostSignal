"""Tests for alert validation, background queuing, and investigation report exposure."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from ghost.api import routes_alert, routes_investigations
from ghost.api.main import app
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
async def test_alert_is_validated_and_queued_without_blocking_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    tasks = BackgroundTasks()
    monkeypatch.setattr(
        routes_alert,
        "get_settings",
        lambda: SimpleNamespace(alert_webhook_secret="test-secret"),
    )

    result = await routes_alert.receive_alert(payload, tasks, "Bearer test-secret")

    assert result["accepted"] is True
    assert result["firing_count"] == 1
    assert len(tasks.tasks) == 1


@pytest.mark.asyncio
async def test_alert_webhook_rejects_missing_or_invalid_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes_alert,
        "get_settings",
        lambda: SimpleNamespace(alert_webhook_secret="test-secret"),
    )
    with pytest.raises(HTTPException) as missing_secret:
        await routes_alert.receive_alert(_payload(), BackgroundTasks(), None)
    with pytest.raises(HTTPException) as invalid_secret:
        await routes_alert.receive_alert(_payload(), BackgroundTasks(), "Bearer wrong-secret")

    assert missing_secret.value.status_code == 401
    assert invalid_secret.value.status_code == 401


@pytest.mark.asyncio
async def test_alert_webhook_authentication_is_bound_to_http_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes_alert,
        "get_settings",
        lambda: SimpleNamespace(alert_webhook_secret="test-secret"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post("/webhook/alert", json={"status": "resolved"})
        accepted = await client.post(
            "/webhook/alert",
            json={"status": "resolved"},
            headers={"Authorization": "bEaReR test-secret"},
        )

    assert rejected.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json()["firing_count"] == 0


@pytest.mark.asyncio
async def test_alert_group_continues_after_one_investigation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _payload()
    second_alert = payload.alerts[0].model_copy(
        update={"fingerprint": "second-alert", "status": "firing"}
    )
    payload.alerts.append(second_alert)
    processed: list[str] = []

    async def process(alert: routes_alert.Alert) -> None:
        processed.append(alert.fingerprint)
        if alert.fingerprint == "stable-alert-fingerprint":
            raise RuntimeError("synthetic collection failure")

    monkeypatch.setattr(routes_alert, "_process_alert", process)

    await routes_alert._start_investigation(payload)

    assert processed == ["stable-alert-fingerprint", "second-alert"]


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


@pytest.mark.asyncio
async def test_evidence_endpoint_returns_only_requested_bundle_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def get_saved_result(_investigation_id: int) -> dict[str, Any]:
        return {
            "bundle": {
                "logs": [{"log_id": "log-1", "message": "redacted"}],
                "metrics": [{"metric_id": "metric-1", "value": 2}],
                "anomalies": [{"metric_id": "metric-1", "observed_value": 2}],
            }
        }

    monkeypatch.setattr(routes_investigations, "get_investigation_result", get_saved_result)

    result = await routes_investigations.get_investigation_evidence(9, "metric-1")

    assert result["investigation_id"] == 9
    assert [item["kind"] for item in result["evidence"]] == ["metric", "anomaly"]


@pytest.mark.asyncio
async def test_ofrenda_lists_saved_completed_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    class RowsResult:
        def fetchall(self) -> list[Any]:
            return [
                SimpleNamespace(
                    _mapping={
                        "id": 9,
                        "alertname": "HighErrorRate",
                        "service": "gateway",
                        "severity": "critical",
                        "summary": "Error rate was high",
                        "root_cause": None,
                        "alert_time": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:01:00Z",
                        "report_json": json.dumps({"incident_summary": "An error spike"}),
                    }
                )
            ]

    class OfrendaDB:
        async def execute(self, *_args: Any, **_kwargs: Any) -> RowsResult:
            return RowsResult()

    monkeypatch.setattr(routes_investigations, "get_db", lambda: FakeDBContext(OfrendaDB()))

    entries = await routes_investigations.list_ofrenda()

    assert entries[0]["id"] == 9
    assert entries[0]["report"]["incident_summary"] == "An error spike"
