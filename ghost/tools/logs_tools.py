"""Read-only log investigation tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ghost.ingest.redact import redact, redact_dict


def _limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    return limit


def _timestamp(value: Any, *, nanoseconds: bool = False) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, int | float) and not isinstance(value, bool):
        try:
            seconds = value / 1_000_000_000 if nanoseconds else value
            parsed = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        normalized = str(value).strip()
        if nanoseconds:
            try:
                seconds = int(normalized) / 1_000_000_000
            except ValueError:
                pass
            else:
                try:
                    return datetime.fromtimestamp(seconds, tz=UTC)
                except (OverflowError, OSError, ValueError):
                    return None
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _matches_keyword(log: dict[str, Any], keyword: str) -> bool:
    needle = keyword.lower()
    return any(
        needle in str(log.get(field) or "").lower()
        for field in ("message", "log", "stack", "stack_trace", "exception")
    )


def _safe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [redact_dict(record) for record in records]


def search_logs(
    logs: list[dict[str, Any]],
    *,
    service: str | None = None,
    keyword: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Search logs with optional service/keyword filters and a validated limit."""
    limit = _limit(limit)
    matches = logs
    if service:
        matches = [
            log for log in matches if str(log.get("service") or "").lower() == service.lower()
        ]
    if keyword:
        matches = [log for log in matches if _matches_keyword(log, keyword)]
    return _safe_records(matches[:limit])


def search_logs_by_service(
    logs: list[dict[str, Any]], service: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Return logs from a service, maintaining stable log IDs."""
    limit = _limit(limit)
    if not logs:
        return []
    matches = [log for log in logs if str(log.get("service") or "").lower() == str(service).lower()]
    return _safe_records(matches[:limit])


def search_logs_by_time(
    logs: list[dict[str, Any]], start: str | datetime, end: str | datetime, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Filter logs by a time window."""
    limit = _limit(limit)
    start_dt = _timestamp(start)
    end_dt = _timestamp(end)
    if start_dt is None or end_dt is None or start_dt > end_dt:
        raise ValueError("start and end must be valid timestamps in chronological order")
    matches = []
    for log in logs:
        if log.get("timestamp") is not None:
            ts_dt = _timestamp(log["timestamp"])
        else:
            ts_dt = _timestamp(log.get("timestamp_ns"), nanoseconds=True)
        if ts_dt is None:
            continue
        if start_dt <= ts_dt <= end_dt:
            matches.append(log)
    return _safe_records(matches[:limit])


def search_logs_by_severity(
    logs: list[dict[str, Any]], severity: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Filter logs by severity label or textual level."""
    limit = _limit(limit)
    if not isinstance(severity, str) or not severity:
        raise ValueError("severity must be a non-empty string")
    matches = []
    for log in logs:
        level = str(log.get("level") or log.get("severity") or "").lower()
        if level and severity.lower() in level:
            matches.append(log)
    return _safe_records(matches[:limit])


def search_logs_by_keyword(
    logs: list[dict[str, Any]], keyword: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Search log messages or stacks for a keyword."""
    limit = _limit(limit)
    if not isinstance(keyword, str):
        raise ValueError("keyword must be a string")
    matches = [log for log in logs if _matches_keyword(log, keyword)]
    return _safe_records(matches[:limit])


def get_logs_for_cluster(
    logs: list[dict[str, Any]], cluster_id: str, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Return logs that belong to a cluster."""
    limit = _limit(limit)
    return _safe_records(
        [log for log in logs if str(log.get("cluster_id") or "") == str(cluster_id)][:limit]
    )


def get_log_by_id(logs: list[dict[str, Any]], log_id: str) -> dict[str, Any] | None:
    """Return a single log by its stable ID."""
    for log in logs:
        if str(log.get("log_id") or "") == str(log_id):
            return redact_dict(log)
    return None


def summarize_error_patterns(logs: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    """Summarize recurring error signals and include their source log IDs."""
    limit = _limit(limit)
    counts: dict[str, list[str]] = {}
    for log in logs:
        message = str(log.get("message") or "")
        key = redact(message.split(" ", 1)[0]) if message else "unknown"
        counts.setdefault(key, []).append(str(log.get("log_id") or ""))

    summary = []
    for key, log_ids in sorted(counts.items(), key=lambda item: (-len(item[1]), item[0]))[:limit]:
        summary.append(
            {
                "pattern": key,
                "occurrence_count": len(log_ids),
                "log_ids": log_ids,
            }
        )
    return summary
