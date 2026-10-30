FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir \
    fastapi uvicorn[standard] httpx pydantic pydantic-settings \
    sqlalchemy aiosqlite drain3 scikit-learn numpy chromadb rank-bm25 \
    prometheus-client structlog python-dotenv rich typer sse-starlette \
    anyio asyncpg psycopg2-binary pyyaml click requests aiofiles jinja2

# Copy the application
COPY ghost/ ghost/
COPY ui/ ui/
COPY eval/ eval/

# Create data directory
RUN mkdir -p /data

ENV GHOST_DB_PATH=/data/ghost.db
ENV CHROMA_PATH=/data/chroma

EXPOSE 8000
CMD ["uvicorn", "ghost.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
