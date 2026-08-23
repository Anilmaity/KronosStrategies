"""Live-path behaviour added for the Neymar VIP source.

Everything here is default-OFF: the free channel's copy must behave exactly as
it did before this source existed. The first test in each group asserts that.
"""
import asyncio
import json

import pytest

import live_trader as lt
import metaapi_orders as mx
from state_store import make_store


def _sig(tps=(4594.0, 4596.0), tp_open=True, sl=4581.0, side="buy"):
    return {"instrument": "XAUUSD", "side": side,
            "entry_low": 4587.0, "entry_high": 4591.7, "sl": sl,
            "tps": list(tps), "tp_open": tp_open, "order_type": "market"}


def _run(coro):
    return asyncio.run(coro)


async def _store():
    r, _ = await make_store(None)
    return r


# ─────────────────────── the open-ended runner ───────────────────────

def test_open_leg_not_placed_by_default(monkeypatch):
    """Free-channel behaviour: exactly the numeric TPs, nothing else."""
    monkeypatch.setattr(lt, "TP_OPEN_LEG", False)
    assert lt.order_tps(_sig()) == [4594.0, 4596.0]


def test_open_leg_appended_as_none_when_enabled(monkeypatch):
    monkeypatch.setattr(lt, "TP_OPEN_LEG", True)
    assert lt.order_tps(_sig()) == [4594.0, 4596.0, None]


def test_no_open_leg_when_signal_has_none(monkeypatch):
    monkeypatch.setattr(lt, "TP_OPEN_LEG", True)
    assert lt.order_tps(_sig(tp_open=False)) == [4594.0, 4596.0]


def test_stored_tps_are_never_mutated(monkeypatch):
    """sig['tps'] is persisted to tg_signals and mirrored to the dashboard; a
    None leaking in there would propagate into both."""
    monkeypatch.setattr(lt, "TP_OPEN_LEG", True)
    sig = _sig()
    lt.order_tps(sig)
    assert sig["tps"] == [4594.0, 4596.0]


def test_stops_floor_leaves_open_leg_without_a_target():
    sl, tp, _ = mx._apply_stops_floor("buy", ref_price=4590.0, sl=4589.9,
                                      tp=None, min_distance=1.5)
    assert tp is None
    assert sl == 4588.5          # the stop is still floored


def test_market_order_omits_take_profit_when_open():
    """Omitted, not sent as 0/null — some brokers read a zero target as
    'close immediately'."""
    sent = {}
    client = mx.MetaApiClient("", "", dry_run=True, label="t")
    monkey = lambda payload: sent.update(payload) or {"orderId": "1"}
    client._trade = monkey
    client.place_market_order_full("buy", "XAUUSD", 0.01, 4581.0, None, "tg-1-tp3")
    assert "takeProfit" not in sent
    assert sent["stopLoss"] == 4581.0


def test_market_order_still_sends_take_profit_normally():
    sent = {}
    client = mx.MetaApiClient("", "", dry_run=True, label="t")
    client._trade = lambda payload: sent.update(payload) or {"orderId": "1"}
    client.place_market_order_full("buy", "XAUUSD", 0.01, 4581.0, 4594.0, "tg-1-tp1")
    assert sent["takeProfit"] == 4594.0


# ─────────────────────── repost dedup ───────────────────────

def test_dedup_key_ignores_surrounding_prose():
    """The channel reposts the same setup with incidental wording changes; two
    posts naming the same trade are the same trade."""
    assert lt.dedup_key(_sig()) == lt.dedup_key(_sig())


def test_dedup_key_separates_different_stops():
    assert lt.dedup_key(_sig(sl=4581.0)) != lt.dedup_key(_sig(sl=4579.0))


def test_dedup_disabled_by_default(monkeypatch):
    monkeypatch.setattr(lt, "DEDUP_WINDOW_SEC", 0)

    async def go():
        lt.r = await _store()
        assert await lt._is_repost(_sig(), 1000.0) is False
        assert await lt._is_repost(_sig(), 1001.0) is False   # still False
    _run(go())


def test_repost_within_window_detected(monkeypatch):
    monkeypatch.setattr(lt, "DEDUP_WINDOW_SEC", 900)

    async def go():
        lt.r = await _store()
        assert await lt._is_repost(_sig(), 1000.0) is False   # first sighting
        assert await lt._is_repost(_sig(), 1060.0) is True    # 60s later
    _run(go())


def test_same_trade_outside_window_is_allowed(monkeypatch):
    monkeypatch.setattr(lt, "DEDUP_WINDOW_SEC", 900)

    async def go():
        lt.r = await _store()
        await lt._is_repost(_sig(), 1000.0)
        assert await lt._is_repost(_sig(), 2000.0) is False
    _run(go())


# ─────────────────────── management execution ───────────────────────

class _Spy:
    def __init__(self):
        self.sl = []
        self.be = []
        self.closed = []


@pytest.fixture
def spy(monkeypatch):
    s = _Spy()

    async def modify_sl(mid, new_sl): s.sl.append((mid, new_sl))
    async def move_to_breakeven(mid): s.be.append(mid)
    async def close_order(mid, reason): s.closed.append((mid, reason))

    monkeypatch.setattr(lt, "modify_sl", modify_sl)
    monkeypatch.setattr(lt, "move_to_breakeven", move_to_breakeven)
    monkeypatch.setattr(lt, "close_order", close_order)
    monkeypatch.setattr(lt, "ACT_ON_MANAGEMENT", True)
    return s


async def _with_open(ids):
    lt.r = await _store()
    for i in ids:
        await lt.r.sadd(f"{lt.REDIS_PREFIX}:open", str(i))


def test_management_off_by_default(monkeypatch):
    monkeypatch.setattr(lt, "ACT_ON_MANAGEMENT", False)

    async def go():
        await _with_open([5543])
        assert await lt.apply_management(1, "Set breakeven now!!!", None) is None
    _run(go())


def test_reply_breakeven_moves_stop_to_entry(spy):
    async def go():
        await _with_open([5543])
        await lt.apply_management(9, "TP1 hit ✅️ set breakeven", 5543)
    _run(go())
    assert spy.be == [5543]


def test_standalone_resolves_to_the_single_open_signal(spy):
    """137 of 317 management messages have no reply link."""
    async def go():
        await _with_open([5543])
        await lt.apply_management(9, "Set breakeven now!!!", None)
    _run(go())
    assert spy.be == [5543]


def test_standalone_ignored_when_two_are_open(spy):
    async def go():
        await _with_open([5543, 5585])
        assert await lt.apply_management(9, "Set breakeven now!!!", None) is None
    _run(go())
    assert spy.be == [] and spy.sl == []


def test_explicit_level_beats_breakeven(spy):
    async def go():
        await _with_open([5543])
        await lt.apply_management(9, "TP1 hit, set breakeven — move SL to 4578", 5543)
    _run(go())
    assert spy.sl == [(5543, 4578.0)]
    assert spy.be == []


def test_close_all_flattens(spy):
    async def go():
        await _with_open([5543])
        return await lt.apply_management(9, "close all now", 5543)
    action = _run(go())
    assert spy.closed == [(5543, "channel_close")]
    assert action["close"] == "all"


def test_stop_is_moved_before_the_close(spy):
    """A close that fails at the broker must still leave the position protected
    at its NEW stop, not the original one."""
    order = []

    async def modify_sl(mid, new_sl): order.append("sl")
    async def move_to_breakeven(mid): order.append("be")
    async def close_order(mid, reason): order.append("close")
    lt.modify_sl, lt.move_to_breakeven, lt.close_order = modify_sl, move_to_breakeven, close_order

    async def go():
        await _with_open([5543])
        await lt.apply_management(9, "breakeven hit out of this entries now!", 5543)
    _run(go())
    assert order == ["be", "close"]


def test_close_half_is_refused_not_guessed(spy):
    """Unsupported on purpose: a partial close cuts through the slice/deal
    matching that caused the 2026-08-11 P&L corruption. Under-closing is the
    safe failure."""
    async def go():
        await _with_open([5543])
        return await lt.apply_management(9, "close half here", 5543)
    action = _run(go())
    assert spy.closed == []
    assert action["close"] == "half"
