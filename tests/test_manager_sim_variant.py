"""
test_manager_sim_variant.py  (Task 6 — parallel sensitivity)
------------------------------------------------------------
Tests for strategies/backtest/manager_sim_variant.py, the per-process
sensitivity-variant driver.

sys.path: conftest.py adds repo root; this file adds strategies/ so
backtest.* resolves.  The synthetic-cache helper is imported from
test_manager_sim (pytest puts tests/ on sys.path).
"""
from __future__ import annotations

import csv
import json
import os
import sys

import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies")
)
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest.manager_sim_report import (
    SENSITIVITY_VARIANTS,
    _SESSION_WINDOWS_MINUS_30,
    _SESSION_WINDOWS_PLUS_30,
)
from backtest import manager_sim_variant as msv

from test_manager_sim import _write_synthetic_cache, _SHALLOW

UTC_START = "2026-04-06"
UTC_END   = "2026-04-10T21:00"   # matches the synthetic tape's END


# ─────────────────────────────────────────────────────────────────────────────
# Variant definitions — must match run_sensitivity's historical 6 exactly
# ─────────────────────────────────────────────────────────────────────────────

def test_variant_names_are_the_expected_six():
    assert set(SENSITIVITY_VARIANTS) == {
        "vol_loose", "vol_tight", "er_loose", "er_tight",
        "win_minus30", "win_plus30",
    }
    # Insertion order == run_sensitivity's historical run order.
    assert [v["label"] for v in SENSITIVITY_VARIANTS.values()] == [
        "vol(20/70/90)", "vol(30/80/97)",
        "er(0.30/0.15)", "er(0.40/0.25)",
        "windows -30min", "windows +30min",
    ]


def test_threshold_variant_values_match_run_sensitivity():
    assert SENSITIVITY_VARIANTS["vol_loose"]["kind"] == "threshold"
    assert SENSITIVITY_VARIANTS["vol_loose"]["attrs"] == {
        "VOL_PCTL_LOW": 20.0, "VOL_PCTL_HIGH": 70.0, "VOL_PCTL_EXTREME": 90.0,
    }
    assert SENSITIVITY_VARIANTS["vol_tight"]["attrs"] == {
        "VOL_PCTL_LOW": 30.0, "VOL_PCTL_HIGH": 80.0, "VOL_PCTL_EXTREME": 97.0,
    }
    assert SENSITIVITY_VARIANTS["er_loose"]["attrs"] == {
        "ER_TRENDING": 0.30, "ER_RANGING": 0.15,
    }
    assert SENSITIVITY_VARIANTS["er_tight"]["attrs"] == {
        "ER_TRENDING": 0.40, "ER_RANGING": 0.25,
    }


def test_window_variant_values_match_run_sensitivity():
    assert SENSITIVITY_VARIANTS["win_minus30"]["kind"] == "windows"
    assert SENSITIVITY_VARIANTS["win_minus30"]["windows"] is _SESSION_WINDOWS_MINUS_30
    assert SENSITIVITY_VARIANTS["win_minus30"]["windows"] == [[6.25, 9.5], [12.75, 15.5]]
    assert SENSITIVITY_VARIANTS["win_plus30"]["windows"] is _SESSION_WINDOWS_PLUS_30
    assert SENSITIVITY_VARIANTS["win_plus30"]["windows"] == [[7.25, 10.5], [13.75, 16.5]]


def test_variant_driver_uses_the_same_object_no_duplication():
    """manager_sim_variant must import the report's dict, not copy numbers."""
    assert msv.SENSITIVITY_VARIANTS is SENSITIVITY_VARIANTS


# ─────────────────────────────────────────────────────────────────────────────
# CLI error handling
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_variant_exits_with_error(tmp_path):
    with pytest.raises(SystemExit) as ei:
        msv.main([
            "--variant", "bogus_variant",
            "--start", UTC_START, "--end", UTC_END,
            "--cache-dir", str(tmp_path),
            "--out", str(tmp_path / "x.json"),
        ])
    assert ei.value.code not in (0, None)


def test_collect_without_base_csvs_exits_with_error(tmp_path):
    with pytest.raises(SystemExit) as ei:
        msv.main(["--collect", str(tmp_path / "variant_*.json")])
    assert ei.value.code not in (0, None)


# ─────────────────────────────────────────────────────────────────────────────
# JSON output shape (tiny synthetic cache, shallow slice_rows for speed)
# ─────────────────────────────────────────────────────────────────────────────

def test_variant_json_output_shape(tmp_path):
    _write_synthetic_cache(tmp_path)
    out = tmp_path / "variant_vol_loose.json"

    rc = msv.main([
        "--variant", "vol_loose",
        "--start", UTC_START, "--end", UTC_END,
        "--cache-dir", str(tmp_path),
        "--out", str(out),
        "--slice-rows", json.dumps(_SHALLOW),
    ])
    assert rc == 0
    assert out.exists()

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["variant"] == "vol_loose"
    assert data["label"] == "vol(20/70/90)"
    for key in ("trades", "net_usd", "max_dd_usd", "wr", "pf",
                "per_strategy_net"):
        assert key in data, f"missing key {key!r} in variant JSON"
    assert isinstance(data["trades"], int) and data["trades"] >= 0
    assert isinstance(data["net_usd"], (int, float))
    assert isinstance(data["max_dd_usd"], (int, float)) and data["max_dd_usd"] >= 0
    assert 0.0 <= data["wr"] <= 100.0
    assert data["pf"] is None or isinstance(data["pf"], (int, float))
    assert isinstance(data["per_strategy_net"], dict)
    # per-strategy nets must sum to the combined net
    assert sum(data["per_strategy_net"].values()) == pytest.approx(
        data["net_usd"], abs=1e-2
    )


def test_window_variant_runs_through_shifted_specs(tmp_path):
    """Smoke the kind='windows' path (specs replacement) end to end."""
    _write_synthetic_cache(tmp_path)
    out = tmp_path / "variant_win_plus30.json"
    rc = msv.main([
        "--variant", "win_plus30",
        "--start", UTC_START, "--end", UTC_END,
        "--cache-dir", str(tmp_path),
        "--out", str(out),
        "--slice-rows", json.dumps(_SHALLOW),
    ])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["variant"] == "win_plus30"
    assert data["label"] == "windows +30min"


# ─────────────────────────────────────────────────────────────────────────────
# Collect mode
# ─────────────────────────────────────────────────────────────────────────────

def _write_variant_json(path, name, net_usd):
    path.write_text(json.dumps({
        "variant": name, "label": name, "trades": 3, "net_usd": net_usd,
        "max_dd_usd": 5.0, "wr": 66.7, "pf": 2.5, "per_strategy_net": {},
    }), encoding="utf-8")


def _write_trades_csv(path, pnl_usd_values):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["strategy", "pnl_usd"])
        for v in pnl_usd_values:
            w.writerow(["S1", v])


def test_collect_renders_grid_and_cond4_pass(tmp_path):
    # base: gated +50, ungated -5  => delta +55 (positive)
    _write_trades_csv(tmp_path / "g.csv", [30.0, 20.0])
    _write_trades_csv(tmp_path / "u.csv", [10.0, -25.0, 10.0])
    _write_variant_json(tmp_path / "variant_a.json", "vol_loose", 40.0)
    _write_variant_json(tmp_path / "variant_b.json", "win_plus30", 45.0)

    out_md = tmp_path / "sens.md"
    md = msv.collect(str(tmp_path / "variant_*.json"),
                     tmp_path / "g.csv", tmp_path / "u.csv", out_md)
    text = out_md.read_text(encoding="utf-8")
    assert md in text                      # appended verbatim
    assert "vol_loose" in text and "win_plus30" in text
    assert "+55.00" in text                # base delta reconstructed from CSVs
    assert "condition 4" in text and "**PASS**" in text


def test_collect_sign_flip_gives_fail(tmp_path):
    # base delta +55; variant net -10 => variant delta -5 (sign flipped)
    _write_trades_csv(tmp_path / "g.csv", [50.0])
    _write_trades_csv(tmp_path / "u.csv", [-5.0])
    _write_variant_json(tmp_path / "variant_flip.json", "er_tight", -10.0)

    out_md = tmp_path / "sens.md"
    msv.collect(str(tmp_path / "variant_*.json"),
                tmp_path / "g.csv", tmp_path / "u.csv", out_md)
    assert "**FAIL**" in out_md.read_text(encoding="utf-8")
