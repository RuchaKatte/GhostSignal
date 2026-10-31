"""Read-only metric investigation tools."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from math import isfinite
from typing import Any


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def get_metric_values(metrics: list[dict[str, Any]], metric_name: str) -> list[dict[str, Any]]:
    """Fetch all samples for a metric, including timestamps and values."""
    return deepcopy(
        [
            sample
            for sample in metrics
            if str(sample.get("metric_name") or sample.get("name") or "") == str(metric_name)
        ]
    )


def inspect_metric_trend(metrics: list[dict[str, Any]], metric_name: str) -> list[dict[str, Any]]:
    """Return ordered trend values for a metric."""
    entries = get_metric_values(metrics, metric_name)
    return sorted(
        entries,
        key=lambda item: (
            (timestamp := _parse_timestamp(item.get("timestamp"))) is None,
            timestamp or datetime.max.replace(tzinfo=UTC),
        ),
    )


def compare_metric_values(
    metrics: list[dict[str, Any]], metric_name: str, before: str, after: str
) -> dict[str, Any]:
    """Compare a metric before and after an incident window."""
    before_dt = _parse_timestamp(before)
    after_dt = _parse_timestamp(after)
    if before_dt is None or after_dt is None or before_dt > after_dt:
        raise ValueError("before and after must be valid timestamps in chronological order")

    entries = inspect_metric_trend(metrics, metric_name)
    before_values = [
        entry
        for entry in entries
        if (timestamp := _parse_timestamp(entry.get("timestamp"))) is not None
        and timestamp < before_dt
    ]
    after_values = [
        entry
        for entry in entries
        if (timestamp := _parse_timestamp(entry.get("timestamp"))) is not None
        and timestamp >= after_dt
    ]
    before_sample = before_values[-1] if before_values else None
    after_sample = after_values[0] if after_values else None
    before_value = before_sample.get("value") if before_sample else None
    after_value = after_sample.get("value") if after_sample else None
    delta: float | None = None
    if (
        isinstance(before_value, int | float)
        and not isinstance(before_value, bool)
        and isinstance(after_value, int | float)
        and not isinstance(after_value, bool)
    ):
        try:
            numeric_delta = float(after_value) - float(before_value)
        except OverflowError:
            numeric_delta = float("nan")
        if isfinite(numeric_delta):
            delta = numeric_delta
    return {
        "metric_name": metric_name,
        "before": before_sample,
        "after": after_sample,
        "delta": delta,
        "timestamps": [entry.get("timestamp") for entry in entries],
    }


def identify_metric_anomalies(
    metrics: list[dict[str, Any]], *, metric_name: str | None = None
) -> list[dict[str, Any]]:
    """Return anomaly records from metric series."""
    return deepcopy(
        [
            metric
            for metric in metrics
            if (metric.get("severity") or metric.get("detection_method"))
            and (not metric_name or metric.get("metric_name") == metric_name)
        ]
    )


def get_related_metric_observations(
    metrics: list[dict[str, Any]], *, labels: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Return all metric observations matching label filters."""
    if not labels:
        return deepcopy(metrics)
    return deepcopy(
        [
            sample
            for sample in metrics
            if isinstance(sample.get("labels"), dict)
            and all(
                str(sample["labels"].get(key, "")) == str(value) for key, value in labels.items()
            )
        ]
    )
