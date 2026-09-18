"""The VIP "TP: open" runner leg (tp=None) must not break persistence.

Live 2026-08-23..09-18: tg_orders.tp was NOT NULL, so insert_signal -- one
transaction for the signal and all its legs -- aborted on the runner leg for
every VIP signal ending "TP: open" (309/315 of them). 39 VIP signals were
PLACED (Signals tab) but only the 2 without an open leg reached tg_signals;
every later record_update / record_slice_close FK-failed and a restart could
not hydrate them (6975 today: 5 legs at the broker, nothing in the ledger).
"""
import re
from pathlib import Path

import live_trader as lt

_DDL = (Path(__file__).resolve().parent.parent / "init_schema.sql").read_text(encoding="utf-8")


def test_schema_lets_a_runner_leg_store_a_null_tp():
    # the CREATE keeps NOT NULL for fresh installs' history; the idempotent
    # ALTER that follows must relax it on every start.
    assert re.search(r"ALTER TABLE tg_orders\s+ALTER COLUMN tp\s+DROP NOT NULL", _DDL), \
        "tg_orders.tp must be nullable so the TP: open leg can be persisted"


def test_close_reason_handles_runner_leg_without_tp():
    # profit known -> sign decides, tp irrelevant
    assert lt._infer_close_reason(4380.0, 6.67, None, 4395.0) == "tp"
    assert lt._infer_close_reason(4394.9, -2.0, None, 4395.0) == "sl"
    # profit unknown, no tp: only the stop can be recognised
    assert lt._infer_close_reason(4394.95, None, None, 4395.0) == "sl"
    assert lt._infer_close_reason(4380.0, None, None, 4395.0) == "tp"
    assert lt._infer_close_reason(None, None, None, 4395.0) == "tp"


def test_hydration_accepts_a_runner_leg_with_null_tp(monkeypatch):
    import contextlib
    import db_persist as dbp

    class _Cur:
        def __init__(self):
            self.calls = 0

        def execute(self, sql, params=None):
            self.calls += 1

        def fetchall(self):
            if self.calls == 1:   # signals
                return [(6975, "XAUUSD", "sell", 4387.0, 4391.0, 4387.0, 4395.0,
                         [4385.0, 4383.0, 4381.0, 4379.0], 8.0, 0.05, "submitted", "", False, None, None)]
            return [(6975, "119216061", 5, "market", 0.01, 4387.48, 4387.0, None, "filled", 4387.48, "primary")]

    cur = _Cur()

    @contextlib.contextmanager
    def _conn():
        class _C:
            def cursor(self_inner):
                @contextlib.contextmanager
                def _c():
                    yield cur
                return _c()
        yield _C()
    monkeypatch.setattr(dbp, "_connect", _conn)

    pos = dbp.load_open_signals(channel="-1002776523643")
    assert len(pos) == 1 and pos[0]["orders"][0]["tp"] is None, \
        "a leg stored with tp NULL must hydrate, not abort the whole load"
