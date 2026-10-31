"""Skeptical review agent for investigation hypotheses."""

from __future__ import annotations

from typing import Any

from ghost.agents.base import Agent


class SkepticAgent(Agent):
    name = "skeptic agent"

    async def run(
        self, hypotheses: list[dict[str, Any]], evidence_summary: dict[str, Any]
    ) -> dict[str, Any]:
        return await self.generate_json(
            "Challenge each supplied hypothesis. Identify unsupported assumptions, missing "
            "evidence, and correlation-versus-causation issues. Return reviews containing "
            "claim_id, challenge, missing_evidence, and causality_concerns. Do not add or "
            "change evidence IDs.",
            {"hypotheses": hypotheses, "verified_evidence": evidence_summary},
        )
