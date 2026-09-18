"""05_rails.py — kill-switch, soft brake, concurrency (PROTOCOL D9, D10).

    cd strategies && ../.venv/bin/python lab/research_s5/r2_live_parity/05_rails.py

Daily realized USD is rebuilt two ways: (i) manager-view = Position.realized (×100) of the LIVE
roster (engine + both copy slots) attributed to the exit order's UTC day (what the manager sums);
(ii) account-view = every broker deal by deal_time UTC day (includes manual mobile scalps the
manager does not see). Trips are re-detected on the manager-view running total with the
threshold history read from the KILL_SWITCH rows (200 → 150 → 250) and cross-checked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RES, load_deals, pf   # noqa: E402

ENGINE4 = ["s93_fvg_scalp", "s94_sweep_reversal", "s99_mss_fvg", "s100_m3_combo"]
ROSTER_NOW = {"s93_cur": "s93_fvg_scalp", "s94_long25": "s94_sweep_reversal", "s99": "s99_mss_fvg",
              "s100": "s100_m3_combo", "c03": "c03_fvg_fill", "s14_30": "s14_ob_mit_bias"}
RISK_USD, MAX_LOT, MIN_LOT = 38.0, 0.10, 0.01


def threshold_at(day: pd.Timestamp) -> float:
    if day < pd.Timestamp("2026-07-10", tz="UTC"):
        return 200.0
    if day < pd.Timestamp("2026-08-28", tz="UTC"):
        return 150.0
    return 250.0


def lots_for(stop):
    return np.clip(RISK_USD / (np.maximum(stop, 1e-6) * 100.0), MIN_LOT, MAX_LOT)


def main():
    out = []
    pos = pd.read_parquet(RES / "live_positions.parquet")
    for c in ("pos_created_at", "entry_created_at", "exit_created_at"):
        pos[c] = pd.to_datetime(pos[c], utc=True)
    live_keys = ENGINE4 + ["s95_session_breakout", "orb", "c03_fvg_fill", "s14_ob_mit_bias", "copy_free", "copy_vip"]
    closed = pos[(pos.quantity == 0) & pos.key.isin(live_keys) & pos.exit_created_at.notna()].copy()
    closed["usd"] = closed.realized_units * 100.0
    closed["day"] = closed.exit_created_at.dt.floor("D")
    closed["slot"] = np.where(closed.key.isin(["copy_free", "copy_vip"]), "copy", "engine")
    # ---- manager-view daily totals and trip detection ----
    days = closed.groupby("day").usd.sum().sort_index()
    trips = []
    for day, g in closed.sort_values("exit_created_at").groupby("day"):
        thr = threshold_at(day)
        cum = g.usd.cumsum()
        hit = cum[cum <= -thr]
        if len(hit):
            t = g.loc[hit.index[0], "exit_created_at"]
            eng_before = g[g.exit_created_at <= t].query("slot == 'engine'").usd.sum()
            cp_before = g[g.exit_created_at <= t].query("slot == 'copy'").usd.sum()
            trips.append(dict(day=day.date(), thr=thr, trip_time=t, cum_at_trip=round(hit.iloc[0], 1),
                              engine_before=round(eng_before, 1), copy_before=round(cp_before, 1),
                              day_total=round(g.usd.sum(), 1), after_trip_realized=round(g[g.exit_created_at > t].usd.sum(), 1)))
    tr = pd.DataFrame(trips)
    acts = pd.read_csv(RES / "actions.csv", parse_dates=["created_at"])
    ks = acts[acts.action == "KILL_SWITCH"]
    out.append("=== (c1) kill-switch: re-detected trips on the manager-view daily total (Position.realized, exit day UTC) ===")
    out.append(tr.to_string(index=False))
    out.append(f"re-detected {len(tr)} trips vs {len(ks)} KILL_SWITCH ManagerAction rows on days "
               f"{sorted(set(pd.to_datetime(ks.created_at).dt.date))}")
    deals = load_deals()
    dd = deals[deals.entry_type == "DEAL_ENTRY_OUT"].copy()
    dd["usd"] = dd.profit.fillna(0) + dd.commission.fillna(0) + dd.swap.fillna(0)
    dd["day"] = dd.deal_time.dt.floor("D")
    dd["src"] = np.where(dd.raw_reason == "DEAL_REASON_MOBILE", "manual", np.where(dd.raw_comment.fillna("").str.startswith("tg"), "copy", "engine"))
    acc_days = dd.groupby("day").usd.sum()
    out.append(f"\naccount-view (broker deals) daily totals: n days {len(acc_days)}, sum {acc_days.sum():+.0f}; by source: "
               + ", ".join(f"{k} {v:+.0f}" for k, v in dd.groupby("src").usd.sum().items()))
    out.append(f"manager-view: n days {len(days)}, sum {days.sum():+.0f}; engine {closed[closed.slot=='engine'].usd.sum():+.0f}, copy {closed[closed.slot=='copy'].usd.sum():+.0f}")
    out.append("daily distribution (manager-view): " + ", ".join(
        f"<= -{t}: {(days <= -t).sum()} days" for t in (120, 150, 250)) + f"; best {days.max():+.0f}, worst {days.min():+.0f}, median {days.median():+.0f}")
    # copy vs engine share of the loss on trip days
    if len(tr):
        out.append(f"on trip days, loss at the trip: engine {tr.engine_before.sum():+.0f} vs copy {tr.copy_before.sum():+.0f} "
                   f"(copy share {100*tr.copy_before.sum()/(tr.engine_before.sum()+tr.copy_before.sum()):.0f}%)")
    # ---- (c1b) counterfactual: what the engine would have done after each trip ----
    rows = []
    for key in ENGINE4:
        p = pd.read_csv(RES / f"parity_{key}.csv", parse_dates=["entry_time"])
        p["entry_time"] = pd.to_datetime(p.entry_time, utc=True)
        p["day"] = p.entry_time.dt.floor("D")
        p["lots"] = lots_for(p.risk)
        p["usd_q45"] = p.q_pts * p.lots * 100.0
        k = p[p.after_kill]
        rows.append(dict(strategy=key, n_after_trip=len(k), pts_q45=round(k.q_pts.sum(), 1), usd_q45=round(k.usd_q45.sum(), 0),
                         wr=round(100 * (k.q_pts > 0).mean(), 0) if len(k) else np.nan))
    cf = pd.DataFrame(rows)
    out.append("\n(c1b) harness trades the four engine legs would have taken AFTER each trip that day (quote@0.45, live lots rule):")
    out.append(cf.to_string(index=False))
    out.append(f"TOTAL engine after-trip counterfactual: {cf.pts_q45.sum():+.1f} pts / {cf.usd_q45.sum():+.0f} USD over {len(tr)} trip days "
               f"(negative = the kill-switch SAVED that much; copy slot not modelled)")
    # per trip day
    per = []
    for key in ENGINE4:
        p = pd.read_csv(RES / f"parity_{key}.csv", parse_dates=["entry_time"])
        p["entry_time"] = pd.to_datetime(p.entry_time, utc=True)
        p["lots"] = lots_for(p.risk); p["usd_q45"] = p.q_pts * p.lots * 100.0
        k = p[p.after_kill].assign(day=lambda d: d.entry_time.dt.date)
        per.append(k.groupby("day").agg(n=("q_pts", "size"), usd=("usd_q45", "sum")).assign(strategy=key))
    per = pd.concat(per).groupby("day").agg(n=("n", "sum"), usd=("usd", "sum"))
    out.append("by trip day:\n" + per.round(0).to_string())

    # ---- (c2) soft brake ----
    g = pd.read_csv(RES / "gates_counterfactual.csv", parse_dates=["signal_at"])
    sb = g[g.reason == "soft_daily_brake"]
    sb_days = pd.to_datetime(sb.signal_at, utc=True).dt.date.nunique()
    out.append(f"\n=== (c2) soft brake ($120): {len(sb)} rejections on {sb_days} distinct days; counterfactual (04_gates, quote+slip, live lots) "
               f"{sb.pts_slip.sum():+.1f} pts / {sb.usd.sum():+.0f} USD, PF {pf(sb.pts_slip):.2f}, WR {100*(sb.pts_slip>0).mean():.0f}% "
               f"(negative = the brake SAVED)")
    out.append(sb.groupby("key").agg(n=("pts_slip", "size"), pts=("pts_slip", "sum"), usd=("usd", "sum")).round(1).to_string())

    # ---- (c3) concurrency: live open_position_cap + prospective book ----
    oc = g[g.reason == "open_position_cap"]
    out.append(f"\n=== (c3) concurrency ===\nlive per-strategy open_position_cap rejections: {len(oc)}; counterfactual {oc.pts_slip.sum():+.1f} pts / {oc.usd.sum():+.0f} USD, PF {pf(oc.pts_slip):.2f}")
    out.append(oc.groupby("key").agg(n=("pts_slip", "size"), pts=("pts_slip", "sum"), usd=("usd", "sum")).round(1).to_string())
    # prospective: the 09-18 roster over 07-01..09-17 (harness, quote@0.45)
    sims = []
    for arm, key in ROSTER_NOW.items():
        s = pd.read_parquet(RES / f"sim_{arm}.parquet")
        s["entry_time"] = pd.to_datetime(s.entry_time, utc=True); s["exit_time"] = pd.to_datetime(s.exit_time, utc=True)
        s["q_pts"] = s.q_pts0 - 0.45; s["key"] = key
        s["lots"] = lots_for(s.risk); s["usd"] = s.q_pts * s.lots * 100.0
        sims.append(s[["key", "entry_time", "exit_time", "q_pts", "risk", "lots", "usd", "side"]])
    book = pd.concat(sims).sort_values("entry_time").reset_index(drop=True)
    out.append(f"prospective 09-18 roster over the window (harness, quote@0.45): n {len(book)}, pts {book.q_pts.sum():+.1f}, USD {book.usd.sum():+.0f}; per leg:")
    out.append(book.groupby("key").agg(n=("q_pts", "size"), pts=("q_pts", "sum"), usd=("usd", "sum"), pf=("q_pts", pf)).round(1).to_string())
    # simultaneous open positions per minute
    ev = pd.concat([pd.DataFrame({"t": book.entry_time, "d": 1}), pd.DataFrame({"t": book.exit_time, "d": -1})]).sort_values(["t", "d"])
    ev["open"] = ev.d.cumsum()
    ev["dt"] = (ev.t.shift(-1) - ev.t).dt.total_seconds().fillna(0) / 60.0
    occ = ev.groupby("open").dt.sum()
    tot = occ.sum()
    out.append("share of open-market minutes by number of simultaneous open positions (whole roster): " +
               ", ".join(f"{int(k)}: {100*v/tot:.1f}%" for k, v in occ.items()))
    for cap in (3, 5):
        openp, refused, kept = [], [], []
        for r in book.itertuples():
            openp = [x for x in openp if x > r.entry_time]
            if len(openp) >= cap:
                refused.append(r)
            else:
                openp.append(r.exit_time); kept.append(r)
        rf = pd.DataFrame(refused)
        out.append(f"book cap {cap}: entries refused {len(rf)} of {len(book)} ({100*len(rf)/len(book):.1f}%), refused pts {rf.q_pts.sum() if len(rf) else 0:+.1f} "
                   f"/ USD {rf.usd.sum() if len(rf) else 0:+.0f}; refused by leg: " + (", ".join(f"{k} {v}" for k, v in rf.key.value_counts().items()) if len(rf) else "-"))
    # same-side overlap: how often two legs hold the same direction at once (correlated heat)
    ev2 = []
    for r in book.itertuples():
        ev2.append((r.entry_time, 1 if r.side == "BUY" else -1, 1)); ev2.append((r.exit_time, 1 if r.side == "BUY" else -1, -1))
    e = pd.DataFrame(ev2, columns=["t", "s", "d"]).sort_values(["t", "d"])
    e["long"] = (e.s.eq(1) * e.d).cumsum(); e["short"] = (e.s.eq(-1) * e.d).cumsum()
    e["dt"] = (e.t.shift(-1) - e.t).dt.total_seconds().fillna(0) / 60.0
    net = (e.long - e.short).abs()
    out.append("net same-direction exposure |longs - shorts| minutes share: " + ", ".join(f"{int(k)}: {100*v/tot:.1f}%" for k, v in e.groupby(net).dt.sum().items()))
    book.to_parquet(RES / "book_prospective.parquet", index=False)
    days.to_csv(RES / "rails_days.csv"); tr.to_csv(RES / "rails_trips.csv", index=False)
    txt = "\n".join(out); print(txt)
    (RES / "05_rails.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
