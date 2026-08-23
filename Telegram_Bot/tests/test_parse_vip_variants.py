"""Signal-format variants seen in the Neymar VIP channel.

All nine strings here are verbatim failures from the first pass over the
exported history (315 real entry signals, 306 of which the original grammar
already read). They are regression cover for the punctuation and qualifier
tolerances added for that channel — the free channel's own formats are covered
by the existing test files and must keep passing unchanged.
"""
import pytest

from parse_signals import parse_signal


def test_tp_dot_separator():
    """'TP. 4330' instead of 'TP: 4330'."""
    s = parse_signal("Gold buy now 4328.2 - 4325 SL: 4322 TP. 4330 TP. 4332 TP: 4334 TP: open")
    assert s["tps"] == [4330.0, 4332.0, 4334.0]
    assert s["tp_open"] is True


def test_buy_now_at_with_colonless_sl_and_tps():
    s = parse_signal("Gold buy now at 5194.9 - 5188 SL 5181.60 TP 5198 TP 5200 TP open")
    assert s["side"] == "buy"
    assert s["entry_low"] == 5188.0
    assert s["entry_high"] == 5194.9
    assert s["sl"] == 5181.60
    assert s["tps"] == [5198.0, 5200.0]
    assert s["tp_open"] is True


@pytest.mark.parametrize("text,expected", [
    ("Gold buy limit 4020 - 4017 SL: 4014 TP: 4022 TP: open", "limit"),
    ("Gold sell limit 4442- 4445 SL: 4450 TP: 4440 TP: open", "limit"),
    ("Gold sell re-entry 4072.6 - 4077 SL: 4081 TP: 4070 TP: open", "reentry"),
    ("Gold buy now 4591.7 - 4587 SL: 4581 TP: 4594 TP: open", "market"),
    ("Gold sell 4731 - 4734 SL: 4739 TP: 4729 TP: open", "market"),
])
def test_order_type_qualifier_recorded(text, expected):
    """'limit' and 're-entry' previously killed the whole match. They are
    recorded for audit only — build_order_plan decides market-vs-pending for
    real, from where price actually sits relative to the zone."""
    assert parse_signal(text)["order_type"] == expected


def test_curly_apostrophe_before_open():
    """"TP:'open" — strip_decor turns the smart quote into a space."""
    s = parse_signal("Gold sell limit 4704- 4707 SL: 4712 TP: 4702 TP: 4700 TP:'open")
    assert s["tps"] == [4702.0, 4700.0]
    assert s["tp_open"] is True


def test_missing_space_around_entry_dash():
    s = parse_signal("Gold sell limit 4442- 4445 SL: 4450 TP: 4440 TP: open")
    assert s["entry_low"] == 4442.0
    assert s["entry_high"] == 4445.0


def test_five_tps_plus_open():
    """The VIP channel ladders more targets than the free channel does."""
    s = parse_signal("Gold sell re-entry 4072.6 - 4077 SL: 4081 "
                     "TP: 4070 TP: 4068 TP: 4066 TP: 4064 TP: open")
    assert s["tps"] == [4070.0, 4068.0, 4066.0, 4064.0]
    assert s["tp_open"] is True


def test_tp_open_is_flagged_not_dropped_silently():
    """309 of 315 real VIP signals carry an open-ended runner. It must surface
    as a flag so the caller can decide, not vanish into the numeric TP list."""
    s = parse_signal("Gold buy now 4591.7 - 4587 SL: 4581 TP: 4594 TP: open")
    assert s["tps"] == [4594.0]
    assert s["tp_open"] is True


def test_signal_without_open_leg_is_not_flagged():
    s = parse_signal("Gold buy now 4591.7 - 4587 SL: 4581 TP: 4594 TP: 4596")
    assert s["tp_open"] is False
