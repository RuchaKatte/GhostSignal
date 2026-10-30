"""
SQLite database initialization and async session management.
All tables used by the Ghost investigator are created here on startup.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ghost.config import get_settings

_engine = None
_async_session = None


def _get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        db_path = settings.ghost_db_path
        dir_ = os.path.dirname(db_path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        _engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def _get_session_factory():
    global _async_session
    if _async_session is None:
        _async_session = sessionmaker(_get_engine(), class_=AsyncSession, expire_on_commit=False)
    return _async_session


_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS investigations (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        alertname   TEXT NOT NULL,
        severity    TEXT DEFAULT 'warning',
        service     TEXT DEFAULT '',
        summary     TEXT DEFAULT '',
        alert_time  TEXT,
        status      TEXT DEFAULT 'pending',
        root_cause  TEXT,
        narrative   TEXT,
        voice       TEXT DEFAULT 'ghost',
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bundles (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        investigation_id INTEGER NOT NULL,
        window_start    TEXT NOT NULL,
        window_end      TEXT NOT NULL,
        log_count       INTEGER DEFAULT 0,
        cluster_count   INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (investigation_id) REFERENCES investigations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS log_lines (
        id              TEXT PRIMARY KEY,
        bundle_id       INTEGER NOT NULL,
        service         TEXT,
        level           TEXT,
        timestamp_ns    INTEGER,
        message         TEXT,
        raw_json        TEXT,
        cluster_id      TEXT,
        FOREIGN KEY (bundle_id) REFERENCES bundles(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS log_clusters (
        id              TEXT PRIMARY KEY,
        bundle_id       INTEGER NOT NULL,
        template        TEXT,
        count           INTEGER DEFAULT 0,
        first_seen      TEXT,
        last_seen       TEXT,
        services        TEXT,
        sample_ids      TEXT,
        FOREIGN KEY (bundle_id) REFERENCES bundles(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metric_anomalies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        bundle_id       INTEGER NOT NULL,
        metric_name     TEXT,
        onset_time      TEXT,
        peak_value      REAL,
        z_score         REAL,
        anomaly_type    TEXT,
        FOREIGN KEY (bundle_id) REFERENCES bundles(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS claims (
        id              TEXT PRIMARY KEY,
        investigation_id INTEGER NOT NULL,
        text            TEXT NOT NULL,
        status          TEXT DEFAULT 'Plausible',
        confidence      REAL DEFAULT 0.5,
        evidence_ids    TEXT DEFAULT '[]',
        counter_ids     TEXT DEFAULT '[]',
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (investigation_id) REFERENCES investigations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        investigation_id INTEGER NOT NULL,
        verdict         TEXT,
        note            TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (investigation_id) REFERENCES investigations(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ofrenda (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        investigation_id INTEGER NOT NULL,
        root_cause      TEXT,
        summary         TEXT,
        key_patterns    TEXT,
        resolution      TEXT,
        chroma_id       TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (investigation_id) REFERENCES investigations(id)
    )
    """,
]


async def init_db() -> None:
    """Create all tables if they do not exist."""
    engine = _get_engine()
    async with engine.begin() as conn:
        for stmt in _SCHEMA:
            await conn.execute(text(stmt))


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async SQLite session."""
    session_factory = _get_session_factory()
    async with session_factory() as session:
        yield session
