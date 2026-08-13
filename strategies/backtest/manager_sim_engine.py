"""Offline event-loop simulator for the Strategy Manager (spec 2026-07-02).
Imports PRODUCTION compute_regime + POLICIES — never copies them."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

from strategy_manager.policies import POLICIES
from strategy_manager.regime.regime_engine import (
    compute_regime, FRAME_SPEC, session_for_hour,
)
from shared.market_timing import is_market_closed_utc
from shared.gate_rules import (
    in_news_blackout, sl_too_tight, parse_utc_windows, entry_drift_exceeded,
    MIN_SL_DIST_PTS, NEWS_BLACKOUT_UTC,
)
from backtest_strategies import s95_session_breakout, s96_h1_momentum, \
    kronos_session_breakout, s100_m3_combo
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
    model_entry_gates: bool = True
    # Exit-resolution granularity: "1m" (default, historical behaviour) resolves
    # each minute against one bar's high/low with SL checked before TP; "5s"
    # replays the minute's S5 bars in sequence so the outcome comes from the
    # observed order (2026-08-12 fidelity spec). Default-OFF keeps every stored
    # baseline and the Manager Backtest tab directly comparable.
    exec_resolution: str = "1m"
    # Entry-drift gate (live rejects when the market ran past the signal level
    # before the order lands). Inherently sub-minute, so it is only modelable
    # with 5s data — default-OFF per the repo's new-gate discipline.
    model_entry_drift: bool = False
    # Fill entries at the REAL quote (BUY at ask, SELL at bid) from the S5 bar
    # at order time, instead of mid +/- a scalar friction. Requires 5s data
    # carrying bid_c/ask_c (OANDA price=MBA); silently falls back to the
    # friction model when the columns are absent (e.g. tick-derived bars).
    sided_fills: bool = False
    # Charge only slippage on EXITS, not spread/2 + slippage. A stop/target
    # level is already an executable price (the broker triggers on the far side
    # and fills there), so subtracting a half-spread again double-charges it.
    # Measured 2026-08-12: live stops filled AT or slightly better than the
    # level, while the sim booked level - 0.41.
    exit_slippage_only: bool = False
    # Stamp entry_time at the fill moment (the bar's CLOSE) rather than the
    # bar's open. The engine fills at bar["close"], which is realized one minute
    # after now_ts, so max_hold was measured from a minute too early and every
    # TIME exit fired ~60s premature (measured 2026-08-12: sim 08:47:00 vs live
    # 08:48:06). Price-triggered exits are unaffected. Default-OFF because it
    # shifts existing TIME-exit baselines.
    entry_time_at_bar_close: bool = False
    # Realized P&L from sources the sim does NOT simulate (e.g. the Telegram
    # copy-trader), as (utc_time, usd) events. Live's kill-switch sums ALL
    # account P&L, so without these the sim's daily loss is understated and it
    # keeps trading after live had already stopped (observed 2026-08-12: the
    # copy trade's -$76.50 tripped live at 09:11; the sim traded on all day).
    external_pnl: list = field(default_factory=list)
    # Seconds between the M1 close and the order actually landing; the S5 bar at
    # this offset supplies the "LTP at order time" the live gate compares against.
    entry_latency_s: float = 5.0
    # Defaults are single-sourced from shared.gate_rules (same env var names
    # as live entry_manager) so a box-level env override can never make the
    # sim silently model a different gate than live (2026-08 fidelity fix).
    min_sl_dist_pts: float = MIN_SL_DIST_PTS
    news_blackout_utc: str = NEWS_BLACKOUT_UTC
    slice_rows: dict[str, int] = field(default_factory=lambda: {
        "1d": 130, "4h": 560, "1h": 760, "15m": 980, "5m": 60, "1m": 60,
    })

    @property
    def entry_friction_pts(self) -> float:
        return self.spread_pts / 2 + self.slippage_pts

    @property
    def exit_friction_pts(self) -> float:
        """Friction applied when closing. Defaults to the entry figure so the
        historical behaviour is unchanged unless exit_slippage_only is set."""
        return self.slippage_pts if self.exit_slippage_only             else self.entry_friction_pts

    def pts_to_usd(self, pts: float) -> float:
        return pts * self.lots * 100.0


@dataclass(frozen=True)
class StratSpec:
    name: str
    module: object
    policy_key: str
    policy_params: dict
    # Per-strategy lookback depth. None = use the engine default (_WIN_*).
    # Live sets these PER STRATEGY in compose.yml, so a single global window
    # makes the sim generate a different signal set than live — a
    # trade-selection divergence (2026-08-13 fidelity fix).
    win_1m: int | None = None
    win_5m: int | None = None
    win_15m: int | None = None


STRAT_SPECS: list[StratSpec] = [
    StratSpec(s95_session_breakout.NAME, s95_session_breakout, "session_vol", {}),
    StratSpec(s96_h1_momentum.NAME, s96_h1_momentum, "trending", {}),
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
                  cfg: SimConfig, fill_price: float | None = None,
                  exact_fill: float | None = None) -> SimPosition:
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
    if exact_fill is not None:
        # A real quote (ask for BUY / bid for SELL) already embeds the spread —
        # adding scalar friction on top would double-charge it.
        entry_px = exact_fill
    elif sig.side == "BUY":
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


def ltp_at_order_time(s5_slice: pd.DataFrame | None, latency_s: float,
                      fallback: float) -> float:
    """The price the live gate would have seen when the order landed.

    Live fetches the LTP a few seconds after the M1 close, so the drift gate
    compares the signal level against a price that has already moved. Picks the
    S5 bar covering `latency_s` past the minute's start; falls back to the M1
    close when 5s data is unavailable (drift then reads as zero, i.e. the gate
    cannot fire — fail-open, matching live's no-price behaviour).
    """
    if s5_slice is None or len(s5_slice) == 0:
        return fallback
    idx = min(int(latency_s // 5), len(s5_slice) - 1)
    return float(s5_slice["c"].iloc[idx])


def sided_fill_price(s5_slice: pd.DataFrame | None, latency_s: float,
                     side: str) -> float | None:
    """The real quote a market order would cross at: ask for BUY, bid for SELL.

    Returns None when 5s data is absent or carries no quote columns, so the
    caller can fall back to the mid-plus-friction model.
    """
    if s5_slice is None or len(s5_slice) == 0:
        return None
    if "ask_c" not in s5_slice.columns or "bid_c" not in s5_slice.columns:
        return None
    idx = min(int(latency_s // 5), len(s5_slice) - 1)
    col = "ask_c" if side == "BUY" else "bid_c"
    value = s5_slice[col].iloc[idx]
    if pd.isna(value):
        return None
    return float(value)


def step_exit(
    pos: SimPosition,
    bar: pd.Series,
    now: datetime,
    cfg: SimConfig,
    s5_slice: pd.DataFrame | None = None,
    ambiguity: dict | None = None,
) -> tuple[SimPosition | None, TradeRecord | None]:
    """Advance an open position by one minute at the configured resolution.

    Dispatch point for the 5s fidelity work. In "1m" mode this is exactly
    step_position, so the flag is inert by default; in "5s" mode the minute's
    S5 bars are replayed in sequence.

    Falls back to the M1 bar whenever the 5s slice is missing or empty — a data
    gap must degrade to today's behaviour, never skip the exit check.
    """
    if (cfg.exec_resolution or "1m") == "5s" and s5_slice is not None \
            and len(s5_slice) > 0:
        # Lazy import: s5_exec imports SimPosition/TradeRecord from this module,
        # so a top-level import here would be circular.
        from backtest.s5_exec import walk_exit
        return walk_exit(pos, s5_slice, cfg, ambiguity=ambiguity)
    return step_position(pos, bar, now, cfg)


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
# _WIN_1M tracks S100 M3-combo's floor: its get_signal returns None until
# len(w1m) >= MIN_BARS_1M (= 3*_MIN_M3 = 642, the EMA200 warm-up on ~214 M3
# bars; auto-1560 if the ER gate is ever armed). Live runs S100 with
# RESEARCH_WIN_1M >= MIN_BARS_1M; the sim was stuck at 60 and generated ZERO
# S100 signals (2026-08-02 fidelity follow-up). Import the constant so the two
# can't drift.
_WIN_1M  = max(60, s100_m3_combo.MIN_BARS_1M)
_WIN_5M  = 300
_WIN_15M = 350


# Live lookback depths, transcribed from compose.yml (2026-08-13). The box may
# have drifted from the repo's compose — reconfirm against the running services
# before leaning on these for a roster decision.
LIVE_WINDOWS: dict[str, dict[str, int]] = {
    "KRONOS_S93_FVG_SCALP":      {"win_5m": 160},
    "KRONOS_S99_MSS_FVG":        {"win_5m": 160},
    "KRONOS_S94_SWEEP_REVERSAL": {"win_5m": 1500},
    "KRONOS_S100_M3_COMBO":      {"win_1m": 700},
}


def windows_for(spec: StratSpec, frames: dict, cursors: dict
                ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(w1m, w5m, w15m) for one strategy, honouring its per-strategy depth.

    Each window is the CLOSED bars ending at that timeframe's cursor, clamped to
    the history actually available.
    """
    n1 = spec.win_1m or _WIN_1M
    n5 = spec.win_5m or _WIN_5M
    n15 = spec.win_15m or _WIN_15M
    c1, c5, c15 = cursors["1m"], cursors["5m"], cursors["15m"]
    return (
        frames["1m"].iloc[max(0, c1 - n1):c1],
        frames["5m"].iloc[max(0, c5 - n5):c5],
        frames["15m"].iloc[max(0, c15 - n15):c15],
    )


@dataclass
class SimResult:
    """Aggregated output from run_sim."""
    trades: list[TradeRecord]
    regime_rows: list[dict]
    kill_trips: list[str]         # ISO date strings of kill-switch trip days
    paused_pct: dict[str, float]  # strategy name -> % bars where gate was False
    # strategy name -> {"sl_too_tight": n, "news_blackout": n} entry-quality
    # gate rejections (Task 2, manager-backtest-fidelity). Defaulted so the
    # many pre-existing SimResult(...) call sites across the test suite
    # (test_mbt_results.py, test_manager_sim_report.py, test_mbt_worker.py)
    # keep constructing without this field.
    entry_gate_rejects: dict[str, dict[str, int]] = field(default_factory=dict)
    # 5s exec diagnostics (2026-08-12 fidelity spec): "ambiguous_bars" counts
    # single S5 bars that touched BOTH levels, i.e. the residual ordering
    # uncertainty 5s data cannot resolve. Reported, never hidden. Defaulted so
    # existing SimResult(...) call sites keep constructing.
    exec_ambiguity: dict[str, int] = field(default_factory=dict)


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
    progress_cb=None,
) -> SimResult:
    """Replay the Strategy Manager's regime-gated entry logic over historical bars.

    Parameters
    ----------
    frames  : output of load_frames (six TF DataFrames, pre-sliced).
    cfg     : SimConfig controlling gating, friction, kill-switch, etc.
    specs   : list of StratSpec to simulate (default: STRAT_SPECS).
              Task-5 sensitivity runner passes a subset / different policy_params.
    progress_cb : optional Callable[[float], None], invoked every 1000
              processed M1 bars and once at loop end with done/total in
              [0, 1]. Exceptions raised by the callback propagate out of
              run_sim — that is the caller's cancellation path. None (the
              default) leaves behavior byte-identical.
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
                         paused_pct={s.name: 0.0 for s in specs},
                         entry_gate_rejects={})

    # Per-TF time series for cursor advancement.
    tf_series = {tf: frames[tf]["time"] for tf in ["1m", "5m", "15m", "1h", "4h", "1d"]}

    # Optional S5 frame for 5s exit resolution. Hoisted out of the hot loop; when
    # absent or in "1m" mode every minute falls back to the M1 bar, so the flag
    # stays inert and a missing 5s cache can never change existing results.
    df5s = frames.get("5s")
    use_5s = (cfg.exec_resolution or "1m") == "5s" and df5s is not None \
        and len(df5s) > 0
    t5s = df5s["time"] if use_5s else None
    exec_ambiguity: dict[str, int] = {}
    minutes_missing_5s = 0
    if use_5s:
        # Hoisted once per run (s5_exec imports this module, so a top-level
        # import would be circular) and only when 5s mode is actually on.
        from backtest.s5_exec import slice_for_minute as s5_exec_slice

    # Mutable simulation state.
    snap = None              # RegimeSnapshot; None until first regime evaluation
    guard = GuardState()
    open_positions: dict[str, SimPosition] = {}  # strategy name -> SimPosition

    # Live-fidelity cooldown (research_runner.py:133): no signal generation
    # within CONFIG.cooldown_s of the strategy's last accepted signal.
    last_entry: dict[str, datetime] = {}

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

    # Deterministic entry-quality gates (Task 2, manager-backtest-fidelity):
    # same shared predicates the live entry_manager applies, so the sim's
    # trade set moves toward live's.
    _blackout = parse_utc_windows(cfg.news_blackout_utc) if cfg.model_entry_gates else []
    entry_gate_rejects = {s.name: {"sl_too_tight": 0, "news_blackout": 0} for s in specs}

    _ext_events = sorted(cfg.external_pnl or [], key=lambda e: e[0])
    ext_idx = 0

    total_bars = i_end - i_start

    for i in range(i_start, i_end):
        if progress_cb is not None and (i - i_start) % 1000 == 0:
            progress_cb((i - i_start) / total_bars)

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
            ext_idx = 0 if not cfg.external_pnl else ext_idx
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

        # Strategy windows are now computed PER SPEC (see windows_for) because
        # live configures depth per strategy; a single shared window silently
        # changed which signals each strategy could see.

        # S5 bars for this minute (shared by every strategy stepping this bar).
        s5_slice = None
        s5_next = None
        if use_5s:
            s5_slice = s5_exec_slice(df5s, t5s, now_ts)
            if s5_slice is None and open_positions:
                minutes_missing_5s += 1
            if cfg.model_entry_drift:
                # The entry fills at this bar's CLOSE, so the price live's drift
                # gate would read sits in the NEXT minute's S5 bars, not this
                # minute's. Reading the current slice compares the signal
                # against a price from before the bar even finished.
                s5_next = s5_exec_slice(df5s, t5s, now_ts + pd.Timedelta("1min"))

        # Fold in realized P&L from unsimulated sources as it lands, so the
        # kill-switch trips when live's did.
        while (ext_idx < len(_ext_events)
               and _ext_events[ext_idx][0] <= now):
            guard.day_realized_usd += _ext_events[ext_idx][1]
            ext_idx += 1
            if (guard.kill_tripped_date != today
                    and guard.day_realized_usd <= -cfg.kill_switch_usd):
                guard.kill_tripped_date = today
                kill_trips.append(today)

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
                updated, rec = step_exit(pos, bar, now, cfg,
                                         s5_slice=s5_slice,
                                         ambiguity=exec_ambiguity)
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
                    cd = getattr(getattr(spec.module, "CONFIG", None),
                                 "cooldown_s", 0) or 0
                    prev = last_entry.get(spec.name)
                    if prev is not None and (now - prev).total_seconds() < cd:
                        continue
                    w1m, w5m, w15m = windows_for(spec, frames, cursors)
                    try:
                        sig = spec.module.get_signal(w1m, w5m, w15m, now)
                    except Exception:
                        sig = None
                    if sig is not None:
                        if cfg.model_entry_gates:
                            if in_news_blackout(now, _blackout):
                                entry_gate_rejects[spec.name]["news_blackout"] += 1
                                continue
                            if sl_too_tight(sig.entry_price, sig.stop_loss,
                                            cfg.min_sl_dist_pts):
                                entry_gate_rejects[spec.name]["sl_too_tight"] += 1
                                continue
                            if cfg.model_entry_drift:
                                ltp = ltp_at_order_time(
                                    s5_next, cfg.entry_latency_s,
                                    float(bar["close"]))
                                drifted, _detail = entry_drift_exceeded(
                                    sig.side, sig.entry_price, sig.stop_loss,
                                    ltp)
                                if drifted:
                                    entry_gate_rejects[spec.name][
                                        "entry_drift"] = entry_gate_rejects[
                                        spec.name].get("entry_drift", 0) + 1
                                    continue
                        # Stamp on ANY accepted signal, phantom included:
                        # live places the order (instantly closed for a
                        # phantom) and stamps _last_entry_ts either way.
                        last_entry[spec.name] = now
                        # Market-realistic fill: use current bar's close.
                        bar_close = float(bar["close"])
                        friction = cfg.entry_friction_pts
                        # Phantom guard: if the market fill has already blown
                        # through TP (instant close as a phantom winner) or
                        # through SL (instant stop-out as a phantom loser) the
                        # trade is un-tradeable live (live entry manager would
                        # place a MARKET order that is immediately closed or
                        # rejected).  Skip it without booking any P&L.
                        # The SL side also applies to trailing strategies: the
                        # trail seeds hwm from the entry fill, so a fill at or
                        # beyond the signal stop is phantom-stopped immediately.
                        # Real quote at order time when available (BUY crosses
                        # the ask, SELL the bid); otherwise mid +/- friction.
                        quote = (sided_fill_price(s5_next, cfg.entry_latency_s,
                                                  sig.side)
                                 if cfg.sided_fills else None)
                        if quote is not None:
                            fill = quote
                        elif sig.side == "BUY":
                            fill = bar_close + friction
                        else:
                            fill = bar_close - friction

                        if sig.side == "BUY":
                            phantom = (fill >= sig.take_profit
                                       or fill <= sig.stop_loss)
                        else:
                            phantom = (fill <= sig.take_profit
                                       or fill >= sig.stop_loss)
                        if not phantom:
                            fill_moment = (now + timedelta(minutes=1)
                                           if cfg.entry_time_at_bar_close
                                           else now)
                            new_pos = open_position(
                                sig, spec.name, fill_moment, cfg,
                                fill_price=bar_close, exact_fill=quote,
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

    if progress_cb is not None:
        progress_cb(1.0)

    if use_5s and minutes_missing_5s:
        exec_ambiguity["minutes_fell_back_to_1m"] = minutes_missing_5s
        log.warning("[5s] %d minute(s) with an open position had no S5 bars — "
                    "fell back to the M1 bar", minutes_missing_5s)

    return SimResult(
        trades=trades,
        regime_rows=regime_rows,
        kill_trips=kill_trips,
        paused_pct=paused_pct,
        entry_gate_rejects=entry_gate_rejects,
        exec_ambiguity=exec_ambiguity,
    )

