"""Evidence-led log analysis agent."""

from __future__ import annotations

from typing import Any

from ghost.agents.base import Agent
from ghost.ingest.bundle import IncidentBundle
from ghost.tools.registry import ToolRegistry


class LogAnalystAgent(Agent):
    name = "log analyst"

    async def run(self, bundle: IncidentBundle, tools: ToolRegistry) -> dict[str, Any]:
        logs = tools.call("logs.search", limit=200)
        patterns = tools.call("logs.error_patterns", limit=10)
        return await self.generate_json(
            "Analyze the supplied log evidence for relevant errors and suspicious patterns. "
            "Return findings as objects with description and evidence_ids, citing actual "
            "log_id values. Do not infer causes from a single correlated event.",
            {
                "incident_id": bundle.incident_id,
                "alert_name": bundle.alert_name,
                "logs": logs,
                "patterns": patterns,
            },
        )
