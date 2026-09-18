"""Both copy-traders share the tg_* tables (no TG_DB_PREFIX on the box), so a
bot must hydrate only ITS channel's open signals on boot. Live 2026-09-18
09:57: the VIP bot restarted, loaded the free channel's open signal 14117,
mirrored its runner leg into the 'Neymar VIP' dashboard strategy as a fresh
row and started reconciling another bot's trade."""
import contextlib

import db_persist as dbp


class _Cur:
    def __init__(self):
        self.sql, self.params = [], []

    def execute(self, sql, params=None):
        # psycopg2 %-formats the statement whenever params is not None, so a
        # bare LIKE wildcard raises exactly as it did live (10:04:56 UTC).
        if params is not None:
            sql = sql % tuple("'%s'" % v for v in params)
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)

    def fetchall(self):
        return []


def _patch_connect(monkeypatch, cur):
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


def test_load_open_signals_is_scoped_to_the_bots_channel(monkeypatch):
    cur = _Cur(); _patch_connect(monkeypatch, cur)
    dbp.load_open_signals(channel="-1002776523643")
    assert "channel = '-1002776523643'" in cur.sql[0], cur.sql[0]
    assert "NOT LIKE 'closed_%'" in cur.sql[0], cur.sql[0]
    assert cur.params[0] == ("-1002776523643",)


def test_load_open_signals_without_channel_keeps_old_behaviour(monkeypatch):
    cur = _Cur(); _patch_connect(monkeypatch, cur)
    dbp.load_open_signals()
    assert "channel" not in cur.sql[0]
    assert "NOT LIKE 'closed_%'" in cur.sql[0], cur.sql[0]
