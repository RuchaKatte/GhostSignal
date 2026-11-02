# GhostSignal

GhostSignal is an incident investigation service that combines Alertmanager alerts,
Loki logs, Prometheus metrics, deterministic evidence verification, and a local
Ollama model. Observability and LLM HTTP calls are asynchronous; external services
are not required by the unit test suite.

## Architecture

1. `POST /webhook/alert` validates Alertmanager payloads and schedules each firing
   alert for investigation. Alerts with the same fingerprint reuse their existing
   investigation record.
2. The ingestion layer collects logs from Loki and the supported service metrics
   from Prometheus over a 15-minute lookback window. It normalizes timestamps and
   numeric samples, assigns stable log and metric evidence IDs, redacts log text,
   clusters logs, detects metric anomalies, and records missing or invalid data as
   bundle warnings.
3. The Incident Bundle contains alert metadata, redacted log observations, metric
   observations, anomalies, collection metadata, warnings, and evidence ID indexes.
4. Investigation agents use a fixed, read-only tool registry to inspect bundle
   evidence. They do not execute shell commands, make arbitrary HTTP requests, or
   modify infrastructure.
5. The ledger validates each model-proposed evidence ID against the bundle. Claims
   with no valid supporting IDs are **Unsupported**; those supported by one evidence
   source are **Plausible**; a claim supported by both log and metric evidence can
   be **Established**. Invalid references are removed and reported.
6. The structured bundle and report are saved to SQLite and exposed through the
   existing investigation API.

Default metric collection is configured for `gateway`, `orders-api`, and
`payments-worker`. Other services are still investigated using logs and alert
metadata; the bundle includes a warning when no metric queries are configured.
Deployment and historical-incident tools only return data explicitly supplied in a
bundle; there is no automatic deployment database or historical RAG integration.

## Run with Docker Compose

```shell
docker compose up -d --build
docker compose exec ollama ollama pull qwen2.5:3b
```

`OLLAMA_DEFAULT_MODEL` defaults to the value in `ghost/config.py` and can be
overridden in the environment; pull that selected model if you change the default.
The Compose service is available at
`http://localhost:8000`; Ollama, Loki, and Prometheus are available on their
respective mapped ports. The model pull is a one-time step per Ollama data volume.
The Alertmanager webhook uses a bearer token. Set `ALERT_WEBHOOK_SECRET` and keep
the `credentials` value in `observability/alertmanager/alertmanager.yml` identical;
the checked-in `changeme` value is only for local development.

## Run the backend locally

Start Ollama, Loki, and Prometheus separately, then configure the backend URLs and
model. Example PowerShell configuration:

```powershell
$env:OLLAMA_HOST = "http://localhost:11434"
$env:OLLAMA_DEFAULT_MODEL = "<installed-model-name>"
$env:LOKI_URL = "http://localhost:3100"
$env:PROMETHEUS_URL = "http://localhost:9090"
$env:GHOST_DB_PATH = ".\ghost.db"
python -m uvicorn ghost.api.main:app --host 0.0.0.0 --port 8000
```

Configuration is loaded by `ghost.config.get_settings()` from environment
variables and `.env`. Relevant settings include:

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | Ollama HTTP base URL |
| `OLLAMA_DEFAULT_MODEL` | Model used for investigation generation |
| `OLLAMA_TIMEOUT_SECONDS` | Per-generation request timeout |
| `LOKI_URL` | Loki HTTP base URL |
| `PROMETHEUS_URL` | Prometheus HTTP base URL |
| `GHOST_DB_PATH` | SQLite database path |
| `ALERT_WEBHOOK_SECRET` | Bearer token required for Alertmanager webhooks |

No API key is needed for local Ollama. The LLM client does not select or download a
model on its own; the configured model must already be available in Ollama.

## Trigger and inspect an investigation

Send an Alertmanager-compatible firing alert:

```shell
curl.exe -X POST http://localhost:8000/webhook/alert `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer changeme" `
  -d "{\"status\":\"firing\",\"alerts\":[{\"status\":\"firing\",\"labels\":{\"alertname\":\"HighErrorRate\",\"severity\":\"warning\",\"service\":\"gateway\"},\"annotations\":{\"summary\":\"Gateway error rate is high\"},\"startsAt\":\"2026-10-31T12:00:00Z\",\"endsAt\":\"0001-01-01T00:00:00Z\",\"fingerprint\":\"demo-high-error-rate\"}]}"
```

The endpoint responds with `202 Accepted`; collection and investigation run as a
background task. List investigations at `GET /api/investigations`, then retrieve
the record, saved bundle, and report at `GET /api/investigations/{id}`. Redacted
individual evidence is available at `GET /api/investigations/{id}/evidence`, and
completed incident memories at `GET /api/ofrenda`. `/health` reports backend health.

## Testing and linting

```shell
python -m pytest -q
python -m ruff check ghost tests
```

Tests mock Ollama and observability HTTP calls. No running Prometheus, Loki, or LLM
is needed for ordinary unit tests.
