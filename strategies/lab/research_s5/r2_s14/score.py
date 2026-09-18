"""r2_s14/score.py -- score every arm of a study campaign (trades replayed at cost 0):
mid-M1 at 0.75 / 1.00 (cost subtracted per trade) and quote-S5 (lab.s5exit.resolve, start
offset 60 s, max-hold 1000 min) at 0.45 / 0.70; TRAIN < 2025-12-01 <= TEST; bars 1-5 of
PROTOCOL_xau2y (bar 2 at both mid-M1 costs, bar 4/5 by campaign_score's definitions) and the
bar-7 inputs (TEST quote-S5 PF and points at 0.70). Writes <campaign>/SCORE.csv and the
per-trade quote resolution <arm>.q.parquet (cached; reused on re-score).

    ../../../../.venv/bin/python score.py h0_closed_frames [h1_floor ...]
"""
import sys, json
from pathlib import Path
import numpy as np, pandas as pd
_HERE = Path(__file__).resolve().parent; _STRAT = _HERE.parents[2]
sys.path.insert(0, str(_STRAT))
from lab.s5exit import load_s5, resolve
from lab.tools.campaign_score import gold_monthly, monthly_pts

SPLIT = pd.Timestamp("2025-12-01", tz="UTC")
CACHE = _STRAT / "backtest/results/bars_cache_2y"
MID_COSTS = (0.75, 1.00); Q_COSTS = (0.45, 0.70)
_S5 = None


def s5():
    global _S5
    if _S5 is None:
        d = load_s5(); _S5 = (d, d["time"].dt.tz_convert(None).to_numpy("datetime64[ns]"))
    return _S5


def pf(v):
    gw, gl = v[v > 0].sum(), -v[v <= 0].sum()
    return round(float(gw / gl), 3) if gl > 0 else float("inf")


def quote_resolve(trades: pd.DataFrame, cache_path: Path) -> pd.DataFrame:
    if cache_path.exists():
        return pd.read_parquet(cache_path)
    d, t5 = s5()
    lo, hi = d.time.min(), d.time.max()
    out = []
    for t in trades.itertuples():
        if not (t.entry_time >= lo and t.entry_time + pd.Timedelta(minutes=1000) <= hi):
            out.append(dict(q_raw=np.nan, q_outcome=None)); continue
        r = resolve(t, d, t5, "quote_s5", 0.0, 1000, start_offset_s=60)
        if r is None:
            out.append(dict(q_raw=np.nan, q_outcome=None)); continue
        oc, px = r
        raw = (px - t.entry_px) if t.side == "BUY" else (t.entry_px - px)
        out.append(dict(q_raw=raw, q_outcome=oc))
    q = pd.concat([trades.reset_index(drop=True), pd.DataFrame(out)], axis=1)
    q.to_parquet(cache_path)
    return q


def score_arm(camp: Path, label: str, gold: pd.Series) -> dict:
    tr = pd.read_parquet(camp / f"{label}.trades.parquet")
    tr["entry_time"] = pd.to_datetime(tr.entry_time, utc=True)
    raw = tr.pts + json.loads((camp / f"{label}.json").read_text())["cfg"]["cost_pts"]   # back to gross
    tr["raw"] = raw
    q = quote_resolve(tr, camp / f"{label}.q.parquet")
    q["entry_time"] = pd.to_datetime(q.entry_time, utc=True)
    out = dict(label=label, n=len(tr), train_n=int((tr.entry_time < SPLIT).sum()), test_n=int((tr.entry_time >= SPLIT).sum()),
               median_risk=round(float(tr.risk.median()), 2), trades_per_day=round(len(tr) / 531, 2))
    for c in MID_COSTS:
        p = tr.raw - c
        for nm, m in (("all", slice(None)), ("train", tr.entry_time < SPLIT), ("test", tr.entry_time >= SPLIT)):
            v = p[m] if nm != "all" else p
            out[f"m{c:.2f}_{nm}_pf"] = pf(v); out[f"m{c:.2f}_{nm}_pts"] = round(float(v.sum()), 1)
    qq = q.dropna(subset=["q_raw"])
    out["q_n"] = len(qq)
    for c in Q_COSTS:
        p = qq.q_raw - c
        for nm, m in (("all", slice(None)), ("train", qq.entry_time < SPLIT), ("test", qq.entry_time >= SPLIT)):
            v = p[m] if nm != "all" else p
            out[f"q{c:.2f}_{nm}_pf"] = pf(v); out[f"q{c:.2f}_{nm}_pts"] = round(float(v.sum()), 1)
    out["q0.70_wr"] = round(100 * float(((qq.q_raw - 0.70) > 0).mean()), 1) if len(qq) else None
    # bars 1-5 on mid-M1 at 0.75 (base) with stress 1.00, campaign_score definitions
    t75 = tr.assign(pts=tr.raw - 0.75)
    test = t75[t75.entry_time >= SPLIT]
    tm = monthly_pts(test); pos_share = float((tm > 0).mean()) if len(tm) else 0.0
    max_share = float(tm.max() / tm.sum()) if len(tm) and tm.sum() > 0 else np.inf
    fm = monthly_pts(t75); j = pd.concat([fm.rename("pts"), gold.rename("gold")], axis=1).dropna()
    up, dn = j[j.gold > 0], j[j.gold <= 0]
    corr = float(j.pts.corr(j.gold)) if len(j) > 2 else np.nan
    out.update(test_pos_month_share=round(pos_share, 2), test_max_month_share=round(max_share, 2) if np.isfinite(max_share) else None,
               gold_corr=round(corr, 2), up_months_pts=round(float(up.pts.sum()), 1), dn_months_pts=round(float(dn.pts.sum()), 1))
    out["bar1_n"] = out["test_n"] >= 40
    out["bar2_base"] = out["m0.75_test_pf"] > 1.0; out["bar2_stress"] = out["m1.00_test_pf"] > 1.0
    out["bar3_train"] = out["m0.75_train_pf"] > 0.9
    out["bar4_monthly"] = pos_share >= 0.55 and max_share <= 0.5
    out["bar5_regime"] = bool((up.pts.sum() > 0 and dn.pts.sum() > 0) or abs(corr) < 0.4)
    bars = ["bar1_n", "bar2_base", "bar2_stress", "bar3_train", "bar4_monthly", "bar5_regime"]
    out["bars15"] = int(sum(out[b] for b in bars))
    out["q_bar2_test_both"] = out["q0.45_test_pf"] > 1.0 and out["q0.70_test_pf"] > 1.0
    return out


def main(names):
    gold = gold_monthly(CACHE)
    for name in names:
        camp = _HERE / "results" / name
        labels = [p.name[:-5] for p in sorted(camp.glob("*.json")) if not p.name.endswith(".error.json")]
        rows = [score_arm(camp, l, gold) for l in labels]
        df = pd.DataFrame(rows); df.to_csv(camp / "SCORE.csv", index=False)
        cols = ["label", "n", "train_n", "test_n", "median_risk", "m0.75_all_pf", "m1.00_all_pf", "m1.00_train_pf", "m1.00_test_pf", "m1.00_test_pts",
                "q0.70_all_pf", "q0.70_all_pts", "q0.70_train_pf", "q0.70_train_pts", "q0.70_test_pf", "q0.70_test_pts", "q0.45_test_pf", "bars15", "bar4_monthly", "bar5_regime"]
        with pd.option_context("display.width", 300, "display.max_columns", 40):
            print(f"\n=== {name} ===\n" + df[cols].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1:])
