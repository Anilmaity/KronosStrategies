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

# telethon is imported lazily inside main() so this module can be imported (and
# unit-tested) without the Telegram client dependency installed.
from parse_signals import parse_signal, classify_outcome, looks_like_signal, SL_FIX_RE, clean
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


def _build_accounts() -> list[dict]:
    """Accounts each signal is mirrored onto (copy trading).

    Primary is always present; a second ('neymar2') is added only when both
    TG2_META_ACCOUNT_ID and TG2_META_API_TOKEN are set — so with no TG2_* creds
    the single-account behaviour is unchanged. Each account carries its own
    MetaApiClient (independent token / trading host / spec cache) and risk size.
    """
    primary = mx.MetaApiClient(
        os.getenv("META_API_TOKEN", ""), os.getenv("META_ACCOUNT_ID", ""),
        dry_run=DRY_RUN, label="primary")
    accounts = [{"label": "primary", "client": primary, "risk_usd": RISK_PER_TRADE_USD,
                 "apis": apis._default_dashboard}]  # existing 'Neymar Telegram Copy' strategy

    tg2_acct = os.getenv("TG2_META_ACCOUNT_ID", "").strip()
    tg2_tok = os.getenv("TG2_META_API_TOKEN", "").strip()
    if tg2_acct and tg2_tok:
        client2 = mx.MetaApiClient(tg2_tok, tg2_acct, dry_run=DRY_RUN, label="neymar2")
        # Account 2's own platform strategy ('Neymar Telegram Copy (Account 2)').
        # Provisioned by strategies/db/deploy_neymar2_strategy.py; ids overridable.
        dash2 = apis.ApisDashboard(
            os.getenv("APIS2_USER_STRATEGY_ID", "31c5b1cf-8a25-4f5a-983f-2207cceae4b8"),
            os.getenv("APIS2_USER_BROKER_ID", "45b0c6d8-90ed-4996-bbfd-a92a951966bb"),
            apis.CURRENCYPAIR_ID, enabled=apis._ENABLED, label="neymar2",
            strategy_id=os.getenv("APIS2_STRATEGY_ID", "30427449-9705-406c-820d-2b5ff9d8c003"))
        accounts.append({"label": "neymar2", "client": client2,
                         "risk_usd": float(os.getenv("TG2_RISK_USD", str(RISK_PER_TRADE_USD))),
                         "apis": dash2})
    return accounts


ACCOUNTS = _build_accounts()
ACCOUNTS_BY_LABEL = {a["label"]: a["client"] for a in ACCOUNTS}
APIS_BY_LABEL = {a["label"]: a["apis"] for a in ACCOUNTS}

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


def _signal_entry(sig: dict) -> float:
    """The near-edge entry price place_order uses (sell->low, buy->high)."""
    return sig["entry_low"] if sig["side"] == "sell" else sig["entry_high"]


def _record_signals(sig: dict, *, status: str, reason: str = "",
                    rejection_reason: str = "", signal_at=None,
                    only_labels=None, pos_ids: dict | None = None) -> None:
    """Best-effort StrategySignal row per account so the dashboard Signals tab
    shows this telegram signal (one row per platform Strategy, matching the
    per-account positions). Blocking (DB) — call via run_in_executor. Never
    raises: apis.record_signal swallows its own errors."""
    entry = _signal_entry(sig)
    tp = sig["tps"][0] if sig.get("tps") else None
    for acc in ACCOUNTS:
        if only_labels is not None and acc["label"] not in only_labels:
            continue
        dash = acc.get("apis")
        if dash is None:
            continue
        dash.record_signal(sig["side"], entry, sig["sl"], tp, status=status,
                           reason=reason, rejection_reason=rejection_reason,
                           signal_at=signal_at,
                           position_id=(pos_ids or {}).get(acc["label"]))


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

    loop = asyncio.get_running_loop()

    # ── Build ONE order plan shared by every account ──────────────────────────
    # Mirrored accounts must place IDENTICAL SL/TP levels (only the fill price may
    # differ, by broker spread). So we decide the plan once — using the STRICTEST
    # stops-floor across all accounts (so it satisfies every broker) and the
    # reference (first/primary) account's price for the market-vs-limit call —
    # instead of letting each account re-decide against its own price/spec, which
    # is what made neymar2 widen a TP differently and miss fills primary caught.
    stops = await asyncio.gather(*(
        loop.run_in_executor(None, lambda c=acc["client"]: c.stops_level_price(sig["instrument"]))
        for acc in ACCOUNTS), return_exceptions=True)
    min_ds = [s for s in stops if isinstance(s, (int, float))]
    min_d = max(min_ds) if min_ds else None
    ref_client = ACCOUNTS[0]["client"]
    try:
        plan = await loop.run_in_executor(
            None, lambda: ref_client.build_order_plan(
                sig["side"], sig["instrument"], entry_mid, sig["sl"], sig["tps"],
                min_distance=min_d))
    except Exception:
        log.exception("[%s] build_order_plan raised — each account will self-plan", msg_id)
        plan = None

    async def _submit_for_account(acc: dict) -> tuple[str, list[dict], float]:
        """Submit one account's slices and tag them. Returns (label, slices,
        account_volume). Swallows its own broker errors so one account failing
        can never abort another whose orders may already be live at the broker."""
        client, label = acc["client"], acc["label"]
        total_vol = acc["risk_usd"] / (risk_pts * USD_PER_POINT_PER_LOT)
        total_vol = max(total_vol, MIN_LOT * len(sig["tps"]))
        try:
            submitted = await loop.run_in_executor(
                None,
                lambda c=client, v=total_vol: c.submit_signal_orders(
                    side=sig["side"],
                    symbol=sig["instrument"],
                    entry=entry_mid,
                    sl=sig["sl"],
                    tps=sig["tps"],
                    total_volume=v,
                    msg_id=msg_id,
                    plan=plan,
                ),
            )
        except Exception:
            log.exception("[%s:%s] submit_signal_orders raised", msg_id, label)
            submitted = []
        # Seed per-slice broker bookkeeping the reconciler maintains: a market
        # slice is born "filled", a limit "pending". `observed` flips True once we
        # actually see the slice at the broker, so absence can only conclude a
        # slice we confirmed existed (never a just-placed order not yet listed).
        # Each slice is tagged with its account so modify/close/reconcile route
        # to the right broker.
        for o in submitted:
            o["account"] = label
            o["broker_state"] = "filled" if o.get("kind") == "market" else "pending"
            o["observed"] = False
            o["miss"] = 0
        if not submitted:
            log.warning("[%s:%s] no orders submitted for this account", msg_id, label)
        acct_vol = (round(sum(float(o["volume"]) for o in submitted), 2)
                    if submitted else total_vol)
        return label, submitted, acct_vol

    # Fan out to every account CONCURRENTLY so each makes its entry decision (price
    # fetch + market-vs-limit) at the same wall-clock instant. Sequential
    # submission made later accounts decide several broker round-trips behind the
    # first, fetching a worse price on a fast retrace — our-side fill divergence.
    results = await asyncio.gather(*(_submit_for_account(acc) for acc in ACCOUNTS))

    all_orders: list[dict] = []
    primary_vol: float | None = None
    for label, submitted, acct_vol in results:
        all_orders.extend(submitted)
        if label == "primary":
            primary_vol = acct_vol

    return {
        "msg_id": msg_id,
        "instrument": sig["instrument"],
        "side": sig["side"],
        "entry_lo": sig["entry_low"],
        "entry_hi": sig["entry_high"],
        "entry_mid": entry_mid,
        "sl": sig["sl"],
        "tps": sig["tps"],
        # total_volume drives the (primary-only) dashboard + audit row.
        "total_volume": primary_vol if primary_vol is not None else 0.0,
        "risk_pts": risk_pts,
        "orders": all_orders,
        "status": "submitted" if all_orders else "rejected",
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
        client = ACCOUNTS_BY_LABEL.get(o.get("account", "primary"))
        if client is None:
            continue
        # POSITION_MODIFY only applies once filled; pending limits ignore the call.
        # Safe to attempt either way — MetaAPI returns an error we log and move on.
        await loop.run_in_executor(None, client.modify_position_sl, o["ticket_id"], new_sl)

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

    # Close at the broker AND settle each leg in the same pass. Ending a signal
    # from a channel reply takes it out of the ':open' set, so reconcile_broker
    # can never revisit it — whatever we fail to record here is lost for good.
    # (Signal 11001, 2026-08-10: legs 2-4 sat 'filled' with NULL realized while
    # the broker had paid +25.24/+25.36/+24.72.)
    for o in pos.get("orders", []):
        client = ACCOUNTS_BY_LABEL.get(o.get("account", "primary"))
        if client is None:
            continue
        was_filled = o.get("broker_state") == "filled" or o["kind"] != "limit"
        if o["kind"] == "limit" and o.get("broker_state") != "filled":
            await loop.run_in_executor(None, client.cancel_order, o["ticket_id"])
        else:
            await loop.run_in_executor(None, client.close_position, o["ticket_id"])
        if o.get("broker_state") in ("closed", "cancelled"):
            continue                    # already settled (reconcile got there first)
        if was_filled:
            # We just closed it, so history-deals may not have settled yet;
            # _slice_realized_pnl falls back to the last live snapshot.
            val = await _slice_realized_pnl(loop, client, o)
            o["broker_state"], o["realized_pnl"] = "closed", val
            await loop.run_in_executor(None, db.record_slice_close,
                                       msg_id, o["tp_index"], o["ticket_id"],
                                       reason, val)
        else:
            o["broker_state"] = "cancelled"
            await loop.run_in_executor(None, db.record_slice_close,
                                       msg_id, o["tp_index"], o["ticket_id"],
                                       "cancelled", None)

    pos["status"] = f"closed_{reason}"
    pos["closed_at"] = datetime.now(timezone.utc).isoformat()
    await r.set(key, json.dumps(pos))
    await r.srem(f"{REDIS_PREFIX}:open", str(msg_id))

    # conclude_signal, not close_signal: the latter stamps status only, leaving
    # realized_pnl NULL on every reply-closed signal.
    settled = [o.get("realized_pnl") for o in pos.get("orders", [])
               if o.get("broker_state") == "closed" and o.get("realized_pnl") is not None]
    total = round(sum(settled), 2) if settled else None
    await loop.run_in_executor(None, db.conclude_signal, msg_id, reason, total)

    # Mirror the close into each slice's own dashboard row. The quantity>0 guard
    # in conclude_position makes this a no-op if broker reconciliation already
    # concluded that row, and never-filled legs have no row.
    for acc in ACCOUNTS:
        label, dash = acc["label"], acc.get("apis")
        if dash is None:
            continue
        for o in pos.get("orders", []):
            if o.get("account", "primary") != label:
                continue
            pid = o.get("apis_pos_id")
            if not pid or o.get("broker_state") != "closed":
                continue
            await loop.run_in_executor(
                None, lambda d=dash, p=pid, rl=o.get("realized_pnl"),
                cp=o.get("last_price"), v=float(o["volume"]):
                d.conclude_position(p, rl, cp, pos["side"], v, reason))
    log.info(f"[{msg_id}] CLOSE ({reason}) realized={total}")


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


async def _slice_realized_pnl(loop, client, o: dict):
    """Best-effort broker-TRUE realized PnL for a filled slice that has left (or
    is leaving) the broker.

    Prefers the settled figure from MetaAPI history deals; falls back to the last
    live `profit` snapshot when the deal lookup is unavailable or the position
    hasn't settled in history yet — so a close is never lost waiting on deals.
    Side effects: stamps o['last_price'] with the real close price and
    o['pnl_source'] ('deal' | 'snapshot') when a settled deal is found. Returns
    the realized PnL (float | None).
    """
    bpid = o.get("broker_position_id")
    deal = None
    if client is not None and bpid:
        deal = await loop.run_in_executor(
            None, lambda c=client, p=bpid: c.get_position_realized_pnl(p))
    if deal and deal.get("closed"):
        if deal.get("close_price") is not None:
            o["last_price"] = deal["close_price"]
        o["pnl_source"] = "deal"
        return deal["realized_pnl"]
    o["pnl_source"] = "snapshot"
    return o.get("last_profit")


def _slice_entry_px(pos: dict, o: dict) -> float:
    """This slice's own broker fill price, falling back to the signal entry."""
    fp = o.get("fill_price")
    return float(fp) if fp is not None else float(pos["entry_mid"])


def _slice_broker_ref(o: dict) -> str | None:
    """The MetaAPI *position* id for this slice — what history-deals keys on,
    and what fill_reconciler matches the dashboard row back to. Falls back to
    the order ticket (they coincide for market fills, differ for limit fills).
    """
    return str(o.get("broker_position_id") or o.get("ticket_id") or "") or None


async def _ensure_slice_row(loop, dash, pos: dict, o: dict) -> str | None:
    """Create this slice's dashboard row once, remembering it on the slice.

    One broker position == one dashboard row, so the Orders tab shows the real
    trades (0.03 x 4) instead of one averaged 0.12 fiction.
    """
    if o.get("apis_pos_id"):
        return o["apis_pos_id"]
    pid = await loop.run_in_executor(
        None, lambda d=dash, e=_slice_entry_px(pos, o), v=float(o["volume"]),
        t=_slice_broker_ref(o): d.open_position(pos["side"], e, v, t))
    if pid:
        o["apis_pos_id"] = pid
    return pid


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

    # Build per-account tag maps from each account's own broker truth. Keeping
    # them per-account is the safety-critical bit: a slice is only ever matched
    # against positions/orders from ITS OWN account, never another's. If ANY
    # configured account can't be queried, skip this whole cycle and fail safe.
    acct_maps: dict[str, tuple[dict, dict]] = {}
    for acc in ACCOUNTS:
        client, label = acc["client"], acc["label"]
        positions = await loop.run_in_executor(None, lambda c=client: c.get_open_positions(symbol))
        orders = await loop.run_in_executor(None, lambda c=client: c.get_pending_orders(symbol))
        if positions is None or orders is None:
            return  # could not verify a broker — skip this cycle, fail safe
        pos_by_tag, ord_by_tag = {}, {}
        for p in positions:
            m = _TAG_RE.search(p.get("comment") or "")
            if m:
                pos_by_tag[(int(m.group(1)), int(m.group(2)))] = p
        for o in orders:
            m = _TAG_RE.search(o.get("comment") or "")
            if m:
                ord_by_tag[(int(m.group(1)), int(m.group(2)))] = o
        acct_maps[label] = (pos_by_tag, ord_by_tag)

    for sid in open_ids:
        key = f"{REDIS_PREFIX}:signal:{sid}"
        pos_json = await r.get(key)
        if not pos_json:
            continue
        pos = json.loads(pos_json)
        sid_i = int(sid)
        changed = False

        for o in pos.get("orders", []):
            label = o.get("account", "primary")
            maps = acct_maps.get(label)
            if maps is None:
                continue  # slice's account isn't configured/polled — leave untouched
            pos_by_tag, ord_by_tag = maps
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
                    log.info("[%s:%s] TP%d FILLED @ %.2f (broker)", sid_i, label, idx, fp)
                # Capture the broker's POSITION id (distinct from our order ticket
                # for limit fills) so we can pull this slice's real close deal from
                # history once it leaves the broker.
                if bpos.get("id") is not None:
                    o["broker_position_id"] = bpos.get("id")
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
                        # Prefer the broker's TRUE realized PnL (history deals)
                        # over the stale last-live snapshot; falls back to the
                        # snapshot if deals aren't available/settled yet.
                        pnl = await _slice_realized_pnl(loop, ACCOUNTS_BY_LABEL.get(label), o)
                        reason = _infer_close_reason(o.get("last_price"), pnl, o["tp"], o["sl"])
                        o["broker_state"], o["realized_pnl"] = "closed", pnl
                        await loop.run_in_executor(None, db.record_slice_close,
                                                   sid_i, idx, o["ticket_id"], reason, pnl)
                        log.info("[%s:%s] TP%d CLOSED (~%s, pnl=%s via %s) (broker)",
                                 sid_i, label, idx, reason, pnl, o.get("pnl_source"))
                    else:                              # pending -> gone, never filled
                        o["broker_state"] = "cancelled"
                        await loop.run_in_executor(None, db.record_slice_close,
                                                   sid_i, idx, o["ticket_id"], "cancelled", None)
                        log.info("[%s:%s] TP%d CANCELLED unfilled (broker)", sid_i, label, idx)
                    changed = True

        if changed:
            await r.set(key, json.dumps(pos))

        # ── Dashboard mirror (apis_position) — one row PER BROKER SLICE ───
        # Each TP leg is a separate position at the broker, so it gets its own
        # dashboard row carrying its own volume, fill price, broker position id
        # and PnL. Clubbing the legs into one averaged row hid the real trades
        # and — because the clubbed row referenced only slice 1's ticket — let
        # fill_reconciler restate the whole signal from that one slice.
        # Created lazily on first fill; live PnL refreshed each poll.
        # Best-effort — never blocks reconciliation.
        mirrored = False
        for acc in ACCOUNTS:
            label, dash = acc["label"], acc.get("apis")
            if dash is None:
                continue
            for o in pos.get("orders", []):
                if o.get("account", "primary") != label or o.get("broker_state") != "filled":
                    continue
                had = bool(o.get("apis_pos_id"))
                pid = await _ensure_slice_row(loop, dash, pos, o)
                if not pid:
                    continue
                mirrored = mirrored or not had
                await loop.run_in_executor(
                    None, lambda d=dash, p=pid, lp=o.get("last_price"),
                    pl=o.get("last_profit"): d.update_live(p, lp, pl))
        if mirrored:
            await r.set(key, json.dumps(pos))

        # Conclude the signal once EVERY slice (across all accounts) is terminal.
        states = [o.get("broker_state") for o in pos.get("orders", [])]
        if states and all(s in ("closed", "cancelled") for s in states):
            closed_all = [o for o in pos["orders"] if o.get("broker_state") == "closed"]
            # Aggregate realized PnL across all accounts for the audit row.
            all_pnls = [o.get("realized_pnl") for o in closed_all if o.get("realized_pnl") is not None]
            total = round(sum(all_pnls), 2) if all_pnls else None
            # Outcome (tp vs sl) is a market property; use the primary account's
            # realized sign, falling back to the aggregate.
            primary_closed = [o for o in pos["orders"]
                              if o.get("account", "primary") == "primary" and o.get("broker_state") == "closed"]
            primary_pnls = [o.get("realized_pnl") for o in primary_closed if o.get("realized_pnl") is not None]
            primary_total = round(sum(primary_pnls), 2) if primary_pnls else None
            if closed_all:
                basis = primary_total if primary_total is not None else total
                reason = "broker_tp" if (basis is not None and basis > 0) else "broker_sl"
            else:
                reason = "broker_cancelled"
            pos["status"] = f"closed_{reason}"
            pos["closed_at"] = datetime.now(timezone.utc).isoformat()
            await r.set(key, json.dumps(pos))
            await r.srem(f"{REDIS_PREFIX}:open", str(sid_i))
            await loop.run_in_executor(None, db.conclude_signal, sid_i, reason, total)
            # Flatten EACH slice's own dashboard row with its own realized PnL.
            # If a broker filled then closed a leg entirely between polls we may
            # never have created its row — create it now so the trade still shows.
            # Cancelled legs never traded, so they get no row.
            for acc in ACCOUNTS:
                label, dash = acc["label"], acc.get("apis")
                if dash is None:
                    continue
                for o in pos["orders"]:
                    if o.get("account", "primary") != label or o.get("broker_state") != "closed":
                        continue
                    pid = await _ensure_slice_row(loop, dash, pos, o)
                    if not pid:
                        continue
                    await loop.run_in_executor(
                        None, lambda d=dash, p=pid, rl=o.get("realized_pnl"),
                        cp=o.get("last_price"), v=float(o["volume"]):
                        d.conclude_position(p, rl, cp, pos["side"], v, reason))
            await r.set(key, json.dumps(pos))
            log.info("[%s] CONCLUDED from broker: %s pnl=%s (all-acct %s)",
                     sid_i, reason, primary_total, total)


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

    A signal is "live" if ANY account reports an open position whose comment
    carries its `tg-<msg_id>-` tag; otherwise it is "unfilled" (still pending,
    or already gone) and may be superseded — we never supersede while the signal
    holds a live position on any mirrored account. The bool return is `ok`: False
    means a broker position query failed, so the caller must fail safe and keep
    the no-pyramiding guard rather than cancel anything it could not verify.
    """
    loop = asyncio.get_running_loop()
    symbol = next(iter(ALLOWED_INSTRUMENTS))
    all_positions: list[dict] = []
    for acc in ACCOUNTS:
        client = acc["client"]
        positions = await loop.run_in_executor(None, lambda c=client: c.get_open_positions(symbol))
        if positions is None:
            return [], [], False  # could not verify an account — caller must not cancel
        all_positions.extend(positions)
    live, unfilled = [], []
    for sid in open_ids:
        if not await r.get(f"{REDIS_PREFIX}:signal:{sid}"):
            unfilled.append(sid)  # dangling id, no detail row — safe to drop
            continue
        needle = f"tg-{sid}-"
        if any(needle in (p.get("comment") or "") for p in all_positions):
            live.append(sid)
        else:
            unfilled.append(sid)
    return live, unfilled, True


async def handle_new_signal(msg) -> None:
    text = clean(msg.text or "")
    sig = parse_signal(text)
    if not sig:
        # A message that reads like a signal (instrument + side + SL) but the
        # grammar can't parse is an UNHANDLED FORMAT, not chatter — shout so it
        # never again vanishes in silence the way the TP1/TP2/TP3 form did.
        if looks_like_signal(text):
            log.warning("[%s] UNPARSEABLE signal-like message — not traded: %r", msg.id, text)
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
    # Strategy-Manager gate: the primary account's UserStrategy row is this
    # bot's on/off switch, flipped by the manager loop while armed. New entries
    # only — replies, closes, sweeps, and broker reconciliation are never gated.
    loop = asyncio.get_running_loop()
    allowed = await loop.run_in_executor(
        None, APIS_BY_LABEL["primary"].entries_allowed)
    if allowed is not True:
        why = ("manager_gate (strategy paused)" if allowed is False
               else "manager_gate (DB unreachable — fail-closed)")
        log.warning(f"[{msg.id}] {why} — skip")
        await loop.run_in_executor(None, lambda: _record_signals(
            sig, status="REJECTED", rejection_reason=why,
            signal_at=msg.date.astimezone(timezone.utc).isoformat()))
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
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: _record_signals(
                sig, status="REJECTED",
                rejection_reason="open_position_cap (no pyramiding)",
                signal_at=msg.date.astimezone(timezone.utc).isoformat()))
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
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: _record_signals(
            sig, status="REJECTED",
            rejection_reason="no orders submitted (broker rejection)",
            signal_at=pos.get("posted_at")))
        return
    await r.set(f"{REDIS_PREFIX}:signal:{msg.id}", json.dumps(pos))
    await r.sadd(f"{REDIS_PREFIX}:open", str(msg.id))
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: db.insert_signal(pos, CHANNEL))
    # Surface this signal on the dashboard Signals tab: one row per account that
    # got orders (PLACED), one per account that the broker rejected (REJECTED).
    placed = {o.get("account", "primary") for o in pos["orders"]}
    reject = {a["label"] for a in ACCOUNTS} - placed
    reason = (f"TG @{CHANNEL} | zone {sig['entry_low']}-{sig['entry_high']} "
              f"| SL {sig['sl']} | TP {sig['tps']}")
    await loop.run_in_executor(None, lambda: _record_signals(
        sig, status="PLACED", reason=reason,
        signal_at=pos.get("posted_at"), only_labels=placed))
    if reject:
        await loop.run_in_executor(None, lambda: _record_signals(
            sig, status="REJECTED", rejection_reason="no orders on this account",
            signal_at=pos.get("posted_at"), only_labels=reject))


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
    from telethon import TelegramClient, events  # runtime-only dependency

    r, kind = await make_store(REDIS_URL)
    log.info(f"State store: {kind}")
    log.info("Fanning out each signal to accounts: %s",
             [a["label"] for a in ACCOUNTS])

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
