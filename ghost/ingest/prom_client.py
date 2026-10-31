"""Prometheus HTTP client for incident evidence retrieval."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ghost.config import get_settings
from ghost.ingest.ids import make_metric_id

_STEP_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:ms|s|m|h|d|w|y))+")
_STEP_PART_RE = re.compile(r"(\d+(?:\.\d+)?)(?:ms|s|m|h|d|w|y)")


@dataclass(frozen=True)
class MetricSample:
    """A single metric sample returned by Prometheus."""

    metric_name: str
    labels: dict[str, str]
    timestamp: datetime
    value: float

    @property
    def metric_id(self) -> str:
        return make_metric_id(self.metric_name, self.labels, self.timestamp, self.value)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["metric_id"] = self.metric_id
        return result


class PrometheusClient:
    """Async client for Prometheus instant and range queries."""

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.prometheus_url).rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("Prometheus base URL must use HTTP or HTTPS")

    @staticmethod
    def _validate_expression(expr: str) -> None:
        if not isinstance(expr, str) or not expr.strip():
            raise ValueError("Prometheus query expression cannot be empty")

    @staticmethod
    def _validate_step(step: str) -> None:
        if not isinstance(step, str):
            raise ValueError("Prometheus query step must be a positive duration such as '15s'")
        normalized_step = step.strip()
        if not _STEP_RE.fullmatch(normalized_step):
            raise ValueError("Prometheus query step must be a positive duration such as '15s'")
        parts = [float(value) for value in _STEP_PART_RE.findall(normalized_step)]
        if not all(math.isfinite(value) for value in parts) or not any(
            value > 0 for value in parts
        ):
            raise ValueError("Prometheus query step must be greater than zero")

    async def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(
                        f"{self.base_url}{path}",
                        params=params,
                    )

                response.raise_for_status()
                try:
                    payload = response.json()
                except (ValueError, TypeError) as exc:
                    raise RuntimeError("Prometheus returned invalid JSON") from exc

                if not isinstance(payload, dict):
                    raise RuntimeError("Invalid Prometheus response: expected an object")

                if payload.get("status") == "error":
                    raise RuntimeError(payload.get("error", "Prometheus query failed"))

                if payload.get("status") != "success":
                    raise RuntimeError("Invalid Prometheus response")

                return payload

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {429, 500, 502, 503, 504}:
                    raise
                last_error = exc

            if attempt < 2:
                await asyncio.sleep(0.5 * (2**attempt))

        raise RuntimeError(f"Prometheus request failed after retries: {last_error}") from last_error

    @staticmethod
    def _to_datetime(
        value: datetime | int | float | str | None,
    ) -> datetime:
        if value is None:
            return datetime.now(UTC)

        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)

        if isinstance(value, bool):
            raise TypeError("Boolean values are not valid timestamps")

        if isinstance(value, int | float):
            try:
                numeric_value = float(value)
            except OverflowError as exc:
                raise ValueError("Timestamp must be finite") from exc
            if not math.isfinite(numeric_value):
                raise ValueError("Timestamp must be finite")
            try:
                return datetime.fromtimestamp(numeric_value, tz=UTC)
            except (OverflowError, OSError, ValueError) as exc:
                raise ValueError("Timestamp is outside the supported range") from exc

        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                raise ValueError("Timestamp cannot be empty")
            if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized):
                return PrometheusClient._to_datetime(float(normalized))
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            return PrometheusClient._to_datetime(parsed)

        raise TypeError(f"Unsupported timestamp: {value!r}")

    @staticmethod
    def _parse_value(raw_value: str) -> float | None:
        if isinstance(raw_value, bool):
            return None
        try:
            value = float(raw_value)
        except (OverflowError, TypeError, ValueError):
            return None

        return value if math.isfinite(value) else None

    @staticmethod
    def _parse_labels(labels: dict[str, Any]) -> dict[str, str] | None:
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
        ):
            return None
        return {key: value for key, value in labels.items() if key != "__name__"}

    @staticmethod
    def _result_series(payload: dict[str, Any]) -> list[Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("Invalid Prometheus response: missing data object")
        result = data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Invalid Prometheus response: result must be a list")
        return result

    @staticmethod
    def _sample_timestamp(value: Any) -> datetime | None:
        try:
            return PrometheusClient._to_datetime(value)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    async def query_instant(
        self,
        expr: str,
        at: datetime | int | float | str | None = None,
    ) -> list[MetricSample]:
        self._validate_expression(expr)

        params = {"query": expr}

        if at is not None:
            params["time"] = self._to_datetime(at).isoformat()

        payload = await self._request_json("/api/v1/query", params)

        result = self._result_series(payload)
        samples: list[MetricSample] = []

        for item in result:
            if not isinstance(item, dict):
                continue
            labels = item.get("metric", {})
            values = item.get("value", [])
            if not isinstance(labels, dict) or not isinstance(values, list | tuple):
                continue
            if len(values) != 2:
                continue

            timestamp = self._sample_timestamp(values[0])
            value = self._parse_value(values[1])
            if timestamp is None or value is None:
                continue

            metric_name = labels.get("__name__", expr)
            if not isinstance(metric_name, str) or not metric_name.strip():
                continue
            clean_labels = self._parse_labels(labels)
            if clean_labels is None:
                continue
            samples.append(
                MetricSample(
                    metric_name=metric_name,
                    labels=clean_labels,
                    timestamp=timestamp,
                    value=value,
                )
            )

        return samples

    async def query_range(
        self,
        expr: str,
        start: datetime | int | float | str,
        end: datetime | int | float | str,
        step: str = "15s",
    ) -> list[MetricSample]:
        self._validate_expression(expr)
        self._validate_step(step)
        step = step.strip()

        start_dt = self._to_datetime(start)
        end_dt = self._to_datetime(end)

        if start_dt >= end_dt:
            raise ValueError("Start time must be earlier than end time")

        payload = await self._request_json(
            "/api/v1/query_range",
            {
                "query": expr,
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "step": step,
            },
        )

        result = self._result_series(payload)
        samples: list[MetricSample] = []

        for item in result:
            if not isinstance(item, dict):
                continue
            labels = item.get("metric", {})
            if not isinstance(labels, dict):
                continue
            metric_name = labels.get("__name__", expr)
            values = item.get("values", [])
            if not isinstance(values, list):
                continue

            for observation in values:
                if not isinstance(observation, list | tuple) or len(observation) != 2:
                    continue
                timestamp_value, raw_value = observation
                timestamp = self._sample_timestamp(timestamp_value)
                value = self._parse_value(raw_value)

                if timestamp is None or value is None:
                    continue

                metric_name = labels.get("__name__", expr)
                if not isinstance(metric_name, str) or not metric_name.strip():
                    continue
                clean_labels = self._parse_labels(labels)
                if clean_labels is None:
                    continue
                samples.append(
                    MetricSample(
                        metric_name=metric_name,
                        labels=clean_labels,
                        timestamp=timestamp,
                        value=value,
                    )
                )

        return samples

    async def get_metric(
        self,
        metric_name: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> list[MetricSample]:
        return await self.query_range(
            metric_name,
            start,
            end,
            step=step,
        )
