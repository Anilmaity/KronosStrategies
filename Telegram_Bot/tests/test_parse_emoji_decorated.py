"""Regression: from 2026-08-03 the channel began decorating every field of the
signal with emoji ("4062 - 4059 🔥 SL: 4055 ⚠️ TP: 4064 ✅ TP: 4066 ✅ ...").

SIGNAL_RE only allowed whitespace between the entry zone, the SL and the TP
block, so the grammar stopped matching at the first emoji and parse_signal
returned None — 7 live signals in 5 days were logged UNPARSEABLE and never
traded, while the bot itself looked perfectly healthy.

A subtler second failure hid behind the first: the TP block is captured as one
run of "TP<n>: <val>" tokens, so an emoji *between* two TPs truncates the run.
Even a signal whose head matched would have yielded only the first TP — one
order leg instead of three or four. test_all_tps_captured_between_emoji pins it.

Messages below are copied verbatim from the prod telegram_trader logs.
"""
import parse_signals as ps
from parse_signals import parse_signal, clean, looks_like_signal


def test_emoji_between_entry_zone_and_sl():
    sig = parse_signal(clean(
        "Gold Buy Now 4302 - 4298 💰📈 SL: 4295 ⛔ TP: 4304 🎯 TP: 4306 🎯 TP: 4308 🎯 TP: Open 🔓"))
    assert sig is not None
    assert sig["side"] == "buy"
    assert sig["instrument"] == "XAUUSD"
    assert sig["entry_low"] == 4298.0
    assert sig["entry_high"] == 4302.0
    assert sig["sl"] == 4295.0


def test_all_tps_captured_between_emoji():
    """Emoji separating the TP tokens must not truncate the TP run."""
    sig = parse_signal(clean(
        "Gold Buy Now 4062 - 4059 🔥 SL: 4055 ⚠️ TP: 4064 ✅ TP: 4066 ✅ TP: 4068 ✅ TP: 4070 ✅"))
    assert sig is not None
    assert sig["tps"] == [4064.0, 4066.0, 4068.0, 4070.0]


def test_emoji_decorated_open_runner_sets_tp_open():
    sig = parse_signal(clean(
        "Gold SELL now 4061.1 - 4064.3 🪙🔻 SL: 4069 ⛔ TP: 4059 🎯 TP: 4057 🎯 "
        "TP: 4055 🎯 TP: 4053 🎯 TP: open 🔓"))
    assert sig is not None
    assert sig["side"] == "sell"
    assert sig["tps"] == [4059.0, 4057.0, 4055.0, 4053.0]
    assert sig["tp_open"] is True


def test_leading_emoji_before_instrument():
    sig = parse_signal(clean(
        "📈 GOLD BUY NOW 4268.4 - 4265 💰 🛑 SL: 4262 🎯 TP: 4271 🎯 TP: 4273 🎯 TP: 4275 🎯 TP: OPEN"))
    assert sig is not None
    assert sig["entry_low"] == 4265.0
    assert sig["entry_high"] == 4268.4
    assert sig["sl"] == 4262.0
    assert sig["tps"] == [4271.0, 4273.0, 4275.0]


def test_trailing_chatter_after_open_runner_ignored():
    """"TP: open 200 pips = 📈" — the runner is followed by free text."""
    sig = parse_signal(clean(
        "Gold BUY NOW 4050 - 4046 ✨ SL: 4042 🚫 TP: 4052 🎯 TP: 4054 🎯 TP: 4056 🎯 "
        "TP: 4058 🎯 TP: open 200 pips = 📈"))
    assert sig is not None
    assert sig["tps"] == [4052.0, 4054.0, 4056.0, 4058.0]
    assert sig["tp_open"] is True


def test_plain_format_unaffected():
    """The undecorated form the channel still posts must parse identically."""
    sig = parse_signal(clean("Gold sell 4731 - 4734 SL: 4739 TP: 4729 TP: 4727 TP: 4725 TP: open"))
    assert sig is not None
    assert sig["tps"] == [4729.0, 4727.0, 4725.0]
    assert sig["tp_open"] is True


def test_emoji_chatter_is_still_not_a_signal():
    """Stripping decoration must not turn ordinary chatter into a tradeable signal."""
    assert parse_signal(clean("All 3 TPs hit 🎯🎯🎯 great day 🚀")) is None
    assert looks_like_signal(clean("All 3 TPs hit 🎯🎯🎯 great day 🚀")) is False


def test_reply_outcome_emoji_preserved_for_classifier():
    """clean() must NOT strip emoji: classify_outcome keys off the ✅ tick."""
    assert ps.classify_outcome(clean("TP1 ✅")) == {"tp_hit": 1}
