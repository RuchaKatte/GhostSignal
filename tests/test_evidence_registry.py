"""Tests for registered read-only evidence tools and claim verification."""

from ghost.ingest.bundle import IncidentBundle
from ghost.ledger.models import ClaimStatus, InvestigationClaim
from ghost.ledger.verifier import verify_claim, verify_evidence_claim
from ghost.tools.logs_tools import search_logs, search_logs_by_time
from ghost.tools.metrics_tools import compare_metric_values, inspect_metric_trend
from ghost.tools.registry import register_bundle_tools


def _bundle() -> IncidentBundle:
    return IncidentBundle(
        incident_id="inc-1",
        alert_name="HighErrorRate",
        service="gateway",
        logs=[
            {
                "log_id": "log-real",
                "service": "gateway",
                "message": "failed for person@example.com",
            }
        ],
        metrics=[
            {
                "metric_id": "metric-real",
                "metric_name": "gateway_errors_total",
                "labels": {"service": "gateway"},
                "timestamp": "2026-01-01T00:00:00+00:00",
                "value": 2,
            }
        ],
        anomalies=[{"metric_name": "gateway_errors_total", "severity": "high"}],
    )


def test_bundle_registry_exposes_only_fixed_read_only_observation_tools() -> None:
    bundle = _bundle()
    registry = register_bundle_tools(bundle)

    assert {
        "logs.search",
        "logs.get",
        "logs.error_patterns",
        "metrics.list",
        "metrics.trend",
        "metrics.anomalies",
        "deployments.history",
        "memory.search",
    } <= set(registry.tools)
    assert registry.call("logs.get", log_id="not-real") is None
    result = registry.call("logs.get", log_id="log-real")
    assert result is not None
    assert "person@example.com" not in result["message"]

    metric = registry.call("metrics.list", metric_name="gateway_errors_total")[0]
    metric["labels"]["service"] = "mutated"
    assert bundle.metrics[0]["labels"]["service"] == "gateway"


def test_log_search_combines_filters_and_searches_stack_content() -> None:
    logs = [
        {"log_id": "gateway-match", "service": "gateway", "message": "failed", "stack": "pool"},
        {"log_id": "gateway-no-match", "service": "gateway", "message": "failed"},
        {"log_id": "orders-match", "service": "orders-api", "message": "failed", "stack": "pool"},
    ]

    matches = search_logs(logs, service="gateway", keyword="pool")

    assert [item["log_id"] for item in matches] == ["gateway-match"]


def test_log_time_filter_interprets_nanoseconds_as_utc() -> None:
    timestamp_ns = 1_767_225_600_000_000_000
    result = search_logs_by_time(
        [{"log_id": "log-1", "timestamp_ns": timestamp_ns}],
        "2026-01-01T00:00:00Z",
        "2026-01-01T00:00:01Z",
    )

    assert [item["log_id"] for item in result] == ["log-1"]
    assert [item["log_id"] for item in result] == ["log-1"]


def test_metric_trends_compare_timestamps_across_timezone_offsets() -> None:
    metrics = [
        {"metric_name": "requests", "timestamp": "2026-01-01T01:00:00+01:00", "value": 2},
        {"metric_name": "requests", "timestamp": "2026-01-01T00:01:00Z", "value": 5},
    ]

    trend = inspect_metric_trend(metrics, "requests")
    comparison = compare_metric_values(
        metrics,
        "requests",
        before="2026-01-01T00:00:30Z",
        after="2026-01-01T00:00:30Z",
    )

    assert [sample["value"] for sample in trend] == [2, 5]
    assert comparison["before"]["value"] == 2
    assert comparison["after"]["value"] == 5
    assert comparison["delta"] == 3


def test_registry_rejects_unknown_tools_and_unvalidated_arguments() -> None:
    registry = register_bundle_tools(_bundle())

    try:
        registry.call("logs.get", log_id="log-real", shell="whoami")
    except ValueError as exc:
        assert "does not accept" in str(exc)
    else:
        raise AssertionError("Unexpected tool argument was accepted")

    try:
        registry.call("not-registered")
    except KeyError:
        pass
    else:
        raise AssertionError("Unregistered tool was callable")


def test_claim_verifier_rejects_fabricated_ids_and_derives_status() -> None:
    bundle = _bundle()

    status, valid = verify_claim(
        "A cause",
        ["log-real", "metric-real", "invented-id"],
        bundle,
    )
    assert status == ClaimStatus.ESTABLISHED.value
    assert valid == ["log-real", "metric-real"]

    result = verify_evidence_claim(
        InvestigationClaim(
            claim_id="claim-1",
            description="The database pool is exhausted",
            status=ClaimStatus.ESTABLISHED,
            supporting_evidence_ids=["invented-id"],
        ),
        bundle,
    )
    assert result.status == ClaimStatus.UNSUPPORTED.value
    assert result.invalid_evidence_ids == ["invented-id"]
    assert result.warnings


def test_one_evidence_source_is_plausible_and_contradiction_blocks_established() -> None:
    bundle = _bundle()
    plausible = verify_evidence_claim(
        {
            "description": "There was an error",
            "status": "Established",
            "supporting_evidence_ids": ["log-real"],
        },
        bundle,
    )
    assert plausible.status == ClaimStatus.PLAUSIBLE.value

    contradicted = verify_evidence_claim(
        {
            "description": "There was an error",
            "status": "Established",
            "supporting_evidence_ids": ["log-real", "metric-real"],
            "contradicting_evidence_ids": ["metric-real"],
        },
        bundle,
    )
    assert contradicted.status == ClaimStatus.PLAUSIBLE.value
