"""common.py — shared loaders for r2_live_parity (PROTOCOL.md D1–D4).

live_trades(): one row per engine live trade with broker-truth points/USD and the
nominal signal levels. All timestamps tz-aware UTC.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results"

ENGINE = ("s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo",
          "s95_session_breakout", "orb", "c03_fvg_fill", "s14_ob_mit_bias")
COPY = ("copy_free", "copy_vip")
USD_PER_UNIT = 100.0


def load_signals() -> pd.DataFrame:
    s = pd.read_parquet(RES / "live_signals.parquet")
    s["signal_at"] = pd.to_datetime(s["signal_at"], utc=True)
    s["reason_key"] = s["rejection_reason"].fillna("").str.split(":").str[0].str.strip()
    s.loc[s.status == "PLACED", "reason_key"] = "PLACED"
    s["hour"] = s.signal_at.dt.hour
    s["sl_dist"] = (s.entry_price - s.stop_loss).abs()
    return s


def load_deals() -> pd.DataFrame:
    d = pd.read_parquet(RES / "deals.parquet")
    d["deal_time"] = pd.to_datetime(d["deal_time"], utc=True)
    return d


def deal_truth(deals: pd.DataFrame) -> pd.DataFrame:
    """Per broker position_id: vwap entry / exit, USD total, exit reason, first/last times."""
    d = deals[deals.position_id.notna() & deals.entry_type.isin(["DEAL_ENTRY_IN", "DEAL_ENTRY_OUT"])].copy()
    d["pv"] = d.price * d.volume
    g_in = d[d.entry_type == "DEAL_ENTRY_IN"].groupby("position_id").agg(
        in_px=("pv", "sum"), in_vol=("volume", "sum"), in_time=("deal_time", "min"),
        in_type=("deal_type", "first"))
    g_in["in_px"] = g_in.in_px / g_in.in_vol
    g_out = d[d.entry_type == "DEAL_ENTRY_OUT"].groupby("position_id").agg(
        out_px=("pv", "sum"), out_vol=("volume", "sum"), out_time=("deal_time", "max"),
        out_reason=("raw_reason", "last"), out_sl=("raw_sl", "last"), out_tp=("raw_tp", "last"),
        n_out=("deal_id", "size"))
    g_out["out_px"] = g_out.out_px / g_out.out_vol
    g_usd = d.groupby("position_id").agg(usd_profit=("profit", "sum"), usd_comm=("commission", "sum"),
                                         usd_swap=("swap", "sum"))
    t = g_in.join(g_out, how="outer").join(g_usd, how="left")
    t["usd"] = t.usd_profit.fillna(0) + t.usd_comm.fillna(0) + t.usd_swap.fillna(0)
    t["deal_side"] = np.where(t.in_type == "DEAL_TYPE_BUY", "BUY", "SELL")
    t["deal_pts"] = np.where(t.deal_side == "BUY", t.out_px - t.in_px, t.in_px - t.out_px)
    return t.reset_index()


def live_trades() -> pd.DataFrame:
    p = pd.read_parquet(RES / "live_positions.parquet")
    for c in ("pos_created_at", "entry_created_at", "exit_created_at"):
        p[c] = pd.to_datetime(p[c], utc=True)
    p = p[p.key.isin(ENGINE) & (p.quantity == 0) & (p.broker_order_id.fillna("") != "")
          & (p.entry_reason != "TEST_FILL_VALIDATION")].copy()
    s = load_signals()
    sp = s[s.status == "PLACED"][["position_id", "signal_id", "signal_at", "entry_price", "stop_loss",
                                   "take_profit", "reason"]].rename(
        columns={"entry_price": "sig_entry", "stop_loss": "sig_sl", "take_profit": "sig_tp", "reason": "sig_reason"})
    p = p.merge(sp, on="position_id", how="left")
    t = deal_truth(load_deals())
    p = p.merge(t, left_on="broker_order_id", right_on="position_id", how="left", suffixes=("", "_deal"))
    p["has_deal"] = p.out_px.notna() & p.in_px.notna()
    # D4: broker truth first, fallback realized_units / lots
    p["pts_fallback"] = p.realized_units / p.lots
    p["usd_fallback"] = p.realized_units * USD_PER_UNIT
    p["pts"] = np.where(p.has_deal, p.deal_pts, p.pts_fallback)
    p["usd"] = np.where(p.has_deal, p.usd, p.usd_fallback)
    p["src"] = np.where(p.has_deal, "deal", "fallback")
    p["sl_dist"] = (p.sig_entry - p.sig_sl).abs()
    p["r"] = p.pts / p.sl_dist
    p["entry_hour"] = p.entry_created_at.dt.hour
    p["side"] = p.entry_side
    p["exit_kind"] = p.out_reason.map({"DEAL_REASON_SL": "SL", "DEAL_REASON_TP": "TP",
                                       "DEAL_REASON_EXPERT": "ACTIVE", "DEAL_REASON_MOBILE": "MANUAL"})
    p.loc[p.exit_kind.isna(), "exit_kind"] = p.exit_condition.map(
        {"STOPLOSS": "SL", "TARGET": "TP", "TIME_EXIT": "TIME"}).fillna("?")
    return p.sort_values("entry_created_at").reset_index(drop=True)


def pf(v) -> float:
    v = pd.Series(v).dropna()
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def boot_ci(x, n=10000, q=(5, 95), seed=0):
    x = np.asarray(pd.Series(x).dropna(), float)
    if len(x) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    m = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return tuple(float(v) for v in np.percentile(m, q))
