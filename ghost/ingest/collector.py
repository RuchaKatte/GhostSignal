"""Collect and normalize observability evidence into an Incident Bundle."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from ghost.ingest.anomaly import detect_anomalies
from ghost.ingest.bundle import IncidentBundle, build_bundle
from ghost.ingest.drain import cluster_logs
from ghost.ingest.ids import make_log_id, make_metric_id
from ghost.ingest.loki_client import LokiClient
from ghost.ingest.prom_client import MetricSample, PrometheusClient
from ghost.ingest.redact import redact, redact_dict

_METRICS_BY_SERVICE: dict[str, tuple[str, ...]] = {
    "gateway": (
        "gateway_requests_total",
        "gateway_errors_total",
        "gateway_request_duration_seconds_bucket",
    ),
    "orders-api": (
        "orders_db_pool_in_use",
        "orders_db_pool_size",
        "orders_db_errors_total",
    ),
    "payments-worker": (
        "payments_processed_total",
        "payments_memory_bytes",
    ),
}


def _datetime_to_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _utc_datetime(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_log(record: dict[str, Any]) -> dict[str, Any] | None:
    message = record.get("message") or record.get("log") or ""
    if not isinstance(message, str):
        return None

    raw_timestamp = record.get("timestamp_ns")
    if raw_timestamp is None:
        raw_timestamp = record.get("timestamp")
    if isinstance(raw_timestamp, bool) or raw_timestamp is None:
        return None
    try:
        if isinstance(raw_timestamp, datetime):
            timestamp_dt = _utc_datetime(raw_timestamp, "log timestamp")
            timestamp_ns = _datetime_to_ns(timestamp_dt)
        elif isinstance(raw_timestamp, str):
            normalized_timestamp = raw_timestamp.strip()
            try:
                timestamp_ns = int(normalized_timestamp)
            except ValueError:
                timestamp_dt = datetime.fromisoformat(normalized_timestamp.replace("Z", "+00:00"))
                if timestamp_dt.tzinfo is None or timestamp_dt.utcoffset() is None:
                    timestamp_dt = timestamp_dt.replace(tzinfo=UTC)
                timestamp_dt = timestamp_dt.astimezone(UTC)
                timestamp_ns = _datetime_to_ns(timestamp_dt)
        elif isinstance(raw_timestamp, int):
            timestamp_ns = raw_timestamp
        else:
            return None
        timestamp_dt = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            microseconds=timestamp_ns // 1_000
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return None

    raw_labels = record.get("labels")
    labels = raw_labels if isinstance(raw_labels, dict) else {}
    service = redact(
        str(
            record.get("service")
            or record.get("service_name")
            or labels.get("service")
            or "unknown"
        )
    )
    labels = redact_dict(labels)
    log_id = make_log_id(service, timestamp_ns, message)

    normalized_message = redact(message)
    normalized = redact_dict(
        {
            key: value
            for key, value in record.items()
            if key not in {"message", "log", "raw_message", "redacted_message"}
        }
    )
    normalized.update(
        {
            "log_id": log_id,
            "timestamp_ns": timestamp_ns,
            "timestamp": timestamp_dt.isoformat(),
            "service": service,
            "labels": labels,
            "message": normalized_message,
            "redacted_message": normalized_message,
        }
    )
    return normalized


def _normalize_metric(sample: MetricSample | dict[str, Any]) -> dict[str, Any] | None:
    raw = sample.to_dict() if isinstance(sample, MetricSample) else sample
    if not isinstance(raw, dict):
        return None
    name = raw.get("metric_name") or raw.get("name")
    labels = raw.get("labels", {})
    timestamp = raw.get("timestamp")
    value = raw.get("value")
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(labels, dict)
        or value is None
    ):
        return None
    try:
        if isinstance(timestamp, datetime):
            timestamp_dt = timestamp
        elif isinstance(timestamp, str):
            timestamp_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        elif isinstance(timestamp, int | float) and not isinstance(timestamp, bool):
            timestamp_float = float(timestamp)
            if not isfinite(timestamp_float):
                return None
            timestamp_dt = datetime.fromtimestamp(timestamp_float, tz=UTC)
        else:
            return None
        if timestamp_dt.tzinfo is None or timestamp_dt.utcoffset() is None:
            timestamp_dt = timestamp_dt.replace(tzinfo=UTC)
        timestamp_dt = timestamp_dt.astimezone(UTC)
        if isinstance(value, bool):
            return None
        numeric_value = float(value)
        if not isfinite(numeric_value):
            return None
    except (TypeError, ValueError, OverflowError, OSError):
        return None

    clean_labels = {str(key): str(item) for key, item in labels.items()}
    return {
        "metric_id": make_metric_id(name, clean_labels, timestamp_dt, numeric_value),
        "metric_name": name,
        "labels": redact_dict(clean_labels),
        "timestamp": timestamp_dt.isoformat(),
        "value": numeric_value,
    }


async def collect_incident_bundle(
    *,
    incident_id: str,
    alert_name: str,
    service: str,
    window_start: datetime,
    window_end: datetime,
    alert_details: dict[str, Any] | None = None,
    loki_client: LokiClient | None = None,
    prometheus_client: PrometheusClient | None = None,
    metric_queries: tuple[str, ...] | None = None,
) -> IncidentBundle:
    """Fetch logs and metrics, preserving partial evidence when a source is unavailable."""
    start = _utc_datetime(window_start, "window_start")
    end = _utc_datetime(window_end, "window_end")
    if start > end:
        raise ValueError("window_start must not be later than window_end")

    loki = loki_client or LokiClient()
    prometheus = prometheus_client or PrometheusClient()
    warnings: list[str] = []
    sources: dict[str, Any] = {}

    logs_task = loki.query_logs(service=service, start=start, end=end)
    queries = metric_queries if metric_queries is not None else _METRICS_BY_SERVICE.get(service, ())
    metric_tasks = [prometheus.query_range(query, start, end, step="15s") for query in queries]
    results = await asyncio.gather(logs_task, *metric_tasks, return_exceptions=True)

    raw_logs_result = results[0]
    if isinstance(raw_logs_result, BaseException):
        if not isinstance(raw_logs_result, Exception):
            raise raw_logs_result
        warnings.append(f"Loki log collection failed: {type(raw_logs_result).__name__}")
        raw_logs: list[Any] = []
        sources["loki"] = {"status": "unavailable", "count": 0}
    else:
        raw_logs = raw_logs_result
        sources["loki"] = {"status": "available", "count": len(raw_logs)}

    logs: list[dict[str, Any]] = []
    invalid_log_count = 0
    out_of_window_log_count = 0
    start_ns = _datetime_to_ns(start)
    end_ns = _datetime_to_ns(end)
    for raw_log in raw_logs:
        if not isinstance(raw_log, dict):
            invalid_log_count += 1
            continue
        normalized_log = _normalize_log(raw_log)
        if normalized_log is None:
            invalid_log_count += 1
            continue
        if not start_ns <= normalized_log["timestamp_ns"] <= end_ns:
            out_of_window_log_count += 1
            continue
        logs.append(normalized_log)
    if invalid_log_count:
        warnings.append(f"Skipped {invalid_log_count} invalid log observation(s).")
    if out_of_window_log_count:
        warnings.append(
            f"Skipped {out_of_window_log_count} log observation(s) outside the requested window."
        )
    unique_logs: dict[str, dict[str, Any]] = {}
    for log in logs:
        unique_logs.setdefault(log["log_id"], log)
    if len(unique_logs) != len(logs):
        warnings.append(f"Removed {len(logs) - len(unique_logs)} duplicate log observation(s).")
    logs = list(unique_logs.values())
    logs.sort(key=lambda item: (item["timestamp_ns"], item["log_id"]))
    sources["loki"]["count"] = len(logs)

    metrics: list[dict[str, Any]] = []
    failed_queries: list[str] = []
    invalid_metric_count = 0
    out_of_window_metric_count = 0
    for query, result in zip(queries, results[1:], strict=True):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            failed_queries.append(query)
            continue
        for sample in result:
            normalized_metric = _normalize_metric(sample)
            if normalized_metric is None:
                invalid_metric_count += 1
                continue
            metric_timestamp = datetime.fromisoformat(
                normalized_metric["timestamp"].replace("Z", "+00:00")
            )
            if not start <= metric_timestamp <= end:
                out_of_window_metric_count += 1
                continue
            metrics.append(normalized_metric)
    if failed_queries:
        warnings.append(f"Prometheus collection failed for: {', '.join(failed_queries)}.")
    if invalid_metric_count:
        warnings.append(f"Skipped {invalid_metric_count} invalid metric observation(s).")
    if out_of_window_metric_count:
        warnings.append(
            "Skipped "
            f"{out_of_window_metric_count} metric observation(s) outside the requested window."
        )
    unique_metrics: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        unique_metrics.setdefault(metric["metric_id"], metric)
    if len(unique_metrics) != len(metrics):
        warnings.append(
            f"Removed {len(metrics) - len(unique_metrics)} duplicate metric observation(s)."
        )
    metrics = list(unique_metrics.values())
    metrics.sort(key=lambda item: (item["metric_name"], item["timestamp"], item["metric_id"]))
    if not queries:
        warnings.append(f"No default metric queries are configured for service {service!r}.")
    elif not metrics and not failed_queries:
        warnings.append("Prometheus returned no metric observations for the requested window.")
    if not logs:
        warnings.append("Loki returned no log observations for the requested window.")
    sources["prometheus"] = {
        "status": "partial" if failed_queries else "available",
        "queries": list(queries),
        "failed_queries": failed_queries,
        "count": len(metrics),
    }

    grouped_metrics: defaultdict[tuple[str, tuple[tuple[str, str], ...]], list[dict[str, Any]]]
    grouped_metrics = defaultdict(list)
    for metric in metrics:
        label_key = tuple(sorted(metric["labels"].items()))
        grouped_metrics[(metric["metric_name"], label_key)].append(metric)

    anomalies: list[dict[str, Any]] = []
    for (metric_name, _), series in grouped_metrics.items():
        detected = detect_anomalies(series, metric_name=metric_name)
        metric_by_observation = {
            (metric["timestamp"], metric["value"]): metric for metric in series
        }
        for anomaly in detected:
            source_metric = metric_by_observation.get(
                (anomaly["timestamp"], anomaly["observed_value"])
            )
            if source_metric is not None:
                anomaly["metric_id"] = source_metric["metric_id"]
                anomaly["evidence_ids"] = [source_metric["metric_id"]]
            anomalies.append(anomaly)

    clusters = cluster_logs(logs)
    collection_metadata = {
        "collected_at": datetime.now(UTC).isoformat(),
        "sources": sources,
        "log_count": len(logs),
        "metric_count": len(metrics),
        "anomaly_count": len(anomalies),
    }
    return build_bundle(
        incident_id=incident_id,
        alert_name=alert_name,
        service=service or "unknown",
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        alert_details=redact_dict(alert_details or {}),
        logs=logs,
        log_clusters=clusters,
        metrics=metrics,
        anomalies=anomalies,
        collection_metadata=collection_metadata,
        data_quality_warnings=warnings,
        evidence={
            "log_ids": [record["log_id"] for record in logs],
            "metric_ids": [record["metric_id"] for record in metrics],
        },
    )
