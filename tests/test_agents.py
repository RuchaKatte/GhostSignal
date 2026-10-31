"""Tests for each investigation agent and the evidence-verified workflow."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

import pytest

from ghost.agents.hypothesis import HypothesisAgent
from ghost.agents.log_analyst import LogAnalystAgent
from ghost.agents.metrics_analyst import MetricsAnalystAgent
from ghost.agents.narrator import NarratorAgent
from ghost.agents.orchestrator import InvestigationOrchestrator
from ghost.agents.skeptic import SkepticAgent
from ghost.agents.triage import TriageAgent
from ghost.ingest.bundle import IncidentBundle
from ghost.llm.client import LLMConnectionError, LLMResponse
from ghost.tools.registry import register_bundle_tools


class ScriptedLLM:
    def __init__(self, outputs: Iterable[dict[str, Any] | Exception]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_kwargs: Any) -> LLMResponse:
        self.prompts.append(prompt)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        text = json.dumps(output)
        return LLMResponse(text=text, model="test", done=True, structured=output)


def _bundle() -> IncidentBundle:
    return IncidentBundle(
        incident_id="inc-1",
        alert_name="HighErrorRate",
        service="gateway",
        window_start="2026-01-01T00:00:00Z",
        window_end="2026-01-01T00:10:00Z",
        alert_details={"summary": "Error for secret.person@example.com"},
        logs=[
            {
                "log_id": "log-real",
                "service": "gateway",
                "timestamp": "2026-01-01T00:01:00+00:00",
                "message": "failed for secret.person@example.com",
            }
        ],
        metrics=[
            {
                "metric_id": "metric-real",
                "metric_name": "gateway_errors_total",
                "labels": {"service": "gateway"},
                "timestamp": "2026-01-01T00:01:00+00:00",
                "value": 10,
            }
        ],
    )


@pytest.mark.asyncio
async def test_individual_agents_return_structured_outputs_using_bundle_tools() -> None:
    bundle = _bundle()
    llm = ScriptedLLM(
        [
            {"priorities": ["Review gateway errors"]},
            {"findings": [{"description": "A request failed", "evidence_ids": ["log-real"]}]},
            {
                "findings": [
                    {"description": "Error count increased", "evidence_ids": ["metric-real"]}
                ]
            },
            {"hypotheses": [{"description": "Possible service overload"}]},
            {"reviews": [{"claim_id": "hypothesis-1", "challenge": "Could be a side effect"}]},
            {
                "incident_summary": "An error was observed",
                "recommended_next_steps": ["Check traces"],
            },
        ]
    )
    tools = register_bundle_tools(bundle)

    await TriageAgent(llm).run(bundle)
    log_result = await LogAnalystAgent(llm).run(bundle, tools)
    metric_result = await MetricsAnalystAgent(llm).run(bundle, tools)
    await HypothesisAgent(llm).run({}, log_result, metric_result)
    await SkepticAgent(llm).run([], {})
    await NarratorAgent(llm).run({})

    assert len(llm.prompts) == 6
    assert all("secret.person@example.com" not in prompt for prompt in llm.prompts)
    assert log_result["findings"][0]["evidence_ids"] == ["log-real"]
    assert metric_result["findings"][0]["evidence_ids"] == ["metric-real"]


@pytest.mark.asyncio
async def test_orchestrator_rejects_fabricated_ids_and_generates_structured_report() -> None:
    llm = ScriptedLLM(
        [
            {"summary": "Gateway affected", "priorities": ["Review errors"]},
            {
                "findings": [
                    {
                        "description": "A request failed",
                        "evidence_ids": ["log-real", "fabricated-log"],
                    }
                ]
            },
            {
                "findings": [
                    {"description": "Errors were observed", "evidence_ids": ["metric-real"]}
                ]
            },
            {
                "hypotheses": [
                    {
                        "description": "Gateway errors align with the alert",
                        "confidence": 0.9,
                        "supporting_evidence_ids": ["log-real", "metric-real", "fake-metric"],
                    }
                ]
            },
            {
                "reviews": [
                    {"claim_id": "hypothesis-1", "challenge": "Correlation is not causation"}
                ]
            },
            {"recommended_next_steps": ["Compare traces with the incident window"]},
        ]
    )
    orchestrator = InvestigationOrchestrator(llm, result_store=None)

    report = await orchestrator.investigate(_bundle())

    hypothesis = report["root_cause_hypotheses"][0]
    assert hypothesis["status"] == "Plausible"
    assert hypothesis["supporting_evidence_ids"] == ["log-real", "metric-real"]
    assert hypothesis["rejected_evidence_ids"] == ["fake-metric"]
    assert report["important_log_findings"][0]["rejected_evidence_ids"] == ["fabricated-log"]
    assert report["skeptic_reviews"][0]["claim_id"] == "hypothesis-1"
    assert report["affected_services"] == ["gateway"]
    assert report["pipeline_status"] == "completed"
    assert report["confidence_level"] == "medium"
    assert "secret.person@example.com" not in "\n".join(llm.prompts)


@pytest.mark.asyncio
async def test_llm_failure_yields_explicit_uncertainty_and_persists_partial_report() -> None:
    saved: list[tuple[int, IncidentBundle, dict[str, Any]]] = []

    async def save_result(
        investigation_id: int, bundle: IncidentBundle, report: dict[str, Any]
    ) -> None:
        saved.append((investigation_id, bundle, report))

    llm = ScriptedLLM([LLMConnectionError("offline")])
    orchestrator = InvestigationOrchestrator(llm, result_store=save_result)

    report = await orchestrator.investigate(_bundle(), investigation_id=23)

    assert report["pipeline_status"] == "partial"
    assert "Insufficient evidence" in report["incident_summary"]
    assert any("LLMConnectionError" in warning for warning in report["data_quality_warnings"])
    assert saved[0][0] == 23
