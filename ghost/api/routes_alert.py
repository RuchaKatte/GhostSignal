"""
Alert webhook route - receives Alertmanager webhook payloads.
Triggers incident collection and investigation as a background task.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
import structlog
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ghost.agents.base import AgentResponseError
from ghost.config import get_settings
from ghost.ingest.collector import collect_incident_bundle
from ghost.ingest.redact import redact
from ghost.llm.client import LLMError
from ghost.storage.db import get_db

log = structlog.get_logger(service="ghost.alert")
router = APIRouter(prefix="/webhook", tags=["alerts"])


class AlertAnnotations(BaseModel):
    summary: str = ""
    description: str = ""


class AlertLabels(BaseModel):
    model_config = ConfigDict(extra="allow")

    alertname: str = Field(min_length=1)
    severity: str = ""
    service: str = ""


class Alert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: AlertLabels
    annotations: AlertAnnotations = Field(default_factory=AlertAnnotations)
    startsAt: datetime
    endsAt: datetime | None = None
    generatorURL: str = ""
    fingerprint: str = ""

    @field_validator("startsAt", "endsAt")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Alert timestamps must include a timezone")
        return value


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    groupKey: str = ""
    truncatedAlerts: int = 0
    status: Literal["firing", "resolved"]
    receiver: str = ""
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""
    alerts: list[Alert] = Field(default_factory=list)


def _fingerprint(alert: Alert) -> str:
    if alert.fingerprint:
        return alert.fingerprint
    canonical = json.dumps(
        {
            "labels": alert.labels.model_dump(mode="json"),
            "starts_at": alert.startsAt.astimezone(UTC).isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _create_or_get_investigation(alert: Alert) -> tuple[int, bool]:
    fingerprint = _fingerprint(alert)
    async with get_db() as db:
        existing = await db.execute(
            text("SELECT id, status FROM investigations WHERE fingerprint = :fingerprint"),
            {"fingerprint": fingerprint},
        )
        row = existing.fetchone()
        if row is not None:
            investigation_id = int(row.id)
            if row.status == "failed":
                await db.execute(
                    text(
                        "UPDATE investigations SET status = 'pending', "
                        "updated_at = datetime('now') WHERE id = :id"
                    ),
                    {"id": investigation_id},
                )
                await db.commit()
                return investigation_id, True
            return investigation_id, False

        result = await db.execute(
            text(
                """INSERT OR IGNORE INTO investigations
                   (alertname, severity, service, summary, alert_time, fingerprint, status)
                   VALUES (:alertname, :severity, :service, :summary, :alert_time,
                           :fingerprint, 'pending')"""
            ),
            {
                "alertname": alert.labels.alertname,
                "severity": alert.labels.severity or "warning",
                "service": alert.labels.service or "unknown",
                "summary": redact(alert.annotations.summary),
                "alert_time": alert.startsAt.astimezone(UTC).isoformat(),
                "fingerprint": fingerprint,
            },
        )
        await db.commit()
        existing = await db.execute(
            text("SELECT id FROM investigations WHERE fingerprint = :fingerprint"),
            {"fingerprint": fingerprint},
        )
        row = existing.fetchone()
        if row is None:
            raise RuntimeError("Could not create or retrieve the alert investigation")
        return int(row.id), getattr(result, "rowcount", 0) == 1


async def _mark_investigation_failed(investigation_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            text(
                "UPDATE investigations SET status = 'failed', updated_at = datetime('now') "
                "WHERE id = :id"
            ),
            {"id": investigation_id},
        )
        await db.commit()


async def _process_alert(alert: Alert) -> None:
    investigation_id, should_run = await _create_or_get_investigation(alert)
    if not should_run:
        log.info("investigation_already_exists", investigation_id=investigation_id)
        return

    window_end = datetime.now(UTC)
    window_start = window_end - timedelta(minutes=15)
    log.warning(
        "investigation_triggered",
        investigation_id=investigation_id,
        alertname=alert.labels.alertname,
        severity=alert.labels.severity,
        service=alert.labels.service,
        summary=redact(alert.annotations.summary),
    )

    try:
        bundle = await collect_incident_bundle(
            incident_id=str(investigation_id),
            alert_name=alert.labels.alertname,
            service=alert.labels.service or "unknown",
            window_start=window_start,
            window_end=window_end,
            alert_details=alert.model_dump(mode="json"),
        )
        from ghost.agents.orchestrator import InvestigationOrchestrator

        await InvestigationOrchestrator().investigate(
            bundle,
            investigation_id=investigation_id,
        )
    except (
        AgentResponseError,
        LLMError,
        SQLAlchemyError,
        httpx.HTTPError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        await _mark_investigation_failed(investigation_id)
        log.error(
            "investigation_failed",
            investigation_id=investigation_id,
            error_type=type(exc).__name__,
        )
        raise


async def _start_investigation(payload: AlertmanagerPayload) -> None:
    """Run collection and analysis for all firing alerts in a webhook group."""
    for alert in payload.alerts:
        if alert.status == "firing":
            try:
                await _process_alert(alert)
            except (
                AgentResponseError,
                LLMError,
                SQLAlchemyError,
                httpx.HTTPError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log.error(
                    "alert_investigation_failed",
                    fingerprint=_fingerprint(alert),
                    error_type=type(exc).__name__,
                )


@router.post("/alert", status_code=202)
async def receive_alert(
    payload: AlertmanagerPayload,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict[str, Any]:
    """Validate an Alertmanager webhook and queue any firing alerts."""
    expected_secret = get_settings().alert_webhook_secret
    scheme, separator, credentials = (authorization or "").partition(" ")
    bearer_secret = credentials.strip() if separator and scheme.casefold() == "bearer" else None
    if (
        not expected_secret
        or bearer_secret is None
        or not hmac.compare_digest(bearer_secret.encode("utf-8"), expected_secret.encode("utf-8"))
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    firing_count = sum(alert.status == "firing" for alert in payload.alerts)
    log.info(
        "alert_received",
        status=payload.status,
        n_alerts=len(payload.alerts),
        firing_count=firing_count,
        group_key=payload.groupKey,
    )

    if firing_count:
        background_tasks.add_task(_start_investigation, payload)
        return {
            "accepted": True,
            "firing_count": firing_count,
            "message": "Investigation queued",
        }

    return {"accepted": True, "firing_count": 0, "message": "No firing alerts"}
