"""Tests for end-to-end Phase 2 evidence collection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ghost.ingest.collector import collect_incident_bundle
from ghost.ingest.prom_client import MetricSample
from ghost.ledger.models import ClaimStatus
from ghost.ledger.verifier import verify_evidence_claim
from ghost.tools.registry import register_bundle_tools


class StubLoki:
    def __init__(self, logs: list[dict[str, Any]] | Exception) -> None:
        self.logs = logs
        self.calls: list[dict[str, Any]] = []

    async def query_logs(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        if isinstance(self.logs, Exception):
            raise self.logs
        return self.logs


class StubPrometheus:
    def __init__(self, samples: list[MetricSample] | Exception) -> None:
        self.samples = samples
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def query_range(self, *args: Any, **kwargs: Any) -> list[MetricSample]:
        self.calls.append((args, kwargs))
        if isinstance(self.samples, Exception):
            raise self.samples
        return self.samples


def _time(offset_seconds: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=offset_seconds)


@pytest.mark.asyncio
async def test_collection_redacts_logs_and_assigns_stable_evidence_ids() -> None:
    raw_message = "request failed for john.doe@example.com from 10.1.2.3"
    logs = [
        {
            "service": "gateway",
            "timestamp_ns": int(_time(i).timestamp() * 1_000_000_000),
            "message": raw_message,
        }
        for i in range(1)
    ]
    samples = [
        MetricSample(
            metric_name="gateway_errors_total",
            labels={"service": "gateway"},
            timestamp=_time(index),
            value=value,
        )
        for index, value in enumerate([10, 10, 10, 10, 100])
    ]
    kwargs = {
        "incident_id": "inc-1",
        "alert_name": "HighErrorRate",
        "service": "gateway",
        "window_start": _time(0),
        "window_end": _time(10),
        "alert_details": {"summary": "Email john.doe@example.com"},
        "loki_client": StubLoki(logs),
        "prometheus_client": StubPrometheus(samples),
        "metric_queries": ("gateway_errors_total",),
    }

    first = await collect_incident_bundle(**kwargs)
    second = await collect_incident_bundle(**kwargs)

    assert first.logs[0]["log_id"] == second.logs[0]["log_id"]
    assert first.metrics[0]["metric_id"] == second.metrics[0]["metric_id"]
    assert first.metrics[0]["metric_id"] == samples[0].metric_id
    assert first.logs[0]["message"] != raw_message
    assert "[EMAIL]" in first.logs[0]["message"]
    assert "[IP]" in first.logs[0]["message"]
    assert raw_message not in first.to_json()
    assert "john.doe@example.com" not in first.to_json()
    assert first.anomalies
    assert first.anomalies[0]["metric_id"] in first.evidence["metric_ids"]


@pytest.mark.asyncio
async def test_missing_sources_and_invalid_observations_keep_bundle_available() -> None:
    logs = [
        {"service": "gateway", "timestamp_ns": "not-a-time", "message": "invalid"},
        {"service": "gateway", "timestamp_ns": int(_time(1).timestamp() * 1e9), "message": "ok"},
    ]
    bundle = await collect_incident_bundle(
        incident_id="inc-2",
        alert_name="NoData",
        service="gateway",
        window_start=_time(0),
        window_end=_time(5),
        loki_client=StubLoki(logs),
        prometheus_client=StubPrometheus(RuntimeError("Prometheus unavailable")),
        metric_queries=("gateway_errors_total",),
    )

    assert len(bundle.logs) == 1
    assert bundle.metrics == []
    assert bundle.anomalies == []
    assert any("invalid log" in warning for warning in bundle.data_quality_warnings)
    assert any(
        "Prometheus collection failed" in warning for warning in bundle.data_quality_warnings
    )


@pytest.mark.asyncio
async def test_loki_failure_does_not_discard_metric_evidence() -> None:
    sample = MetricSample("up", {"job": "gateway"}, _time(1), 1)
    bundle = await collect_incident_bundle(
        incident_id="inc-3",
        alert_name="TargetDown",
        service="gateway",
        window_start=_time(0),
        window_end=_time(5),
        loki_client=StubLoki(RuntimeError("Loki unavailable")),
        prometheus_client=StubPrometheus([sample]),
        metric_queries=("up",),
    )

    assert bundle.logs == []
    assert len(bundle.metrics) == 1
    assert any("Loki log collection failed" in warning for warning in bundle.data_quality_warnings)


@pytest.mark.asyncio
async def test_collection_limits_evidence_to_the_requested_window_and_links_anomalies() -> None:
    start = _time(0)
    end = _time(5)
    logs = [
        {
            "service": "gateway",
            "timestamp_ns": int(_time(offset).timestamp() * 1_000_000_000),
            "message": "api_key=supersecretvalue",
            "metadata": {"contact": "person@example.com"},
        }
        for offset in (1, 10)
    ]
    samples = [
        MetricSample("gateway_errors_total", {"service": "gateway"}, _time(index), value)
        for index, value in enumerate([1, 1, 1, 1, 20])
    ]
    samples.append(MetricSample("gateway_errors_total", {"service": "gateway"}, _time(10), 100))
    loki = StubLoki(logs)
    prometheus = StubPrometheus(samples)

    bundle = await collect_incident_bundle(
        incident_id="inc-window",
        alert_name="HighErrorRate",
        service="gateway",
        window_start=start,
        window_end=end,
        loki_client=loki,
        prometheus_client=prometheus,
        metric_queries=("gateway_errors_total",),
    )

    assert loki.calls == [{"service": "gateway", "start": start, "end": end}]
    assert prometheus.calls[0][0][1:3] == (start, end)
    assert len(bundle.logs) == 1
    assert len(bundle.metrics) == 5
    assert len(bundle.anomalies) == 1
    assert bundle.anomalies[0]["metric_id"] == bundle.metrics[-1]["metric_id"]
    assert bundle.anomalies[0]["evidence_ids"] == [bundle.metrics[-1]["metric_id"]]
    assert "supersecretvalue" not in bundle.to_json()
    assert "person@example.com" not in bundle.to_json()

    normalized_log_id = bundle.logs[0]["log_id"]
    assert normalized_log_id != logs[0].get("log_id")
    claim = verify_evidence_claim(
        {
            "description": "The gateway error count spiked.",
            "supporting_evidence_ids": [
                normalized_log_id,
                bundle.anomalies[0]["metric_id"],
            ],
        },
        bundle,
    )
    assert claim.status == ClaimStatus.ESTABLISHED.value
    registry = register_bundle_tools(bundle)
    assert registry.call("logs.get", log_id=normalized_log_id)["message"] == ("api_key=[REDACTED]")
    assert registry.call("metrics.anomalies", metric_name="gateway_errors_total")[0][
        "evidence_ids"
    ] == [bundle.metrics[-1]["metric_id"]]


@pytest.mark.asyncio
async def test_empty_metric_query_list_disables_metric_collection() -> None:
    start = _time(0)
    end = _time(5)
    prometheus = StubPrometheus([])

    bundle = await collect_incident_bundle(
        incident_id="inc-no-metrics",
        alert_name="NoMetrics",
        service="unknown",
        window_start=start,
        window_end=end,
        loki_client=StubLoki([]),
        prometheus_client=prometheus,
        metric_queries=(),
    )

    assert prometheus.calls == []
    assert bundle.metrics == []
