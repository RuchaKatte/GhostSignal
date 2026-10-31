"""Stable ID helpers for incident log ingestion."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from math import isfinite
from typing import Any


def _datetime_to_nanoseconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _normalize_timestamp(timestamp_ns: int | str | datetime | None) -> int:
    """Return a stable nanosecond timestamp value for a log record."""
    if timestamp_ns is None:
        return 0
    if isinstance(timestamp_ns, datetime):
        return _datetime_to_nanoseconds(timestamp_ns)
    if isinstance(timestamp_ns, str):
        value = timestamp_ns.strip()
        if value.lstrip("+-").isdigit():
            return int(value)
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return _datetime_to_nanoseconds(dt)
        except ValueError:
            # Fall back to a stable hash of the raw string to preserve determinism.
            return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)
    return int(timestamp_ns)


def make_log_id(
    service: str | None,
    timestamp_ns: int | str | datetime | None,
    message: str | None,
    *extras: Any,
) -> str:
    """Create a deterministic, collision-resistant ID for an ingested log line."""
    service_name = (service or "unknown").strip() or "unknown"
    normalized_message = message or ""
    timestamp_value = _normalize_timestamp(timestamp_ns)
    payload = f"{service_name}:{timestamp_value}:{normalized_message}"
    if extras:
        payload += ":" + "|".join(str(part) for part in extras)
    short_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    return f"{service_name}:{timestamp_value}:{short_hash}"


def make_log_id_from_record(record: Mapping[str, Any]) -> str:
    """Build a log ID from a dictionary-like log record."""
    raw_labels = record.get("labels")
    labels = raw_labels if isinstance(raw_labels, Mapping) else {}
    service = str(
        record.get("service") or record.get("service_name") or labels.get("service") or "unknown"
    )
    timestamp_ns = record.get("timestamp_ns")
    if timestamp_ns is None:
        timestamp_ns = record.get("timestamp")
    if timestamp_ns is None:
        timestamp_ns = record.get("ts")
    message = record.get("message") or record.get("log") or ""
    return make_log_id(service, timestamp_ns, str(message))


def make_metric_id(
    metric_name: str,
    labels: Mapping[str, Any],
    timestamp: datetime | int | float | str,
    value: int | float,
) -> str:
    """Create a stable ID for one normalized Prometheus observation."""
    if isinstance(timestamp, datetime | str):
        timestamp_value = _normalize_timestamp(timestamp)
    else:
        if isinstance(timestamp, bool):
            raise ValueError("metric timestamp must be finite")
        try:
            numeric_timestamp = float(timestamp)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("metric timestamp must be finite") from exc
        if not isfinite(numeric_timestamp):
            raise ValueError("metric timestamp must be finite")
        timestamp_value = _normalize_timestamp(int(numeric_timestamp * 1_000_000_000))

    if isinstance(value, bool):
        raise ValueError("metric value must be finite")
    try:
        numeric_value = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("metric value must be finite") from exc
    if not isfinite(numeric_value):
        raise ValueError("metric value must be finite")

    canonical_labels = sorted((str(key), str(item)) for key, item in labels.items())
    payload = "\n".join(
        (
            str(metric_name),
            str(timestamp_value),
            format(numeric_value, ".17g"),
            *(f"{key}={item}" for key, item in canonical_labels),
        )
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"metric:{digest}"
