"""
Chaos Injector - triggers named failure scenarios against the demo system.
Each scenario is defined in scenarios/<name>.yaml with a ground_truth block.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import yaml
import click
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger(service="chaos-injector")

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8001")
ORDERS_URL = os.getenv("ORDERS_API_URL", "http://orders-api:8002")
PAYMENTS_URL = os.getenv("PAYMENTS_WORKER_URL", "http://payments-worker:8003")

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def load_scenario(name: str) -> dict:
    path = SCENARIOS_DIR / f"{name}.yaml"
    if not path.exists():
        available = [f.stem for f in SCENARIOS_DIR.glob("*.yaml")]
        log.error("scenario_not_found", name=name, available=available)
        sys.exit(1)
    with path.open() as f:
        return yaml.safe_load(f)


def apply_chaos(scenario: dict) -> None:
    """Push chaos config to each affected service."""
    actions = scenario.get("actions", [])
    for action in actions:
        service = action.get("service")
        config = action.get("config", {})
        url_map = {
            "gateway": GATEWAY_URL,
            "orders-api": ORDERS_URL,
            "payments-worker": PAYMENTS_URL,
        }
        base = url_map.get(service)
        if not base:
            log.warning("unknown_service", service=service)
            continue
        try:
            r = httpx.post(f"{base}/_chaos/set", json=config, timeout=5.0)
            log.info("chaos_applied", service=service, status=r.status_code, config=config)
        except Exception as exc:
            log.error("chaos_apply_failed", service=service, error=str(exc))

    # Post a deploy event if the scenario includes one
    deploy = scenario.get("deploy_event")
    if deploy:
        try:
            r = httpx.post(f"{ORDERS_URL}/_events/deploy", json=deploy, timeout=5.0)
            log.info("deploy_event_posted", deploy=deploy, status=r.status_code)
        except Exception as exc:
            log.error("deploy_event_failed", error=str(exc))


def clear_chaos() -> None:
    """Clear chaos from all services."""
    for name, url in [("gateway", GATEWAY_URL), ("orders-api", ORDERS_URL),
                      ("payments-worker", PAYMENTS_URL)]:
        try:
            r = httpx.post(f"{url}/_chaos/clear", timeout=5.0)
            log.info("chaos_cleared", service=name, status=r.status_code)
        except Exception as exc:
            log.error("clear_failed", service=name, error=str(exc))


@click.command()
@click.option("--scenario", "-s", default=None, help="Scenario name to inject")
@click.option("--stop", is_flag=True, help="Stop all chaos")
@click.option("--duration", "-d", default=None, type=int,
              help="Auto-stop after N seconds (optional)")
def main(scenario: str | None, stop: bool, duration: int | None) -> None:
    if stop:
        log.info("stopping_all_chaos")
        clear_chaos()
        return

    if not scenario:
        available = [f.stem for f in SCENARIOS_DIR.glob("*.yaml")]
        log.error("no_scenario_specified", available=available)
        sys.exit(1)

    data = load_scenario(scenario)
    log.warning(
        "injecting_chaos",
        scenario=scenario,
        description=data.get("description", ""),
        ground_truth=data.get("ground_truth", {}).get("root_cause_category", "unknown"),
    )

    apply_chaos(data)

    if duration:
        log.info("chaos_will_auto_stop", seconds=duration)
        time.sleep(duration)
        clear_chaos()
        log.info("chaos_stopped_after_duration", seconds=duration)
    else:
        log.warning("chaos_running_indefinitely", hint="run with --stop to clear")


if __name__ == "__main__":
    main()
