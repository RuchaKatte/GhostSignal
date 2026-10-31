"""Tests proving log line IDs are stable (same input always yields same ID)."""

import hashlib
from datetime import UTC, datetime, timedelta

from ghost.ingest.ids import (
    make_log_id as make_log_id_from_values,
)
from ghost.ingest.ids import (
    make_log_id_from_record,
    make_metric_id,
)


def make_log_id(service: str, timestamp_ns: int, message: str) -> str:
    """Replicate the ID generation logic from bundle.py."""
    hash_input = f"{service}:{timestamp_ns}:{message}"
    short = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    return f"{service}:{timestamp_ns}:{short}"


def test_id_is_stable():
    """Same inputs always produce the same ID."""
    id1 = make_log_id("orders-api", 1730000000000000000, "db_pool_exhausted")
    id2 = make_log_id("orders-api", 1730000000000000000, "db_pool_exhausted")
    assert id1 == id2


def test_id_differs_by_service():
    id1 = make_log_id("gateway", 1730000000000000000, "error")
    id2 = make_log_id("orders-api", 1730000000000000000, "error")
    assert id1 != id2


def test_id_differs_by_timestamp():
    id1 = make_log_id("gateway", 1730000000000000000, "error")
    id2 = make_log_id("gateway", 1730000000000000001, "error")
    assert id1 != id2


def test_id_format():
    log_id = make_log_id("payments-worker", 1730000000123456789, "payment_failed")
    parts = log_id.split(":")
    assert parts[0] == "payments-worker"
    assert parts[1] == "1730000000123456789"
    assert len(parts[2]) == 8


def test_metric_ids_preserve_datetime_microsecond_precision() -> None:
    timestamp = datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    next_microsecond = timestamp + timedelta(microseconds=1)

    first = make_metric_id("requests_total", {"service": "gateway"}, timestamp, 1)
    second = make_metric_id("requests_total", {"service": "gateway"}, next_microsecond, 1)
    same_time_iso = make_metric_id(
        "requests_total",
        {"service": "gateway"},
        "2026-01-01T00:00:00.123456Z",
        1,
    )

    assert first != second
    assert first == same_time_iso
    expected_timestamp_ns = 1_767_225_600_123_456_000
    payload = "\n".join(("requests_total", str(expected_timestamp_ns), "1", "service=gateway"))
    expected = hashlib.sha256(payload.encode()).hexdigest()[:16]
    assert first == f"metric:{expected}"


def test_log_id_from_record_handles_missing_labels_and_zero_timestamp() -> None:
    record = {"service": "gateway", "timestamp_ns": 0, "message": "started"}

    assert make_log_id_from_record(record) == make_log_id_from_values("gateway", 0, "started")
    assert make_log_id_from_record({**record, "labels": None}) == make_log_id_from_values(
        "gateway", 0, "started"
    )
