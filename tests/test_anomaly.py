"""Tests for robust metric anomaly detection."""

from datetime import UTC, datetime, timedelta
from math import inf, nan

import pytest

from ghost.ingest.anomaly import detect_anomalies
from ghost.ingest.prom_client import MetricSample


def _series(values: list[object]) -> list[dict[str, object]]:
    start = datetime(2025, 1, 1)
    return [
        {
            "metric_name": "request_count",
            "timestamp": (start + timedelta(minutes=index)).isoformat(),
            "value": value,
            "labels": {"service": "gateway"},
        }
        for index, value in enumerate(values)
    ]


def test_zero_values_are_valid_and_anomaly_metadata_is_preserved() -> None:
    anomalies = detect_anomalies(_series([0, 0, 0, 0, 10]))

    assert len(anomalies) == 1
    assert anomalies[0]["metric_name"] == "request_count"
    assert anomalies[0]["observed_value"] == 10
    assert anomalies[0]["expected_value"] == 0
    assert anomalies[0]["timestamp"] == "2025-01-01T00:04:00"
    assert anomalies[0]["labels"] == {"service": "gateway"}


def test_nan_infinities_and_missing_observations_are_filtered() -> None:
    rows = _series([0, 0, 0, 10, nan, inf, -inf, None, True])

    anomalies = detect_anomalies(rows)

    assert [item["observed_value"] for item in anomalies] == [10]


def test_extreme_outlier_does_not_distort_its_own_baseline() -> None:
    anomalies = detect_anomalies(_series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1_000_000]))

    assert [item["observed_value"] for item in anomalies] == [1_000_000]
    assert anomalies[0]["expected_value"] == 6
    assert anomalies[0]["severity"] == "critical"


def test_constant_series_has_no_anomalies() -> None:
    assert detect_anomalies(_series([5, 5, 5, 5, 5, 5, 5, 5, 5, 5])) == []


def test_insufficient_valid_observations_are_ignored() -> None:
    assert detect_anomalies(_series([0, 10])) == []
    assert detect_anomalies(_series([0, 0, 0, 10]), min_points=5) == []


def test_isolation_forest_can_detect_without_zscore_detection() -> None:
    rows = _series([*range(1, 21), 1000])

    anomalies = detect_anomalies(rows, threshold=1000)

    outlier = next(item for item in anomalies if item["observed_value"] == 1000)
    assert outlier["detection_method"] == "isolation_forest"


def test_multiple_anomalies_are_returned() -> None:
    anomalies = detect_anomalies(_series([0, 0, 0, 0, 20, 30]))

    assert [item["observed_value"] for item in anomalies] == [20, 30]


@pytest.mark.parametrize("threshold", [0, -1, nan, inf, -inf, "3"])
def test_invalid_thresholds_raise_value_error(threshold: object) -> None:
    with pytest.raises(ValueError, match="threshold"):
        detect_anomalies([], threshold=threshold)  # type: ignore[arg-type]


@pytest.mark.parametrize("min_points", [0, -1, 2, 3.0, True])
def test_invalid_minimum_observation_counts_raise_value_error(
    min_points: object,
) -> None:
    with pytest.raises(ValueError, match="min_points"):
        detect_anomalies([], min_points=min_points)  # type: ignore[arg-type]


def test_missing_or_invalid_timestamps_are_skipped() -> None:
    rows = _series([0, 0, 0, 0, 10])
    rows[0].pop("timestamp")
    rows[1]["timestamp"] = "not-a-timestamp"

    anomalies = detect_anomalies(rows)

    assert [item["observed_value"] for item in anomalies] == [10]
    assert anomalies[0]["timestamp"] == "2025-01-01T00:04:00"


@pytest.mark.parametrize(
    ("outlier", "threshold", "expected_severity"),
    [(22, 2.5, "medium"), (32, 3.0, "high"), (45, 3.0, "critical")],
)
def test_severity_uses_robust_deviation_bands(
    outlier: int, threshold: float, expected_severity: str
) -> None:
    anomalies = detect_anomalies(_series([*range(-10, 11), outlier]), threshold=threshold)

    detected = next(item for item in anomalies if item["observed_value"] == outlier)
    assert detected["severity"] == expected_severity


def test_anomaly_dicts_remain_compatible_with_incident_bundle() -> None:
    from ghost.ingest.bundle import IncidentBundle

    anomalies = detect_anomalies(_series([0, 0, 0, 0, 10]))
    bundle = IncidentBundle(anomalies=anomalies)

    assert bundle.model_dump(mode="json")["anomalies"] == anomalies


def test_anomaly_detector_accepts_prometheus_metric_samples() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    samples = [
        MetricSample(
            metric_name="request_count",
            labels={"service": "gateway"},
            timestamp=start + timedelta(minutes=index),
            value=value,
        )
        for index, value in enumerate([0, 0, 0, 0, 10])
    ]

    anomalies = detect_anomalies(samples)

    assert len(anomalies) == 1
    assert anomalies[0]["metric_name"] == "request_count"
    assert anomalies[0]["observed_value"] == 10
    assert anomalies[0]["labels"] == {"service": "gateway"}
