"""Sequential, evidence-verified incident investigation workflow."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from math import isfinite
from typing import Any

from ghost.agents.base import AgentResponseError, GenerationClient
from ghost.agents.hypothesis import HypothesisAgent
from ghost.agents.log_analyst import LogAnalystAgent
from ghost.agents.metrics_analyst import MetricsAnalystAgent
from ghost.agents.narrator import NarratorAgent
from ghost.agents.skeptic import SkepticAgent
from ghost.agents.triage import TriageAgent
from ghost.ingest.bundle import IncidentBundle
from ghost.ingest.redact import redact, redact_dict
from ghost.ledger.models import ClaimStatus
from ghost.ledger.verifier import verify_evidence_claim
from ghost.llm.client import LLMError, OllamaClient
from ghost.storage.results import save_investigation_result
from ghost.tools.registry import register_bundle_tools

ResultStore = Callable[[int, IncidentBundle, dict[str, Any]], Awaitable[None]]


class InvestigationOrchestrator:
    """Runs fixed agents in order and validates each evidence reference locally."""

    def __init__(
        self,
        llm: GenerationClient | None = None,
        *,
        result_store: ResultStore | None = save_investigation_result,
    ) -> None:
        client = llm or OllamaClient()
        self.triage = TriageAgent(client)
        self.log_analyst = LogAnalystAgent(client)
        self.metrics_analyst = MetricsAnalystAgent(client)
        self.hypothesis_agent = HypothesisAgent(client)
        self.skeptic = SkepticAgent(client)
        self.narrator = NarratorAgent(client)
        self.result_store = result_store

    async def investigate(
        self,
        bundle: IncidentBundle,
        *,
        investigation_id: int | None = None,
    ) -> dict[str, Any]:
        """Investigate a bundle, downgrading or rejecting unverified LLM claims."""
        safe_bundle = self._sanitize_bundle(bundle)
        tools = register_bundle_tools(safe_bundle)
        warnings = list(safe_bundle.data_quality_warnings)
        pipeline_status = "partial" if warnings else "completed"
        completed_stages: list[str] = []
        current_stage: str | None = None
        triage: dict[str, Any] = {}
        log_output: dict[str, Any] = {}
        metric_output: dict[str, Any] = {}
        hypotheses: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        narration: dict[str, Any] = {}

        try:
            current_stage = "triage"
            triage = await self.triage.run(safe_bundle)
            completed_stages.append(current_stage)
            current_stage = "log_analysis"
            log_output = await self.log_analyst.run(safe_bundle, tools)
            completed_stages.append(current_stage)
            current_stage = "metric_analysis"
            metric_output = await self.metrics_analyst.run(safe_bundle, tools)
            completed_stages.append(current_stage)
            current_stage = "hypothesis_generation"
            log_findings = self._validate_findings(
                log_output.get("findings"), "log-finding", safe_bundle, warnings
            )
            metric_findings = self._validate_findings(
                metric_output.get("findings"), "metric-finding", safe_bundle, warnings
            )
            raw_hypotheses = await self.hypothesis_agent.run(
                self._incident_context(safe_bundle),
                {"findings": log_findings},
                {"findings": metric_findings},
            )
            hypotheses = self._validate_hypotheses(
                raw_hypotheses.get("hypotheses"), safe_bundle, warnings
            )
            completed_stages.append(current_stage)
            current_stage = "skeptical_review"
            skeptic_output = await self.skeptic.run(
                hypotheses, self._evidence_summary(log_findings, metric_findings)
            )
            reviews = self._validate_reviews(skeptic_output.get("reviews"), hypotheses)
            completed_stages.append(current_stage)
            current_stage = "narration"
            narration = await self.narrator.run(
                self._report_context(
                    safe_bundle, triage, log_findings, metric_findings, hypotheses, reviews
                )
            )
            completed_stages.append(current_stage)
        except (LLMError, AgentResponseError) as exc:
            pipeline_status = "partial"
            warnings.append(f"AI investigation did not complete: {type(exc).__name__}.")

        log_findings = self._validate_findings(
            log_output.get("findings"), "log-finding", safe_bundle, warnings
        )
        metric_findings = self._validate_findings(
            metric_output.get("findings"), "metric-finding", safe_bundle, warnings
        )
        hypotheses = self._revalidate_hypotheses(hypotheses, safe_bundle, warnings)
        report = self._build_report(
            safe_bundle,
            triage,
            log_findings,
            metric_findings,
            hypotheses,
            reviews,
            narration,
            warnings,
            pipeline_status,
            completed_stages,
            current_stage if pipeline_status == "partial" else None,
        )
        if investigation_id is not None and self.result_store is not None:
            await self.result_store(investigation_id, safe_bundle, report)
        return report

    @staticmethod
    def _sanitize_bundle(bundle: IncidentBundle) -> IncidentBundle:
        data = redact_dict(bundle.model_dump(mode="json"))
        for log in data.get("logs", []):
            message = log.get("message") or log.get("redacted_message") or log.get("log") or ""
            safe_message = redact(str(message))
            for field in ("raw_message", "log"):
                log.pop(field, None)
            log["message"] = safe_message
            log["redacted_message"] = safe_message
        return IncidentBundle.from_dict(data)

    @staticmethod
    def _incident_context(bundle: IncidentBundle) -> dict[str, Any]:
        return {
            "incident_id": bundle.incident_id,
            "alert_name": bundle.alert_name,
            "service": bundle.service,
            "window_start": bundle.window_start,
            "window_end": bundle.window_end,
            "alert_details": bundle.alert_details,
            "data_quality_warnings": bundle.data_quality_warnings,
        }

    @staticmethod
    def _evidence_ids(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))

    @classmethod
    def _validate_findings(
        cls,
        raw_findings: Any,
        prefix: str,
        bundle: IncidentBundle,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_findings, list):
            return []
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(raw_findings):
            if not isinstance(item, dict):
                continue
            description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                continue
            evidence_ids = cls._evidence_ids(item.get("evidence_ids"))
            verification = verify_evidence_claim(
                {
                    "claim_id": f"{prefix}-{index + 1}",
                    "description": description,
                    "status": ClaimStatus.PLAUSIBLE.value,
                    "supporting_evidence_ids": evidence_ids,
                },
                bundle,
            )
            if verification.invalid_evidence_ids:
                warnings.append(
                    f"Rejected fabricated evidence reference(s) in {prefix}-{index + 1}."
                )
            findings.append(
                {
                    "finding_id": f"{prefix}-{index + 1}",
                    "description": redact(description),
                    "status": verification.status,
                    "evidence_ids": verification.valid_evidence_ids,
                    "rejected_evidence_ids": verification.invalid_evidence_ids,
                }
            )
        return findings

    @classmethod
    def _validate_hypotheses(
        cls,
        raw_hypotheses: Any,
        bundle: IncidentBundle,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_hypotheses, list):
            return []
        validated: list[dict[str, Any]] = []
        for index, item in enumerate(raw_hypotheses):
            if not isinstance(item, dict):
                continue
            description = item.get("description")
            if not isinstance(description, str) or not description.strip():
                continue
            confidence_value = item.get("confidence", 0.25)
            try:
                confidence = float(confidence_value)
            except (TypeError, ValueError, OverflowError):
                confidence = 0.25
            if not isfinite(confidence) or not 0 <= confidence <= 1:
                confidence = 0.25
                warnings.append(
                    "Invalid hypothesis confidence was replaced with a conservative value."
                )

            support = cls._evidence_ids(item.get("supporting_evidence_ids"))
            contradicting = cls._evidence_ids(item.get("contradicting_evidence_ids"))
            claim = {
                "claim_id": f"hypothesis-{index + 1}",
                "description": redact(description),
                "status": ClaimStatus.PLAUSIBLE.value,
                "supporting_evidence_ids": support,
                "contradicting_evidence_ids": contradicting,
                "confidence": confidence,
            }
            verification = verify_evidence_claim(claim, bundle)
            if verification.invalid_evidence_ids:
                warnings.append(
                    f"Rejected fabricated evidence reference(s) in {claim['claim_id']}."
                )
            claim["status"] = verification.status
            if claim["status"] == ClaimStatus.ESTABLISHED.value:
                claim["status"] = ClaimStatus.PLAUSIBLE.value
                warnings.append(
                    "Root-cause hypotheses remain plausible; correlated logs and metrics "
                    "do not establish causation."
                )
            claim["supporting_evidence_ids"] = [
                evidence_id
                for evidence_id in verification.valid_evidence_ids
                if evidence_id in support
            ]
            claim["contradicting_evidence_ids"] = [
                evidence_id
                for evidence_id in verification.valid_evidence_ids
                if evidence_id in contradicting
            ]
            claim["rejected_evidence_ids"] = verification.invalid_evidence_ids
            claim["rejected_evidence_ids"] = list(
                dict.fromkeys(
                    [
                        *claim["rejected_evidence_ids"],
                        *cls._evidence_ids(item.get("rejected_evidence_ids")),
                    ]
                )
            )
            validated.append(claim)
        return validated

    @classmethod
    def _revalidate_hypotheses(
        cls,
        hypotheses: list[dict[str, Any]],
        bundle: IncidentBundle,
        warnings: list[str],
    ) -> list[dict[str, Any]]:
        return cls._validate_hypotheses(hypotheses, bundle, warnings)

    @staticmethod
    def _validate_reviews(
        raw_reviews: Any, hypotheses: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_reviews, list):
            return []
        claim_ids = {item["claim_id"] for item in hypotheses}
        reviews: list[dict[str, Any]] = []
        for item in raw_reviews:
            if not isinstance(item, dict) or item.get("claim_id") not in claim_ids:
                continue
            reviews.append(
                {
                    "claim_id": item["claim_id"],
                    "challenge": redact(str(item.get("challenge") or "")),
                    "missing_evidence": redact_dict(item.get("missing_evidence") or []),
                    "causality_concerns": redact_dict(item.get("causality_concerns") or []),
                }
            )
        return reviews

    @staticmethod
    def _evidence_summary(
        log_findings: list[dict[str, Any]], metric_findings: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {"log_findings": log_findings, "metric_findings": metric_findings}

    @classmethod
    def _report_context(
        cls,
        bundle: IncidentBundle,
        triage: dict[str, Any],
        log_findings: list[dict[str, Any]],
        metric_findings: list[dict[str, Any]],
        hypotheses: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            **cls._incident_context(bundle),
            "triage": triage,
            "log_findings": log_findings,
            "metric_findings": metric_findings,
            "root_cause_hypotheses": hypotheses,
            "skeptic_reviews": reviews,
        }

    @staticmethod
    def _timeline(bundle: IncidentBundle) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        if bundle.window_start:
            timeline.append(
                {
                    "timestamp": bundle.window_start,
                    "event": "Alert investigation window started",
                    "evidence_ids": [],
                }
            )
        for log in bundle.logs:
            timeline.append(
                {
                    "timestamp": log.get("timestamp"),
                    "event": str(log.get("message") or "Log observation"),
                    "evidence_ids": [str(log["log_id"])] if log.get("log_id") else [],
                }
            )
        for anomaly in bundle.anomalies:
            evidence_id = anomaly.get("metric_id")
            timeline.append(
                {
                    "timestamp": anomaly.get("timestamp"),
                    "event": (
                        f"Detected {anomaly.get('severity', 'unknown')} anomaly in "
                        f"{anomaly.get('metric_name', 'metric')}"
                    ),
                    "evidence_ids": [str(evidence_id)] if evidence_id else [],
                }
            )
        timeline.sort(key=lambda item: str(item.get("timestamp") or ""))
        return timeline

    @classmethod
    def _build_report(
        cls,
        bundle: IncidentBundle,
        triage: dict[str, Any],
        log_findings: list[dict[str, Any]],
        metric_findings: list[dict[str, Any]],
        hypotheses: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        narration: dict[str, Any],
        warnings: list[str],
        pipeline_status: str,
        completed_stages: list[str],
        failed_stage: str | None,
    ) -> dict[str, Any]:
        ranked = sorted(
            hypotheses,
            key=lambda item: (
                item["status"] != ClaimStatus.ESTABLISHED.value,
                item["status"] != ClaimStatus.PLAUSIBLE.value,
                -item["confidence"],
            ),
        )
        services = {bundle.service} if bundle.service and bundle.service != "unknown" else set()
        services.update(
            str(log.get("service"))
            for log in bundle.logs
            if log.get("service") and str(log.get("service")) != "unknown"
        )
        services.update(
            str(metric.get("labels", {}).get("service"))
            for metric in bundle.metrics
            if metric.get("labels", {}).get("service")
        )
        established = [
            item
            for item in [*log_findings, *metric_findings, *ranked]
            if item["status"] == ClaimStatus.ESTABLISHED.value
        ]
        plausible = [
            item
            for item in [*log_findings, *metric_findings, *ranked]
            if item["status"] == ClaimStatus.PLAUSIBLE.value
        ]
        unsupported = [
            item
            for item in [*log_findings, *metric_findings, *ranked]
            if item["status"] == ClaimStatus.UNSUPPORTED.value
        ]
        if established:
            summary = f"Evidence establishes: {established[0]['description']}"
        elif plausible:
            summary = (
                f"Evidence supports a possible explanation, but does not establish a root cause: "
                f"{plausible[0]['description']}"
            )
        else:
            summary = (
                f"Insufficient evidence to determine a root cause for {bundle.alert_name} "
                f"affecting {bundle.service}."
            )

        recommendations = narration.get("recommended_next_steps")
        if not isinstance(recommendations, list):
            recommendations = []
        recommendations = [
            redact(step.strip())[:500]
            for step in recommendations
            if isinstance(step, str) and step.strip()
        ][:10]
        if not recommendations:
            recommendations = [
                "Collect additional service logs and metric observations for the incident window."
            ]
        confidence_level = (
            "medium"
            if any(
                item["status"] == ClaimStatus.PLAUSIBLE.value and item["confidence"] >= 0.5
                for item in ranked
            )
            else "low"
        )
        return {
            "incident_id": bundle.incident_id,
            "incident_summary": summary,
            "affected_services": sorted(services),
            "triage_priorities": [
                redact(item)[:300] for item in triage.get("priorities", []) if isinstance(item, str)
            ][:10]
            if isinstance(triage.get("priorities", []), list)
            else [],
            "timeline": cls._timeline(bundle),
            "detected_anomalies": bundle.anomalies,
            "important_log_findings": log_findings,
            "important_metric_findings": metric_findings,
            "root_cause_hypotheses": ranked,
            "supporting_evidence": sorted(
                {evidence_id for item in ranked for evidence_id in item["supporting_evidence_ids"]}
            ),
            "contradicting_evidence": sorted(
                {
                    evidence_id
                    for item in ranked
                    for evidence_id in item["contradicting_evidence_ids"]
                }
            ),
            "established_findings": established,
            "plausible_hypotheses": plausible,
            "unsupported_claims": unsupported,
            "skeptic_reviews": reviews,
            "confidence_level": confidence_level,
            "recommended_next_steps": recommendations,
            "data_quality_warnings": list(dict.fromkeys(warnings)),
            "collection_metadata": bundle.collection_metadata,
            "pipeline_status": pipeline_status,
            "completed_stages": completed_stages,
            "failed_stage": failed_stage,
        }
