"""
apis_persist must support more than one dashboard target in a single process so
fan-out can record each account's trades under its OWN platform strategy/broker.

ApisDashboard(user_strategy_id, user_broker_id, currencypair_id, ...) writes rows
under those ids; the module-level functions stay as a default instance (primary),
so the single-account path is unchanged.
"""
import apis_persist as apis


class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append((sql, tuple(params) if params else ()))

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return _FakeCursor(self._sink)


def _params_blob(sink):
    return [p for _sql, params in sink for p in params]


def test_open_position_writes_under_instance_ids(monkeypatch):
    d = apis.ApisDashboard(user_strategy_id="US2", user_broker_id="UB2",
                           currencypair_id="CP2", enabled=True, label="neymar2")
    sink = []
    monkeypatch.setattr(d, "_connect", lambda: _FakeConn(sink))
    pid = d.open_position("buy", 2000.0, 0.02, "TICKET1")
    assert pid is not None
    blob = _params_blob(sink)
    assert "US2" in blob           # apis_position.user_strategy_id
    assert "CP2" in blob           # apis_position.currencypair_id
    assert "UB2" in blob           # apis_order.user_broker_id


def test_two_dashboards_are_independent(monkeypatch):
    a = apis.ApisDashboard("US_A", "UB_A", "CP", enabled=True, label="primary")
    b = apis.ApisDashboard("US_B", "UB_B", "CP", enabled=True, label="neymar2")
    sa, sb = [], []
    monkeypatch.setattr(a, "_connect", lambda: _FakeConn(sa))
    monkeypatch.setattr(b, "_connect", lambda: _FakeConn(sb))
    a.open_position("buy", 2000.0, 0.01)
    b.open_position("sell", 2000.0, 0.01)
    assert "US_A" in _params_blob(sa) and "US_A" not in _params_blob(sb)
    assert "US_B" in _params_blob(sb) and "US_B" not in _params_blob(sa)


def test_disabled_dashboard_is_noop():
    d = apis.ApisDashboard("US", "UB", "CP", enabled=False)
    assert d.open_position("buy", 2000.0, 0.01) is None
    assert d.find_open_position_id() is None


def test_pnl_cash_conversion_uses_contract_size():
    d = apis.ApisDashboard("US", "UB", "CP", enabled=True, contract_size=100)
    assert d._to_cash(100.0) == 1.0
    assert d._to_cash(None) is None


def test_module_default_api_still_present():
    for name in ("open_position", "update_live", "conclude_position", "find_open_position_id"):
        assert callable(getattr(apis, name)), name
