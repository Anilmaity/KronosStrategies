"""
Live trader for @NeymarGoldTrader signals.

Pipeline:
  Telegram channel  --(Telethon NewMessage)-->  parse_signal()
       |
       v
  Redis  (hft:tg:signal:<id>, hft:tg:open)
       |
       v
  place_order()   <-- stubbed; wire to MetaAPI when ready (DRY_RUN gates it)
       |
       v
  Reply handler   --(typo fix / TP hit / SL hit)-->  modify_order() / close_order()

Run:
  TG_API_ID=... TG_API_HASH=... python live_trader.py
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load MetaAPI / Telegram creds from .env before importing metaapi_orders,
# which reads token/account at import time. Probe a few candidate paths;
# silently skip ones that don't exist (depth varies inside the container).
_HERE = Path(__file__).resolve().parent
_candidates = [_HERE / ".env"]
for n in (1, 2, 3):
    try:
        _candidates.append(_HERE.parents[n - 1] / ".env")
        _candidates.append(_HERE.parents[n - 1] / "Kronos App" / ".env")
    except IndexError:
        break
for _p in _candidates:
    if _p.exists():
        load_dotenv(_p, override=False)

from telethon import TelegramClient, events

from parse_signals import parse_signal, classify_outcome, SL_FIX_RE, clean
import metaapi_orders as mx
import db_persist as db
from state_store import make_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tg-trader")

API_ID = int(os.getenv("TG_API_ID", "30334024"))
API_HASH = os.getenv("TG_API_HASH", "f7f83460d3bae2e462c02f144dbc114f")
CHANNEL = os.getenv("TG_CHANNEL", "Test_XAU_USD")
# Telethon session path. Kept under TG_SESSION_DIR so it can be persisted on a
# docker volume: without a saved session every (re)start re-triggers an
# interactive phone/code login, which hangs (then crash-loops) in a headless
# container — i.e. the bot never reaches the message handler and places no trades.
SESSION = str(Path(os.getenv("TG_SESSION_DIR", str(_HERE))) / "kronos_tg")

# Empty REDIS_URL → use in-memory state store. Set to a redis:// URL to enable Redis.
REDIS_URL = os.getenv("REDIS_URL", "").strip() or None
REDIS_PREFIX = "hft:tg"

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
MAX_SIGNAL_AGE_SEC = 120          # ignore signals we receive late (recovery from downtime)
ALLOWED_INSTRUMENTS = {"XAUUSD"}
RISK_PER_TRADE_USD = float(os.getenv("RISK_USD", "100"))

# XAUUSD: $1 PnL per 0.01 price move per 0.01 lot (varies by broker; verify).
# volume_lots = risk_usd / (risk_points * USD_PER_POINT_PER_LOT)
USD_PER_POINT_PER_LOT = float(os.getenv("USD_PER_POINT_PER_LOT", "100"))  # XAUUSD default
MIN_LOT = float(os.getenv("MIN_LOT", "0.01"))

# A signal is only removed from the :open set on a TP3 / SL reply. If the channel
# announces TP1/TP2 then goes quiet (no TP3, no SL), it would stay "open" forever
# and the no-pyramiding guard below would skip every later signal. Expire — flatten
# remaining broker orders and untrack — any signal open longer than this.
MAX_OPEN_AGE_SEC = float(os.getenv("MAX_OPEN_AGE_HOURS", "12")) * 3600
SWEEP_INTERVAL_SEC = int(os.getenv("SWEEP_INTERVAL_SEC", "1800"))  # background stale-sweep cadence

r = None  # state store (RedisStore or MemoryStore) — set in main()


def is_malformed(sig: dict) -> str | None:
    em = (sig["entry_low"] + sig["entry_high"]) / 2
    if sig["side"] == "buy" and sig["sl"] >= em:
        return "buy SL above entry"
    if sig["side"] == "sell" and sig["sl"] <= em:
        return "sell SL below entry"
    if not sig["tps"]:
        return "no TPs"
    if sig["side"] == "buy" and any(tp <= em for tp in sig["tps"]):
        return "buy TP below entry"
    if sig["side"] == "sell" and any(tp >= em for tp in sig["tps"]):
        return "sell TP above entry"
    return None


async def place_order(msg_id: int, sig: dict) -> dict:
    """Place 3 MetaAPI orders (one per TP), each at entry midpoint with shared SL."""
    entry_mid = (sig["entry_low"] + sig["entry_high"]) / 2
    risk_pts = abs(entry_mid - sig["sl"])
    total_vol = RISK_PER_TRADE_USD / (risk_pts * USD_PER_POINT_PER_LOT)
    total_vol = max(total_vol, MIN_LOT * len(sig["tps"]))

    loop = asyncio.get_running_loop()
    submitted = await loop.run_in_executor(
        None,
        lambda: mx.submit_signal_orders(
            side=sig["side"],
            symbol=sig["instrument"],
            entry=entry_mid,
            sl=sig["sl"],
            tps=sig["tps"],
            total_volume=total_vol,
            msg_id=msg_id,
        ),
    )

    return {
        "msg_id": msg_id,
        "instrument": sig["instrument"],
        "side": sig["side"],
        "entry_lo": sig["entry_low"],
        "entry_hi": sig["entry_high"],
        "entry_mid": entry_mid,
        "sl": sig["sl"],
        "tps": sig["tps"],
        "total_volume": total_vol,
        "risk_pts": risk_pts,
        "orders": submitted,
        "status": "submitted" if submitted else "rejected",
        "dry_run": DRY_RUN,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }


async def modify_sl(msg_id: int, new_sl: float):
    key = f"{REDIS_PREFIX}:signal:{msg_id}"
    pos_json = await r.get(key)
    if not pos_json:
        return
    pos = json.loads(pos_json)
    pos["sl"] = new_sl
    pos["sl_history"] = pos.get("sl_history", []) + [{"sl": new_sl, "at": datetime.now(timezone.utc).isoformat()}]

    loop = asyncio.get_running_loop()
    for o in pos.get("orders", []):
        # POSITION_MODIFY only applies once filled; pending limits ignore the call.
        # Safe to attempt either way — MetaAPI returns an error we log and move on.
        await loop.run_in_executor(None, mx.modify_position_sl, o["ticket_id"], new_sl)

    await r.set(key, json.dumps(pos))
    await loop.run_in_executor(None, db.update_sl, msg_id, new_sl)
    log.info(f"[{msg_id}] SL modified -> {new_sl} ({'DRY' if DRY_RUN else 'LIVE'})")


async def move_to_breakeven(msg_id: int):
    key = f"{REDIS_PREFIX}:signal:{msg_id}"
    pos_json = await r.get(key)
    if not pos_json:
        return
    pos = json.loads(pos_json)
    await modify_sl(msg_id, pos["entry_mid"])


async def close_order(msg_id: int, reason: str):
    key = f"{REDIS_PREFIX}:signal:{msg_id}"
    pos_json = await r.get(key)
    if not pos_json:
        return
    pos = json.loads(pos_json)

    loop = asyncio.get_running_loop()
    for o in pos.get("orders", []):
        if o["kind"] == "limit":
            await loop.run_in_executor(None, mx.cancel_order, o["ticket_id"])
        else:
            await loop.run_in_executor(None, mx.close_position, o["ticket_id"])

    pos["status"] = f"closed_{reason}"
    pos["closed_at"] = datetime.now(timezone.utc).isoformat()
    await r.set(key, json.dumps(pos))
    await r.srem(f"{REDIS_PREFIX}:open", str(msg_id))
    await loop.run_in_executor(None, db.close_signal, msg_id, reason)
    log.info(f"[{msg_id}] CLOSE ({reason})")


async def sweep_stale_open() -> int:
    """Expire signals open longer than MAX_OPEN_AGE_SEC.

    Closes their remaining broker orders and drops them from the :open set so a
    signal the channel never resolves (TP1/TP2 then silence) can't wedge the
    no-pyramiding guard forever. Returns the number expired.
    """
    open_ids = await r.smembers(f"{REDIS_PREFIX}:open")
    now = datetime.now(timezone.utc)
    expired = 0
    for sid in open_ids:
        pos_json = await r.get(f"{REDIS_PREFIX}:signal:{sid}")
        if not pos_json:
            await r.srem(f"{REDIS_PREFIX}:open", sid)  # dangling id, no detail row
            continue
        pos = json.loads(pos_json)
        stamp = pos.get("opened_at") or pos.get("posted_at")
        if not stamp:
            continue
        try:
            age = (now - datetime.fromisoformat(stamp)).total_seconds()
        except ValueError:
            continue
        if age > MAX_OPEN_AGE_SEC:
            log.warning("[%s] open %.1fh > max %.1fh — expiring (flatten + unblock)",
                        sid, age / 3600, MAX_OPEN_AGE_SEC / 3600)
            await close_order(int(sid), "expired")
            expired += 1
    return expired


async def _stale_sweeper() -> None:
    """Periodically expire stale open signals even when no new signal arrives."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SEC)
        try:
            n = await sweep_stale_open()
            if n:
                log.info("Background stale-sweep expired %d open signal(s)", n)
        except Exception as e:
            log.exception(f"stale sweeper error: {e}")


async def handle_new_signal(msg) -> None:
    text = clean(msg.text or "")
    sig = parse_signal(text)
    if not sig:
        return
    age = (datetime.now(timezone.utc) - msg.date.astimezone(timezone.utc)).total_seconds()
    if age > MAX_SIGNAL_AGE_SEC:
        log.warning(f"[{msg.id}] stale signal ({age:.0f}s old) — skip")
        return
    if sig["instrument"] not in ALLOWED_INSTRUMENTS:
        log.info(f"[{msg.id}] instrument {sig['instrument']} not allowed — skip")
        return
    bad = is_malformed(sig)
    if bad:
        log.warning(f"[{msg.id}] malformed signal ({bad}) — skip")
        return
    await sweep_stale_open()  # clear any wedged stale opens before the guard
    open_ids = await r.smembers(f"{REDIS_PREFIX}:open")
    if open_ids:
        log.warning(f"[{msg.id}] {len(open_ids)} open position(s) already — skip (no pyramiding)")
        return

    log.info(f"[{msg.id}] NEW SIGNAL {sig['side']} {sig['instrument']} "
             f"entry={sig['entry_low']}-{sig['entry_high']} SL={sig['sl']} TPs={sig['tps']}")
    pos = await place_order(msg.id, sig)
    pos["raw"] = text
    pos["posted_at"] = msg.date.astimezone(timezone.utc).isoformat()
    # Only track the signal if at least one slice actually hit the broker. A
    # registration when orders=[] wedges the no-pyramiding guard until the 12h
    # stale sweep — costing every subsequent signal of the session.
    if not pos.get("orders"):
        log.warning(f"[{msg.id}] no orders submitted — not tracking (next signal will be eligible)")
        return
    await r.set(f"{REDIS_PREFIX}:signal:{msg.id}", json.dumps(pos))
    await r.sadd(f"{REDIS_PREFIX}:open", str(msg.id))
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: db.insert_signal(pos, CHANNEL))


async def handle_reply(msg) -> None:
    parent_id = msg.reply_to.reply_to_msg_id
    text = clean(msg.text or "")
    if not text:
        return

    sl_fix = SL_FIX_RE.search(text)
    if sl_fix and "typo" in text.lower():
        await modify_sl(parent_id, float(sl_fix.group(1)))
        return

    outcome = classify_outcome(text)
    if not outcome:
        return
    if outcome.get("tp_hit") == 1 or outcome.get("breakeven"):
        await move_to_breakeven(parent_id)
    if outcome.get("tp_hit") == 3 or "all 3 tps" in text.lower():
        await close_order(parent_id, "tp3")
    if outcome.get("sl_hit"):
        await close_order(parent_id, "sl")


async def hydrate_from_db() -> None:
    """Re-load any open signals from Postgres into the state store.

    Without this, a restart would leave MetaAPI tickets alive but the trader
    blind to subsequent TP/SL/typo replies for those signals. Memory-store
    deployments need this; Redis deployments benefit too when Redis was
    flushed or a different store backend is in use.
    """
    loop = asyncio.get_running_loop()
    positions = await loop.run_in_executor(None, db.load_open_signals)
    if not positions:
        return
    restored = 0
    for pos in positions:
        msg_id = pos["msg_id"]
        key = f"{REDIS_PREFIX}:signal:{msg_id}"
        if await r.get(key):  # already present (e.g. Redis still warm) — skip
            continue
        await r.set(key, json.dumps(pos))
        await r.sadd(f"{REDIS_PREFIX}:open", str(msg_id))
        restored += 1
    if restored:
        log.info(f"Hydrated {restored} open signal(s) from DB")


async def reset_state():
    """Drop all hft:tg:* keys so a fresh test run isn't blocked by stale state."""
    keys = []
    async for k in r.scan_iter(match=f"{REDIS_PREFIX}:*"):
        keys.append(k)
    if keys:
        await r.delete(*keys)
    log.info(f"Reset cleared {len(keys)} state key(s) under {REDIS_PREFIX}:*")


async def main(args):
    global r
    r, kind = await make_store(REDIS_URL)
    log.info(f"State store: {kind}")

    if args.reset:
        await reset_state()

    db.init_schema()
    await hydrate_from_db()
    await sweep_stale_open()  # clear opens that already went stale while we were down

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    log.info(f"Listening to {CHANNEL} (DRY_RUN={DRY_RUN})")

    @client.on(events.NewMessage(chats=CHANNEL))
    async def _handler(event):
        try:
            if event.message.reply_to:
                await handle_reply(event.message)
            else:
                await handle_new_signal(event.message)
        except Exception as e:
            log.exception(f"handler error: {e}")

    asyncio.create_task(_stale_sweeper())
    await client.run_until_disconnected()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram -> MetaAPI live trader")
    parser.add_argument("--reset", action="store_true",
                        help="Clear all hft:tg:* Redis keys before starting (fresh test).")
    parser.add_argument("--reset-only", action="store_true",
                        help="Clear Redis state and exit without listening.")
    args = parser.parse_args()

    if args.reset_only:
        async def _just_reset():
            global r
            r, _ = await make_store(REDIS_URL)
            await reset_state()
        asyncio.run(_just_reset())
    else:
        asyncio.run(main(args))
