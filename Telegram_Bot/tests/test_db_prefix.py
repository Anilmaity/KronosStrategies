"""
Per-instance table isolation for the Telegram trader.

A second copy of the trader mirrors the SAME channel (identical message ids) to a
DIFFERENT MetaAPI account. Since tg_signals.msg_id is the primary key, both copies
sharing one table would overwrite each other's rows and merge both accounts' broker
tickets under one signal — corrupting restart-hydration and broker reconciliation.

TG_DB_PREFIX gives each instance its own tg_* tables. The default ("tg") MUST keep
the existing live instance byte-for-byte unchanged.
"""
import importlib

import pytest


def _reload(monkeypatch, prefix):
    if prefix is None:
        monkeypatch.delenv("TG_DB_PREFIX", raising=False)
    else:
        monkeypatch.setenv("TG_DB_PREFIX", prefix)
    import db_persist
    return importlib.reload(db_persist)


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append(sql)

    def fetchall(self):
        return []


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._sink)


_SAMPLE_POS = {
    "msg_id": 555, "instrument": "XAUUSD", "side": "buy",
    "entry_lo": 2000.0, "entry_hi": 2001.0, "entry_mid": 2000.5,
    "sl": 1995.0, "tps": [2005.0, 2010.0], "risk_pts": 5.5,
    "total_volume": 0.04, "status": "submitted", "raw": "x",
    "dry_run": False, "posted_at": None, "opened_at": None,
    "orders": [
        {"tp_index": 1, "ticket_id": "T1", "kind": "limit",
         "volume": 0.02, "entry": 2000.5, "sl": 1995.0, "tp": 2005.0},
    ],
}


def test_default_prefix_keeps_existing_table_names(monkeypatch):
    mod = _reload(monkeypatch, None)
    assert mod._T_SIGNALS == "tg_signals"
    assert mod._T_ORDERS == "tg_orders"
    assert mod._T_UPDATES == "tg_signal_updates"


def test_custom_prefix_namespaces_table_names(monkeypatch):
    mod = _reload(monkeypatch, "tg_neymar2")
    assert mod._T_SIGNALS == "tg_neymar2_signals"
    assert mod._T_ORDERS == "tg_neymar2_orders"
    assert mod._T_UPDATES == "tg_neymar2_signal_updates"


def test_render_ddl_default_is_byte_for_byte_unchanged(monkeypatch):
    mod = _reload(monkeypatch, None)
    raw = mod._SCHEMA_PATH.read_text(encoding="utf-8")
    assert mod._render_ddl(raw, "tg") == raw


def test_render_ddl_custom_prefixes_tables_and_fks(monkeypatch):
    mod = _reload(monkeypatch, "tg_neymar2")
    raw = mod._SCHEMA_PATH.read_text(encoding="utf-8")
    rendered = mod._render_ddl(raw, "tg_neymar2")
    assert "CREATE TABLE IF NOT EXISTS tg_neymar2_signals" in rendered
    assert "REFERENCES tg_neymar2_signals" in rendered
    # the original (unprefixed) table must not survive...
    assert "IF NOT EXISTS tg_signals" not in rendered
    # ...and the replacement must not double up.
    assert "tg_neymar2_neymar2" not in rendered


def test_insert_signal_writes_to_prefixed_tables(monkeypatch):
    mod = _reload(monkeypatch, "tg_neymar2")
    sink = []
    monkeypatch.setattr(mod, "_connect", lambda: _FakeConn(sink))
    mod.insert_signal(_SAMPLE_POS, "NeymarGoldTrader")
    joined = "\n".join(sink)
    assert "INTO tg_neymar2_signals" in joined
    assert "INTO tg_neymar2_orders" in joined
    assert "INTO tg_signals" not in joined
    assert "INTO tg_orders" not in joined


def test_invalid_prefix_is_rejected(monkeypatch):
    with pytest.raises(ValueError):
        _reload(monkeypatch, "bad-prefix!; DROP TABLE")
