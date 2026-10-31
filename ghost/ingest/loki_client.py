"""Loki client for incident log retrieval."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ghost.config import get_settings
from ghost.ingest.ids import make_log_id
from ghost.ingest.redact import redact

_LABEL_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_NANOSECONDS_PER_SECOND = 1_000_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class LokiClient:
    """Async Loki client wrapping the query_range and query APIs."""

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.loki_url).rstrip("/")

    @staticmethod
    def _validate_datetime(value: datetime, name: str) -> None:
        """Ensure datetime values are timezone-aware."""
        if not isinstance(value, datetime):
            raise ValueError(f"{name} must be a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")

    @staticmethod
    def _datetime_to_ns(value: datetime) -> int:
        """Convert an aware datetime to nanoseconds without float precision loss."""
        delta = value.astimezone(UTC) - _EPOCH
        return (
            (delta.days * 86_400 + delta.seconds) * _NANOSECONDS_PER_SECOND
            + delta.microseconds * 1_000
        )

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be positive")

    @staticmethod
    def _validate_direction(direction: str) -> None:
        if not isinstance(direction, str) or direction not in {"forward", "backward"}:
            raise ValueError("direction must be 'forward' or 'backward'")

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 5000,
        direction: str = "forward",
    ) -> list[dict[str, Any]]:
        """Execute a LogQL range query and return normalized log entries."""

        self._validate_limit(limit)
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query cannot be empty")
        self._validate_direction(direction)
        self._validate_datetime(start, "start")
        self._validate_datetime(end, "end")

        if start > end:
            raise ValueError("start must be less than or equal to end")
        # Normalize timestamps to UTC
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        params = {
            "query": query,
            "start": str(self._datetime_to_ns(start)),
            "end": str(self._datetime_to_ns(end)),
            "limit": str(limit),
            "direction": direction,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params=params,
            )

        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"Loki request failed ({response.status_code}): {response.text[:200]}"
            )

        try:
            data = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Loki returned invalid JSON") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Unexpected Loki response format")

        if data.get("status") != "success":
            raise RuntimeError(f"Loki query was unsuccessful: {data.get('error', 'Unknown error')}")

        result_data = data.get("data")

        if not isinstance(result_data, dict):
            raise RuntimeError("Invalid Loki response: missing data object")

        streams = result_data.get("result")
        if not isinstance(streams, list):
            raise RuntimeError("Invalid Loki response: result must be a list")

        entries: list[dict[str, Any]] = []

        for stream in streams:
            if not isinstance(stream, dict):
                continue

            labels = stream.get("stream", {})

            if not isinstance(labels, dict):
                labels = {}

            values = stream.get("values", [])

            if not isinstance(values, list):
                continue

            for item in values:
                if not isinstance(item, list | tuple) or len(item) != 2:
                    continue

                ts_ns_str, message = item

                if isinstance(ts_ns_str, bool) or not isinstance(ts_ns_str, int | str):
                    continue
                if not isinstance(message, str):
                    continue

                try:
                    timestamp_ns = int(ts_ns_str)
                except (TypeError, ValueError):
                    continue

                service = str(labels.get("service") or labels.get("container") or "unknown")
                normalized_message = message
                redacted_message = redact(normalized_message)
                try:
                    timestamp = (
                        _EPOCH + timedelta(microseconds=timestamp_ns // 1_000)
                    ).isoformat()
                except (OverflowError, OSError, ValueError):
                    continue

                entry = {
                    "timestamp_ns": timestamp_ns,
                    "timestamp": timestamp,
                    "labels": labels,
                    "message": normalized_message,
                    "redacted_message": redacted_message,
                    "service": service,
                    "log_id": make_log_id(
                        service,
                        timestamp_ns,
                        normalized_message,
                    ),
                }

                entries.append(entry)

        # Sort all streams chronologically
        entries.sort(key=lambda entry: entry["timestamp_ns"])

        return entries[:limit]

    async def query_logs(
        self,
        *,
        service: str | None = None,
        labels: dict[str, str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
        direction: str = "forward",
    ) -> list[dict[str, Any]]:
        """Query Loki for logs in a service/time window."""

        self._validate_limit(limit)
        self._validate_direction(direction)
        if service is not None and (
            not isinstance(service, str) or not service.strip()
        ):
            raise ValueError("service must be a non-empty string")
        if labels is not None and not isinstance(labels, dict):
            raise ValueError("labels must be a dictionary")

        end_dt = end if end is not None else datetime.now(UTC)
        start_dt = start if start is not None else end_dt - timedelta(hours=1)

        self._validate_datetime(start_dt, "start")
        self._validate_datetime(end_dt, "end")

        if end_dt < start_dt:
            raise ValueError("end must be greater than or equal to start")

        selectors: list[str] = []

        if service is not None:
            selectors.append(f"service={json.dumps(service)}")

        if labels is not None:
            for key, value in labels.items():
                if not isinstance(key, str) or not _LABEL_NAME_RE.fullmatch(key):
                    raise ValueError(f"Invalid Loki label name: {key!r}")
                if not isinstance(value, str):
                    raise ValueError(f"Loki label value for {key!r} must be a string")
                selectors.append(f"{key}={json.dumps(value)}")

        query = "{" + ",".join(selectors) + "}" if selectors else "{}"

        return await self.query_range(
            query=query,
            start=start_dt,
            end=end_dt,
            limit=min(limit, 5000),
            direction=direction,
        )

    async def labels(self) -> list[str]:
        """Return all label names Loki knows about."""

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{self.base_url}/loki/api/v1/labels")

        response.raise_for_status()

        try:
            data = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Loki returned invalid JSON for labels") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Unexpected Loki labels response format")

        if data.get("status") != "success":
            raise RuntimeError(
                f"Loki labels request was unsuccessful: {data.get('error', 'Unknown error')}"
            )

        labels = data.get("data")

        if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
            raise RuntimeError("Invalid Loki labels response")

        return labels
