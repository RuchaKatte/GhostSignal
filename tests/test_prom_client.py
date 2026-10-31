"""Focused tests for Prometheus response normalization and validation."""

from __future__ import annotations

from datetime import UTC, datetime
from math import inf, nan
from typing import Any

import httpx
import pytest

from ghost.ingest.prom_client import PrometheusClient


class FakeAsyncClient:
    def __init__(self, response: httpx.Response | Any) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, *, params: dict[str, Any]) -> Any:
        self.calls.append((url, params))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "http://prometheus/api/v1/query"),
    )


@pytest.mark.asyncio
async def test_query_instant_normalizes_samples_and_skips_non_finite_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"__name__": "up", "job": "gateway"}, "value": [1_700_000_000, "1"]},
                    {"metric": {"__name__": "up"}, "value": [1_700_000_001, str(nan)]},
                    {"metric": {"__name__": "up"}, "value": [1_700_000_002, str(inf)]},
                    {"metric": {"__name__": "up"}, "value": ["bad", "1"]},
                    {"metric": {"__name__": "up"}, "value": [1_700_000_004, True]},
                    {"metric": {"__name__": 123}, "value": [1_700_000_005, "2"]},
                    {
                        "metric": {"__name__": "up", "job": ["gateway"]},
                        "value": [1_700_000_006, "2"],
                    },
                    {"metric": [], "value": [1_700_000_003, "2"]},
                ]
            },
        }
    )
    fake = FakeAsyncClient(response)
    monkeypatch.setattr("ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: fake)

    query_time = datetime(2023, 11, 14, 22, 13, 20, 123456, tzinfo=UTC)
    samples = await PrometheusClient("http://prometheus").query_instant("up", query_time)

    assert len(samples) == 1
    assert samples[0].metric_name == "up"
    assert samples[0].labels == {"job": "gateway"}
    assert samples[0].timestamp == datetime.fromtimestamp(1_700_000_000, UTC)
    assert samples[0].value == 1
    assert samples[0].to_dict()["metric_id"].startswith("metric:")
    assert fake.calls[0][1]["time"] == "2023-11-14T22:13:20.123456+00:00"


@pytest.mark.asyncio
async def test_query_range_normalizes_timestamps_and_metric_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response(
        {
            "status": "success",
            "data": {
                "result": [
                    {
                        "metric": {"__name__": "requests_total", "service": "gateway"},
                        "values": [[1_700_000_000, "0"], [1_700_000_005.5, "12.5"], ["bad", "1"]],
                    }
                ]
            },
        }
    )
    fake = FakeAsyncClient(response)
    monkeypatch.setattr("ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: fake)

    samples = await PrometheusClient("http://prometheus").query_range(
        "requests_total",
        datetime.fromtimestamp(1_700_000_000, UTC),
        datetime.fromtimestamp(1_700_000_060, UTC),
        "5s",
    )

    assert [sample.value for sample in samples] == [0, 12.5]
    assert samples[0].timestamp == datetime.fromtimestamp(1_700_000_000, UTC)
    assert samples[0].metric_id == samples[0].to_dict()["metric_id"]
    assert fake.calls[0][1]["step"] == "5s"
    assert fake.calls[0][1]["start"] == "2023-11-14T22:13:20+00:00"
    assert fake.calls[0][1]["end"] == "2023-11-14T22:14:20+00:00"


@pytest.mark.asyncio
async def test_range_query_rejects_invalid_expression_window_and_step() -> None:
    client = PrometheusClient("http://prometheus")
    start = datetime.fromtimestamp(1_700_000_000, UTC)
    end = datetime.fromtimestamp(1_700_000_010, UTC)

    with pytest.raises(ValueError, match="expression"):
        await client.query_range(" ", start, end)
    with pytest.raises(ValueError, match="earlier"):
        await client.query_range("up", end, start)
    with pytest.raises(ValueError, match="step"):
        await client.query_range("up", start, end, "0")
    with pytest.raises(ValueError, match="greater than zero"):
        await client.query_range("up", start, end, "0s")
    with pytest.raises(ValueError, match="step"):
        await client.query_range("up", start, end, [])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="step"):
        await client.query_range("up", start, end, f"{'9' * 400}s")


@pytest.mark.asyncio
async def test_instant_query_rejects_non_finite_query_time() -> None:
    with pytest.raises(ValueError, match="finite"):
        await PrometheusClient("http://prometheus").query_instant("up", nan)


@pytest.mark.asyncio
async def test_http_errors_are_reported_after_bounded_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _response({"status": "success"}, status_code=503)
    fake = FakeAsyncClient(response)
    monkeypatch.setattr("ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: fake)
    monkeypatch.setattr("ghost.ingest.prom_client.asyncio.sleep", _no_sleep)

    with pytest.raises(RuntimeError, match="after retries"):
        await PrometheusClient("http://prometheus").query_instant("up")
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_timeouts_are_retried_and_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(httpx.ReadTimeout("request timed out"))
    monkeypatch.setattr("ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: fake)
    monkeypatch.setattr("ghost.ingest.prom_client.asyncio.sleep", _no_sleep)

    with pytest.raises(RuntimeError, match="after retries"):
        await PrometheusClient("http://prometheus").query_instant("up")

    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_malformed_prometheus_responses_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(_response({"status": "success", "data": {"result": {}}}))
    monkeypatch.setattr("ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: fake)

    with pytest.raises(RuntimeError, match="result must be a list"):
        await PrometheusClient("http://prometheus").query_instant("up")


@pytest.mark.asyncio
async def test_invalid_json_and_non_retriable_http_errors_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = PrometheusClient("http://prometheus")
    invalid_json = FakeAsyncClient(
        httpx.Response(
            200,
            text="not-json",
            request=httpx.Request("GET", "http://prometheus/api/v1/query"),
        )
    )
    monkeypatch.setattr(
        "ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: invalid_json
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await client.query_instant("up")
    assert len(invalid_json.calls) == 1

    bad_request = FakeAsyncClient(_response({"error": "bad query"}, status_code=400))
    monkeypatch.setattr("ghost.ingest.prom_client.httpx.AsyncClient", lambda **_kwargs: bad_request)
    with pytest.raises(httpx.HTTPStatusError):
        await client.query_instant("up")
    assert len(bad_request.calls) == 1


async def _no_sleep(_delay: float) -> None:
    return None
