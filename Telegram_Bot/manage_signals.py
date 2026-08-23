"""Position-management messages — the instructions that change a LIVE position.

The Neymar VIP channel does not only post entries. Across its history it sends
~317 messages telling subscribers to move a stop or close early: 260 breakeven
calls, 49 explicit "Move SL to <price>", a handful of manual closes. Acting on
them is not cosmetic — a channel whose stops move to breakeven after TP1 is a
materially different (and differently-risked) system from one that leaves the
original stop sitting there.

Why this is separate from parse_signals.classify_outcome():

  classify_outcome() answers "what HAPPENED to this trade" (TP1 hit, SL hit) and
  is driven by the free channel's fairly disciplined reply format. It recognises
  only 185 of the VIP channel's 317 management messages, because the VIP
  phrasing is much looser — "set breakeven for zero risk now!!", "breakeven hit
  out of this entries now!", "TP1 hit move sl to entry now 🍸".

  This module answers the different question "what should I DO to the position
  right now", and is deliberately tolerant of that phrasing.

Targeting: 180 of the 317 are replies and resolve to their parent signal. The
other 137 are standalone — no reply link at all. Those are resolved against the
caller's single open signal, which is well-defined because the trader enforces
one open signal at a time (the no-pyramiding guard in live_trader). When more
than one is somehow open, resolve_target() refuses rather than guess: acting on
the wrong position is worse than not acting.
"""
from __future__ import annotations

import re

# "Move SL to 4578" / "SL to 4578.5" / "move stop to 4578" — an ABSOLUTE new level.
MOVE_SL_RE = re.compile(
    r"(?:move\s+)?(?:the\s+)?(?:sl|stop(?:\s*loss)?)\s*(?:to|@|at)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE)

# Breakeven, in the many ways this channel says it. "move sl to entry" lands here
# rather than in MOVE_SL_RE because "entry" is not a number.
# `e[nm][tr][tr]y` deliberately also matches the channel's live misspellings of
# "entry" — "enrty" and "emtry" both appear in real breakeven instructions. A
# typo in a message telling us to protect an open position must not cost us the
# instruction; the pattern is narrow enough that it matches nothing else.
BREAKEVEN_RE = re.compile(
    r"break\s*even"
    r"|\bset\s+be\b"
    r"|(?:sl|stop)\s*(?:to|at)\s*(?:the\s+)?e[nm][tr][tr]y"
    r"|risk\s*[- ]?\s*free"
    r"|zero\s+risk",
    re.IGNORECASE)

CLOSE_ALL_RE = re.compile(
    r"close\s+(?:all|everything|the\s+trade)"
    r"|\bexit\s+all\b"
    r"|out\s+of\s+(?:this|these|the)\s+(?:entry|entries|trade|trades)",
    re.IGNORECASE)

CLOSE_HALF_RE = re.compile(
    r"close\s+half|half\s+(?:off|out)|take\s+partial", re.IGNORECASE)


def classify_management(text: str) -> dict | None:
    """What this message tells us to do to a live position, or None.

    Returns any of:
        {"move_sl": 4578.0}   absolute new stop level
        {"breakeven": True}   move the stop to the entry price
        {"close": "all"}      flatten every remaining slice
        {"close": "half"}     flatten half the remaining slices

    A numeric "move SL to X" wins over a breakeven match, because a message
    naming a price is more specific than one naming a concept — "TP1 hit, move
    SL to 4578" is an instruction to 4578, not to entry.

    Close and stop instructions can co-occur ("breakeven hit, out of this entries
    now") and both are returned; the caller applies the stop change first so a
    failed close still leaves the position protected.
    """
    t = text or ""
    out: dict = {}

    m = MOVE_SL_RE.search(t)
    if m:
        out["move_sl"] = float(m.group(1))
    elif BREAKEVEN_RE.search(t):
        out["breakeven"] = True

    if CLOSE_HALF_RE.search(t):
        out["close"] = "half"
    elif CLOSE_ALL_RE.search(t):
        out["close"] = "all"

    return out or None


def resolve_target(msg_reply_to: int | None, open_signal_ids) -> int | None:
    """Which signal a management message applies to.

    A reply names its target directly. A standalone message is resolved against
    the single open signal. Ambiguity (zero open, or more than one) returns None
    — the caller logs and does nothing, because moving the stop on the wrong
    position is a real loss, while missing one management message is not.
    """
    if msg_reply_to is not None:
        return msg_reply_to
    ids = list(open_signal_ids)
    return ids[0] if len(ids) == 1 else None
