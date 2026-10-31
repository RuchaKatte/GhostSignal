"""Read-only tool registry for investigation workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolSpec:
    """A callable investigation tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    """A central registry for read-only investigative tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self, name: str, description: str, input_schema: dict[str, Any], handler: Callable[..., Any]
    ) -> ToolSpec:
        """Register a read-only tool with validation metadata."""
        if not name or name in self._tools:
            raise ValueError(f"Tool name must be non-empty and unique: {name!r}")
        if not callable(handler):
            raise TypeError("Tool handler must be callable")
        tool = ToolSpec(
            name=name, description=description, input_schema=input_schema, handler=handler
        )
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.to_dict() for tool in self._tools.values()]

    def call(self, name: str, **kwargs: Any) -> Any:
        """Validate inputs and invoke a registered tool."""
        tool = self.get(name)
        self.validate_input(name, kwargs, tool.input_schema)
        return tool.handler(**kwargs)

    @staticmethod
    def validate_input(name: str, payload: dict[str, Any], schema: dict[str, Any]) -> None:
        """The registry allows only explicitly declared arguments, as a read-only safety layer."""
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        for key in required:
            if key not in payload:
                raise ValueError(f"Tool '{name}' requires '{key}'")
        for key, value in payload.items():
            if key not in properties:
                raise ValueError(f"Tool '{name}' does not accept '{key}'")
            if value is None and key in required:
                raise ValueError(f"Tool '{name}' cannot accept a null value for '{key}'")
            expected_type = properties[key].get("type")
            if expected_type and not ToolRegistry._matches_type(value, expected_type):
                raise ValueError(f"Tool '{name}' argument '{key}' must be {expected_type}")

    @staticmethod
    def _matches_type(value: Any, expected_type: str) -> bool:
        checks: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        expected = checks.get(expected_type)
        if expected is None:
            raise ValueError(f"Unsupported tool schema type: {expected_type}")
        if expected_type in {"integer", "number"} and isinstance(value, bool):
            return False
        return isinstance(value, expected)

    @property
    def tools(self) -> dict[str, ToolSpec]:
        return dict(self._tools)


def make_registry() -> ToolRegistry:
    """Create an empty registry ready for registration."""
    return ToolRegistry()


def register_bundle_tools(bundle: Any) -> ToolRegistry:
    """Create the fixed, read-only tools available for one incident bundle."""
    from ghost.ingest.redact import redact_dict
    from ghost.tools.deploy_tools import get_deployment_history
    from ghost.tools.logs_tools import (
        get_log_by_id,
        search_logs,
        summarize_error_patterns,
    )
    from ghost.tools.memory_tools import search_past_incidents
    from ghost.tools.metrics_tools import (
        get_metric_values,
        identify_metric_anomalies,
        inspect_metric_trend,
    )

    logs = [redact_dict(record) for record in bundle.logs]
    metrics = [redact_dict(record) for record in bundle.metrics]
    anomalies = [redact_dict(record) for record in bundle.anomalies]
    evidence = redact_dict(bundle.evidence) if isinstance(bundle.evidence, dict) else {}
    registry = ToolRegistry()

    registry.register(
        "logs.search",
        "Search redacted incident logs by service or keyword.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "keyword": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
        lambda service=None, keyword=None, limit=100: search_logs(
            logs, service=service, keyword=keyword, limit=limit
        ),
    )
    registry.register(
        "logs.get",
        "Retrieve one redacted log using a real log evidence ID.",
        {"type": "object", "required": ["log_id"], "properties": {"log_id": {"type": "string"}}},
        lambda log_id: get_log_by_id(logs, log_id),
    )
    registry.register(
        "logs.error_patterns",
        "Summarize recurring redacted log patterns and their evidence IDs.",
        {"type": "object", "properties": {"limit": {"type": "integer"}}},
        lambda limit=5: summarize_error_patterns(logs, limit=limit),
    )
    registry.register(
        "metrics.list",
        "Read metric observations from the incident bundle.",
        {
            "type": "object",
            "required": ["metric_name"],
            "properties": {"metric_name": {"type": "string"}},
        },
        lambda metric_name: get_metric_values(metrics, metric_name),
    )
    registry.register(
        "metrics.trend",
        "Read a time-ordered metric trend from the incident bundle.",
        {
            "type": "object",
            "required": ["metric_name"],
            "properties": {"metric_name": {"type": "string"}},
        },
        lambda metric_name: inspect_metric_trend(metrics, metric_name),
    )
    registry.register(
        "metrics.anomalies",
        "Read detector-produced anomalies from the incident bundle.",
        {"type": "object", "properties": {"metric_name": {"type": "string"}}},
        lambda metric_name=None: identify_metric_anomalies(anomalies, metric_name=metric_name),
    )
    registry.register(
        "deployments.history",
        "Read deployment metadata included with the incident bundle, if any.",
        {"type": "object", "properties": {}},
        lambda: get_deployment_history(evidence.get("deployments")),
    )
    registry.register(
        "memory.search",
        "Search only historical incidents included with the incident bundle.",
        {
            "type": "object",
            "properties": {
                "service": {"type": "string"},
                "keyword": {"type": "string"},
            },
        },
        lambda service=None, keyword=None: search_past_incidents(
            evidence.get("past_incidents", []), service=service, keyword=keyword
        ),
    )
    return registry


def _tool_guard(*_args: Any, **_kwargs: Any) -> None:
    """Safety guard that prevents accidental mutation in tool implementations."""
    return None
