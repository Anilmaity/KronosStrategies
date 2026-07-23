# tests/test_compose_window_coverage.py
"""Compose env regression guard: candle-fetch depth must cover strategy windows.

Root cause (2026-07-23 live-vs-sim audit): research_runner windows are
tail(WIN_N) of a days-limited OANDA fetch. Across the weekend close
(Fri 21:00 -> Sun 22:00 UTC) a too-small RESEARCH_DAYS_5M leaves the window
short of the strategy's validated lookback, and get_signal returns None
silently — S95 generated 8 live signals vs 24 in the same-window sim, and
S93/S99 (DAYS_5M=2 < their 120-bar M5 minimum) went dark on Monday mornings.

The guard computes worst-case bars available at Monday 06:00 UTC (first
London killzone after the weekend) and requires it to cover WIN_5M / WIN_15M
with margin for holiday closures.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import yaml

COMPOSE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "compose.yml")
)

# Worst-case evaluation moment: Monday 06:00 UTC (2026-07-20 is a Monday).
_EVAL = datetime(2026, 7, 20, 6, 0, tzinfo=timezone.utc)

# Safety margin for holiday closures / OANDA gaps (fraction of required bars).
_MARGIN = 1.10


def _market_open(t: datetime) -> bool:
    """XAUUSD CME-style hours: closed Fri 21:00 -> Sun 22:00 UTC and during
    the daily 21:00-22:00 UTC maintenance break."""
    if t.weekday() == 4 and t.hour >= 21:  # Friday night
        return False
    if t.weekday() == 5:                   # Saturday
        return False
    if t.weekday() == 6 and t.hour < 22:   # Sunday until reopen
        return False
    if t.hour == 21:                       # daily break
        return False
    return True


def _bars_available(days: int, bars_per_hour: int) -> int:
    """Open-market bars in [eval - days, eval], counted hour by hour."""
    start = _EVAL - timedelta(days=days)
    hours = int((_EVAL - start).total_seconds() // 3600)
    return sum(
        bars_per_hour
        for i in range(hours)
        if _market_open(start + timedelta(hours=i))
    )


def _research_services() -> dict[str, dict]:
    with open(COMPOSE, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    out = {}
    for name, svc in (doc.get("services") or {}).items():
        env = svc.get("environment") or {}
        if isinstance(env, list):  # KEY=VAL list form
            env = dict(e.split("=", 1) for e in env)
        if "RESEARCH_STRATEGY" in env:
            out[name] = env
    return out


def test_compose_has_research_services():
    assert _research_services(), "no RESEARCH_STRATEGY services found in compose.yml"


def test_days_5m_covers_win_5m_across_weekend():
    failures = []
    for name, env in _research_services().items():
        win = int(env.get("RESEARCH_WIN_5M", 80))
        days = int(env.get("RESEARCH_DAYS_5M", 2))
        have = _bars_available(days, bars_per_hour=12)
        need = int(win * _MARGIN)
        if have < need:
            failures.append(
                f"{name}: DAYS_5M={days} gives {have} M5 bars worst-case "
                f"but WIN_5M={win} needs >={need} (incl. {_MARGIN:.0%} margin)"
            )
    assert not failures, "\n".join(failures)


def test_days_15m_covers_win_15m_across_weekend():
    failures = []
    for name, env in _research_services().items():
        win = int(env.get("RESEARCH_WIN_15M", 100))
        days = int(env.get("RESEARCH_DAYS_15M", 3))
        have = _bars_available(days, bars_per_hour=4)
        need = int(win * _MARGIN)
        if have < need:
            failures.append(
                f"{name}: DAYS_15M={days} gives {have} M15 bars worst-case "
                f"but WIN_15M={win} needs >={need} (incl. {_MARGIN:.0%} margin)"
            )
    assert not failures, "\n".join(failures)
