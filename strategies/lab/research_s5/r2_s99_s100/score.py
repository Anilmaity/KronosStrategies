"""score.py -- quote-S5 resolution + bars 1-5 for every arm of the r2_s99_s100 sweeps.

For each arm (results/sweep_s99/*.json, results/sweep_s100/*.json):
  * mid-M1 series: pts at 0.75 (the replay) and 1.00 (derived: pts - 0.25);
  * quote-S5 series: the same entries re-resolved on the S5 bid/ask (vectorised replica of
    lab/s5exit.resolve, mode quote_s5, start_offset 60 s, SL before TP, horizon = the module's
    _MAX_HOLD_MIN), raw points then cost 0.45 / 0.70;
  * bars 1-5 of PROTOCOL_xau2y (campaign_score logic re-implemented on an arbitrary pts
    column) for each of the four series, TRAIN/TEST halves at split 2025-12-01.
Outputs results/scores.csv (one row per arm x model x cost) and results/quote/<arm>.parquet.

    cd KronosStrategies/strategies
    ../.venv/bin/python -m lab.research_s5.r2_s99_s100.score [--validate]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))
RES = HERE / "results"
QDIR = RES / "quote"
QDIR.mkdir(parents=True, exist_ok=True)
CACHE2Y = STRAT / "backtest" / "results" / "bars_cache_2y"
NPZ = Path("/private/tmp/claude-501/-Users-anil-Projects-Kronos/979109dc-9856-4311-91b0-14d2dda5b135/scratchpad/s5_window.npz")

SPLIT = "2025-12-01"
MAX_HOLD = {"s99_mss_fvg": 480, "s100_m3_combo": 72}
MID_COSTS = (0.75, 1.00)
QUOTE_COSTS = (0.45, 0.70)
CHUNKS = (17_280, 120_960, 10**9)


# ---------------------------------------------------------------- S5 + resolver
class S5:
    def __init__(self):
        if NPZ.exists():
            z = np.load(NPZ)
            t, bid, ask, c = z["t"], z["bid"], z["ask"], z["c"]
        else:
            from lab.tools.qa_s5_cache import load_s5
            d = load_s5()
            t = d["time"].dt.tz_convert(None).to_numpy("datetime64[s]")
            bid, ask, c = d.bid_c.to_numpy(float), d.ask_c.to_numpy(float), d.c.to_numpy(float)
        self.T = t.astype("datetime64[s]")
        self.bid, self.ask, self.c = bid, ask, c


def _resolve_one(s5: S5, i, j, long_, sl, tp):
    if j <= i:
        return None, -1
    a = i
    for ch in CHUNKS:
        b = min(j, a + ch)
        px = s5.bid[a:b] if long_ else s5.ask[a:b]
        if long_:
            sh, th = px <= sl, px >= tp
        else:
            sh, th = px >= sl, px <= tp
        ks = int(np.argmax(sh)) if sh.any() else None
        kt = int(np.argmax(th)) if th.any() else None
        if ks is not None and (kt is None or ks <= kt):
            return "SL", a + ks
        if kt is not None:
            return "TP", a + kt
        a = b
        if a >= j:
            break
    return "TIME", j - 1


def resolve_quote(s5: S5, trades: pd.DataFrame, max_hold_min: int) -> pd.DataFrame:
    """Same entries, exits on the quote. Returns entry_time, side, risk, entry_px, quote_outcome,
    quote_raw (cost-free points), inside (bool: the S5 cache covers the trade's horizon)."""
    ent = pd.to_datetime(trades.entry_time, utc=True).dt.tz_convert(None).to_numpy("datetime64[s]")
    i = np.searchsorted(s5.T, ent + np.timedelta64(60, "s"), "right")
    j = np.searchsorted(s5.T, ent + np.timedelta64(int(max_hold_min * 60), "s"), "right")
    long_ = (trades.side == "BUY").to_numpy()
    sl, tp, epx = trades.sl.to_numpy(float), trades.tp.to_numpy(float), trades.entry_px.to_numpy(float)
    oc, px = [], np.empty(len(trades))
    for n in range(len(trades)):
        o, k = _resolve_one(s5, int(i[n]), int(j[n]), bool(long_[n]), sl[n], tp[n])
        oc.append(o)
        px[n] = sl[n] if o == "SL" else tp[n] if o == "TP" else (s5.c[k] if k >= 0 else np.nan)
    oc = np.array(oc, dtype=object)
    raw = np.where(long_, px - epx, epx - px)
    inside = (ent >= s5.T[0]) & (ent + np.timedelta64(int(max_hold_min * 60), "s") <= s5.T[-1])
    return pd.DataFrame({"entry_time": pd.to_datetime(trades.entry_time, utc=True), "side": trades.side.to_numpy(),
                         "risk": trades.risk.to_numpy(float), "entry_px": epx, "sl": sl, "tp": tp,
                         "mid_outcome": trades.outcome.to_numpy(), "mid_raw": trades.pts.to_numpy(float) + 0.75,
                         "quote_outcome": oc, "quote_raw": raw, "inside": inside,
                         "hour": trades.hour.to_numpy(), "reason": trades.reason.to_numpy()})


# ---------------------------------------------------------------- bars
def gold_monthly() -> pd.Series:
    d = pd.read_parquet(CACHE2Y / "is_XAU_USD_1d.parquet")
    t = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
    d = d.assign(time=t).sort_values("time").set_index("time")
    m = d["close"].resample("ME").agg(["first", "last"])
    g = (m["last"] / m["first"] - 1.0) * 100.0
    g.index = g.index.strftime("%Y-%m")
    return g.rename("gold_pct")


def pf(v) -> float:
    v = np.asarray(v, float)
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return float(gw / gl) if gl > 0 else float("inf")


def bars(et: pd.Series, pts: np.ndarray, gold: pd.Series, n_min: int = 40) -> dict:
    """Bars 1, 2(base only -- the stress twin is joined by the caller), 3, 4, 5 on one pts series."""
    df = pd.DataFrame({"et": pd.to_datetime(et, utc=True), "pts": np.asarray(pts, float)})
    cut = pd.Timestamp(SPLIT, tz="UTC")
    tr, te = df[df.et < cut], df[df.et >= cut]
    out = dict(n=len(df), pts=round(df.pts.sum(), 1), pf=round(pf(df.pts), 3), wr=round(100 * (df.pts > 0).mean(), 1) if len(df) else 0.0,
               train_n=len(tr), train_pts=round(tr.pts.sum(), 1), train_pf=round(pf(tr.pts), 3),
               test_n=len(te), test_pts=round(te.pts.sum(), 1), test_pf=round(pf(te.pts), 3))
    eq = df.pts.cumsum()
    out["maxdd"] = round(float((eq - eq.cummax()).min()), 1) if len(df) else 0.0
    tm = te.groupby(te.et.dt.strftime("%Y-%m"))["pts"].sum()
    if len(tm):
        pos = float((tm > 0).mean()); net = float(tm.sum())
        mx = float(tm.max() / net) if net > 0 else float("inf")
    else:
        pos, mx = 0.0, float("inf")
    out.update(test_months=len(tm), test_pos_share=round(pos, 2), test_max_share=(round(mx, 2) if np.isfinite(mx) else None))
    fm = df.groupby(df.et.dt.strftime("%Y-%m"))["pts"].sum()
    j = pd.concat([fm.rename("pts"), gold], axis=1).dropna()
    if len(j) >= 6:
        up, dn = j[j.gold_pct > 0], j[j.gold_pct <= 0]
        corr = float(np.corrcoef(j.pts, j.gold_pct)[0, 1]) if j.pts.std() > 0 else 0.0
        out.update(gold_corr=round(corr, 2), up_pts=round(float(up.pts.sum()), 1), dn_pts=round(float(dn.pts.sum()), 1))
        b5 = bool((up.pts.sum() > 0 and dn.pts.sum() > 0) or abs(corr) < 0.4)
    else:
        out.update(gold_corr=None, up_pts=None, dn_pts=None); b5 = False
    out["bar1_n"] = out["test_n"] >= n_min
    out["bar2_base"] = out["test_pf"] > 1.0
    out["bar3_train"] = out["train_pf"] > 0.9
    out["bar4_monthly"] = pos >= 0.55 and mx <= 0.5
    out["bar5_regime"] = b5
    return out


# ---------------------------------------------------------------- driver
def score_arm(s5: S5, jpath: Path, gold: pd.Series) -> list[dict]:
    row = json.loads(jpath.read_text())
    if row.get("status") != "ok":
        return [dict(label=row["label"], status="error")]
    tr = pd.read_parquet(jpath.with_name(jpath.name[:-5] + ".trades.parquet"))
    strat = row["strategy"]
    assert abs(row["cost"] - 0.75) < 1e-9 or abs(row["cost"] - 1.00) < 1e-9, row["cost"]
    base_cost = row["cost"]
    qp = QDIR / (jpath.name[:-5] + ".parquet")
    if qp.exists():
        q = pd.read_parquet(qp)
    else:
        tr2 = tr.copy(); tr2["pts"] = tr2.pts + (base_cost - 0.75)     # normalise to the 0.75 replay
        q = resolve_quote(s5, tr2, MAX_HOLD[strat])
        q.to_parquet(qp, index=False)
    rows = []
    for c in MID_COSTS:
        b = bars(q.entry_time, q.mid_raw - c, gold)
        rows.append(dict(label=row["label"], strategy=strat, model="mid_M1", cost=c, **b))
    qi = q[q.inside]
    for c in QUOTE_COSTS:
        b = bars(qi.entry_time, qi.quote_raw - c, gold)
        b["n_outside_s5"] = int((~q.inside).sum())
        rows.append(dict(label=row["label"], strategy=strat, model="quote_S5", cost=c, **b))
    return rows


def validate(s5: S5) -> None:
    """Reproduce lab/results/s5exit/s14_ob_mit_bias_c0.80.parquet (quote_s5 outcomes) from the
    Stage-1 s14 trades with this resolver; the horizon there was 1000 min (s14_minstop campaign)."""
    ref = pd.read_parquet(STRAT / "lab/results/s5exit/s14_ob_mit_bias_c0.80.parquet")
    st = pd.read_parquet(STRAT / "lab/results/xau2y_stage1/s14_ob_mit_bias_c0.80.trades.parquet")
    st = st.copy(); st["pts"] = st.pts + 0.05     # to the 0.75 convention resolve_quote expects
    for hold in (1000, 45 * 24 * 60):
        q = resolve_quote(s5, st, hold)
        q["entry_time"] = pd.to_datetime(q.entry_time, utc=True)
        m = ref.merge(q, on=["entry_time", "side"], how="inner", suffixes=("_ref", ""))
        agree = (m.quote_s5_outcome == m.quote_outcome).mean()
        dpts = np.abs((m.quote_s5_pts + 0.80) - m.quote_raw).max()
        print(f"validate s14 @hold {hold}: matched {len(m)}/{len(ref)}  outcome agreement {100*agree:.3f}%  max|raw diff| {dpts:.4f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--which", default="s99,s100")
    a = ap.parse_args()
    s5 = S5()
    print(f"S5 window {s5.T[0]} .. {s5.T[-1]}  bars {len(s5.T):,}")
    if a.validate:
        validate(s5); return 0
    gold = gold_monthly()
    rows = []
    for w in a.which.split(","):
        for jp in sorted((RES / f"sweep_{w}").glob("*.json")):
            if jp.name.endswith(".error.json"):
                continue
            rows += score_arm(s5, jp, gold)
            print("scored", jp.name, flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(RES / "scores.csv", index=False)
    cols = ["label", "model", "cost", "n", "pf", "pts", "train_n", "train_pf", "train_pts", "test_n", "test_pf", "test_pts",
            "test_pos_share", "test_max_share", "gold_corr", "bar1_n", "bar2_base", "bar3_train", "bar4_monthly", "bar5_regime"]
    with pd.option_context("display.width", 250, "display.max_rows", 500, "display.max_columns", 40):
        print(df[[c for c in cols if c in df]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
