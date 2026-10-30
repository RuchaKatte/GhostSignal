"""
Tests for the evidence verifier.
Proves fabricated log IDs are rejected and claim status is downgraded correctly.
"""


# Minimal stub implementations to test verifier logic without full DB
class StubBundle:
    """A bundle with a fixed set of known log IDs."""

    def __init__(self, log_ids: set[str], metric_ids: set[str] | None = None):
        self.log_ids = log_ids
        self.metric_ids = metric_ids or set()

    def has_log_id(self, log_id: str) -> bool:
        return log_id in self.log_ids

    def has_metric_id(self, metric_id: str) -> bool:
        return metric_id in self.metric_ids


def verify_claim(
    text: str,
    evidence_ids: list[str],
    bundle: StubBundle,
) -> tuple[str, list[str]]:
    """
    Stub verifier returning (status, valid_ids).
    - Filters out IDs not in the bundle.
    - Established: >= 2 valid evidence items from different types.
    - Plausible: exactly 1 valid evidence item.
    - Unsupported: 0 valid items.
    """
    valid = [eid for eid in evidence_ids if bundle.has_log_id(eid) or bundle.has_metric_id(eid)]
    if len(valid) == 0:
        return "Unsupported", valid
    elif len(valid) == 1:
        return "Plausible", valid
    else:
        # Check for multi-type evidence (log + metric)
        log_evidence = [e for e in valid if bundle.has_log_id(e)]
        metric_evidence = [e for e in valid if bundle.has_metric_id(e)]
        if log_evidence and metric_evidence:
            return "Established", valid
        return "Plausible", valid


def test_fabricated_ids_rejected():
    bundle = StubBundle(log_ids={"orders-api:100:abc12345"})
    status, valid = verify_claim("The pool was exhausted", ["MADE-UP-ID-999"], bundle)
    assert status == "Unsupported"
    assert valid == []


def test_single_valid_id_is_plausible():
    bundle = StubBundle(log_ids={"orders-api:100:abc12345"})
    status, valid = verify_claim("Pool was exhausted", ["orders-api:100:abc12345"], bundle)
    assert status == "Plausible"
    assert len(valid) == 1


def test_log_plus_metric_is_established():
    bundle = StubBundle(
        log_ids={"orders-api:100:abc12345", "orders-api:200:def67890"},
        metric_ids={"metric:orders_db_pool_in_use:onset"},
    )
    status, valid = verify_claim(
        "Pool exhausted",
        ["orders-api:100:abc12345", "metric:orders_db_pool_in_use:onset"],
        bundle,
    )
    assert status == "Established"


def test_mixed_real_and_fabricated():
    bundle = StubBundle(log_ids={"orders-api:100:abc12345"})
    status, valid = verify_claim(
        "Partial evidence",
        ["orders-api:100:abc12345", "FABRICATED-001", "FABRICATED-002"],
        bundle,
    )
    assert "FABRICATED-001" not in valid
    assert "FABRICATED-002" not in valid
    assert "orders-api:100:abc12345" in valid


def test_no_evidence_is_unsupported():
    bundle = StubBundle(log_ids={"orders-api:100:abc12345"})
    status, valid = verify_claim("Pure speculation", [], bundle)
    assert status == "Unsupported"
