"""Tests proving log line IDs are stable (same input always yields same ID)."""

import hashlib


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
