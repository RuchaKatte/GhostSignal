"""Root-cause hypothesis generation agent."""

from __future__ import annotations

from typing import Any

from ghost.agents.base import Agent


class HypothesisAgent(Agent):
    name = "hypothesis agent"

    async def run(
        self,
        incident: dict[str, Any],
        log_analysis: dict[str, Any],
        metric_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.generate_json(
            "Combine the verified log and metric findings into ranked possible explanations. "
            "Return hypotheses as objects with description, confidence (0 to 1), "
            "supporting_evidence_ids, and contradicting_evidence_ids. Distinguish correlation "
            "from causation and do not claim a root cause without independent evidence.",
            {
                "incident": incident,
                "log_analysis": log_analysis,
                "metric_analysis": metric_analysis,
            },
        )
