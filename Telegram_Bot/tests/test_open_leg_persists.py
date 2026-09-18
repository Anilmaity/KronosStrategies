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
