"""Evidence-led metric analysis agent."""

from __future__ import annotations

from typing import Any

from ghost.agents.base import Agent
from ghost.ingest.bundle import IncidentBundle
from ghost.tools.registry import ToolRegistry


class MetricsAnalystAgent(Agent):
    name = "metrics analyst"

    async def run(self, bundle: IncidentBundle, tools: ToolRegistry) -> dict[str, Any]:
        names = sorted(
            {
                str(sample.get("metric_name") or sample.get("name"))
                for sample in bundle.metrics
                if sample.get("metric_name") or sample.get("name")
            }
        )
        observations = {name: tools.call("metrics.list", metric_name=name) for name in names}
        anomalies = tools.call("metrics.anomalies")
        return await self.generate_json(
            "Analyze metric trends and detector-produced anomalies. Return findings as objects "
            "with description and evidence_ids, citing only metric_id values in the supplied "
            "observations. Treat anomaly detection as a signal, not proof of cause.",
            {
                "incident_id": bundle.incident_id,
                "observations": observations,
                "anomalies": anomalies,
            },
        )
