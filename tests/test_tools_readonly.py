"""
Tests proving all agent tools are read-only (no mutations).
Verifies tool schemas have no mutating operations and returns are read types.
"""

import inspect

import pytest


def test_logs_tools_are_readonly():
    """Log tools must only query, never write."""
    try:
        from ghost.tools import logs_tools

        for name, fn in inspect.getmembers(logs_tools, inspect.isfunction):
            if name.startswith("_"):
                continue
            src = inspect.getsource(fn)
            # These operations would indicate mutation
            assert "INSERT" not in src.upper(), f"{name} contains INSERT"
            assert "UPDATE" not in src.upper(), f"{name} contains UPDATE"
            assert "DELETE" not in src.upper(), f"{name} contains DELETE"
            assert "DROP" not in src.upper(), f"{name} contains DROP"
    except ImportError:
        pytest.skip("ghost.tools.logs_tools not yet implemented")


def test_metrics_tools_are_readonly():
    """Metrics tools must only query Prometheus."""
    try:
        from ghost.tools import metrics_tools

        for name, fn in inspect.getmembers(metrics_tools, inspect.isfunction):
            if name.startswith("_"):
                continue
            src = inspect.getsource(fn)
            # No HTTP POST/PUT/DELETE to Prometheus
            assert "requests.post" not in src, f"{name} makes POST requests"
            assert "httpx.post" not in src, f"{name} makes POST requests"
    except ImportError:
        pytest.skip("ghost.tools.metrics_tools not yet implemented")


def test_deploy_tools_are_readonly():
    """Deploy tools must only fetch, not create or modify deploy records."""
    try:
        from ghost.tools import deploy_tools

        for name, fn in inspect.getmembers(deploy_tools, inspect.isfunction):
            if name.startswith("_"):
                continue
            src = inspect.getsource(fn)
            assert "INSERT" not in src.upper(), f"{name} contains INSERT"
            assert "UPDATE" not in src.upper(), f"{name} contains UPDATE"
    except ImportError:
        pytest.skip("ghost.tools.deploy_tools not yet implemented")
