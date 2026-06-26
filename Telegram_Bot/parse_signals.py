"""
Parse NeymarGoldTrader Telegram messages into structured trades.

Input:  messages.csv  (from test.py)
Output:
  signals.csv  — one row per trade signal with entry zone, SL, TPs, outcomes
  signals.json — same data as JSON (richer: keeps update history)

Signal grammar (free-text, observed):
  <Instrument> <buy|sell> <e1>- <e2>   SL: <sl>   TP: <t1> TP: <t2> TP: <t3> TP: open
  e.g. "Gold sell 4731 - 4734  SL: 4739  TP: 4729 TP: 4727 TP: 4725 TP: open"

Outcome replies (attach to parent signal via reply_to):
  "TP1 CHECK", "TP2 HIT", "TP3", "All 3 TPs HIT", "SL", "Hit SL",
  "Move SL to entry" / "Set breakeven", "Typo mistake SL is <x>"
"""

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

NOW_LOOKBACK_MIN = 10

HERE = Path(__file__).parent
IN_CSV = HERE / "messages.csv"
OUT_CSV = HERE / "signals.csv"
OUT_JSON = HERE / "signals.json"

CLEAN_MD = re.compile(r"\*+|__+|\[.*?\]\(.*?\)|`")

SIGNAL_RE = re.compile(
    r"""
    (?P<instr>Gold|XAU(?:USD)?|BTC(?:USD)?|ETH(?:USD)?|GBP\w+|EUR\w+|USD\w+)
    \s+(?P<side>buy|sell)(?:\s+now)?\s+
    (?P<e1>\d+(?:\.\d+)?)\s*-\s*(?P<e2>\d+(?:\.\d+)?)
    \s+SL[:\s]+(?P<sl>\d+(?:\.\d+)?)
    \s+(?P<tps>(?:TP\d{0,2}[:\s]+\S+\s*)+)
    """,
    re.IGNORECASE | re.VERBOSE,
)
# Accept both the bare "TP: 4729" / "TP 4729" form and the numbered
# "TP1 3338" / "TP2: 3342" form the channel actually posts (the digit glued to
# "TP" previously matched zero TP tokens and silently dropped the whole signal).
TP_VAL_RE = re.compile(r"TP\d{0,2}[:\s]+(\d+(?:\.\d+)?|open)", re.IGNORECASE)
NOW_RE = re.compile(r"\b(Gold|XAU\w*|BTC\w*)\s+(buy|sell)\s+now\b", re.IGNORECASE)
# Loose "this message is a trade signal" detector: an instrument + a side + a
# stop-loss. Used only to tell a genuinely-unparseable SIGNAL (worth shouting
# about) apart from ordinary channel chatter or the bare "Gold buy now" trigger
# (no SL) — so a future format the strict SIGNAL_RE can't read is logged loudly
# instead of being dropped in silence, as the TP1/TP2/TP3 form was.
LOOKS_LIKE_SIGNAL_RE = re.compile(
    r"(?:Gold|XAU\w*|BTC\w*|ETH\w*|GBP\w+|EUR\w+|USD\w+)\s+(?:buy|sell)\b.*\bSL\b",
    re.IGNORECASE | re.DOTALL,
)
SL_FIX_RE = re.compile(r"SL\s+is\s+\*{0,2}(\d+(?:\.\d+)?)", re.IGNORECASE)


def clean(t: str) -> str:
    t = CLEAN_MD.sub("", t or "")
    return re.sub(r"\s+", " ", t).strip()


def classify_outcome(text: str) -> dict | None:
    t = text.lower()
    out = {}
    if "all 3 tps hit" in t or ("tp3" in t and "hit" in t) or "all take profits" in t:
        out["tp_hit"] = 3
    elif "tp3" in t and ("check" in t or "✅" in text or "smacked" in t):
        out["tp_hit"] = 3
    elif "tp2" in t and ("check" in t or "hit" in t or "✅" in text):
        out["tp_hit"] = 2
    elif "tp1" in t and ("check" in t or "hit" in t or "✅" in text):
        out["tp_hit"] = 1
    if "hit sl" in t or re.search(r"\bsl\b.*\b(hit|smacked|taken)\b", t) or "stopped out" in t:
        out["sl_hit"] = True
    if "set breakeven" in t or "move sl to entry" in t or "set be" in t or "to entry" in t:
        out["breakeven"] = True
    return out or None


def looks_like_signal(text: str) -> bool:
    """True when text reads like a trade signal (instrument + side + SL) even if
    the strict grammar can't parse it. Lets the live trader distinguish a signal
    it FAILED to parse (alert-worthy) from ordinary chatter (ignore quietly)."""
    return bool(LOOKS_LIKE_SIGNAL_RE.search(text or ""))


def parse_signal(text: str) -> dict | None:
    m = SIGNAL_RE.search(text)
    if not m:
        return None
    tps = [v for v in TP_VAL_RE.findall(m.group("tps"))]
    tp_nums = [float(v) for v in tps if v.lower() != "open"]
    instr = m.group("instr").upper()
    if instr == "GOLD":
        instr = "XAUUSD"
    return {
        "instrument": instr,
        "side": m.group("side").lower(),
        "entry_low": min(float(m.group("e1")), float(m.group("e2"))),
        "entry_high": max(float(m.group("e1")), float(m.group("e2"))),
        "sl": float(m.group("sl")),
        "tps": tp_nums,
        "tp_open": any(v.lower() == "open" for v in tps),
    }


def main():
    rows = []
    with open(IN_CSV, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["text"] = clean(r["text"])
            rows.append(r)
    rows.sort(key=lambda r: int(r["id"]))

    signals: dict[str, dict] = {}
    children: dict[str, list[dict]] = defaultdict(list)
    # Track most recent "Gold buy/sell now" announcement per side (no prices).
    last_now: dict[str, dict] = {}  # side -> {"date": str, "dt": datetime}

    for r in rows:
        text = r["text"]
        msg_dt = datetime.fromisoformat(r["date"]) if r["date"] else None

        sig = parse_signal(text)
        if sig and not r["reply_to"]:
            posted_at = r["date"]
            entry_date = posted_at
            # If the signal text itself does NOT contain "now", look back for a
            # recent "Gold <side> now" announcement (sender posts it 1-4 min before
            # the full SL/TP message). Use that earlier timestamp as the entry time.
            if not re.search(r"\bnow\b", text, re.IGNORECASE):
                prev = last_now.get(sig["side"])
                if prev and msg_dt and (msg_dt - prev["dt"]) <= timedelta(minutes=NOW_LOOKBACK_MIN) and (msg_dt - prev["dt"]) >= timedelta(seconds=0):
                    entry_date = prev["date"]
            signals[r["id"]] = {
                "id": r["id"],
                "date": entry_date,
                "posted_at": posted_at,
                "raw": text,
                **sig,
                "updates": [],
                "tp_hit_max": 0,
                "sl_hit": False,
                "breakeven": False,
                "closed_at": None,
            }
            # Once consumed, clear the "now" tracker so it isn't reused.
            last_now.pop(sig["side"], None)
        else:
            # Bare "Gold buy now" / "Gold sell now" with no price — track as entry trigger.
            nm = NOW_RE.search(text)
            if nm and msg_dt and not r["reply_to"]:
                side = nm.group(2).lower()
                # Only record if there are no prices in this message (not already a full signal).
                if not re.search(r"SL[:\s]+\d", text, re.IGNORECASE):
                    last_now[side] = {"date": r["date"], "dt": msg_dt}
            if r["reply_to"]:
                children[r["reply_to"]].append(r)

    for parent_id, kids in children.items():
        if parent_id not in signals:
            continue
        sig = signals[parent_id]
        for k in sorted(kids, key=lambda r: int(r["id"])):
            text = k["text"]
            if not text:
                continue
            sl_fix = SL_FIX_RE.search(text)
            if sl_fix and "typo" in text.lower():
                sig["sl"] = float(sl_fix.group(1))
                sig["updates"].append({"date": k["date"], "type": "sl_fix", "value": sig["sl"], "text": text})
                continue
            outcome = classify_outcome(text)
            if outcome:
                if "tp_hit" in outcome:
                    sig["tp_hit_max"] = max(sig["tp_hit_max"], outcome["tp_hit"])
                if outcome.get("sl_hit"):
                    sig["sl_hit"] = True
                    sig["closed_at"] = k["date"]
                if outcome.get("breakeven"):
                    sig["breakeven"] = True
                sig["updates"].append({"date": k["date"], "type": "outcome", **outcome, "text": text})
                if sig["tp_hit_max"] >= len(sig["tps"]) and sig["tps"]:
                    sig["closed_at"] = sig["closed_at"] or k["date"]

    out = sorted(signals.values(), key=lambda s: s["date"])

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "date", "posted_at", "instrument", "side", "entry_low", "entry_high",
            "sl", "tp1", "tp2", "tp3", "tp_open",
            "tp_hit_max", "sl_hit", "breakeven", "closed_at", "raw",
        ])
        for s in out:
            tps = s["tps"] + [""] * (3 - len(s["tps"]))
            w.writerow([
                s["id"], s["date"], s.get("posted_at", s["date"]),
                s["instrument"], s["side"],
                s["entry_low"], s["entry_high"], s["sl"],
                tps[0], tps[1], tps[2], s["tp_open"],
                s["tp_hit_max"], s["sl_hit"], s["breakeven"],
                s["closed_at"] or "", s["raw"],
            ])

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    total = len(out)
    wins = sum(1 for s in out if s["tp_hit_max"] >= 1 and not s["sl_hit"])
    losses = sum(1 for s in out if s["sl_hit"])
    full_wins = sum(1 for s in out if s["tps"] and s["tp_hit_max"] >= len(s["tps"]))
    unresolved = total - wins - losses
    print(f"Parsed {total} signals")
    print(f"  Wins (>=TP1, no SL):     {wins}")
    print(f"  Full wins (all TPs hit): {full_wins}")
    print(f"  SL hit:                  {losses}")
    print(f"  Unresolved:              {unresolved}")
    print(f"Wrote {OUT_CSV.name} and {OUT_JSON.name}")


if __name__ == "__main__":
    main()
