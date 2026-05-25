-- NeymarGoldTrader Telegram signal tables.
-- Idempotent: safe to run on every container start.

CREATE TABLE IF NOT EXISTS tg_signals (
    msg_id        BIGINT       PRIMARY KEY,
    channel       TEXT         NOT NULL,
    instrument    TEXT         NOT NULL,
    side          TEXT         NOT NULL CHECK (side IN ('buy', 'sell')),
    entry_low     NUMERIC(12,3),
    entry_high    NUMERIC(12,3),
    entry_mid     NUMERIC(12,3),
    sl            NUMERIC(12,3),
    tps           NUMERIC(12,3)[],
    risk_pts      NUMERIC(12,3),
    total_volume  NUMERIC(12,3),
    status        TEXT         NOT NULL DEFAULT 'submitted',
    close_reason  TEXT,
    raw           TEXT,
    dry_run       BOOLEAN      NOT NULL DEFAULT FALSE,
    posted_at     TIMESTAMPTZ,
    opened_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tg_signals_opened_at ON tg_signals (opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_tg_signals_status    ON tg_signals (status);

CREATE TABLE IF NOT EXISTS tg_orders (
    ticket_id    TEXT         PRIMARY KEY,
    msg_id       BIGINT       NOT NULL REFERENCES tg_signals(msg_id) ON DELETE CASCADE,
    tp_index     INT          NOT NULL,
    kind         TEXT         NOT NULL CHECK (kind IN ('market', 'limit')),
    volume       NUMERIC(12,3) NOT NULL,
    entry        NUMERIC(12,3) NOT NULL,
    sl           NUMERIC(12,3) NOT NULL,
    tp           NUMERIC(12,3) NOT NULL,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tg_orders_msg_id ON tg_orders (msg_id);

CREATE TABLE IF NOT EXISTS tg_signal_updates (
    id        BIGSERIAL    PRIMARY KEY,
    msg_id    BIGINT       NOT NULL REFERENCES tg_signals(msg_id) ON DELETE CASCADE,
    type      TEXT         NOT NULL,
    payload   JSONB,
    at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tg_signal_updates_msg_id ON tg_signal_updates (msg_id, at DESC);
