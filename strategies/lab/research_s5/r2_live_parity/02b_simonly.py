"""02b_simonly.py — where do the harness trades that live never generated come from?
Classifies each sim-only harness trade by whether the live strategy was ACTIVE (per the
manager's START/PAUSE rows) at the harness entry time. PROTOCOL deviation 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import RES   # noqa: E402

NAMES = {"s93_fvg_scalp": "S93 FVG Scalp", "s94_sweep_reversal": "S94 Sweep Reversal",
         "s99_mss_fvg": "S99 MSS FVG Reversal", "s100_m3_combo": "S100 M3 Combo Scalper"}


def active_windows(acts: pd.DataFrame, name: str):
    a = acts[(acts.strategy_name == name) & acts.action.isin(["START", "PAUSE"])].sort_values("created_at")
    wins, cur = [], None
    for t, act in zip(a.created_at, a.action):
        if act == "START" and cur is None:
            cur = t
        elif act == "PAUSE" and cur is not None:
            wins.append((cur, t)); cur = None
    if cur is not None:
        wins.append((cur, pd.Timestamp("2026-09-18 12:00", tz="UTC")))
    return wins


def is_active(t, wins):
    return any(a <= t < b for a, b in wins)


def main():
    acts = pd.read_csv(RES / "manager_actions_all.csv", parse_dates=["created_at"])
    acts["created_at"] = pd.to_datetime(acts.created_at, utc=True)
    kill = acts[acts.action == "KILL_SWITCH"].created_at
    lines = []
    for key, name in NAMES.items():
        wins = active_windows(acts, name)
        p = pd.read_csv(RES / f"parity_{key}.csv", parse_dates=["entry_time"])
        p["entry_time"] = pd.to_datetime(p.entry_time, utc=True)
        p["live_active"] = [is_active(t, wins) for t in p.entry_time]
        # a kill-switch day: same UTC date as a trip and after the trip time
        p["after_kill"] = [any((t.date() == k.date()) and (t >= k) for k in kill) for t in p.entry_time]
        so = p[p.live_status == "sim_only"]
        pause_reason = np.where(so.after_kill, "kill_switch_pause",
                                np.where(~so.live_active, "other_pause_or_down", "live_active"))
        so = so.assign(cls=pause_reason)
        g = so.groupby("cls").agg(n=("q_pts", "size"), q45=("q_pts", "sum"), m75=("m_pts", "sum"))
        lines.append(f"\n=== {key}: sim-only harness trades n={len(so)} (all harness {len(p)}; live-active windows {len(wins)}) ===")
        lines.append(g.round(1).to_string())
        # the same classification for ALL harness trades: how much of the harness result sits in live-inactive time
        allc = np.where(p.after_kill, "kill_switch_pause", np.where(~p.live_active, "other_pause_or_down", "live_active"))
        ga = p.assign(cls=allc).groupby("cls").agg(n=("q_pts", "size"), q45=("q_pts", "sum"), m75=("m_pts", "sum"))
        lines.append("all harness trades by live-activity class:\n" + ga.round(1).to_string())
        # sanity: live-matched harness trades should be live_active
        lm = p[p.live_status != "sim_only"]
        lines.append(f"harness trades matched to a live signal but outside live-active windows: {int((~lm.live_active).sum())} of {len(lm)}")
        p.to_csv(RES / f"parity_{key}.csv", index=False)
    txt = "\n".join(lines); print(txt)
    (RES / "02b_simonly.txt").write_text(txt + "\n")


if __name__ == "__main__":
    main()
