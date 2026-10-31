"""Deterministic verification of investigation claims against bundles."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

from ghost.ledger.models import ClaimStatus, InvestigationClaim


@dataclass
class VerificationResult:
    """Result of claim verification against evidence."""

    status: str
    valid_evidence_ids: list[str] = field(default_factory=list)
    invalid_evidence_ids: list[str] = field(default_factory=list)
    missing_references: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "valid_evidence_ids": self.valid_evidence_ids,
            "invalid_evidence_ids": self.invalid_evidence_ids,
            "missing_references": self.missing_references,
            "warnings": self.warnings,
        }


def _normalize_claim_status(status: str | ClaimStatus) -> str:
    if isinstance(status, ClaimStatus):
        return status.value
    return str(status).strip() if str(status).strip() else ClaimStatus.PLAUSIBLE.value


def _to_bundle_ids(bundle: Any) -> tuple[set[str], set[str]]:
    log_ids: set[str] = set()
    metric_ids: set[str] = set()
    if hasattr(bundle, "log_ids"):
        log_ids.update(bundle.log_ids)
    if hasattr(bundle, "metric_ids"):
        metric_ids.update(bundle.metric_ids)
    if hasattr(bundle, "logs"):
        for record in bundle.logs:
            if isinstance(record, dict) and record.get("log_id"):
                log_ids.add(str(record["log_id"]))
    if hasattr(bundle, "metrics"):
        for record in bundle.metrics:
            if isinstance(record, dict):
                if record.get("metric_id"):
                    metric_ids.add(str(record["metric_id"]))
                if record.get("id"):
                    metric_ids.add(str(record["id"]))
    if hasattr(bundle, "has_log_id") and hasattr(bundle, "has_metric_id"):
        # The bundle is a richer object; keep the `has_*` methods as the primary validation path.
        return log_ids, metric_ids
    return log_ids, metric_ids


def _evidence_kind(bundle: Any, evidence_id: str) -> set[str]:
    log_ids, metric_ids = _to_bundle_ids(bundle)
    kinds: set[str] = set()
    if evidence_id in log_ids or (hasattr(bundle, "has_log_id") and bundle.has_log_id(evidence_id)):
        kinds.add("log")
    if evidence_id in metric_ids or (
        hasattr(bundle, "has_metric_id") and bundle.has_metric_id(evidence_id)
    ):
        kinds.add("metric")
    return kinds


def _classify_supporting_evidence(valid_support_ids: list[str], bundle: Any) -> str:
    if not valid_support_ids:
        return ClaimStatus.UNSUPPORTED.value
    kinds = set().union(*(_evidence_kind(bundle, evidence_id) for evidence_id in valid_support_ids))
    if {"log", "metric"}.issubset(kinds):
        return ClaimStatus.ESTABLISHED.value
    return ClaimStatus.PLAUSIBLE.value


def _claim_ids(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return list(
        dict.fromkeys(
            str(item)
            for item in value
            if isinstance(item, str | int) and not isinstance(item, bool) and str(item)
        )
    )


def verify_claim(
    text: str,
    evidence_ids: list[str],
    bundle: Any,
) -> tuple[str, list[str]]:
    """Validate a claim against a bundle.

    This function intentionally mirrors the expected minimal verifier behavior from the
    project tests: fabricated evidence is rejected, single valid IDs are plausible, and
    cross-modality evidence can establish a factual claim. Causal hypotheses are kept
    plausible by the orchestrator because correlated observations do not prove causation.
    """
    valid: list[str] = []
    for evidence_id in evidence_ids:
        if _evidence_kind(bundle, evidence_id):
            valid.append(evidence_id)

    valid = list(dict.fromkeys(valid))
    return _classify_supporting_evidence(valid, bundle), valid


def verify_evidence_claim(
    claim: InvestigationClaim | dict[str, Any],
    bundle: Any,
) -> VerificationResult:
    """Verify a structured claim and return the full validation result."""
    if isinstance(claim, dict):
        raw_confidence = claim.get("confidence", 0.5)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError, OverflowError):
            confidence = 0.5
        if not isfinite(confidence) or not 0 <= confidence <= 1:
            confidence = 0.5
        claim_obj = InvestigationClaim(
            claim_id=str(claim.get("claim_id") or "claim"),
            description=str(claim.get("description") or claim.get("text") or ""),
            status=claim.get("status") or "Plausible",
            supporting_evidence_ids=_claim_ids(claim.get("supporting_evidence_ids")),
            contradicting_evidence_ids=_claim_ids(claim.get("contradicting_evidence_ids")),
            confidence=confidence,
            explanation=str(claim.get("explanation") or ""),
        )
    else:
        claim_obj = claim

    supporting_ids = [str(item) for item in claim_obj.supporting_evidence_ids]
    contradicting_ids = [str(item) for item in claim_obj.contradicting_evidence_ids]
    evidence_ids = supporting_ids + contradicting_ids
    valid_ids: list[str] = []
    invalid_ids: list[str] = []
    valid_supporting_ids: list[str] = []
    valid_contradicting_ids: list[str] = []

    for evidence_id in supporting_ids:
        if _evidence_kind(bundle, evidence_id):
            valid_ids.append(evidence_id)
            valid_supporting_ids.append(evidence_id)
        else:
            invalid_ids.append(evidence_id)

    for evidence_id in contradicting_ids:
        if _evidence_kind(bundle, evidence_id):
            valid_ids.append(evidence_id)
            valid_contradicting_ids.append(evidence_id)
        else:
            invalid_ids.append(evidence_id)

    status = _classify_supporting_evidence(list(dict.fromkeys(valid_supporting_ids)), bundle)
    missing = [eid for eid in evidence_ids if eid not in valid_ids]
    warnings: list[str] = []
    if invalid_ids:
        warnings.append("Fabricated or invalid evidence references were rejected.")
    if status == ClaimStatus.UNSUPPORTED.value:
        warnings.append("No valid supporting evidence was found for the claim.")
    if valid_contradicting_ids:
        status = (
            ClaimStatus.PLAUSIBLE.value if valid_supporting_ids else ClaimStatus.UNSUPPORTED.value
        )
        warnings.append("Contradicting evidence prevents an established classification.")
    if _normalize_claim_status(claim_obj.status) != status:
        warnings.append("The claim status was recalculated from validated evidence.")

    return VerificationResult(
        status=status,
        valid_evidence_ids=sorted(set(valid_ids)),
        invalid_evidence_ids=sorted(set(invalid_ids)),
        missing_references=sorted(set(missing)),
        warnings=warnings,
    )


def verify_claims(
    bundle: Any, claims: Iterable[InvestigationClaim | dict[str, Any]]
) -> list[VerificationResult]:
    """Verify an iterable of claims against a bundle."""
    return [verify_evidence_claim(claim, bundle) for claim in claims]
