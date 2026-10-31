"""Focused tests for Loki query validation and log normalization."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from ghost.ingest.ids import make_log_id
from ghost.ingest.loki_client import LokiClient


class FakeAsyncClient:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.params: dict[str, Any] | None = None

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.params = params or {}
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(result: list[dict[str, Any]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"status": "success", "data": {"result": result}},
        request=httpx.Request("GET", "http://loki/loki/api/v1/query_range"),
    )


@pytest.mark.asyncio
async def test_loki_normalizes_entries_and_skips_unrepresentable_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = 1_767_225_600_000_000_000
    response = _response(
        [
            {
                "stream": {"service": "gateway"},
                "values": [
                    [str(timestamp), "failed for person@example.com"],
                    ["9999999999999999999999999999999999999999999", "invalid time"],
                ],
            }
        ]
    )
    fake = FakeAsyncClient(response)
    monkeypatch.setattr("ghost.ingest.loki_client.httpx.AsyncClient", lambda **_kwargs: fake)

    logs = await LokiClient("http://loki").query_range(
        '{service="gateway"}',
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert len(logs) == 1
    assert logs[0]["log_id"]
    assert "person@example.com" in logs[0]["message"]
    assert "[EMAIL]" in logs[0]["redacted_message"]


@pytest.mark.asyncio
async def test_loki_query_builder_escapes_values_and_rejects_bad_label_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(_response([]))
    monkeypatch.setattr("ghost.ingest.loki_client.httpx.AsyncClient", lambda **_kwargs: fake)
    client = LokiClient("http://loki")
    now = datetime.now(UTC)
    start = now - timedelta(minutes=1)
    value = 'gateway"} |= "unexpected" {service="all"}'

    await client.query_logs(service=value, start=start, end=now)

    assert fake.params is not None
    assert json.dumps(value) in fake.params["query"]
    with pytest.raises(ValueError, match="label name"):
        await client.query_logs(
            labels={"bad label": "value"},
            start=start,
            end=now,
        )


@pytest.mark.asyncio
async def test_loki_rejects_invalid_query_and_naive_time() -> None:
    client = LokiClient("http://loki")
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="query"):
        await client.query_range("", now, now)
    with pytest.raises(ValueError, match="timezone-aware"):
        await client.query_range("{}", datetime(2026, 1, 1), now)


@pytest.mark.asyncio
async def test_loki_query_range_encodes_datetime_bounds_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeAsyncClient(_response([]))
    monkeypatch.setattr("ghost.ingest.loki_client.httpx.AsyncClient", lambda **_kwargs: fake)
    start = datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    end = datetime(2026, 1, 1, 1, 0, 0, 654321, tzinfo=UTC)

    await LokiClient("http://loki").query_range("{}", start, end)

    assert fake.params is not None
    assert fake.params["start"] == str(int(start.timestamp()) * 1_000_000_000 + 123_456_000)
    assert fake.params["end"] == str(int(end.timestamp()) * 1_000_000_000 + 654_321_000)


@pytest.mark.asyncio
async def test_loki_skips_malformed_values_and_uses_container_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = 1_767_225_600_000_000_000
    fake = FakeAsyncClient(
        _response(
            [
                {
                    "stream": {"container": "payments-worker"},
                    "values": [
                        [str(timestamp), "api_key=supersecretvalue"],
                        [True, "invalid timestamp"],
                        [str(timestamp), {"not": "a log line"}],
                    ],
                }
            ]
        )
    )
    monkeypatch.setattr("ghost.ingest.loki_client.httpx.AsyncClient", lambda **_kwargs: fake)

    logs = await LokiClient("http://loki").query_range(
        "{}",
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 2, tzinfo=UTC),
    )

    assert len(logs) == 1
    assert logs[0]["service"] == "payments-worker"
    assert logs[0]["message"] == "api_key=supersecretvalue"
    assert logs[0]["redacted_message"] == "api_key=[REDACTED]"
    assert logs[0]["log_id"] == make_log_id(
        "payments-worker",
        timestamp,
        "api_key=supersecretvalue",
    )


@pytest.mark.asyncio
async def test_loki_surfaces_http_invalid_json_and_timeout_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LokiClient("http://loki")
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "ghost.ingest.loki_client.httpx.AsyncClient",
        lambda **_kwargs: FakeAsyncClient(
            httpx.Response(
                502,
                text="upstream unavailable",
                request=httpx.Request("GET", "http://loki/loki/api/v1/query_range"),
            )
        ),
    )
    with pytest.raises(RuntimeError, match=r"Loki request failed \(502\)"):
        await client.query_range("{}", now, now)

    monkeypatch.setattr(
        "ghost.ingest.loki_client.httpx.AsyncClient",
        lambda **_kwargs: FakeAsyncClient(
            httpx.Response(
                200,
                text="not-json",
                request=httpx.Request("GET", "http://loki/loki/api/v1/query_range"),
            )
        ),
    )
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await client.query_range("{}", now, now)

    monkeypatch.setattr(
        "ghost.ingest.loki_client.httpx.AsyncClient",
        lambda **_kwargs: FakeAsyncClient(
            httpx.Response(
                200,
                json={"status": "success", "data": {}},
                request=httpx.Request("GET", "http://loki/loki/api/v1/query_range"),
            )
        ),
    )
    with pytest.raises(RuntimeError, match="result must be a list"):
        await client.query_range("{}", now, now)

    monkeypatch.setattr(
        "ghost.ingest.loki_client.httpx.AsyncClient",
        lambda **_kwargs: FakeAsyncClient(httpx.ReadTimeout("request timed out")),
    )
    with pytest.raises(httpx.ReadTimeout, match="timed out"):
        await client.query_range("{}", now, now)


@pytest.mark.asyncio
async def test_loki_query_logs_validates_service_direction_and_label_values() -> None:
    client = LokiClient("http://loki")
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="service"):
        await client.query_logs(service="", start=now, end=now)
    with pytest.raises(ValueError, match="direction"):
        await client.query_logs(direction=[], start=now, end=now)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="label name"):
        await client.query_logs(labels={1: "value"}, start=now, end=now)  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="label value"):
        await client.query_logs(labels={"service": 1}, start=now, end=now)  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="greater than or equal"):
        await client.query_logs(start=now, end=now - timedelta(seconds=1))


@pytest.mark.asyncio
async def test_loki_labels_validates_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    response = httpx.Response(
        200,
        json={"status": "success", "data": ["service", "container"]},
        request=httpx.Request("GET", "http://loki/loki/api/v1/labels"),
    )
    monkeypatch.setattr(
        "ghost.ingest.loki_client.httpx.AsyncClient",
        lambda **_kwargs: FakeAsyncClient(response),
    )

    assert await LokiClient("http://loki").labels() == ["service", "container"]

    malformed = httpx.Response(
        200,
        json={"status": "success", "data": ["service", 2]},
        request=httpx.Request("GET", "http://loki/loki/api/v1/labels"),
    )
    monkeypatch.setattr(
        "ghost.ingest.loki_client.httpx.AsyncClient",
        lambda **_kwargs: FakeAsyncClient(malformed),
    )
    with pytest.raises(RuntimeError, match="Invalid Loki labels"):
        await LokiClient("http://loki").labels()
