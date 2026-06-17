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

-- Broker fill/PnL tracking (added 2026-06-05). The trader used to learn outcomes
-- only from the channel's text replies; these columns hold the broker truth that
-- the reconciliation poller writes: which slices actually filled, at what price,
-- and the realized PnL (estimated from the last live snapshot before close, since
-- this account does not expose REST deal history). Idempotent ADD COLUMN IF NOT
-- EXISTS so existing deployments upgrade in place.
ALTER TABLE tg_signals ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(14,4);
ALTER TABLE tg_orders  ADD COLUMN IF NOT EXISTS broker_state TEXT;        -- pending|filled|closed|cancelled
ALTER TABLE tg_orders  ADD COLUMN IF NOT EXISTS fill_price   NUMERIC(12,3);
ALTER TABLE tg_orders  ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(14,4);
ALTER TABLE tg_orders  ADD COLUMN IF NOT EXISTS closed_at    TIMESTAMPTZ;

-- Copy-trade fan-out (added 2026-06-17): one signal is mirrored onto more than
-- one MetaAPI account in a single process, so each TP slice records which account
-- it was placed on. Existing rows default to 'primary'. Reconciliation routes each
-- slice to its own account's broker state by this column.
ALTER TABLE tg_orders  ADD COLUMN IF NOT EXISTS account TEXT NOT NULL DEFAULT 'primary';
