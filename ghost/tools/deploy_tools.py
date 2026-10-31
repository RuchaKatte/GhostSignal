"""Read-only deployment investigation tools."""

from __future__ import annotations

from typing import Any


def get_deployment_history(deployments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return deployment metadata when available; otherwise a clear explicit message."""
    if not deployments:
        return {
            "available": False,
            "message": "Deployment history is unavailable in this environment.",
        }
    return {"available": True, "deployments": deployments}


def get_deployment_timestamps(deployments: list[dict[str, Any]] | None = None) -> list[str]:
    """Return deployment timestamps or an empty list if no data exists."""
    if not deployments:
        return []
    return [
        str(item.get("timestamp") or item.get("deployed_at") or "")
        for item in deployments
        if item.get("timestamp") or item.get("deployed_at")
    ]


def get_version_changes(deployments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return version transitions or a clear empty result."""
    if not deployments:
        return []
    return [item for item in deployments if item.get("from_version") or item.get("to_version")]


def get_service_restart_events(
    deployments: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return restart events only when the source data explicitly includes them."""
    if not deployments:
        return []
    return [
        item
        for item in deployments
        if str(item.get("event") or "").lower()
        in {"restart", "service_restart", "deployment_restart"}
    ]
