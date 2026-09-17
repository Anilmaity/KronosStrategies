"""lab/sweep.py -- the parallel, resumable campaign runner.

The contract that matters: an arm run through the pool must produce EXACTLY what a
direct `replay()` call produces (same summary numbers, same trade list), every arm
lands in its own result file the moment it finishes, and a re-run skips finished
arms. Arms are tiny (S99 over three days) so the whole file runs in seconds.
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from lab import sweep                                   # noqa: E402
from lab.harness import CACHE, Cfg, load_bars, replay   # noqa: E402

pytestmark = pytest.mark.skipif(
    not (CACHE / "is_XAU_USD_1m.parquet").exists(), reason="bars cache not present")

START, END, SPLIT = "2026-05-04", "2026-05-07", "2026-05-06"


def _arms():
    return [
        sweep.Arm("c0.45", "s99_mss_fvg", Cfg(cost_pts=0.45), START, END, split=SPLIT),
        sweep.Arm("c0.80 blk", "s99_mss_fvg", Cfg(cost_pts=0.80, block_hours=(7,)), START, END,
                  split=SPLIT),
    ]


def test_slug_is_filesystem_safe():
    assert sweep.slug("ARM sd2.5_c0.45 / be=1R") == "ARM_sd2.5_c0.45_be_1R"


def test_pool_matches_direct_replay_and_writes_per_arm_files(tmp_path):
    arms = _arms()
    df = sweep.run_campaign("t", arms, out_dir=tmp_path, workers=2)

    bars = load_bars()
    for arm in arms:
        ref = replay(arm.strategy, bars, start=arm.start, end=arm.end, cfg=arm.cfg)
        row = df.loc[df.label == arm.label].iloc[0]
        assert row.status == "ok"
        for k in ("n", "pts", "pf", "wr", "maxdd_pts"):
            assert row[k] == ref[k], (arm.label, k)

        js = json.loads((tmp_path / "t" / f"{sweep.slug(arm.label)}.json").read_text())
        assert js["n"] == ref["n"] and js["strategy"] == arm.strategy
        assert "trades" not in js                      # trades go to parquet, not json
        assert js["elapsed_s"] > 0

        got = pd.read_parquet(tmp_path / "t" / f"{sweep.slug(arm.label)}.trades.parquet")
        exp = ref["trades"].reset_index(drop=True)
        pd.testing.assert_frame_equal(got, exp, check_dtype=False)


def test_halves_are_reported_when_split_given(tmp_path):
    df = sweep.run_campaign("t", _arms()[:1], out_dir=tmp_path, workers=1)
    row = df.iloc[0]
    assert row.train_n + row.test_n == row.n
    assert row.train_n > 0 and row.test_n > 0
    assert {"train_pf", "test_pf", "train_pts", "test_pts"} <= set(df.columns)


def test_zero_trade_arm_reports_zero_halves(tmp_path):
    arm = sweep.Arm("blocked", "s99_mss_fvg", Cfg(block_hours=tuple(range(24))), START, END,
                    split=SPLIT)
    df = sweep.run_campaign("t", [arm], out_dir=tmp_path, workers=1)
    row = df.iloc[0]
    assert row.status == "ok" and row.n == 0 and row.train_n == 0 and row.test_n == 0


def test_rerun_skips_finished_arms(tmp_path):
    arms = _arms()[:1]
    sweep.run_campaign("t", arms, out_dir=tmp_path, workers=1)
    f = tmp_path / "t" / f"{sweep.slug(arms[0].label)}.json"
    before = f.stat().st_mtime_ns

    df = sweep.run_campaign("t", arms, out_dir=tmp_path, workers=1)
    assert f.stat().st_mtime_ns == before
    assert df.iloc[0].status == "skipped"

    df = sweep.run_campaign("t", arms, out_dir=tmp_path, workers=1, force=True)
    assert f.stat().st_mtime_ns > before
    assert df.iloc[0].status == "ok"


def test_failed_arm_is_recorded_and_does_not_sink_the_others(tmp_path):
    arms = [sweep.Arm("bad", "s_does_not_exist", Cfg(), START, END)] + _arms()[:1]
    df = sweep.run_campaign("t", arms, out_dir=tmp_path, workers=2)
    bad = df.loc[df.label == "bad"].iloc[0]
    assert bad.status == "error" and "s_does_not_exist" in bad.error
    assert (tmp_path / "t" / "bad.error.json").exists()
    assert df.loc[df.label == "c0.45"].iloc[0].status == "ok"


def test_summary_reads_back_the_result_files(tmp_path):
    arms = _arms()
    sweep.run_campaign("t", arms, out_dir=tmp_path, workers=2)
    df = sweep.load_campaign("t", out_dir=tmp_path)
    assert sorted(df.label) == sorted(a.label for a in arms)
    assert (df.status == "ok").all()
