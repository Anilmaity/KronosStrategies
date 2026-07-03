"""Offline event-loop simulator for the Strategy Manager (spec 2026-07-02).
Imports PRODUCTION compute_regime + POLICIES — never copies them."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

from strategy_manager.policies import POLICIES
from strategy_manager.regime.regime_engine import (
    compute_regime, FRAME_SPEC, session_for_hour,
)
from shared.market_timing import is_market_closed_utc
from backtest_strategies import s95_session_breakout, s96_h1_momentum, \
    s97_snap_scalper_m5, kronos_session_breakout
from backtest_strategies.base import Signal


@dataclass
class SimConfig:
    start: datetime
    end: datetime
    spread_pts: float = 0.30
    slippage_pts: float = 0.10
    lots: float = 0.02
    kill_switch_usd: float = 150.0
    max_concurrent: int = 3
    regime_cadence_min: int = 5
    gated: bool = True
    slice_rows: dict[str, int] = field(default_factory=lambda: {
        "1d": 130, "4h": 560, "1h": 760, "15m": 980, "5m": 60, "1m": 60,
    })

    @property
    def entry_friction_pts(self) -> float:
        return self.spread_pts / 2 + self.slippage_pts

    def pts_to_usd(self, pts: float) -> float:
        return pts * self.lots * 100.0


@dataclass(frozen=True)
class StratSpec:
    name: str
    module: object
    policy_key: str
    policy_params: dict


STRAT_SPECS: list[StratSpec] = [
    StratSpec(s95_session_breakout.NAME, s95_session_breakout, "session_vol", {}),
    StratSpec(s96_h1_momentum.NAME, s96_h1_momentum, "trending", {}),
    StratSpec(s97_snap_scalper_m5.NAME, s97_snap_scalper_m5, "quiet_fade", {}),
    StratSpec(kronos_session_breakout.NAME, kronos_session_breakout, "session_vol", {}),
]


@dataclass
class GuardState:
    kill_tripped_date: str | None = None
    day_realized_usd: float = 0.0
    day: str = ""


def evaluate_gates(snap, now_utc: datetime, guard: GuardState,
                   open_count: int, cfg: SimConfig,
                   specs: list[StratSpec] | None = None) -> dict[str, tuple[bool, str]]:
    """Mirror of strategy_manager.manager.evaluate_tick guard order."""
    if specs is None:
        specs = STRAT_SPECS

    if not cfg.gated:
        return {s.name: (True, "ungated") for s in specs}

    if snap.market_closed:
        return {s.name: (False, "market closed") for s in specs}

    today = now_utc.date().isoformat()
    if guard.kill_tripped_date == today:
        return {s.name: (False, "kill-switch tripped") for s in specs}

    if open_count >= cfg.max_concurrent:
        return {s.name: (False, f"max concurrent {open_count}/{cfg.max_concurrent}")
                for s in specs}

    out: dict[str, tuple[bool, str]] = {}
    for s in specs:
        out[s.name] = POLICIES[s.policy_key](snap, s.policy_params, now_utc)
    return out


# ── Position lifecycle ─────────────────────────────────────────────────────────


@dataclass
class SimPosition:
    """Open simulated position tracked by the event loop."""
    strategy: str
    side: str               # "BUY" | "SELL"
    entry_time: datetime
    entry_px: float
    sl: float               # current stop level (ratcheted for trailing)
    tp: float
    max_hold_min: float | None
    trailing: bool
    trail_dist: float       # abs(sig.entry_price - sig.stop_loss), fixed at open
    hwm: float              # BUY: running high-water mark; SELL: running low-water mark
    gate_reason: str = ""   # reason string captured from evaluate_gates at open


@dataclass
class TradeRecord:
    """Completed trade produced by step_position."""
    strategy: str
    entry_time: datetime
    side: str
    entry_px: float
    sl: float
    tp: float
    exit_px: float
    exit_time: datetime
    outcome: str            # "TP" | "SL" | "TIME" | "TRAIL" | "OPEN"
    pnl_pts: float
    pnl_usd: float
    gate_reason: str        # captured at entry by event loop; empty when not available


def open_position(sig: Signal, strat_name: str, now: datetime,
                  cfg: SimConfig, fill_price: float | None = None) -> SimPosition:
    """Apply entry friction and initialise a SimPosition.

    Entry friction (spread/2 + slippage) worsens the fill:
      BUY  → pays more  (entry_px = base + friction)
      SELL → receives less (entry_px = base - friction)

    Parameters
    ----------
    fill_price : float | None
        When provided (market-realistic mode), the 1m bar close at detection
        time is used as the fill basis: entry_px = fill_price ± friction.
        When None, the original behaviour is preserved and sig.entry_price is
        used as the fill basis.  Existing unit tests always call without
        fill_price so they remain unaffected.

    SL/TP levels stay exactly as signalled (unchanged by fill_price).
    trail_dist is fixed at |sig.entry_price - sig.stop_loss| and never changes.
    hwm starts at entry_px (the worst fill, used as the initial water mark).
    """
    friction = cfg.entry_friction_pts
    base = fill_price if fill_price is not None else sig.entry_price
    if sig.side == "BUY":
        entry_px = base + friction
    else:  # SELL
        entry_px = base - friction

    return SimPosition(
        strategy=strat_name,
        side=sig.side,
        entry_time=now,
        entry_px=entry_px,
        sl=sig.stop_loss,
        tp=sig.take_profit,
        max_hold_min=sig.max_hold_min,
        trailing=sig.trailing,
        trail_dist=abs(sig.entry_price - sig.stop_loss),
        hwm=entry_px,
    )


def step_position(
    pos: SimPosition,
    bar: pd.Series,
    now: datetime,
    cfg: SimConfig,
) -> tuple[SimPosition | None, TradeRecord | None]:
    """Advance an open position by one 1-minute bar.

    Check order (prevents intra-bar look-ahead — ratchet from this bar's
    high/low is applied LAST and only takes effect on the NEXT bar):

      1. SL touch  — checked against the PRE-UPDATE stop level
      2. TP touch  — only for non-trailing positions; also pre-update
      3. TIME exit — when elapsed >= max_hold_min (bar close price)
      4. Trailing ratchet update — new sl/hwm applied to future bars only

    Exit friction worsens every exit by cfg.entry_friction_pts (same magnitude
    as entry friction):
      BUY exits:  exit_px = level - friction  (sell back at lower price)
      SELL exits: exit_px = level + friction  (buy back at higher price)

    Returns (updated_pos, None) when position stays open,
            (None, TradeRecord) when position is closed this bar.
    """
    friction = cfg.entry_friction_pts
    is_buy = pos.side == "BUY"

    # Snapshot stop level BEFORE any ratchet update (prevents look-ahead).
    pre_sl = pos.sl

    # ── 1. SL touch ───────────────────────────────────────────────────────────
    # Strict comparison (</>): a bar whose low/high exactly equals the stop is
    # NOT treated as a hit (avoids false exits on round-number wicks).
    sl_hit = (bar["low"] < pre_sl) if is_buy else (bar["high"] > pre_sl)

    if sl_hit:
        exit_px = (pre_sl - friction) if is_buy else (pre_sl + friction)
        outcome = "TRAIL" if pos.trailing else "SL"
        pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
        return None, TradeRecord(
            strategy=pos.strategy,
            entry_time=pos.entry_time,
            side=pos.side,
            entry_px=pos.entry_px,
            sl=pre_sl,
            tp=pos.tp,
            exit_px=exit_px,
            exit_time=now,
            outcome=outcome,
            pnl_pts=pnl_pts,
            pnl_usd=cfg.pts_to_usd(pnl_pts),
            gate_reason="",
        )

    # ── 2. TP touch (static positions only) ───────────────────────────────────
    if not pos.trailing:
        tp_hit = (bar["high"] >= pos.tp) if is_buy else (bar["low"] <= pos.tp)
        if tp_hit:
            exit_px = (pos.tp - friction) if is_buy else (pos.tp + friction)
            pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
            return None, TradeRecord(
                strategy=pos.strategy,
                entry_time=pos.entry_time,
                side=pos.side,
                entry_px=pos.entry_px,
                sl=pos.sl,
                tp=pos.tp,
                exit_px=exit_px,
                exit_time=now,
                outcome="TP",
                pnl_pts=pnl_pts,
                pnl_usd=cfg.pts_to_usd(pnl_pts),
                gate_reason="",
            )

    # ── 3. Time exit ──────────────────────────────────────────────────────────
    if pos.max_hold_min is not None:
        elapsed_min = (now - pos.entry_time).total_seconds() / 60.0
        if elapsed_min >= pos.max_hold_min:
            close = bar["close"]
            exit_px = (close - friction) if is_buy else (close + friction)
            pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
            return None, TradeRecord(
                strategy=pos.strategy,
                entry_time=pos.entry_time,
                side=pos.side,
                entry_px=pos.entry_px,
                sl=pos.sl,
                tp=pos.tp,
                exit_px=exit_px,
                exit_time=now,
                outcome="TIME",
                pnl_pts=pnl_pts,
                pnl_usd=cfg.pts_to_usd(pnl_pts),
                gate_reason="",
            )

    # ── 4. Trailing ratchet update (applies to NEXT bar's SL check) ───────────
    if pos.trailing:
        if is_buy:
            new_hwm = max(pos.hwm, bar["high"])
            new_sl = max(pos.sl, new_hwm - pos.trail_dist)
        else:
            new_hwm = min(pos.hwm, bar["low"])
            new_sl = min(pos.sl, new_hwm + pos.trail_dist)
        pos = replace(pos, hwm=new_hwm, sl=new_sl)

    return pos, None


# ── Event loop ─────────────────────────────────────────────────────────────────

# Pandas Timedelta strings for each TF — used by the closed-bar cursor logic.
# A bar is OPEN-stamped: the bar at time T covers [T, T + _TF_DELTA[tf]).
# We include a bar only if it has CLOSED by the time we process the current
# 1m bar (whose open == now_ts, treated as just-closed):
#   open + delta <= now_ts + 1min  ↔  open <= cutoff - delta
# where cutoff = now_ts + 1min.
_TF_DELTA: dict[str, str] = {
    "1m": "1min", "5m": "5min", "15m": "15min",
    "1h": "1h",   "4h": "4h",   "1d":  "1D",
}


def _closed_bar_cursor(times: pd.Series, tf: str, now_ts: pd.Timestamp) -> int:
    """Return the slice cursor for a TF series including only CLOSED bars.

    ``now_ts`` is the open time of the 1m bar being processed, treated as
    just-closed.  Bars are OPEN-stamped, so a TF bar covering
    [open, open + tf_delta) may be included only once it has closed:

        open + tf_delta <= now_ts + 1min   (cutoff)
        <=>  open <= cutoff - tf_delta

    For 1m this reduces to ``open <= now_ts`` (the pre-FIX-2 behaviour).
    ``df.iloc[0:cursor]`` then contains exactly the closed bars.
    """
    cutoff = now_ts + pd.Timedelta("1min")
    return int(times.searchsorted(cutoff - pd.Timedelta(_TF_DELTA[tf]), side="right"))


# Row counts for each TF slice — live-faithful defaults live on SimConfig.slice_rows.
# (Tests pass shallow overrides {1d:40, …} for speed; production uses the defaults.)

# Strategy window widths passed to get_signal.
# _WIN_5M raised to 300 so SESSION_BREAKOUT (needs EMA(240) = 240 M5 bars
# plus a 48-bar slope look-back + margin) can actually fire.
_WIN_1M  = 60
_WIN_5M  = 300
_WIN_15M = 350


@dataclass
class SimResult:
    """Aggregated output from run_sim."""
    trades: list[TradeRecord]
    regime_rows: list[dict]
    kill_trips: list[str]         # ISO date strings of kill-switch trip days
    paused_pct: dict[str, float]  # strategy name -> % bars where gate was False


def load_frames(cache_dir: Path, start, end) -> dict[str, pd.DataFrame]:
    """Load the six XAU_USD parquets, pre-sliced to [start - warmup, end].

    Warmup = max(FRAME_SPEC.values()) + 5 days (125 d) so regime lookbacks
    have enough history from the very first cadence evaluation.

    Parameters
    ----------
    cache_dir : Path  directory containing ``is_XAU_USD_{tf}.parquet`` files.
    start, end : datetime-like (tz-aware UTC or tz-naive treated as UTC).
    """
    warmup = pd.Timedelta(days=max(FRAME_SPEC.values()) + 5)

    start_ts = pd.Timestamp(start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    end_ts = pd.Timestamp(end)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")

    tfs = ["1m", "5m", "15m", "1h", "4h", "1d"]
    frames: dict[str, pd.DataFrame] = {}
    for tf in tfs:
        path = Path(cache_dir) / f"is_XAU_USD_{tf}.parquet"
        df = pd.read_parquet(path)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        mask = (df["time"] >= start_ts - warmup) & (df["time"] <= end_ts)
        frames[tf] = df[mask].reset_index(drop=True)
    return frames


def run_sim(
    frames: dict[str, pd.DataFrame],
    cfg: SimConfig,
    specs: list[StratSpec] | None = None,
) -> SimResult:
    """Replay the Strategy Manager's regime-gated entry logic over historical bars.

    Parameters
    ----------
    frames  : output of load_frames (six TF DataFrames, pre-sliced).
    cfg     : SimConfig controlling gating, friction, kill-switch, etc.
    specs   : list of StratSpec to simulate (default: STRAT_SPECS).
              Task-5 sensitivity runner passes a subset / different policy_params.
    """
    if specs is None:
        specs = STRAT_SPECS

    # Reset module-level dedup state for each strategy (causal replay).
    for spec in specs:
        if hasattr(spec.module, "reset_state"):
            spec.module.reset_state()

    # Normalise start/end to tz-aware UTC timestamps.
    start_ts = pd.Timestamp(cfg.start)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    end_ts = pd.Timestamp(cfg.end)
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")

    df1m = frames["1m"]
    t1m = df1m["time"]  # pd.Series[DatetimeTZDtype[UTC]]

    # Find the 1m bar index range [start, end) — closed bars only.
    i_start = int(t1m.searchsorted(start_ts, side="left"))
    i_end   = int(t1m.searchsorted(end_ts,   side="left"))

    if i_start >= i_end:
        return SimResult(trades=[], regime_rows=[], kill_trips=[],
                         paused_pct={s.name: 0.0 for s in specs})

    # Per-TF time series for cursor advancement.
    tf_series = {tf: frames[tf]["time"] for tf in ["1m", "5m", "15m", "1h", "4h", "1d"]}

    # Mutable simulation state.
    snap = None              # RegimeSnapshot; None until first regime evaluation
    guard = GuardState()
    open_positions: dict[str, SimPosition] = {}  # strategy name -> SimPosition

    # Regime memoization cache: key = (last_d1_ts, last_h4_ts, last_h1_ts,
    # last_m15_ts, hour_utc).  m5/m1 excluded because they only appear in
    # snap.details and change every cadence tick (0% hit rate if included).
    # session / market_closed are patched from now_utc after a cache hit.
    _regime_cache: dict[tuple, object] = {}

    # Accumulators.
    trades: list[TradeRecord] = []
    regime_rows: list[dict] = []
    kill_trips: list[str] = []
    gate_total:  dict[str, int] = {s.name: 0 for s in specs}
    gate_paused: dict[str, int] = {s.name: 0 for s in specs}

    for i in range(i_start, i_end):
        bar    = df1m.iloc[i]
        now_ts = t1m.iloc[i]           # pd.Timestamp UTC
        now    = now_ts.to_pydatetime()  # Python datetime (tz-aware UTC)

        # Advance per-TF cursors — closed-bar semantics (see _closed_bar_cursor).
        cursors = {tf: _closed_bar_cursor(tf_series[tf], tf, now_ts)
                   for tf in tf_series}

        # Day-boundary tracking: reset daily P&L accumulator.
        today = now.strftime("%Y-%m-%d")
        if guard.day != today:
            guard.day = today
            guard.day_realized_usd = 0.0
            # Kill-switch auto-resets next day: evaluate_gates checks
            # guard.kill_tripped_date == today, so once today advances the
            # trip no longer gates entries (no explicit clear needed).

        # ── Regime evaluation (at cadence; also on the very first bar) ────────
        if snap is None or (now.minute % cfg.regime_cadence_min == 0):
            frames_slice = {
                tf: frames[tf].iloc[
                    max(0, cursors[tf] - cfg.slice_rows[tf]):cursors[tf]
                ]
                for tf in ["1d", "4h", "1h", "15m", "5m", "1m"]
            }
            # Memoisation key: last timestamp of D1/H4/H1/M15 frames + UTC hour.
            # M5 and M1 are excluded because they only affect snap.details and
            # change every cadence tick.  session and market_closed are patched
            # from now_utc after a cache hit (they depend on the exact datetime,
            # not on the OHLC frames).
            def _last_ts(tf: str):
                fs = frames_slice[tf]
                return fs["time"].iloc[-1] if len(fs) > 0 else None

            cache_key = (_last_ts("1d"), _last_ts("4h"),
                         _last_ts("1h"), _last_ts("15m"), now.hour)

            if cache_key in _regime_cache:
                cached = _regime_cache[cache_key]
                # Patch the time-dependent fields on a shallow copy.
                snap = replace(cached,
                               session=session_for_hour(now.hour),
                               market_closed=is_market_closed_utc(now))
            else:
                try:
                    snap = compute_regime(frames_slice, now)
                    _regime_cache[cache_key] = snap
                except Exception as exc:
                    log.warning("compute_regime failed at %s: %s", now_ts, exc)

            if snap is not None:
                row: dict = {
                    "time":          now.isoformat(),
                    "d1_bias":       snap.d1_bias,
                    "h4_bias":       snap.h4_bias,
                    "vol_regime":    snap.vol_regime,
                    "trend_regime":  snap.trend_regime,
                    "session":       snap.session,
                    "market_closed": snap.market_closed,
                }
                row.update(snap.details)
                regime_rows.append(row)

        # Inactive until first successful regime evaluation.
        if snap is None:
            continue

        # ── Strategy windows ───────────────────────────────────────────────────
        w1m  = df1m.iloc[max(0, cursors["1m"]  - _WIN_1M) :cursors["1m"]]
        w5m  = frames["5m"].iloc[max(0, cursors["5m"]  - _WIN_5M) :cursors["5m"]]
        w15m = frames["15m"].iloc[max(0, cursors["15m"] - _WIN_15M):cursors["15m"]]

        open_count = len(open_positions)
        gates = evaluate_gates(snap, now, guard, open_count, cfg, specs=specs)

        # Accumulate gate stats (all bars where snap is available).
        for spec in specs:
            gate_total[spec.name] += 1
            if not gates[spec.name][0]:
                gate_paused[spec.name] += 1

        # ── Per-strategy step ──────────────────────────────────────────────────
        for spec in specs:
            pos = open_positions.get(spec.name)

            if pos is not None:
                # Exits always run regardless of gate state.
                updated, rec = step_position(pos, bar, now, cfg)
                if rec is not None:
                    # Patch gate_reason from the position (step_position leaves it "").
                    rec = replace(rec, gate_reason=pos.gate_reason)
                    trades.append(rec)
                    del open_positions[spec.name]
                    # Update daily P&L; trip kill-switch if threshold crossed.
                    guard.day_realized_usd += rec.pnl_usd
                    if (guard.kill_tripped_date != today
                            and guard.day_realized_usd <= -cfg.kill_switch_usd):
                        guard.kill_tripped_date = today
                        kill_trips.append(today)
                else:
                    open_positions[spec.name] = updated

            else:
                gate_ok, reason = gates[spec.name]
                if gate_ok:
                    try:
                        sig = spec.module.get_signal(w1m, w5m, w15m, now)
                    except Exception:
                        sig = None
                    if sig is not None:
                        # Market-realistic fill: use current bar's close.
                        bar_close = float(bar["close"])
                        friction = cfg.entry_friction_pts
                        # Phantom guard: if the market fill has already blown
                        # through TP the trade is un-tradeable live (live entry
                        # manager would place a MARKET order and instantly close
                        # or be rejected).  Skip it without booking any P&L.
                        if sig.side == "BUY":
                            phantom = bar_close + friction >= sig.take_profit
                        else:
                            phantom = bar_close - friction <= sig.take_profit
                        if not phantom:
                            new_pos = open_position(
                                sig, spec.name, now, cfg, fill_price=bar_close
                            )
                            new_pos.gate_reason = reason
                            open_positions[spec.name] = new_pos

    # ── Mark still-open positions as OPEN at sim end ──────────────────────────
    if i_end > i_start:
        last_bar   = df1m.iloc[i_end - 1]
        last_time  = t1m.iloc[i_end - 1].to_pydatetime()
        last_close = float(last_bar["close"])
        friction   = cfg.entry_friction_pts
        for spec in specs:
            pos = open_positions.get(spec.name)
            if pos is None:
                continue
            is_buy  = pos.side == "BUY"
            exit_px = (last_close - friction) if is_buy else (last_close + friction)
            pnl_pts = (exit_px - pos.entry_px) if is_buy else (pos.entry_px - exit_px)
            trades.append(TradeRecord(
                strategy=pos.strategy,
                entry_time=pos.entry_time,
                side=pos.side,
                entry_px=pos.entry_px,
                sl=pos.sl,
                tp=pos.tp,
                exit_px=exit_px,
                exit_time=last_time,
                outcome="OPEN",
                pnl_pts=pnl_pts,
                pnl_usd=cfg.pts_to_usd(pnl_pts),
                gate_reason=pos.gate_reason,
            ))

    paused_pct = {
        s.name: (100.0 * gate_paused[s.name] / gate_total[s.name])
                 if gate_total[s.name] > 0 else 0.0
        for s in specs
    }

    return SimResult(
        trades=trades,
        regime_rows=regime_rows,
        kill_trips=kill_trips,
        paused_pct=paused_pct,
    )

