"""Robust anomaly detection for Prometheus metric time series.

The detector uses the median and median absolute deviation (MAD), so an observation
does not substantially shift its own baseline. The ``threshold`` is a modified
Z-score cutoff (3.0 by default). When scikit-learn is available, Isolation Forest
runs independently with at least 10 valid observations and a 5% contamination
assumption; results from both methods are combined. These are heuristics, not a
time-series forecast:
the baseline assumes most observations are representative, ignores sample spacing,
and does not interpolate missing values. Rows without a parseable timestamp or a
finite numeric value are omitted. Severity is based on robust deviation regardless
of which detector flagged a sample.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from sys import float_info
from typing import Any

try:  # pragma: no cover - optional dependency
    from sklearn.ensemble import IsolationForest
except ImportError:  # pragma: no cover
    IsolationForest = None  # type: ignore[assignment]


_MODIFIED_ZSCORE_FACTOR = 0.67448975
_ISOLATION_FOREST_MIN_POINTS = 10
_ISOLATION_FOREST_CONTAMINATION = 0.05


@dataclass(frozen=True)
class AnomalyRecord:
    """Structured anomaly result."""

    metric_name: str
    timestamp: datetime
    observed_value: float
    expected_value: float
    lower_bound: float
    upper_bound: float
    severity: str
    detection_method: str
    labels: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "timestamp": self.timestamp.isoformat(),
            "observed_value": self.observed_value,
            "expected_value": self.expected_value,
            "lower_bound": self.lower_bound,
            "upper_bound": self.upper_bound,
            "severity": self.severity,
            "detection_method": self.detection_method,
            "labels": self.labels or {},
        }


def _coerce_numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return ordered[middle - 1] / 2 + ordered[middle] / 2


def _robust_scores(values: list[float]) -> tuple[float, float, list[float]]:
    """Return median, half-MAD, and modified Z-scores for finite values."""
    center = _median(values)
    half_deviations = [abs(value / 2 - center / 2) for value in values]
    half_mad = _median(half_deviations)
    if half_mad == 0:
        scores = [0.0 if value == center else float("inf") for value in values]
    else:
        scores = [_MODIFIED_ZSCORE_FACTOR * (deviation / half_mad) for deviation in half_deviations]
    return center, half_mad, scores


def _zscore_detect(values: list[float], threshold: float = 3.0) -> list[bool]:
    """Detect samples beyond ``threshold`` modified robust Z-scores."""
    _, _, scores = _robust_scores(values)
    return [score > threshold for score in scores]


def _isolation_forest_detect(values: list[float]) -> list[bool]:
    """Use Isolation Forest with a documented 5% contamination assumption."""
    if IsolationForest is None or len(values) < _ISOLATION_FOREST_MIN_POINTS:
        return [False for _ in values]
    scale = max(abs(value) for value in values)
    if scale == 0:
        return [False for _ in values]
    model = IsolationForest(
        contamination=_ISOLATION_FOREST_CONTAMINATION,
        random_state=0,
    )
    predictions = model.fit_predict([[value / scale] for value in values])
    return [prediction == -1 for prediction in predictions]


def _severity(score: float) -> str:
    """Classify robust deviation using the detector's established severity bands."""
    if score >= 5:
        return "critical"
    if score >= 3.5:
        return "high"
    if score >= 2.5:
        return "medium"
    return "low"


def _finite_bound(center: float, half_mad: float, threshold: float, direction: int) -> float:
    offset = half_mad * (threshold / _MODIFIED_ZSCORE_FACTOR) * 2
    if not isfinite(offset):
        return float_info.max if direction > 0 else -float_info.max
    bound = center + direction * offset
    return max(-float_info.max, min(float_info.max, bound))


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def _validate_options(threshold: float, min_points: int) -> None:
    if isinstance(threshold, bool) or not isinstance(threshold, int | float):
        raise ValueError("threshold must be a finite positive number")
    try:
        numeric_threshold = float(threshold)
    except OverflowError:
        numeric_threshold = float("inf")
    if not isfinite(numeric_threshold) or numeric_threshold <= 0:
        raise ValueError("threshold must be a finite positive number")
    if isinstance(min_points, bool) or not isinstance(min_points, int) or min_points < 3:
        raise ValueError("min_points must be an integer of at least 3")


def detect_anomalies(
    series: list[Any],
    metric_name: str | None = None,
    threshold: float = 3.0,
    min_points: int = 3,
) -> list[dict[str, Any]]:
    """Return robustly detected metric anomalies as Incident Bundle-compatible dicts.

    ``threshold`` is the modified Z-score cutoff, and ``min_points`` is the minimum
    number of finite, timestamped observations required to analyze a series. Both
    Z-score and Isolation Forest detections are retained independently. Severity
    bands are low (<2.5), medium (>=2.5), high (>=3.5), and critical (>=5) robust
    deviation scores; they are descriptive heuristics, not probabilities.
    """
    _validate_options(threshold, min_points)
    if not series:
        return []

    metric_name = metric_name or str(
        _row_value(series[0], "metric_name") or _row_value(series[0], "metric") or "metric"
    )
    observations: list[tuple[datetime, float, dict[str, str]]] = []
    for row in series:
        if not isinstance(row, Mapping) and not (
            hasattr(row, "timestamp") and hasattr(row, "value")
        ):
            continue
        timestamp = _parse_timestamp(_row_value(row, "timestamp"))
        if timestamp is None:
            continue

        raw_value = _row_value(row, "value")
        if raw_value is None:
            raw_value = _row_value(row, "observed_value")
        numeric = _coerce_numeric(raw_value)
        if numeric is None:
            continue

        raw_labels = _row_value(row, "labels")
        labels = (
            {str(key): str(value) for key, value in raw_labels.items()}
            if isinstance(raw_labels, Mapping)
            else {}
        )
        observations.append((timestamp, numeric, labels))

    if len(observations) < min_points:
        return []

    numeric_values = [observation[1] for observation in observations]
    center, half_mad, scores = _robust_scores(numeric_values)
    if all(value == numeric_values[0] for value in numeric_values):
        return []

    zscore_mask = [score > threshold for score in scores]
    isolation_mask = _isolation_forest_detect(numeric_values)
    lower_bound = _finite_bound(center, half_mad, threshold, -1)
    upper_bound = _finite_bound(center, half_mad, threshold, 1)

    anomalies: list[dict[str, Any]] = []
    for observation, score, zscore_flag, isolation_flag in zip(
        observations, scores, zscore_mask, isolation_mask, strict=True
    ):
        if not (zscore_flag or isolation_flag):
            continue

        methods = []
        if zscore_flag:
            methods.append("zscore")
        if isolation_flag:
            methods.append("isolation_forest")
        timestamp, observed_value, labels = observation
        anomalies.append(
            AnomalyRecord(
                metric_name=metric_name,
                timestamp=timestamp,
                observed_value=observed_value,
                expected_value=center,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
                severity=_severity(score),
                detection_method="+".join(methods),
                labels=labels,
            ).to_dict()
        )
    return anomalies


def analyze_metric_series(
    series: list[Any], metric_name: str | None = None, threshold: float = 3.0
) -> list[dict[str, Any]]:
    """Public alias for the standard metric anomaly detector."""
    return detect_anomalies(series, metric_name=metric_name, threshold=threshold)
