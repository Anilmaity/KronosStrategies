"""03_execution.py — live fills vs signal levels and vs the OANDA S5 quote; stop-out slippage vs
the execution study's gap-through model (PROTOCOL (b)). All engine live trades with broker truth.

    cd strategies && ../.venv/bin/python lab/research_s5/r2_live_parity/03_execution.py

Sign convention: ADVERSE is positive (a BUY filled above the level, a SELL below; a stop
filled beyond the stop). Favourable is negative.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(STRAT))
from common import RES, live_trades, load_signals, boot_ci   # noqa: E402
from lab.s5exit import load_s5                              # noqa: E402

EXEC = STRAT / "lab" / "research_s5" / "execution" / "results"
KEYS = ["s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo"]


def s5_at(s5, t5, ts, offset_s=0):
    """Index of the S5 bar containing ts+offset (the bar whose open <= t)."""
    t = np.datetime64(pd.Timestamp(ts).tz_convert("UTC").tz_localize(None)) + np.timedelta64(offset_s, "s")
    i = int(np.searchsorted(t5, t, "right")) - 1
    return max(i, 0)


def main():
    lt = live_trades()
    lt = lt[lt.has_deal & lt.key.isin(KEYS)].copy()
    sig = load_signals()
    s5 = load_s5()
    t5 = s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
    bid, ask, mid = s5.bid_c.to_numpy(float), s5.ask_c.to_numpy(float), s5.c.to_numpy(float)
    out = []

    # ---- (b1) entry fill vs nominal, vs OANDA quote at fill time ----
    lt["lat_s"] = (lt.in_time - lt.signal_at).dt.total_seconds()
    sgn = np.where(lt.side == "BUY", 1.0, -1.0)
    lt["fill_vs_nominal"] = sgn * (lt.in_px - lt.sig_entry)
    i_sig = [s5_at(s5, t5, t) for t in lt.signal_at]
    i_fill = [s5_at(s5, t5, t) for t in lt.in_time]
    lt["mid_at_signal"] = mid[i_sig]
    lt["quote_at_fill"] = np.where(lt.side == "BUY", ask[i_fill], bid[i_fill])
    lt["nominal_vs_mid"] = sgn * (lt.mid_at_signal - lt.sig_entry)      # market already past the level at signal time
    lt["fill_vs_quote"] = sgn * (lt.in_px - lt.quote_at_fill)           # broker vs OANDA sided quote (basis + latency)
    lt["spread_at_fill"] = ask[i_fill] - bid[i_fill]
    out.append("=== (b1) ENTRY: live fill vs nominal signal level (adverse +), n with broker truth ===")
    g = lt.groupby("key").agg(n=("pts", "size"), lat_med=("lat_s", "median"), lat_p90=("lat_s", lambda x: x.quantile(.9)),
                              fill_vs_nom_mean=("fill_vs_nominal", "mean"), fill_vs_nom_med=("fill_vs_nominal", "median"),
                              nominal_vs_mid_mean=("nominal_vs_mid", "mean"), fill_vs_quote_mean=("fill_vs_quote", "mean"),
                              fill_vs_quote_med=("fill_vs_quote", "median"), spread_fill=("spread_at_fill", "median"))
    out.append(g.round(3).to_string())
    for k, d in lt.groupby("key"):
        lo, hi = boot_ci(d.fill_vs_nominal)
        out.append(f"  {k}: fill_vs_nominal mean {d.fill_vs_nominal.mean():+.3f} [90% CI {lo:+.3f}, {hi:+.3f}]  share favourable (<0) {(d.fill_vs_nominal < 0).mean():.0%}")
    out.append("\nfill vs nominal by UTC hour (all four legs):")
    h = lt.groupby("entry_hour").agg(n=("pts", "size"), fill_vs_nom=("fill_vs_nominal", "mean"),
                                     fill_vs_quote=("fill_vs_quote", "mean"), nominal_vs_mid=("nominal_vs_mid", "mean"))
    out.append(h.round(3).to_string())

    # ---- the drift-gate truncation check: rejected entry_drift detail strings ----
    rj = sig[(sig.reason_key == "entry_drift") & sig.key.isin(KEYS)].copy()
    m = rj.rejection_reason.str.extract(r"drift ([+-]?\d+\.\d+)pt vs budget (\d+\.\d+)pt")
    rj["drift"] = m[0].astype(float); rj["budget"] = m[1].astype(float)
    out.append("\n=== drift gate: REJECTED entry_drift detail (adverse drift at the gate) ===")
    out.append(rj.groupby("key").agg(n=("drift", "size"), drift_mean=("drift", "mean"), drift_med=("drift", "median"),
                                     drift_p90=("drift", lambda x: x.quantile(.9)), budget_med=("budget", "median")).round(3).to_string())
    # accepted: adverse drift at the gate is not logged; the fill-vs-nominal of PLACED trades is its proxy
    out.append("accepted (PLACED) fill_vs_nominal quantiles, all legs: " +
               ", ".join(f"p{q} {np.percentile(lt.fill_vs_nominal, q):+.2f}" for q in (5, 25, 50, 75, 95)))

    # ---- (b2) stop-out slippage vs the model ----
    sl = lt[lt.exit_kind == "SL"].copy()
    ssg = np.where(sl.side == "BUY", 1.0, -1.0)
    sl["stop_level"] = sl.sig_sl
    sl["stop_slip"] = ssg * (sl.sig_sl - sl.out_px)          # BUY: filled below the stop = adverse
    sl["broker_sl_moved"] = (sl.out_sl.notna()) & ((sl.out_sl - sl.sig_sl).abs() > 0.005)
    sl["exit_hour"] = sl.out_time.dt.hour
    sl["stop_bucket"] = pd.cut(sl.sl_dist, [0, 2, 3, 5, 10, 1e9], labels=["<2", "2-3", "3-5", "5-10", "10+"])
    ov = pd.read_csv(EXEC / "overshoot_hour.csv").set_index("hour")
    js = pd.read_csv(EXEC / "jumps_slippage_hour.csv").set_index("hour")
    ob = pd.read_csv(EXEC / "overshoot_stopbucket.csv").set_index("rb")
    out.append(f"\n=== (b2) STOP-OUTS: live slip beyond the nominal stop (adverse +), n={len(sl)}; broker SL level differed from the signal SL on {int(sl.broker_sl_moved.sum())} ===")
    out.append(f"all: mean {sl.stop_slip.mean():+.3f}, median {sl.stop_slip.median():+.3f}, p90 {sl.stop_slip.quantile(.9):+.3f}, share > 1 pt {(sl.stop_slip > 1).mean():.1%}")
    slc = sl[~sl.broker_sl_moved]
    out.append(f"excluding moved-SL rows: mean {slc.stop_slip.mean():+.3f}, median {slc.stop_slip.median():+.3f}, p90 {slc.stop_slip.quantile(.9):+.3f}")
    out.append("by strategy:\n" + sl.groupby("key").agg(n=("stop_slip", "size"), mean=("stop_slip", "mean"), med=("stop_slip", "median"),
                                                       p90=("stop_slip", lambda x: x.quantile(.9)), moved=("broker_sl_moved", "sum")).round(3).to_string())
    hb = sl.groupby("exit_hour").agg(n=("stop_slip", "size"), live_mean=("stop_slip", "mean"), live_med=("stop_slip", "median"))
    hb["model_S5_overshoot_TEST"] = ov.overshoot_test.reindex(hb.index).round(3)
    hb["model_jump_slip_TEST"] = js.slip_test.reindex(hb.index).round(3)
    out.append("by exit hour (live) vs the execution study's TEST-era S5 overshoot and E[J^2]/2E[J] slip:\n" + hb.round(3).to_string())
    bb = sl.groupby("stop_bucket", observed=True).agg(n=("stop_slip", "size"), live_mean=("stop_slip", "mean"), live_p90=("stop_slip", lambda x: x.quantile(.9)))
    bb["model_overshoot_TEST"] = ob.test.reindex(bb.index).round(3)
    out.append("by stop bucket vs the study's TEST overshoot:\n" + bb.round(3).to_string())
    core = sl[(sl.exit_hour >= 7) & (sl.exit_hour <= 16)]
    lo, hi = boot_ci(core.stop_slip)
    out.append(f"07-16 UTC pooled: live {core.stop_slip.mean():+.3f} [90% CI {lo:+.3f}, {hi:+.3f}] n={len(core)} vs study TEST 0.517 (S5 overshoot) / 0.618 (jump model)")

    # ---- (b3) TP fills ----
    tp = lt[lt.exit_kind == "TP"].copy()
    tsg = np.where(tp.side == "BUY", 1.0, -1.0)
    tp["tp_slip"] = tsg * (tp.sig_tp - tp.out_px)             # BUY filled below the target = adverse
    out.append(f"\n=== (b3) TARGET fills vs nominal TP (adverse +), n={len(tp)} ===")
    out.append(tp.groupby("key").agg(n=("tp_slip", "size"), mean=("tp_slip", "mean"), med=("tp_slip", "median")).round(3).to_string())

    # ---- (b4) per-trade all-in live execution cost vs the harness's nominal geometry ----
    # For a trade with the same outcome, live_pts - (nominal raw pts) = -(fill_vs_nominal) - exit slip.
    lt["exit_slip"] = np.nan
    lt.loc[sl.index, "exit_slip"] = sl.stop_slip
    lt.loc[tp.index, "exit_slip"] = tp.tp_slip
    nom_raw = np.where(lt.exit_kind == "SL", -lt.sl_dist, np.where(lt.exit_kind == "TP", (lt.sig_tp - lt.sig_entry).abs(), np.nan))
    lt["allin_cost"] = nom_raw - lt.pts        # positive = live paid this many points vs the nominal-level model
    out.append("\n=== (b4) all-in live cost per trade vs the nominal-level model (pts; positive = paid): SL and TP exits only ===")
    out.append(lt.dropna(subset=["allin_cost"]).groupby("key").agg(n=("allin_cost", "size"), mean=("allin_cost", "mean"),
                                                                   med=("allin_cost", "median"), p90=("allin_cost", lambda x: x.quantile(.9))).round(3).to_string())
    pooled = lt.allin_cost.dropna()
    lo, hi = boot_ci(pooled)
    out.append(f"pooled: mean {pooled.mean():+.3f} [90% CI {lo:+.3f}, {hi:+.3f}], median {pooled.median():+.3f}, n={len(pooled)}  "
               f"(the harness convention charges 0.45 on quote-S5 / 0.75 on mid-M1)")
    lt.to_parquet(RES / "exec_trades.parquet", index=False)
    h.to_csv(RES / "exec_entry_by_hour.csv"); hb.to_csv(RES / "exec_stop_by_hour.csv")
    txt = "\n".join(out); print(txt)
    (RES / "03_execution.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()


def backstop():
    """(b5) stop-outs where the broker SL (widened to the 3.0-pt stops floor) filled instead of
    the monitor's active close at the strategy stop."""
    lt = pd.read_parquet(RES / "exec_trades.parquet")
    sl = lt[lt.exit_kind == "SL"].copy()
    ssg = np.where(sl.side == "BUY", 1.0, -1.0)
    sl["stop_slip"] = ssg * (sl.sig_sl - sl.out_px)
    sl["moved"] = sl.out_sl.notna() & ((sl.out_sl - sl.sig_sl).abs() > 0.005)
    sl["brk_dist"] = (sl.out_sl - sl.sig_entry).abs()
    mv = sl[sl.moved]
    out = ["\n=== (b5) broker backstop exits (SL level at the broker != signal SL) ==="]
    out.append(f"n={len(mv)} of {len(sl)} stop-outs; signal stop dist median {mv.sl_dist.median():.2f} (max {mv.sl_dist.max():.2f}); "
               f"broker SL dist from nominal entry median {mv.brk_dist.median():.2f}")
    out.append(f"share of ALL stop-outs with signal stop < 3.0: {(sl.sl_dist < 3).mean():.0%} (n={(sl.sl_dist < 3).sum()}); of those, backstop-filled: {mv.shape[0] / max(1,(sl.sl_dist < 3).sum()):.0%}")
    out.append(f"extra loss beyond the signal stop on backstop rows: mean {mv.stop_slip.mean():+.3f}, median {mv.stop_slip.median():+.3f}, total {mv.stop_slip.sum():+.1f} pts, "
               f"USD at their lots {-(mv.stop_slip * mv.lots * 100).sum():+.0f}")
    out.append("by strategy:\n" + mv.groupby("key").agg(n=("stop_slip", "size"), extra_mean=("stop_slip", "mean"), extra_total=("stop_slip", "sum"),
                                                       usd=("lots", lambda x: 0)).round(2).to_string())
    out.append("by month:\n" + mv.assign(m=mv.out_time.dt.strftime("%Y-%m")).groupby("m").agg(n=("stop_slip", "size"), extra_total=("stop_slip", "sum")).round(1).to_string())
    # the non-moved tight stops: how did the active close do?
    tight = sl[(~sl.moved) & (sl.sl_dist < 3)]
    out.append(f"tight (<3 pt) stops closed by the monitor at the strategy stop: n={len(tight)}, slip mean {tight.stop_slip.mean():+.3f}, p90 {tight.stop_slip.quantile(.9):+.3f}")
    txt = "\n".join(out); print(txt)
    with open(RES / "03_execution.txt", "a") as f:
        f.write(txt + "\n")


if __name__ == "__main__":
    backstop()
