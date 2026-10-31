"""Tests for Drain3 log clustering integration."""

import hashlib

import pytest

from ghost.ingest.drain import LogClusterer


def test_fallback_cluster_and_log_ids_are_stable() -> None:
    clusterer = LogClusterer()
    clusterer._miner = None

    clusters = clusterer.cluster(["database unavailable"])
    digest = hashlib.sha256(b"database unavailable").hexdigest()[:16]

    assert clusters[0]["cluster_id"] == f"cluster:{digest}"
    assert clusters[0]["log_ids"] == [f"unknown:{digest}"]


def test_fallback_cluster_normalizes_service_and_nanosecond_timestamps() -> None:
    clusterer = LogClusterer()
    clusterer._miner = None

    clusters = clusterer.cluster(
        [
            {
                "message": "database unavailable",
                "labels": {"service": "orders-api"},
                "timestamp_ns": 1_767_225_600_000_000_000,
            },
            {
                "message": "database unavailable",
                "service": "gateway",
                "timestamp_ns": 1_767_225_601_000_000_000,
            },
        ]
    )

    assert clusters[0]["services"] == ["gateway", "orders-api"]
    assert clusters[0]["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert clusters[0]["last_seen"] == "2026-01-01T00:00:01+00:00"


def test_drain_clusters_similar_messages():
    """Drain should cluster similar log lines under the same template."""
    try:
        from drain3 import TemplateMiner
        from drain3.template_miner_config import TemplateMinerConfig

        config = TemplateMinerConfig()
        config.drain_depth = 4
        config.drain_sim_th = 0.4
        miner = TemplateMiner(config=config)

        lines = [
            "db_pool_timeout pool_max=10 held_connections=9 request_id=abc",
            "db_pool_timeout pool_max=10 held_connections=8 request_id=def",
            "db_pool_timeout pool_max=10 held_connections=7 request_id=ghi",
        ]
        results = [miner.add_log_message(line) for line in lines]

        # All three should land in the same cluster
        cluster_ids = {r["cluster_id"] for r in results}
        assert len(cluster_ids) == 1, f"Expected 1 cluster, got {len(cluster_ids)}"
    except ImportError:
        pytest.skip("drain3 not installed")


def test_drain_separates_different_messages():
    """Drain should create separate clusters for distinctly different messages."""
    try:
        from drain3 import TemplateMiner
        from drain3.template_miner_config import TemplateMinerConfig

        config = TemplateMinerConfig()
        config.drain_depth = 4
        config.drain_sim_th = 0.5
        miner = TemplateMiner(config=config)

        r1 = miner.add_log_message("db_pool_timeout pool_max=10 request_id=abc")
        r2 = miner.add_log_message("payment_failed order_id=xyz error=timeout")

        assert r1["cluster_id"] != r2["cluster_id"]
    except ImportError:
        pytest.skip("drain3 not installed")
