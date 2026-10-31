"""Read-only investigation tool registry and helpers."""

from ghost.tools.deploy_tools import (
    get_deployment_history,
    get_deployment_timestamps,
    get_service_restart_events,
    get_version_changes,
)
from ghost.tools.logs_tools import (
    get_log_by_id,
    get_logs_for_cluster,
    search_logs,
    search_logs_by_keyword,
    search_logs_by_service,
    search_logs_by_severity,
    search_logs_by_time,
    summarize_error_patterns,
)
from ghost.tools.memory_tools import (
    get_associated_evidence,
    get_previous_root_causes,
    get_similar_incidents,
    search_past_incidents,
)
from ghost.tools.metrics_tools import (
    compare_metric_values,
    get_metric_values,
    get_related_metric_observations,
    identify_metric_anomalies,
    inspect_metric_trend,
)
from ghost.tools.registry import ToolRegistry, make_registry, register_bundle_tools

__all__ = [
    "ToolRegistry",
    "compare_metric_values",
    "get_associated_evidence",
    "get_deployment_history",
    "get_deployment_timestamps",
    "get_log_by_id",
    "get_logs_for_cluster",
    "get_metric_values",
    "get_previous_root_causes",
    "get_related_metric_observations",
    "get_service_restart_events",
    "get_similar_incidents",
    "get_version_changes",
    "identify_metric_anomalies",
    "inspect_metric_trend",
    "make_registry",
    "register_bundle_tools",
    "search_logs_by_keyword",
    "search_logs",
    "search_logs_by_service",
    "search_logs_by_severity",
    "search_logs_by_time",
    "search_past_incidents",
    "summarize_error_patterns",
]
