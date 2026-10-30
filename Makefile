.PHONY: up down chaos eval demo logs ps build clean test lint

# ── Stack control ──────────────────────────────────────────────────────────────
up:
	docker compose up -d --build
	@echo "Stack is rising from the dead..."
	@echo "  Ghost UI:          http://localhost:8000"
	@echo "  Prometheus:        http://localhost:9090"
	@echo "  Alertmanager:      http://localhost:9093"
	@echo "  Loki (API):        http://localhost:3100"

down:
	docker compose down -v

ps:
	docker compose ps

logs:
	docker compose logs -f $(SERVICE)

build:
	docker compose build

# ── Chaos injection ───────────────────────────────────────────────────────────
# Usage: make chaos SCENARIO=conn_leak
SCENARIO ?= conn_leak
chaos:
	docker compose exec chaos python /app/injector.py --scenario $(SCENARIO)

chaos-stop:
	docker compose exec chaos python /app/injector.py --stop

# ── Development ───────────────────────────────────────────────────────────────
install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

lint:
	ruff check ghost/ tests/
	ruff format --check ghost/ tests/

fmt:
	ruff format ghost/ tests/
	ruff check --fix ghost/ tests/

# ── Evaluation ────────────────────────────────────────────────────────────────
eval:
	docker compose exec ghost python /app/eval/run_eval.py
	@echo "Results written to eval/results/"

# ── Demo helper ───────────────────────────────────────────────────────────────
demo:
	@echo "Starting demo: injecting conn_leak in 5 seconds..."
	sleep 5
	$(MAKE) chaos SCENARIO=conn_leak

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
