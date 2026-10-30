"""
Loki client - queries LogQL against Grafana Loki.
Used by the bundle builder to fetch logs for an incident window.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from ghost.config import get_settings


class LokiClient:
    """Async Loki client wrapping the query_range and query APIs."""

    def __init__(self, base_url: str | None = None) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.loki_url

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int = 5000,
        direction: str = "forward",
    ) -> list[dict[str, Any]]:
        """
        Execute a LogQL range query and return flat list of log entries.
        Each entry: {timestamp_ns, labels, message, raw}.
        """
        params = {
            "query": query,
            "start": str(int(start.timestamp() * 1e9)),
            "end": str(int(end.timestamp() * 1e9)),
            "limit": str(limit),
            "direction": direction,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self.base_url}/loki/api/v1/query_range",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        entries: list[dict[str, Any]] = []
        for stream in data.get("data", {}).get("result", []):
            labels = stream.get("stream", {})
            for ts_ns_str, message in stream.get("values", []):
                entries.append({
                    "timestamp_ns": int(ts_ns_str),
                    "labels": labels,
                    "message": message,
                    "service": labels.get("service", labels.get("container", "unknown")),
                })
        return entries

    async def labels(self) -> list[str]:
        """Return all label names Loki knows about."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/loki/api/v1/labels")
            resp.raise_for_status()
            return resp.json().get("data", [])
