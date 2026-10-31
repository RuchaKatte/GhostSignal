"""Read-only incident memory access for historical investigations."""

from __future__ import annotations

from typing import Any


def search_past_incidents(
    incidents: list[dict[str, Any]], *, service: str | None = None, keyword: str | None = None
) -> list[dict[str, Any]]:
    """Search stored incidents without performing a full RAG pass."""
    matches = list(incidents)
    if service:
        matches = [
            incident
            for incident in matches
            if str(incident.get("service") or "").lower() == str(service).lower()
        ]
    if keyword:
        matches = [
            incident
            for incident in matches
            if str(incident.get("summary") or incident.get("root_cause") or "")
            .lower()
            .find(str(keyword).lower())
            != -1
        ]
    return matches


def get_similar_incidents(
    incidents: list[dict[str, Any]], service: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Return past incidents for the same service."""
    return [
        incident
        for incident in incidents
        if str(incident.get("service") or "").lower() == str(service).lower()
    ][:limit]


def get_previous_root_causes(incidents: list[dict[str, Any]], *, limit: int = 5) -> list[str]:
    """Return stored root-cause summaries."""
    return [
        str(incident.get("root_cause") or "")
        for incident in incidents[:limit]
        if incident.get("root_cause")
    ]


def get_associated_evidence(
    incidents: list[dict[str, Any]], incident_id: str | int
) -> dict[str, Any]:
    """Return the stored evidence attached to a prior incident."""
    for incident in incidents:
        if str(incident.get("id") or incident.get("incident_id") or "") == str(incident_id):
            return {"incident_id": incident_id, "evidence": incident.get("evidence") or []}
    return {"incident_id": incident_id, "evidence": []}
