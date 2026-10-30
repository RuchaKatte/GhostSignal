"""
Prometheus metrics for the Ghost investigator itself.
Tracks agent performance, tool usage, and investigation outcomes.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, Summary

# ── Investigation metrics ──────────────────────────────────────────────────────
INVESTIGATIONS_TOTAL = Counter(
    "ghost_investigations_total",
    "Total investigations started",
    ["trigger", "scenario"],
)

INVESTIGATION_DURATION = Histogram(
    "ghost_investigation_duration_seconds",
    "Time to complete an investigation",
    buckets=[10, 30, 60, 120, 300, 600],
)

INVESTIGATIONS_IN_PROGRESS = Gauge(
    "ghost_investigations_in_progress",
    "Investigations currently running",
)

# ── Token metrics ──────────────────────────────────────────────────────────────
TOKENS_TOTAL = Counter(
    "ghost_tokens_total",
    "Total tokens used",
    ["agent", "direction"],  # direction: prompt | completion
)

# ── Agent step metrics ─────────────────────────────────────────────────────────
AGENT_STEPS_TOTAL = Counter(
    "ghost_agent_steps_total",
    "Total agent steps executed",
    ["agent"],
)

AGENT_STEP_LATENCY = Histogram(
    "ghost_agent_step_duration_seconds",
    "Agent step execution time",
    ["agent"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)

# ── Tool metrics ───────────────────────────────────────────────────────────────
TOOL_CALLS_TOTAL = Counter(
    "ghost_tool_calls_total",
    "Total tool calls made by agents",
    ["tool_name", "agent"],
)

TOOL_CALL_LATENCY = Histogram(
    "ghost_tool_call_duration_seconds",
    "Tool call execution time",
    ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 5],
)

# ── Verifier metrics ───────────────────────────────────────────────────────────
VERIFIER_REJECTIONS_TOTAL = Counter(
    "ghost_verifier_rejections_total",
    "Claims rejected by verifier (fabricated IDs)",
)

CLAIMS_BY_STATUS = Counter(
    "ghost_claims_by_status_total",
    "Claims by final status",
    ["status"],  # Established | Plausible | Unsupported
)

# ── LLM metrics ────────────────────────────────────────────────────────────────
LLM_REQUESTS_TOTAL = Counter(
    "ghost_llm_requests_total",
    "Total LLM requests",
    ["model", "agent"],
)

LLM_ERRORS_TOTAL = Counter(
    "ghost_llm_errors_total",
    "LLM request errors",
    ["error_type"],
)

LLM_LATENCY = Histogram(
    "ghost_llm_request_duration_seconds",
    "LLM request latency",
    ["model"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
)
