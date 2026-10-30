-- Ghost Demo Database Schema

CREATE TABLE IF NOT EXISTS orders (
    id          TEXT PRIMARY KEY,
    item        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT ''pending'',
    request_id  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id          TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL,
    amount      NUMERIC(10,2) DEFAULT 0.00,
    status      TEXT NOT NULL DEFAULT ''pending'',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deploy_events (
    id          SERIAL PRIMARY KEY,
    service     TEXT NOT NULL,
    version     TEXT NOT NULL,
    deployed_by TEXT DEFAULT ''ci'',
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed a baseline deploy event
INSERT INTO deploy_events (service, version, notes)
VALUES
    (''gateway'', ''v1.0.0'', ''Initial deployment''),
    (''orders-api'', ''v1.0.0'', ''Initial deployment''),
    (''payments-worker'', ''v1.0.0'', ''Initial deployment'');
