"""04_gates.py — what each entry gate refused (PROTOCOL D8, second half).

Every REJECTED live signal is resolved from its OWN nominal SL/TP on the S5 quote, with the
entry at the sided OANDA quote (long ask / short bid) on the S5 bar 1 s after signal_at — the
price the market actually offered, which is what an entry_drift rejection is about — and the
strategy's own max hold. Points are raw quote-to-quote (spread already inside), so the
like-for-like live comparator is live broker pts (no extra cost charged). A second column adds
the measured live stop slippage (+0.26 on SL outcomes, 07_execution) as a sensitivity.
USD uses the live sizing rule: lots = clip($38 / (stop pts × 100), 0.01, 0.10).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
STRAT = HERE.parents[2]
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(STRAT))
from common import RES, load_signals, live_trades, pf   # noqa: E402
from lab.s5exit import load_s5                         # noqa: E402

MAX_HOLD = {"s93_fvg_scalp": 120, "s94_sweep_reversal": 1200, "s99_mss_fvg": 480, "s100_m3_combo": 72,
            "c03_fvg_fill": 5 * 24 * 60, "s14_ob_mit_bias": 5 * 24 * 60}
LIVE_SLIP_SL = 0.26
RISK_USD, MAX_LOT, MIN_LOT = 38.0, 0.10, 0.01


def lots_for(stop):
    return float(np.clip(RISK_USD / (stop * 100.0), MIN_LOT, MAX_LOT)) if stop > 0 else MIN_LOT


def resolve_quote(row, s5, t5, bid, ask, hi, lo):
    ent = np.datetime64(row.signal_at.tz_convert("UTC").tz_localize(None))
    i = int(np.searchsorted(t5, ent + np.timedelta64(1, "s"), "right")) - 1
    if i < 0:
        return None
    end = ent + np.timedelta64(int(MAX_HOLD[row.key] * 60), "s")
    j = int(np.searchsorted(t5, end, "right"))
    if j <= i + 1:
        return None
    long_ = row.side == "BUY"
    fill = ask[i] if long_ else bid[i]
    px = bid[i + 1:j] if long_ else ask[i + 1:j]     # exit side quote
    if long_:
        s = np.flatnonzero(px <= row.stop_loss); t = np.flatnonzero(px >= row.take_profit)
    else:
        s = np.flatnonzero(px >= row.stop_loss); t = np.flatnonzero(px <= row.take_profit)
    ks = s[0] if len(s) else np.inf
    kt = t[0] if len(t) else np.inf
    if ks == np.inf and kt == np.inf:
        oc, xpx = "TIME", float(s5.c.iloc[j - 1])
    elif ks <= kt:
        oc, xpx = "SL", float(row.stop_loss)
    else:
        oc, xpx = "TP", float(row.take_profit)
    raw = (xpx - fill) if long_ else (fill - xpx)
    return oc, fill, raw


def main():
    sig = load_signals()
    rj = sig[(sig.status == "REJECTED") & sig.key.isin(MAX_HOLD)].copy()
    rj = rj.dropna(subset=["stop_loss", "take_profit"])
    s5 = load_s5()
    t5 = s5["time"].dt.tz_convert(None).to_numpy("datetime64[ns]")
    bid, ask = s5.bid_c.to_numpy(float), s5.ask_c.to_numpy(float)
    res = []
    for r in rj.itertuples():
        out = resolve_quote(r, s5, t5, bid, ask, None, None)
        if out is None:
            continue
        oc, fill, raw = out
        stop = abs(r.entry_price - r.stop_loss)
        adverse = (fill - r.entry_price) if r.side == "BUY" else (r.entry_price - fill)
        raw_s = raw - (LIVE_SLIP_SL if oc == "SL" else 0.0)
        lots = lots_for(stop)
        res.append(dict(key=r.key, reason=r.reason_key, signal_at=r.signal_at, side=r.side, stop=stop,
                        adverse_fill=adverse, outcome=oc, pts=raw, pts_slip=raw_s, lots=lots,
                        usd=raw_s * lots * 100.0, r=raw_s / stop if stop > 0 else np.nan))
    d = pd.DataFrame(res)
    d.to_csv(RES / "gates_counterfactual.csv", index=False)
    lt = live_trades()
    lines = ["=== counterfactual of REJECTED signals, resolved on the S5 quote from their own levels (raw quote pts; _slip adds +0.26 on SL) ==="]
    for key, g in d.groupby("key"):
        lv = lt[lt.key == key]
        lines.append(f"\n--- {key}: rejected {len(g)} (resolved), live placed {len(lv)} pts {lv.pts.sum():+.1f} PF {pf(lv.pts):.2f} meanR {lv.r.mean():+.3f} ---")
        t = g.groupby("reason").agg(n=("pts", "size"), adverse_fill=("adverse_fill", "mean"), wr=("pts", lambda x: 100 * (x > 0).mean()),
                                    pts=("pts", "sum"), pts_slip=("pts_slip", "sum"), meanR=("r", "mean"), usd=("usd", "sum"))
        t["pf"] = [pf(g[g.reason == k].pts_slip) for k in t.index]
        lines.append(t.round(2).to_string())
        lines.append(f"ALL rejected: pts {g.pts.sum():+.1f} / with slip {g.pts_slip.sum():+.1f} / USD {g.usd.sum():+.0f} / PF {pf(g.pts_slip):.2f} / meanR {g.r.mean():+.3f}")
    # pooled by reason across the four live legs
    d4 = d[d.key.isin(["s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo"])]
    lines.append("\n=== pooled over S93/S94/S99/S100, by gate ===")
    t = d4.groupby("reason").agg(n=("pts", "size"), adverse_fill=("adverse_fill", "mean"), wr=("pts", lambda x: 100 * (x > 0).mean()),
                                 pts=("pts", "sum"), pts_slip=("pts_slip", "sum"), meanR=("r", "mean"), usd=("usd", "sum"))
    t["pf"] = [pf(d4[d4.reason == k].pts_slip) for k in t.index]
    t["pts_per_trade"] = t.pts_slip / t.n
    lines.append(t.round(2).to_string())
    lv4 = lt[lt.key.isin(["s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo"])]
    lines.append(f"live PLACED (same four): n {len(lv4)}, pts {lv4.pts.sum():+.1f}, pts/trade {lv4.pts.mean():+.3f}, PF {pf(lv4.pts):.2f}, meanR {lv4.r.mean():+.3f}, USD {lv4.usd.sum():+.0f}")
    lines.append(f"ALL rejected (four legs): n {len(d4)}, pts_slip {d4.pts_slip.sum():+.1f}, pts/trade {d4.pts_slip.mean():+.3f}, PF {pf(d4.pts_slip):.2f}, meanR {d4.r.mean():+.3f}, USD {d4.usd.sum():+.0f}")
    # entry_drift: the gate's adverse-fill vs outcome relation
    ed = d4[d4.reason == "entry_drift"]
    lines.append(f"\nentry_drift rejections: adverse fill at the offered price mean {ed.adverse_fill.mean():+.2f}; "
                 f"outcome mix SL {100*(ed.outcome=='SL').mean():.0f}% TP {100*(ed.outcome=='TP').mean():.0f}% TIME {100*(ed.outcome=='TIME').mean():.0f}%; "
                 f"vs live placed outcome mix SL {100*(lv4.exit_kind=='SL').mean():.0f}% TP {100*(lv4.exit_kind=='TP').mean():.0f}%")
    # what if the drift budget were 1.0 instead of 0.5 (subset of rejections with adverse <= 1.0)
    for b in (0.75, 1.0, 1.5):
        sub = ed[ed.adverse_fill <= b]
        lines.append(f"  entry_drift rejections with offered adverse <= {b}: n {len(sub)}, pts_slip {sub.pts_slip.sum():+.1f}, PF {pf(sub.pts_slip):.2f}, meanR {sub.r.mean():+.3f}")
    txt = "\n".join(lines); print(txt)
    (RES / "04_gates.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
