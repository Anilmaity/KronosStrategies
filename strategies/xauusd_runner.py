"""
xauusd_runner.py
----------------
Parametric live runner for the OOS-validated XAUUSD ICT strategies.

Dispatch is controlled by env var XAUUSD_STRATEGY ∈ {S2, S4}:

  S2 -> ICT FVG Fill (M15)       buf=0.3
  S4 -> ICT Breaker Block (M15)  buf=0.3
  (S6 ICT Daily CRT retired 2026-05-18 — sample too small.)

Pipeline:
  - Poll TSDB via tsdb_reader.fetch_candles for the strategy's timeframe.
  - On a *new* completed bar, build the feature DataFrame and ATR.
  - Run the detector. If a setup fires, create EntrySignal and call
    strategy.entry_manager.place_entry, which inserts Position/Order/Trigger
    rows AND fires the MetaAPI market order (via utils.metaapi_client).

Variation tag maps to entry_manager._VARIATION_STRATEGY_NAME so the right
deployed UserStrategy is selected for each strategy.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.tsdb_reader import fetch_candles
from shared.market_timing import is_market_closed_utc
from strategy.ict_engine import EntrySignal
from strategy.entry_manager import place_entry

from xauusd_strategies.engine import atr as compute_atr
from xauusd_strategies import s02_fvg_fill as s02
from xauusd_strategies import s04_breaker as s04


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("xauusd_runner")


SYMBOL = os.getenv("XAUUSD_SYMBOL", "XAU_USD")
STRATEGY = os.getenv("XAUUSD_STRATEGY", "S2").upper()
POLL_INTERVAL = int(os.getenv("XAUUSD_POLL_SEC", "60"))


# Per-strategy config. The variation tag must match entry_manager._VARIATION_STRATEGY_NAME.
CONFIGS = {
    "S2": {
        "tf_tsdb": "15m",
        "n_days":  60,
        "kwargs":  {**s02.DEFAULTS, "stop_buffer_atr": 0.3},
        "variation": "ICT_S2_FVG",
        "valid_bars": 96,
    },
    "S4": {
        "tf_tsdb": "15m",
        "n_days":  60,
        "kwargs":  {**s04.DEFAULTS, "stop_buffer_atr": 0.3},
        "variation": "ICT_S4_BREAKER",
        "valid_bars": 96,
    },
    # S6 (ICT Daily CRT) retired 2026-05-18 — see git history for details.
}


_last_bar_seen: Optional[pd.Timestamp] = None
_open_local: bool = False  # cooperative single-position guard inside the process


def to_indexed_df(candles: pd.DataFrame) -> pd.DataFrame:
    """Convert tsdb_reader output (columns: time, open, high, low, close, volume)
    into the DatetimeIndex DataFrame our detectors expect."""
    if candles is None or candles.empty:
        return pd.DataFrame()
    df = candles.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time").sort_index()
    return df[["open", "high", "low", "close"]].astype(float)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-strategy feature build."""
    if STRATEGY == "S2":
        return s02.build_features(df, s02.DEFAULTS["h4_ema"])
    return df  # S4, S6 use raw OHLC


def make_detector():
    cfg = CONFIGS[STRATEGY]
    if STRATEGY == "S2":
        return s02.make_detector(**cfg["kwargs"])
    if STRATEGY == "S4":
        return s04.make_detector(**cfg["kwargs"])
    raise ValueError(f"Unknown strategy: {STRATEGY}")


def tick(detector) -> None:
    global _last_bar_seen, _open_local
    cfg = CONFIGS[STRATEGY]

    raw = fetch_candles(cfg["tf_tsdb"], cfg["n_days"], symbol=SYMBOL)
    df = to_indexed_df(raw)
    if df.empty or len(df) < 30:
        log.debug("[%s] not enough data yet (%d bars)", STRATEGY, len(df))
        return

    feat = build_features(df)
    # Drop the still-forming last bar (engine's convention)
    completed = feat.iloc[:-1].copy()
    if completed.empty:
        return

    last_completed_time = completed.index[-1]
    if _last_bar_seen is None:
        # warm-up: register but don't trade on first observation
        _last_bar_seen = last_completed_time
        log.info("[%s] warmup at bar %s", STRATEGY, last_completed_time)
        return
    if last_completed_time <= _last_bar_seen:
        return

    _last_bar_seen = last_completed_time
    log.info("[%s] new %s bar closed at %s", STRATEGY, cfg["tf_tsdb"], last_completed_time)

    if _open_local:
        log.debug("[%s] local open-position guard set; skipping detect", STRATEGY)
        return

    completed["atr"] = compute_atr(completed)
    a = completed.iloc[-1]["atr"]
    if pd.isna(a):
        return

    setup = detector(completed, len(completed) - 1, a)
    if setup is None:
        return

    side_str = "BUY" if setup.side == "long" else "SELL"
    # zone bounds: use stop..entry for long, entry..stop for short
    if setup.side == "long":
        zone_low, zone_high = float(setup.stop), float(setup.entry)
    else:
        zone_low, zone_high = float(setup.entry), float(setup.stop)
    sig = EntrySignal(
        side=side_str,
        entry_price=float(setup.entry),
        stop_loss=float(setup.stop),
        take_profit=float(setup.tp),
        reason=cfg["variation"],
        zone_low=zone_low,
        zone_high=zone_high,
    )
    log.info("[%s] setup: %s entry=%.2f sl=%.2f tp=%.2f", STRATEGY, side_str,
             setup.entry, setup.stop, setup.tp)
    try:
        result = place_entry(sig, symbol=SYMBOL, variation=cfg["variation"])
        log.info("[%s] place_entry returned: %s", STRATEGY, result)
        if result:
            _open_local = True
    except Exception:
        log.exception("[%s] place_entry failed", STRATEGY)


def main():
    if STRATEGY not in CONFIGS:
        sys.exit(f"Unknown XAUUSD_STRATEGY={STRATEGY}; expected one of {list(CONFIGS)}")
    log.info("xauusd_runner starting: strategy=%s symbol=%s poll=%ds",
             STRATEGY, SYMBOL, POLL_INTERVAL)
    log.info("variation tag: %s", CONFIGS[STRATEGY]["variation"])

    detector = make_detector()
    while True:
        try:
            if is_market_closed_utc():
                log.debug("market closed — skipping tick")
            else:
                tick(detector)
        except KeyboardInterrupt:
            log.info("shutdown")
            return
        except Exception:
            log.exception("tick failed (continuing)")

        try:
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            log.info("shutdown")
            return


if __name__ == "__main__":
    main()
