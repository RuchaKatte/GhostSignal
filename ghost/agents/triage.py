"""Incident triage agent."""

from __future__ import annotations

from typing import Any

from ghost.agents.base import Agent, bundle_context
from ghost.ingest.bundle import IncidentBundle


class TriageAgent(Agent):
    name = "triage agent"

    async def run(self, bundle: IncidentBundle) -> dict[str, Any]:
        return await self.generate_json(
            "Summarize the alert, identify affected services from the supplied data, "
            "and list investigation priorities. Return keys: summary, affected_services, "
            "priorities.",
            bundle_context(bundle),
        )
