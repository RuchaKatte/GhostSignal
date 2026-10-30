"""
Alert webhook route - receives Alertmanager webhook payloads.
Triggers the incident investigation pipeline asynchronously.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field

log = structlog.get_logger(service="ghost.alert")
router = APIRouter(prefix="/webhook", tags=["alerts"])


class AlertAnnotations(BaseModel):
    summary: str = ""
    description: str = ""


class AlertLabels(BaseModel):
    alertname: str = ""
    severity: str = ""
    service: str = ""

    class Config:
        extra = "allow"


class Alert(BaseModel):
    status: str  # "firing" | "resolved"
    labels: AlertLabels
    annotations: AlertAnnotations
    startsAt: str
    endsAt: str
    generatorURL: str = ""
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    groupKey: str = ""
    truncatedAlerts: int = 0
    status: str
    receiver: str = ""
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str = ""
    alerts: list[Alert] = Field(default_factory=list)


async def _start_investigation(payload: AlertmanagerPayload) -> None:
    """Background task: start an investigation for the incoming alert."""
    from sqlalchemy import text

    from ghost.storage.db import get_db

    firing = [a for a in payload.alerts if a.status == "firing"]
    if not firing:
        log.info("no_firing_alerts", status=payload.status)
        return

    alert = firing[0]
    alert_time = datetime.now(UTC)

    log.warning(
        "investigation_triggered",
        alertname=alert.labels.alertname,
        severity=alert.labels.severity,
        service=alert.labels.service,
        summary=alert.annotations.summary,
    )

    # Record the investigation in SQLite
    async with get_db() as db:
        result = await db.execute(
            text(
                """INSERT INTO investigations
                   (alertname, severity, service, summary, alert_time, status)
                   VALUES (:alertname, :severity, :service, :summary, :alert_time, :status)"""
            ),
            {
                "alertname": alert.labels.alertname,
                "severity": alert.labels.severity,
                "service": alert.labels.service,
                "summary": alert.annotations.summary,
                "alert_time": alert_time.isoformat(),
                "status": "pending",
            },
        )
        inv_id = result.lastrowid
        await db.commit()

    log.info("investigation_created", inv_id=inv_id)

    # Phase 1: just record it. Phases 2+ will kick off the full pipeline.
    # The orchestrator will be wired in Phase 4.


@router.post("/alert", status_code=202)
async def receive_alert(
    payload: AlertmanagerPayload,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Receive Alertmanager webhook and queue an investigation."""
    log.info(
        "alert_received",
        status=payload.status,
        n_alerts=len(payload.alerts),
        group_key=payload.groupKey,
    )

    firing = [a for a in payload.alerts if a.status == "firing"]
    if firing:
        background_tasks.add_task(_start_investigation, payload)
        return {
            "accepted": True,
            "firing_count": len(firing),
            "message": "Investigation queued",
        }

    return {"accepted": True, "firing_count": 0, "message": "No firing alerts"}
