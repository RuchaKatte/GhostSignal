"""Log template clustering with Drain3 support and a deterministic fallback."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol

try:  # pragma: no cover - optional dependency
    from drain3 import TemplateMiner
    from drain3.template_miner_config import TemplateMinerConfig
except ImportError:  # pragma: no cover - fallback for tests without Drain3
    TemplateMiner = None  # type: ignore[assignment]
    TemplateMinerConfig = None  # type: ignore[assignment]


class _DrainMiner(Protocol):
    def add_log_message(self, message: str) -> dict[str, Any]: ...


@dataclass
class ClusterSummary:
    """A single cluster of related log messages."""

    cluster_id: str
    template: str
    count: int = 0
    log_ids: list[str] = field(default_factory=list)
    services: set[str] = field(default_factory=set)
    first_seen: str | None = None
    last_seen: str | None = None
    sample_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "template": self.template,
            "count": self.count,
            "log_ids": self.log_ids,
            "services": sorted(self.services),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sample_message": self.sample_message,
        }


def _fallback_template(message: str) -> str:
    """Create a rough template from a raw message."""
    cleaned = message.strip()
    if not cleaned:
        return "<empty>"
    template = re.sub(r"\b\d+(?:\.\d+)?\b", "<*>", cleaned)
    template = re.sub(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        "<uuid>",
        template,
    )
    template = re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "<ip>", template)
    return template


def _stable_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _parse_record_timestamp(item: dict[str, Any]) -> datetime | None:
    timestamp = item.get("timestamp")
    if isinstance(timestamp, datetime):
        parsed = timestamp
    elif isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
    elif isinstance(timestamp, int | float) and not isinstance(timestamp, bool):
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            parsed = None
    else:
        parsed = None

    if parsed is not None:
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    timestamp_ns = item.get("timestamp_ns")
    if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int | str):
        return None
    try:
        nanoseconds = int(timestamp_ns)
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=nanoseconds // 1_000)
    except (OverflowError, ValueError):
        return None


def _record_fields(item: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(item, str):
        message = item
        service = "unknown"
        log_id = f"unknown:{_stable_digest(message)}"
        timestamp = None
    else:
        message = str(item.get("message") or item.get("log") or "")
        labels = item.get("labels")
        label_service = labels.get("service") if isinstance(labels, dict) else None
        service = str(item.get("service") or item.get("service_name") or label_service or "unknown")
        log_id = str(item.get("log_id") or item.get("id") or f"{service}:{_stable_digest(message)}")
        timestamp = _parse_record_timestamp(item)
    return {
        "log_id": log_id,
        "service": service,
        "message": message,
        "timestamp": timestamp,
    }


class LogClusterer:
    """Cluster log lines into recurring templates. Use Drain3 when available."""

    def __init__(self, depth: int = 4, similarity_threshold: float = 0.4) -> None:
        self.depth = depth
        self.similarity_threshold = similarity_threshold
        self._miner: _DrainMiner | None = None
        if TemplateMiner is not None and TemplateMinerConfig is not None:
            config = TemplateMinerConfig()
            config.drain_depth = depth
            config.drain_sim_th = similarity_threshold
            self._miner = TemplateMiner(config=config)  # type: ignore[operator]

    def cluster(self, records: list[dict[str, Any]] | list[str]) -> list[dict[str, Any]]:
        """Cluster log records, returning structured cluster data."""
        if not records:
            return []
        if self._miner is not None:
            return self._cluster_with_drain(records)
        return self._cluster_fallback(records)

    def _cluster_with_drain(
        self, records: list[dict[str, Any]] | list[str]
    ) -> list[dict[str, Any]]:
        miner = self._miner
        if miner is None:
            return self._cluster_fallback(records)

        group_by_cluster: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        templates: dict[str, str] = {}
        for item in records:
            entry = _record_fields(item)
            template_result = miner.add_log_message(entry["message"])
            cluster_id = str(
                template_result.get("cluster_id", f"cluster:{_stable_digest(entry['message'])}")
            )
            mined_template = template_result.get("template_mined")
            if isinstance(mined_template, str):
                templates[cluster_id] = mined_template
            group_by_cluster[cluster_id].append(entry)

        clusters: list[dict[str, Any]] = []
        for cluster_id, entries in group_by_cluster.items():
            sample = entries[0]
            representative = templates.get(cluster_id)
            services = {entry["service"] for entry in entries}
            template = str(representative or _fallback_template(sample["message"]))
            clusters.append(
                {
                    "cluster_id": cluster_id,
                    "template": template,
                    "count": len(entries),
                    "log_ids": [entry["log_id"] for entry in entries],
                    "services": sorted(services),
                    "first_seen": _format_timestamp(entries, min),
                    "last_seen": _format_timestamp(entries, max),
                    "sample_message": sample["message"],
                }
            )
        return sorted(clusters, key=lambda c: (-c["count"], c["cluster_id"]))

    def _cluster_fallback(self, records: list[dict[str, Any]] | list[str]) -> list[dict[str, Any]]:
        groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in records:
            entry = _record_fields(item)
            template = _fallback_template(entry["message"])
            groups[template].append(entry)

        result: list[dict[str, Any]] = []
        for template, entries in groups.items():
            services = {entry["service"] for entry in entries}
            result.append(
                {
                    "cluster_id": f"cluster:{_stable_digest(template)}",
                    "template": template,
                    "count": len(entries),
                    "log_ids": [entry["log_id"] for entry in entries],
                    "services": sorted(services),
                    "first_seen": _format_timestamp(entries, min),
                    "last_seen": _format_timestamp(entries, max),
                    "sample_message": entries[0]["message"],
                }
            )
        return sorted(result, key=lambda c: (-c["count"], c["cluster_id"]))


def _format_timestamp(
    entries: list[dict[str, Any]], selector: Callable[[list[datetime]], datetime]
) -> str:
    timestamps = [entry["timestamp"] for entry in entries if entry["timestamp"] is not None]
    return selector(timestamps).isoformat() if timestamps else ""


def cluster_logs(records: list[dict[str, Any]] | list[str], **kwargs: Any) -> list[dict[str, Any]]:
    """Convenience wrapper around the default clusterer."""
    return LogClusterer(**kwargs).cluster(records)
