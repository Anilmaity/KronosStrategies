"""S100 closed-bucket resample: the numpy path must equal the pandas path exactly.

`_resample_pandas` below is the pre-2026-09-18 implementation kept verbatim as the
reference. The production functions (`_resample_m3`, `_resample_closed`) are compared
against it with assert_frame_equal at full strictness (values, dtypes, index, and the
tz / unit of the `time` column) over:

  * synthetic edge cases -- tail buckets at every completeness, missing minutes inside
    a bucket, a weekend gap, a window starting mid-bucket, one-bar windows, tz-aware
    and tz-naive time columns, and a non-ns datetime unit;
  * 200 random windows of the real M1 cache at the harness's win_1m (700 bars) plus
    the M15/H1 rules the ER gate uses.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from backtest_strategies import s100_m3_combo as s100   # noqa: E402
from lab.harness import CACHE, TF_FILES                  # noqa: E402


def _resample_pandas(w1m: pd.DataFrame, rule: str, bucket_min: int) -> pd.DataFrame:
    """Reference: the original pandas implementation, verbatim."""
    df = (w1m.set_index("time")
          .resample(rule)
          .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
          .dropna())
    if len(df) == 0:
        return df.reset_index()
    last_m1 = w1m["time"].iloc[-1]
    last_bucket = df.index[-1]
    if (pd.Timestamp(last_m1) - pd.Timestamp(last_bucket)) < pd.Timedelta(
            minutes=bucket_min - 1):
        df = df.iloc[:-1]
    return df.reset_index()


def _m1(times, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(times)
    c = 4000 + np.cumsum(rng.normal(0, 0.3, n))
    o = np.concatenate(([c[0]], c[:-1]))
    return pd.DataFrame({"time": pd.DatetimeIndex(times),
                         "open": o, "high": np.maximum(o, c) + 0.2,
                         "low": np.minimum(o, c) - 0.2, "close": c})


def _check(w1m: pd.DataFrame, rule="3min", bucket_min=3):
    exp = _resample_pandas(w1m, rule, bucket_min)
    got = (s100._resample_m3(w1m) if bucket_min == 3
           else s100._resample_closed(w1m, rule, bucket_min))
    pd.testing.assert_frame_equal(got, exp)


# ---- synthetic edge cases ------------------------------------------------------------
@pytest.mark.parametrize("n", range(1, 13))
def test_tail_completeness_every_length(n):
    # windows of 1..12 bars from 03:00 -> tail bucket has 1, 2 or 3 bars
    _check(_m1(pd.date_range("2026-07-20 03:00", periods=n, freq="1min", tz="UTC")))


@pytest.mark.parametrize("offset", [1, 2])
def test_window_starting_mid_bucket(offset):
    t = pd.date_range("2026-07-20 03:00", periods=20, freq="1min", tz="UTC")[offset:]
    _check(_m1(t))


def test_missing_minutes_inside_buckets():
    t = pd.date_range("2026-07-20 03:00", periods=30, freq="1min", tz="UTC")
    keep = [i for i in range(30) if i not in (4, 7, 8, 13, 20, 21, 22, 27)]   # 20..22 = empty bucket
    _check(_m1(t[keep]))


def test_tail_bucket_with_only_its_last_minute_is_kept():
    # bucket 03:06 has only the 03:08 bar: the pandas rule keeps it (gap = 2 min)
    t = pd.date_range("2026-07-20 03:00", periods=6, freq="1min", tz="UTC").append(
        pd.DatetimeIndex([pd.Timestamp("2026-07-20 03:08", tz="UTC")]))
    _check(_m1(t))


def test_weekend_gap():
    fri = pd.date_range("2026-07-17 20:40", periods=80, freq="1min", tz="UTC")
    sun = pd.date_range("2026-07-19 22:00", periods=40, freq="1min", tz="UTC")
    _check(_m1(fri.append(sun)))


def test_tz_naive_time_column():
    t = pd.date_range("2026-07-20 03:00", periods=40, freq="1min")     # no tz
    _check(_m1(t))


def test_non_ns_datetime_unit():
    t = pd.date_range("2026-07-20 03:00", periods=40, freq="1min", tz="UTC").as_unit("us")
    _check(_m1(t))


@pytest.mark.parametrize("rule,bucket_min", [("15min", 15), ("1h", 60)])
def test_er_gate_rules(rule, bucket_min):
    t = pd.date_range("2026-07-20 02:07", periods=700, freq="1min", tz="UTC")
    keep = [i for i in range(700) if i % 97 != 5]
    _check(_m1(t[keep]), rule, bucket_min)


# ---- real data -----------------------------------------------------------------------
@pytest.mark.skipif(not (CACHE / TF_FILES["1m"]).exists(), reason="bars cache not present")
def test_real_m1_cache_random_windows():
    df = pd.read_parquet(CACHE / TF_FILES["1m"])
    df = df.assign(time=pd.to_datetime(df["time"], utc=True).dt.tz_convert(None)
                   .astype("datetime64[ns]")).sort_values("time").reset_index(drop=True)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    rng = np.random.default_rng(20260918)
    for i0 in rng.integers(0, len(df) - 700, size=200):
        w = df.iloc[i0:i0 + 700].reset_index(drop=True)
        _check(w)
        _check(w, "15min", 15)
        _check(w, "1h", 60)
