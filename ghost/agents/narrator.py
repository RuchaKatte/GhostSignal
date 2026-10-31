"""Final incident report narration agent."""

from __future__ import annotations

from typing import Any

from ghost.agents.base import Agent


class NarratorAgent(Agent):
    name = "narrator agent"

    async def run(self, verified_report: dict[str, Any]) -> dict[str, Any]:
        return await self.generate_json(
            "Prepare a concise human-readable summary and practical next steps using only the "
            "verified incident report supplied. Preserve uncertainty and do not introduce new "
            "claims or evidence. Return incident_summary and recommended_next_steps.",
            verified_report,
        )
