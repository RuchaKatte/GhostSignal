"""Docker-free end-to-end coverage for alert-to-report investigation."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import BackgroundTasks

from ghost.agents.orchestrator import InvestigationOrchestrator
from ghost.api import routes_alert
from ghost.ingest.bundle import IncidentBundle
from ghost.ingest.collector import collect_incident_bundle
from ghost.ingest.prom_client import MetricSample
from ghost.ledger.models import ClaimStatus
from ghost.llm.client import LLMResponse


class SyntheticLoki:
    def __init__(self, timestamp: datetime) -> None:
        self.timestamp = timestamp

    async def query_logs(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "service": "gateway",
                "timestamp": self.timestamp.isoformat(),
                "message": "database request failed for person@example.com",
            }
        ]


class SyntheticPrometheus:
    def __init__(self, start: datetime) -> None:
        self.start = start

    async def query_range(
        self, _query: str, _start: datetime, _end: datetime, *, step: str
    ) -> list[MetricSample]:
        assert step == "15s"
        return [
            MetricSample(
                metric_name="gateway_errors_total",
                labels={"service": "gateway"},
                timestamp=self.start + timedelta(seconds=index),
                value=value,
            )
            for index, value in enumerate([1, 1, 1, 1, 20])
        ]


class SyntheticLLM:
    def __init__(self, log_id: str, metric_id: str) -> None:
        self.outputs = [
            {
                "summary": "Gateway errors increased",
                "affected_services": ["gateway"],
                "priorities": ["Review gateway logs and error metrics"],
            },
            {"findings": [{"description": "Database requests failed", "evidence_ids": [log_id]}]},
            {
                "findings": [
                    {"description": "Error count rose sharply", "evidence_ids": [metric_id]}
                ]
            },
            {
                "hypotheses": [
                    {
                        "description": "Database latency may have contributed to errors",
                        "confidence": 0.8,
                        "supporting_evidence_ids": [log_id, metric_id],
                        "contradicting_evidence_ids": [],
                    }
                ]
            },
            {
                "reviews": [
                    {
                        "claim_id": "hypothesis-1",
                        "challenge": "Correlation does not prove a database cause",
                        "missing_evidence": ["Database latency measurements"],
                        "causality_concerns": ["Temporal correlation alone"],
                    }
                ]
            },
            {
                "incident_summary": "Gateway errors coincided with a metric spike",
                "recommended_next_steps": ["Inspect database latency and traces"],
            },
        ]
        self.prompts: list[str] = []

    async def generate(self, prompt: str, **_kwargs: Any) -> LLMResponse:
        self.prompts.append(prompt)
        output = self.outputs.pop(0)
        return LLMResponse(
            text=json.dumps(output),
            model="synthetic",
            done=True,
            structured=output,
        )


@pytest.mark.asyncio
async def test_alert_collection_and_mocked_investigation_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime.now(UTC) - timedelta(minutes=5)
    end = start + timedelta(seconds=10)
    payload = routes_alert.AlertmanagerPayload.model_validate(
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "HighErrorRate",
                        "severity": "critical",
                        "service": "gateway",
                    },
                    "annotations": {"summary": "Gateway error rate exceeded threshold"},
                    "startsAt": start.isoformat(),
                    "endsAt": end.isoformat(),
                    "fingerprint": "synthetic-high-error-rate",
                }
            ],
        }
    )
    monkeypatch.setattr(
        routes_alert, "get_settings", lambda: SimpleNamespace(alert_webhook_secret="test-secret")
    )

    async def create_investigation(_alert: routes_alert.Alert) -> tuple[int, bool]:
        return 17, True

    monkeypatch.setattr(routes_alert, "_create_or_get_investigation", create_investigation)
    original_collect = collect_incident_bundle
    captured: dict[str, Any] = {}

    async def collect(**kwargs: Any) -> IncidentBundle:
        bundle = await original_collect(
            **kwargs,
            loki_client=SyntheticLoki(start + timedelta(seconds=2)),
            prometheus_client=SyntheticPrometheus(start),
            metric_queries=("gateway_errors_total",),
        )
        captured["bundle"] = bundle
        return bundle

    monkeypatch.setattr(routes_alert, "collect_incident_bundle", collect)

    class MockedOrchestrator:
        async def investigate(
            self, bundle: IncidentBundle, *, investigation_id: int
        ) -> dict[str, Any]:
            assert investigation_id == 17
            llm = SyntheticLLM(
                bundle.logs[0]["log_id"],
                bundle.anomalies[0]["metric_id"],
            )
            report = await InvestigationOrchestrator(llm, result_store=None).investigate(bundle)
            captured["report"] = report
            captured["prompts"] = llm.prompts
            return report

    monkeypatch.setattr("ghost.agents.orchestrator.InvestigationOrchestrator", MockedOrchestrator)
    tasks = BackgroundTasks()
    accepted = await routes_alert.receive_alert(payload, tasks, "Bearer test-secret")
    assert accepted["accepted"] is True
    assert accepted["firing_count"] == 1
    await tasks.tasks[0]()

    bundle = captured["bundle"]
    report = captured["report"]
    prompts = captured["prompts"]
    assert len(bundle.logs) == 1
    assert len(bundle.metrics) == 5
    assert len(bundle.anomalies) == 1
    assert "person@example.com" not in bundle.to_json()

    log_id = bundle.logs[0]["log_id"]
    anomaly_metric_id = bundle.anomalies[0]["metric_id"]

    assert report["incident_id"] == "17"
    assert report["pipeline_status"] == "completed"
    assert report["completed_stages"] == [
        "triage",
        "log_analysis",
        "metric_analysis",
        "hypothesis_generation",
        "skeptical_review",
        "narration",
    ]
    assert report["incident_summary"]
    assert report["timeline"]
    assert report["root_cause_hypotheses"][0]["status"] == ClaimStatus.PLAUSIBLE.value
    assert report["root_cause_hypotheses"][0]["supporting_evidence_ids"] == [
        log_id,
        anomaly_metric_id,
    ]
    assert report["supporting_evidence"] == sorted([log_id, anomaly_metric_id])
    assert report["recommended_next_steps"] == ["Inspect database latency and traces"]
    assert len(prompts) == 6
    assert all("person@example.com" not in prompt for prompt in prompts)
