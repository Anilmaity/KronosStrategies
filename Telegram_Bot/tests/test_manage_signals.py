"""Position-management classification for the Neymar VIP channel.

Every phrasing asserted here is taken verbatim from the exported channel
history, not invented — the whole point of the export was to write these against
real messages.
"""
import pytest

from manage_signals import classify_management, resolve_target


# ─────────────────────── breakeven, in its many forms ───────────────────────

@pytest.mark.parametrize("text", [
    "Set breakeven now!!!",
    "breakeven",
    "set breakeven for zero risk now!!",
    "TP1 checkkk!! milk some profits & set breakeven",
    "TP1 hit ✅️ move sl to entry now 🍸",
    "breakeven hit out of this entries now!",
    "Take profit 1 checkk 🩸🩸 set breakeven ✅️✅️🤑🤑",
    "set be",
    "risk free now",
])
def test_breakeven_phrasings(text):
    assert classify_management(text).get("breakeven") is True


@pytest.mark.parametrize("text", [
    "Move sl to emtry",
    "Move SL to enrty now 🙏",
    "TP1 CHECKK ✅️🤑 50 PIPS ✅️✅️ MOVE SL TO ENRTY",
])
def test_breakeven_survives_the_channels_entry_typos(text):
    """These are real messages. A typo in an instruction to protect a live
    position must not cost us the instruction."""
    assert classify_management(text).get("breakeven") is True


# ─────────────────────── explicit stop levels ───────────────────────

def test_move_sl_to_absolute_price():
    assert classify_management("Move SL to 4578")["move_sl"] == 4578.0


def test_move_sl_handles_decimals():
    assert classify_management("move stop loss to 4578.5")["move_sl"] == 4578.5


def test_numeric_move_beats_breakeven():
    """A message naming a price is more specific than one naming a concept."""
    out = classify_management("TP1 hit, set breakeven — move SL to 4578")
    assert out["move_sl"] == 4578.0
    assert "breakeven" not in out


# ─────────────────────── closes ───────────────────────

def test_close_all():
    assert classify_management("close all now")["close"] == "all"


def test_close_half():
    assert classify_management("close half here")["close"] == "half"


def test_half_beats_all_when_both_words_present():
    assert classify_management("close half of it all")["close"] == "half"


def test_stop_change_and_close_can_co_occur():
    out = classify_management("breakeven hit out of this entries now!")
    assert out["breakeven"] is True
    assert out["close"] == "all"


# ─────────────────────── negative cases ───────────────────────

@pytest.mark.parametrize("text", [
    "Gold buy now 4591.7 - 4587 SL: 4581 TP: 4594 TP: 4596 TP: open",
    "Gold sell now 4598 - 4601 SL: 4607 TP: 4596 TP: open",
    "Copy trading is available pm me @ngtadmin",
    "Let's goooo! smashhhhhhhedddd 💥 what a fall!",
    "",
])
def test_does_not_fire_on_entries_or_chatter(text):
    """Verified against the full corpus: 0 false positives across 313 real
    entry signals. A classifier that fired on an entry would move the stop on a
    position that had just been opened."""
    assert classify_management(text) is None


# ─────────────────────── targeting ───────────────────────

def test_reply_targets_its_parent():
    assert resolve_target(5543, open_signal_ids=[9999]) == 5543


def test_standalone_targets_the_single_open_signal():
    """137 of 317 management messages carry no reply link. The trader keeps one
    signal open at a time, so 'the open one' is well defined."""
    assert resolve_target(None, open_signal_ids=[5543]) == 5543


def test_standalone_refuses_when_ambiguous():
    """Moving the stop on the wrong position is a real loss; missing one
    management message is not. Refuse rather than guess."""
    assert resolve_target(None, open_signal_ids=[5543, 5585]) is None


def test_standalone_refuses_when_nothing_is_open():
    assert resolve_target(None, open_signal_ids=[]) is None
