"""Regression: the channel posts numbered take-profits ("TP1 3338 TP2 3342
TP3 3350"), not the bare "TP: 3338" form. The original TP regex required a
colon/space *immediately* after "TP", so "TP1" matched zero TP tokens and the
whole signal failed to parse — parse_signal returned None and live_trader
silently dropped the signal (no order, no log, no DB row). Two live "Gold buy
now ... TP1/TP2/TP3" signals were lost this way.

These tests pin the numbered-TP form (with and without colons, with the "now"
keyword) while keeping the original bare-TP form working.
"""
import parse_signals as ps
from parse_signals import parse_signal, clean, looks_like_signal


def test_numbered_tp_labels_no_colon():
    sig = parse_signal(clean("Gold buy now 3331 - 3334 SL 3325 TP1 3338 TP2 3342 TP3 3350"))
    assert sig is not None
    assert sig["side"] == "buy"
    assert sig["instrument"] == "XAUUSD"
    assert sig["entry_low"] == 3331.0
    assert sig["entry_high"] == 3334.0
    assert sig["sl"] == 3325.0
    assert sig["tps"] == [3338.0, 3342.0, 3350.0]


def test_numbered_tp_labels_with_colon():
    sig = parse_signal(clean("Gold buy now 3331-3334 SL: 3325 TP1: 3338 TP2: 3342 TP3: 3350"))
    assert sig is not None
    assert sig["tps"] == [3338.0, 3342.0, 3350.0]


def test_numbered_tp_with_open_runner():
    sig = parse_signal(clean("Gold sell 4731 - 4734 SL: 4739 TP1 4729 TP2 4727 TP3 open"))
    assert sig is not None
    assert sig["tps"] == [4729.0, 4727.0]
    assert sig["tp_open"] is True


def test_bare_tp_form_still_parses():
    """The original colon/space form must keep working unchanged."""
    sig = parse_signal(clean("Gold sell 4731 - 4734 SL: 4739 TP: 4729 TP: 4727 TP: 4725 TP: open"))
    assert sig is not None
    assert sig["tps"] == [4729.0, 4727.0, 4725.0]
    assert sig["tp_open"] is True


def test_looks_like_signal_flags_unparseable_signal():
    """A signal-shaped message the grammar can't read is flagged (so the live
    trader logs it loudly instead of dropping it silently)."""
    weird = clean("Gold buy now 3331 to 3334 SL 3325 take profits 3338 / 3342")
    assert parse_signal(weird) is None      # grammar can't parse it
    assert looks_like_signal(weird) is True  # but we know it WAS a signal


def test_looks_like_signal_ignores_chatter():
    assert looks_like_signal(clean("Gold buy now")) is False        # bare trigger, no SL
    assert looks_like_signal(clean("All 3 TPs hit, great day")) is False
    assert looks_like_signal(clean("Move SL to entry")) is False
