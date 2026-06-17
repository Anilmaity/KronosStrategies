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
import re
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
import apis_persist as apis
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
# State-key namespace. A second trader copy (parallel account, same channel) sets
# its own prefix so the two never share :open / :signal:* keys when a real Redis
# backend is configured. (The default in-memory store is already per-process.)
REDIS_PREFIX = os.getenv("TG_REDIS_PREFIX", "hft:tg")

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

# Broker reconciliation: poll MetaAPI for the *actual* fate of each tracked
# slice (filled? closed? live PnL?) instead of inferring outcomes only from the
# channel's text replies. A slice that disappears from the broker is confirmed
# terminal only after ABSENT_CONFIRM_POLLS consecutive misses, so a single
# transient empty read can't fake a close.
BROKER_POLL_SEC = int(os.getenv("BROKER_POLL_SEC", "30"))
ABSENT_CONFIRM_POLLS = int(os.getenv("ABSENT_CONFIRM_POLLS", "2"))
_TAG_RE = re.compile(r"tg-(\d+)-tp(\d+)")

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
    """Place one MetaAPI order per TP at the NEAR edge of the entry zone, shared SL.

    The near edge is the price the market touches first as it retraces into the
    zone, so a shallower pullback fills us and we participate more often:
      sell -> entry_low  (price rises into the zone, hits the low edge first)
      buy  -> entry_high (price falls into the zone, hits the high edge first)
    Risk/volume, breakeven, and the recorded entry all derive from this price.
    """
    entry_mid = sig["entry_low"] if sig["side"] == "sell" else sig["entry_high"]
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

    # Seed per-slice broker bookkeeping the reconciler maintains: a market slice
    # is born "filled", a limit "pending". `observed` flips True once we actually
    # see the slice at the broker, so absence can only conclude a slice we have
    # confirmed existed (never a just-placed order the broker hasn't listed yet).
    for o in submitted:
        o["broker_state"] = "filled" if o.get("kind") == "market" else "pending"
        o["observed"] = False
        o["miss"] = 0

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
    # Mirror the close into the dashboard position. Realized PnL from the last
    # broker snapshot of the filled slices, if any. The quantity>0 guard in
    # conclude_position makes this a no-op if broker reconciliation already
    # concluded it, and unfilled superseded/expired signals have no row.
    apis_id = pos.get("apis_pos_id")
    if apis_id:
        filled = [o for o in pos.get("orders", []) if o.get("broker_state") == "filled"]
        rp = [o.get("realized_pnl") if o.get("realized_pnl") is not None else o.get("last_profit")
              for o in filled]
        rp = [x for x in rp if x is not None]
        realized = round(sum(rp), 2) if rp else None
        close_price = next((o.get("last_price") for o in reversed(pos.get("orders", []))
                            if o.get("last_price") is not None), None)
        await loop.run_in_executor(
            None, lambda: apis.conclude_position(apis_id, realized, close_price,
                                                 pos["side"], pos.get("total_volume"), reason))
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


def _infer_close_reason(last_price, last_profit, tp, sl) -> str:
    """Best-effort tp/sl call for a filled slice that left the broker.

    Realized PnL/close deals are not exposed over REST on this account, so we
    use the last live snapshot before the position vanished: a positive last
    profit means it ran to target, negative means it was stopped. Falls back to
    whichever of tp/sl the last seen price was nearer when profit is unknown.
    """
    if last_profit is not None:
        return "tp" if last_profit > 0 else "sl"
    if last_price is None:
        return "tp"
    return "tp" if abs(last_price - tp) <= abs(last_price - sl) else "sl"


async def reconcile_broker() -> None:
    """Reconcile tracked signals against broker positions/orders (broker truth).

    Read-only against MetaAPI. Detects fills (pending limit -> live position),
    snapshots live PnL, and confirms slice closes/cancels. When every slice of a
    signal is terminal it concludes the signal from broker state — recording the
    real outcome + PnL and unblocking the no-pyramiding guard on reality, rather
    than waiting for a channel reply or the 12h stale-sweep.
    """
    open_ids = await r.smembers(f"{REDIS_PREFIX}:open")
    if not open_ids:
        return
    loop = asyncio.get_running_loop()
    symbol = next(iter(ALLOWED_INSTRUMENTS))
    positions = await loop.run_in_executor(None, lambda: mx.get_open_positions(symbol))
    orders = await loop.run_in_executor(None, lambda: mx.get_pending_orders(symbol))
    if positions is None or orders is None:
        return  # could not verify broker — skip this cycle, fail safe

    pos_by_tag, ord_by_tag = {}, {}
    for p in positions:
        m = _TAG_RE.search(p.get("comment") or "")
        if m:
            pos_by_tag[(int(m.group(1)), int(m.group(2)))] = p
    for o in orders:
        m = _TAG_RE.search(o.get("comment") or "")
        if m:
            ord_by_tag[(int(m.group(1)), int(m.group(2)))] = o

    for sid in open_ids:
        key = f"{REDIS_PREFIX}:signal:{sid}"
        pos_json = await r.get(key)
        if not pos_json:
            continue
        pos = json.loads(pos_json)
        sid_i = int(sid)
        changed = False

        for o in pos.get("orders", []):
            idx = o["tp_index"]
            prev = o.get("broker_state") or ("filled" if o.get("kind") == "market" else "pending")
            bpos = pos_by_tag.get((sid_i, idx))
            bord = ord_by_tag.get((sid_i, idx))

            if bpos is not None:                       # slice is a live position
                o["observed"] = True
                o["miss"] = 0
                if prev != "filled":
                    fp = float(bpos.get("openPrice") or o["entry"])
                    o["broker_state"], o["kind"], o["fill_price"] = "filled", "market", fp
                    await loop.run_in_executor(None, db.record_fill, sid_i, idx, o["ticket_id"], fp)
                    log.info("[%s] TP%d FILLED @ %.2f (broker)", sid_i, idx, fp)
                o["last_profit"] = bpos.get("profit")
                o["last_price"] = bpos.get("currentPrice")
                changed = True
            elif bord is not None:                     # still a pending order
                if not o.get("observed"):
                    o["observed"] = True               # persist so absence can later conclude it
                    changed = True
                o["miss"] = 0
                if prev != "pending":
                    o["broker_state"] = "pending"
                    changed = True
            else:                                      # absent from the broker
                if prev in ("closed", "cancelled") or not o.get("observed"):
                    continue                           # already terminal, or never seen yet
                o["miss"] = o.get("miss", 0) + 1
                changed = True  # persist the counter so it accrues across polls
                if o["miss"] >= ABSENT_CONFIRM_POLLS:
                    if prev == "filled":
                        reason = _infer_close_reason(o.get("last_price"), o.get("last_profit"),
                                                     o["tp"], o["sl"])
                        pnl = o.get("last_profit")
                        o["broker_state"], o["realized_pnl"] = "closed", pnl
                        await loop.run_in_executor(None, db.record_slice_close,
                                                   sid_i, idx, o["ticket_id"], reason, pnl)
                        log.info("[%s] TP%d CLOSED (~%s, pnl~%s) (broker)", sid_i, idx, reason, pnl)
                    else:                              # pending -> gone, never filled
                        o["broker_state"] = "cancelled"
                        await loop.run_in_executor(None, db.record_slice_close,
                                                   sid_i, idx, o["ticket_id"], "cancelled", None)
                        log.info("[%s] TP%d CANCELLED unfilled (broker)", sid_i, idx)
                    changed = True

        if changed:
            await r.set(key, json.dumps(pos))

        # ── Dashboard mirror (apis_position) ──────────────────────────────
        # Reflect real broker state into the apis schema the web dashboard
        # reads (the same one the other live strategies write to). Created
        # lazily on first fill so only genuine market entries show; live PnL
        # refreshed each poll. Best-effort — never blocks reconciliation.
        filled_slices = [o for o in pos.get("orders", []) if o.get("broker_state") == "filled"]
        if filled_slices:
            if not pos.get("apis_pos_id"):
                total_vol = round(sum(float(o["volume"]) for o in pos.get("orders", [])), 2)
                first_ticket = str(pos["orders"][0].get("ticket_id")) if pos.get("orders") else None
                apis_id = await loop.run_in_executor(
                    None, lambda: apis.open_position(pos["side"], pos["entry_mid"], total_vol, first_ticket))
                if apis_id:
                    pos["apis_pos_id"] = apis_id
                    await r.set(key, json.dumps(pos))
            if pos.get("apis_pos_id"):
                live_pnls = [o.get("last_profit") for o in filled_slices if o.get("last_profit") is not None]
                live_pnl = round(sum(live_pnls), 2) if live_pnls else None
                prices = [o.get("last_price") for o in filled_slices if o.get("last_price") is not None]
                last_price = prices[-1] if prices else None
                await loop.run_in_executor(
                    None, lambda: apis.update_live(pos.get("apis_pos_id"), last_price, live_pnl))

        # Conclude the signal once every slice is terminal at the broker.
        states = [o.get("broker_state") for o in pos.get("orders", [])]
        if states and all(s in ("closed", "cancelled") for s in states):
            filled = [o for o in pos["orders"] if o.get("broker_state") == "closed"]
            if filled:
                pnls = [o.get("realized_pnl") for o in filled if o.get("realized_pnl") is not None]
                total = round(sum(pnls), 2) if pnls else None
                reason = "broker_tp" if (total is not None and total > 0) else "broker_sl"
            else:
                total, reason = None, "broker_cancelled"
            pos["status"] = f"closed_{reason}"
            pos["closed_at"] = datetime.now(timezone.utc).isoformat()
            await r.set(key, json.dumps(pos))
            await r.srem(f"{REDIS_PREFIX}:open", str(sid_i))
            await loop.run_in_executor(None, db.conclude_signal, sid_i, reason, total)
            # Flatten the dashboard position with the realized PnL. If broker
            # filled then closed entirely between polls we may never have
            # created the row — recover it (or create it) so the concluded
            # trade still shows.
            apis_id = pos.get("apis_pos_id")
            if not apis_id and reason != "broker_cancelled":
                apis_id = await loop.run_in_executor(None, apis.find_open_position_id)
                if not apis_id:
                    total_vol = round(sum(float(o["volume"]) for o in pos.get("orders", [])), 2)
                    apis_id = await loop.run_in_executor(
                        None, lambda: apis.open_position(pos["side"], pos["entry_mid"], total_vol))
            close_price = next((o.get("last_price") for o in reversed(pos.get("orders", []))
                                if o.get("last_price") is not None), None)
            await loop.run_in_executor(
                None, lambda: apis.conclude_position(apis_id, total, close_price,
                                                     pos["side"], pos.get("total_volume"), reason))
            log.info("[%s] CONCLUDED from broker: %s pnl=%s", sid_i, reason, total)


async def _position_poller() -> None:
    """Periodically reconcile tracked signals against broker truth."""
    while True:
        await asyncio.sleep(BROKER_POLL_SEC)
        try:
            await reconcile_broker()
        except Exception as e:
            log.exception(f"position poller error: {e}")


async def _classify_open_signals(open_ids) -> tuple[list[str], list[str], bool]:
    """Split currently-open signals into (live, unfilled) by broker truth.

    The channel re-enters the same zone repeatedly, firing a fresh signal while
    a previous one is still an UNFILLED pending limit. Holding one position at a
    time, we want the new signal to take over those unfilled limits — but never
    to cancel a slice that has actually filled into a live market position.

    A signal is "live" if the broker reports an open position whose comment
    carries its `tg-<msg_id>-` tag; otherwise it is "unfilled" (still pending,
    or already gone) and may be superseded. The bool return is `ok`: False means
    the broker position query failed, so the caller must fail safe and keep the
    no-pyramiding guard rather than cancel anything it could not verify.
    """
    loop = asyncio.get_running_loop()
    positions = await loop.run_in_executor(
        None, lambda: mx.get_open_positions(next(iter(ALLOWED_INSTRUMENTS))))
    if positions is None:
        return [], [], False  # could not verify — caller must not cancel
    live, unfilled = [], []
    for sid in open_ids:
        if not await r.get(f"{REDIS_PREFIX}:signal:{sid}"):
            unfilled.append(sid)  # dangling id, no detail row — safe to drop
            continue
        needle = f"tg-{sid}-"
        if any(needle in (p.get("comment") or "") for p in positions):
            live.append(sid)
        else:
            unfilled.append(sid)
    return live, unfilled, True


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
        live, unfilled, ok = await _classify_open_signals(open_ids)
        if not ok:
            log.warning(f"[{msg.id}] {len(open_ids)} open signal(s), broker check failed "
                        f"— skip (no pyramiding, fail-safe)")
            return
        if live:
            log.warning(f"[{msg.id}] {len(live)} live position(s) in market — skip (no pyramiding)")
            return
        # All prior opens are unfilled pending limits (never entered the market).
        # Supersede them so this fresh re-entry can take over the single slot.
        for sid in unfilled:
            log.info(f"[{msg.id}] superseding unfilled signal {sid} (pending limit, not in market)")
            await close_order(int(sid), "superseded")

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
    asyncio.create_task(_position_poller())
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
