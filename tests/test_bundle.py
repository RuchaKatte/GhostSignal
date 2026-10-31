"""Tests for incident bundle validation and serialization."""

import json
from typing import Any

import pytest

from ghost.ingest.bundle import IncidentBundle, build_bundle


def _build_bundle(**overrides: Any) -> IncidentBundle:
    values: dict[str, Any] = {
        "incident_id": "incident-1",
        "alert_name": "HighErrorRate",
        "service": "orders-api",
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-01-01T01:00:00Z",
    }
    values.update(overrides)
    return build_bundle(**values)


def test_bundle_id_is_deterministic_for_identical_contents() -> None:
    first = _build_bundle(
        logs=[{"log_id": "log-1", "message": "failed"}],
        collection_metadata={"source": "loki", "count": 1},
    )
    second = _build_bundle(
        collection_metadata={"count": 1, "source": "loki"},
        logs=[{"message": "failed", "log_id": "log-1"}],
    )

    assert first.bundle_id == second.bundle_id


def test_bundle_id_excludes_serialized_id_during_round_trip() -> None:
    bundle = _build_bundle(logs=[{"log_id": "log-1"}])
    serialized = bundle.to_dict()

    restored = IncidentBundle.from_dict(serialized)

    assert restored.bundle_id == bundle.bundle_id
    assert restored.to_dict()["bundle_id"] == bundle.bundle_id
    assert "bundle_id" not in restored.model_dump(mode="json")


def test_json_serialization_round_trip_preserves_bundle_fields() -> None:
    bundle = _build_bundle(
        logs=[{"log_id": "log-1", "message": "failed"}],
        log_clusters=[{"cluster_id": "cluster-1"}],
        metrics=[{"metric_id": "metric-1", "value": 2}],
        anomalies=[{"metric_name": "error_rate"}],
        alert_details={"severity": "critical"},
        collection_metadata={"source": "loki"},
        data_quality_warnings=["Some logs were truncated"],
        evidence={"trace_id": "trace-1"},
        custom_field={"retained": True},
    )

    restored = IncidentBundle.from_json(bundle.to_json(indent=2))

    assert restored.to_dict() == bundle.to_dict()
    assert json.loads(bundle.to_json()) == bundle.to_dict()


def test_empty_evidence_collections_are_preserved() -> None:
    bundle = _build_bundle(
        logs=[],
        log_clusters=[],
        metrics=[],
        anomalies=[],
        alert_details={},
        collection_metadata={},
        data_quality_warnings=[],
    )

    assert bundle.logs == []
    assert bundle.log_clusters == []
    assert bundle.metrics == []
    assert bundle.anomalies == []
    assert bundle.alert_details == {}
    assert bundle.collection_metadata == {}
    assert bundle.data_quality_warnings == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("incident_id", ""),
        ("alert_name", "  "),
        ("service", ""),
        ("window_start", ""),
        ("window_end", ""),
    ],
)
def test_missing_required_identifiers_and_windows_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        _build_bundle(**{field: value})


@pytest.mark.parametrize(
    ("window_start", "window_end"),
    [
        ("not-a-timestamp", "2026-01-01T01:00:00Z"),
        ("2026-01-01T02:00:00Z", "2026-01-01T01:00:00Z"),
    ],
)
def test_invalid_investigation_windows_are_rejected(window_start: str, window_end: str) -> None:
    with pytest.raises(ValueError):
        _build_bundle(window_start=window_start, window_end=window_end)


def test_log_id_lookup() -> None:
    bundle = _build_bundle(logs=[{"log_id": "log-1"}])

    assert bundle.has_log_id("log-1")
    assert not bundle.has_log_id("log-2")


@pytest.mark.parametrize("id_field", ["metric_id", "id"])
def test_metric_id_lookup(id_field: str) -> None:
    bundle = _build_bundle(metrics=[{id_field: "metric-1"}])

    assert bundle.has_metric_id("metric-1")
    assert not bundle.has_metric_id("metric-2")


def test_id_lookup_normalizes_non_string_record_ids() -> None:
    bundle = _build_bundle(logs=[{"log_id": 123}], metrics=[{"metric_id": 456}])

    assert bundle.has_log_id("123")
    assert bundle.has_metric_id("456")


def test_duplicate_evidence_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate evidence ID"):
        _build_bundle(
            logs=[{"log_id": "duplicate"}, {"log_id": "duplicate"}],
        )

    with pytest.raises(ValueError, match="Duplicate evidence ID"):
        _build_bundle(
            logs=[{"log_id": "duplicate"}],
            metrics=[{"metric_id": "duplicate"}],
        )


def test_collection_metadata_warnings_and_evidence_are_preserved() -> None:
    metadata = {"source": "prometheus", "attempts": 2}
    warnings = ["One query timed out"]
    evidence = {"trace_id": "trace-1"}
    bundle = _build_bundle(
        collection_metadata=metadata,
        data_quality_warnings=warnings,
        evidence=evidence,
    )

    restored = IncidentBundle.from_dict(bundle.to_dict())

    assert restored.collection_metadata == metadata
    assert restored.data_quality_warnings == warnings
    assert restored.evidence == evidence
