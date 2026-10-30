"""
Ghost configuration - loaded from environment variables via pydantic-settings.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Ollama
    ollama_host: str = Field("http://ollama:11434", alias="OLLAMA_HOST")
    ollama_default_model: str = Field("qwen2.5:7b", alias="OLLAMA_DEFAULT_MODEL")
    ollama_embed_model: str = Field("nomic-embed-text", alias="OLLAMA_EMBED_MODEL")

    # Loki
    loki_url: str = Field("http://loki:3100", alias="LOKI_URL")

    # Prometheus
    prometheus_url: str = Field("http://prometheus:9090", alias="PROMETHEUS_URL")

    # Ghost service
    ghost_host: str = Field("0.0.0.0", alias="GHOST_HOST")
    ghost_port: int = Field(8000, alias="GHOST_PORT")
    ghost_debug: bool = Field(False, alias="GHOST_DEBUG")

    # Storage
    ghost_db_path: str = Field("/data/ghost.db", alias="GHOST_DB_PATH")
    chroma_path: str = Field("/data/chroma", alias="CHROMA_PATH")

    # Postgres (for deploy event queries)
    postgres_host: str = Field("postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(5432, alias="POSTGRES_PORT")
    postgres_db: str = Field("ghostdemo", alias="POSTGRES_DB")
    postgres_user: str = Field("ghost", alias="POSTGRES_USER")
    postgres_password: str = Field("ghostpass", alias="POSTGRES_PASSWORD")

    # Misc
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    alert_webhook_secret: str = Field("changeme", alias="ALERT_WEBHOOK_SECRET")


@lru_cache
def get_settings() -> Settings:
    return Settings()
