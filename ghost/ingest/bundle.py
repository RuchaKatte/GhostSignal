"""Incident bundle model for evidence-backed investigations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _parse_window_timestamp(value: str, field_name: str) -> datetime:
    """Parse an ISO-8601 investigation window timestamp."""
    try:
        timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def _validate_unique_evidence_ids(
    logs: list[dict[str, Any]], metrics: list[dict[str, Any]]
) -> None:
    seen: dict[str, str] = {}
    for evidence_type, records, id_fields in (
        ("log", logs, ("log_id",)),
        ("metric", metrics, ("metric_id", "id")),
    ):
        for index, record in enumerate(records):
            record_ids: set[str] = set()
            for id_field in id_fields:
                raw_id = record.get(id_field)
                if raw_id is None:
                    continue
                evidence_id = str(raw_id)
                if not evidence_id or evidence_id in record_ids:
                    continue
                record_ids.add(evidence_id)
                previous = seen.get(evidence_id)
                if previous is not None:
                    raise ValueError(
                        f"Duplicate evidence ID {evidence_id!r} in {previous} and "
                        f"{evidence_type} record {index}"
                    )
                seen[evidence_id] = f"{evidence_type} record {index}"


class IncidentBundle(BaseModel):
    """A structured evidence bundle assembled during an investigation."""

    model_config = ConfigDict(extra="allow")

    incident_id: str = ""
    alert_name: str = ""
    service: str = "unknown"
    window_start: str = ""
    window_end: str = ""
    status: str = "pending"
    alert_details: dict[str, Any] = Field(default_factory=dict)
    logs: list[dict[str, Any]] = Field(default_factory=list)
    log_clusters: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    anomalies: list[dict[str, Any]] = Field(default_factory=list)
    collection_metadata: dict[str, Any] = Field(default_factory=dict)
    data_quality_warnings: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_and_window(self) -> IncidentBundle:
        if self.window_start or self.window_end:
            if not self.window_start or not self.window_end:
                raise ValueError("window_start and window_end must both be provided")
            start = _parse_window_timestamp(self.window_start, "window_start")
            end = _parse_window_timestamp(self.window_end, "window_end")
            if start > end:
                raise ValueError("window_start must not be later than window_end")

        _validate_unique_evidence_ids(self.logs, self.metrics)
        return self

    @property
    def bundle_id(self) -> str:
        model_data = self.model_dump(mode="json")
        model_data.pop("bundle_id", None)
        payload = json.dumps(model_data, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict[str, Any]:
        model = self.model_dump(mode="json")
        model["bundle_id"] = self.bundle_id
        return model

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent)

    def has_log_id(self, log_id: str) -> bool:
        return any(
            str(record_id) == log_id
            for record in self.logs
            if (record_id := record.get("log_id")) is not None
        )

    def has_metric_id(self, metric_id: str) -> bool:
        return any(
            any(
                record_id is not None and str(record_id) == metric_id
                for record_id in (metric.get("metric_id"), metric.get("id"))
            )
            for metric in self.metrics
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> IncidentBundle:
        model_data = dict(payload)
        model_data.pop("bundle_id", None)
        return cls.model_validate(model_data)

    @classmethod
    def from_json(cls, payload: str) -> IncidentBundle:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Incident bundle JSON must contain an object")
        return cls.from_dict(data)


def build_bundle(
    *,
    incident_id: str,
    alert_name: str,
    service: str,
    window_start: str,
    window_end: str,
    logs: list[dict[str, Any]] | None = None,
    log_clusters: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    anomalies: list[dict[str, Any]] | None = None,
    alert_details: dict[str, Any] | None = None,
    collection_metadata: dict[str, Any] | None = None,
    data_quality_warnings: list[str] | None = None,
    **extra: Any,
) -> IncidentBundle:
    """Create a deterministic IncidentBundle from collected evidence."""
    for field_name, value in (
        ("incident_id", incident_id),
        ("alert_name", alert_name),
        ("service", service),
        ("window_start", window_start),
        ("window_end", window_end),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required")

    evidence = extra.pop("evidence", None)
    if evidence is None:
        evidence = {}
    if not isinstance(evidence, dict):
        raise TypeError("evidence must be a dictionary")
    duplicate_evidence_fields = evidence.keys() & extra.keys()
    if duplicate_evidence_fields:
        names = ", ".join(sorted(duplicate_evidence_fields))
        raise ValueError(f"Duplicate evidence fields: {names}")

    return IncidentBundle(
        incident_id=incident_id,
        alert_name=alert_name,
        service=service,
        window_start=window_start,
        window_end=window_end,
        logs=logs if logs is not None else [],
        log_clusters=log_clusters if log_clusters is not None else [],
        metrics=metrics if metrics is not None else [],
        anomalies=anomalies if anomalies is not None else [],
        alert_details=alert_details if alert_details is not None else {},
        collection_metadata=collection_metadata if collection_metadata is not None else {},
        data_quality_warnings=(data_quality_warnings if data_quality_warnings is not None else []),
        evidence={**evidence, **extra},
    )
