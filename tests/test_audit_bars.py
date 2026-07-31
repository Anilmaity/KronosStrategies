"""Task 5 (Manager Backtest plan): OANDA bars builder + parquet cache.

All HTTP is mocked; no network."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

_STRAT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "strategies"))
if _STRAT_DIR not in sys.path:
    sys.path.insert(0, _STRAT_DIR)

from audit_worker import bars  # noqa: E402


def _utc(y, mo, d, h=0, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _candle(iso, o, h, l, c, complete=True, vol=1):
    return {"complete": complete, "time": iso, "volume": vol,
            "mid": {"o": str(o), "h": str(h), "l": str(l), "c": str(c)}}


class FakeSession:
    """Canned page responses; records every request's params."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        body = self.pages.pop(0) if self.pages else {"candles": []}
        return SimpleNamespace(json=lambda: body, raise_for_status=lambda: None)


@pytest.fixture()
def fake(monkeypatch):
    def install(pages):
        s = FakeSession(pages)
        monkeypatch.setattr(bars, "_session", s)
        return s
    return install


def test_fetch_candles_pages_in_order(fake):
    p1 = {"candles": [
        _candle("2026-01-05T00:00:00.000000000Z", 1, 2, 0.5, 1.5),
        _candle("2026-01-05T00:01:00.000000000Z", 1.5, 2.5, 1.0, 2.0),
    ]}
    p2 = {"candles": [
        _candle("2026-01-05T00:01:00.000000000Z", 1.5, 2.5, 1.0, 2.0),
        _candle("2026-01-05T00:02:00.000000000Z", 2.0, 3.0, 1.5, 2.5),
        _candle("2026-01-05T00:03:00.000000000Z", 2.5, 3.5, 2.0, 3.0, complete=False),
    ]}
    s = fake([p1, p2])
    df = bars.fetch_candles("M1", _utc(2026, 1, 5), _utc(2026, 1, 5, 1))
    assert len(s.calls) >= 2
    assert list(df["close"]) == [1.5, 2.0, 2.5]          # deduped, ordered
    assert df["time"].is_monotonic_increasing
    assert str(df["time"].dt.tz) == "UTC"


def test_fetch_candles_empty(fake):
    fake([{"candles": []}])
    df = bars.fetch_candles("M1", _utc(2026, 1, 5), _utc(2026, 1, 6))
    assert df.empty


def _mk_m1(start, minutes):
    times = pd.date_range(start, periods=minutes, freq="1min", tz="UTC")
    base = pd.Series(range(minutes), dtype=float)
    return pd.DataFrame({
        "time": times, "open": base + 1.0, "high": base + 2.0,
        "low": base + 0.5, "close": base + 1.5, "volume": 1,
    })


def test_ensure_frames_cache_hit_no_http(tmp_path, fake):
    start, end = _utc(2026, 6, 1), _utc(2026, 6, 2)
    covering = _mk_m1(pd.Timestamp(start) - pd.Timedelta(days=bars.WARMUP_DAYS),
                      minutes=(bars.WARMUP_DAYS + 1) * 1440)
    covering.to_parquet(tmp_path / "is_XAU_USD_1m.parquet", index=False)
    s = fake([])
    bars.ensure_frames(tmp_path, start, end)
    assert s.calls == []
    for tf in ("5m", "15m", "1h", "4h", "1d"):
        assert (tmp_path / f"is_XAU_USD_{tf}.parquet").exists()


def test_ensure_frames_fetches_only_tail_gap(tmp_path, fake):
    start, end = _utc(2026, 6, 1), _utc(2026, 6, 3)
    have_until = pd.Timestamp(_utc(2026, 6, 2))
    covering = _mk_m1(pd.Timestamp(start) - pd.Timedelta(days=bars.WARMUP_DAYS),
                      minutes=int((have_until - (pd.Timestamp(start)
                                   - pd.Timedelta(days=bars.WARMUP_DAYS)))
                                  .total_seconds() // 60))
    covering.to_parquet(tmp_path / "is_XAU_USD_1m.parquet", index=False)
    s = fake([{"candles": [
        _candle("2026-06-02T12:00:00.000000000Z", 5, 6, 4, 5.5)]}])
    bars.ensure_frames(tmp_path, start, end)
    # One data page + at most one terminating empty page — and every request
    # starts at/after the existing coverage (tail fetch only, no head refetch).
    assert 1 <= len(s.calls) <= 2
    assert all(c["from"] >= "2026-06-01" for c in s.calls)

    merged = pd.read_parquet(tmp_path / "is_XAU_USD_1m.parquet")
    assert pd.to_datetime(merged["time"], utc=True).max() == pd.Timestamp(
        "2026-06-02T12:00:00Z")


def test_resample_matches_hand_fixture(tmp_path, fake):
    start, end = _utc(2026, 6, 1), _utc(2026, 6, 2)
    covering = _mk_m1(pd.Timestamp(start) - pd.Timedelta(days=bars.WARMUP_DAYS),
                      minutes=(bars.WARMUP_DAYS + 1) * 1440)
    covering.to_parquet(tmp_path / "is_XAU_USD_1m.parquet", index=False)
    fake([])
    bars.ensure_frames(tmp_path, start, end)
    m5 = pd.read_parquet(tmp_path / "is_XAU_USD_5m.parquet")
    first = m5.iloc[0]
    # First 5 M1 rows: opens 1..5, highs 2..6, lows .5..4.5, closes 1.5..5.5
    assert first["open"] == 1.0
    assert first["high"] == 6.0
    assert first["low"] == 0.5
    assert first["close"] == 5.5


def test_ensure_s5_cache_hit_and_empty(tmp_path, fake):
    span0, span1 = _utc(2026, 6, 1, 12, 0), _utc(2026, 6, 1, 12, 1)
    times = pd.date_range(span0, periods=12, freq="5s", tz="UTC")
    pd.DataFrame({
        "time": times, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "volume": 1,
    }).to_parquet(tmp_path / "is_XAU_USD_S5.parquet", index=False)

    s = fake([])
    hit = bars.ensure_s5(tmp_path, span0, span1)
    assert len(hit) == 12 and s.calls == []              # served from cache

    s = fake([{"candles": []}])
    miss = bars.ensure_s5(tmp_path, _utc(2026, 6, 5, 9), _utc(2026, 6, 5, 9, 1))
    assert miss.empty and len(s.calls) == 1              # fetched, none exists


def test_ensure_frames_cold_start_no_cache(tmp_path, fake):
    """First live smoke run (2026-08-01): with NO existing parquet, the empty
    object-dtype seed frame must not poison the merged time dtype."""
    start, end = _utc(2026, 6, 1), _utc(2026, 6, 2)
    fake([{"candles": [
        _candle("2026-05-30T00:00:00.000000000Z", 1, 2, 0.5, 1.5),
        _candle("2026-05-30T00:01:00.000000000Z", 1.5, 2.5, 1.0, 2.0),
    ]}])
    bars.ensure_frames(tmp_path, start, end)
    m1 = pd.read_parquet(tmp_path / "is_XAU_USD_1m.parquet")
    assert len(m1) == 2
    assert str(pd.to_datetime(m1["time"], utc=True).dt.tz) == "UTC"
    assert (tmp_path / "is_XAU_USD_1d.parquet").exists()


def test_ensure_s5_cold_start_dtype(tmp_path, fake):
    span0, span1 = _utc(2026, 6, 1, 12, 0), _utc(2026, 6, 1, 12, 1)
    fake([{"candles": [
        _candle("2026-06-01T12:00:%02d.000000000Z" % s, 1, 2, 0.5, 1.5)
        for s in range(0, 60, 5)
    ]}])
    out = bars.ensure_s5(tmp_path, span0, span1)
    assert len(out) == 12
    assert str(out["time"].dt.tz) == "UTC"
