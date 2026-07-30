"""
obs.py
------
Dependency-free, in-process metrics + Telegram-optional alerting for the live
XAUUSD engine (opt15 task12, report point #5).

Before this module the only execution-quality signal was the 300s
fill_reconciler; every runner, the exit monitor and the manager were otherwise
blind. This gives them a shared, zero-dependency way to accumulate counters and
timing observations and to raise an operator alert -- without pulling in statsd,
Prometheus, or any external metrics service.

Design:
  * A single per-PROCESS registry (each Docker service is its own process, so
    there is no cross-service contamination). Thread-safe under one lock so the
    multi-threaded runners and the 1s monitor can record concurrently.
  * ``count(name)``            -- increment an integer counter.
  * ``observe(name, value)``   -- record a value, keeping count/sum/min/max.
  * ``timer(name)``            -- context manager; observes the elapsed seconds.
  * ``flush_line()``           -- one ASCII ``METRICS {json}`` line (pure read).
  * ``flush_if_due(logger)``   -- logs the METRICS line at most once every
                                  OBS_FLUSH_SEC (default 300) and drains the
                                  window. Wired into the OANDA read chokepoint
                                  (tsdb_reader._oanda_get) so every polling
                                  service emits without touching runner files.
  * ``alert(msg, level)``      -- always logs ``ALERT {level} {msg}``; when
                                  ALERT_TELEGRAM_BOT_TOKEN + ALERT_TELEGRAM_CHAT_ID
                                  are set, also POSTs to the Telegram sendMessage
                                  API (2s timeout, ALL errors swallowed --
                                  alerting must never break trading).

Default state (no env set): behaviour is identical to before except the periodic
METRICS log line. ASCII-only output (Windows cp1252 safe).

NOTE: this file is duplicated byte-for-byte into position_manager/shared/ and
strategy_manager/shared/ (separate Docker build contexts). tests/test_tree_sync.py
enforces the copies stay identical -- edit every copy together.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager

try:  # optional; only used by alert()'s Telegram POST. Never a hard dependency.
    import requests
except Exception:  # pragma: no cover - requests is present in every service image
    requests = None  # type: ignore

log = logging.getLogger(__name__)

# How often flush_if_due emits a METRICS line and drains the window.
OBS_FLUSH_SEC = float(os.getenv("OBS_FLUSH_SEC", "300"))

# ── Per-process registry ──────────────────────────────────────────────────────
_lock = threading.Lock()
_counters: dict = {}        # name -> int
_observations: dict = {}    # name -> {"n": int, "sum": float, "min": float, "max": float}
_last_flush_ts: float = time.time()


# ── Recording ─────────────────────────────────────────────────────────────────
def count(name: str, n: int = 1) -> None:
    """Increment the integer counter `name` by `n` (default 1)."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + int(n)


def observe(name: str, value: float) -> None:
    """Record `value` under `name`, keeping count/sum/min/max."""
    v = float(value)
    with _lock:
        rec = _observations.get(name)
        if rec is None:
            _observations[name] = {"n": 1, "sum": v, "min": v, "max": v}
        else:
            rec["n"] += 1
            rec["sum"] += v
            if v < rec["min"]:
                rec["min"] = v
            if v > rec["max"]:
                rec["max"] = v


@contextmanager
def timer(name: str):
    """Context manager: observe the wall-clock seconds spent in the block under
    `name` (a monotonic perf_counter delta, so it is unaffected by clock steps)."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        observe(name, time.perf_counter() - t0)


# ── Snapshot / flush ──────────────────────────────────────────────────────────
def _obs_summary(rec: dict) -> dict:
    n = rec["n"]
    return {
        "n": n,
        "sum": round(rec["sum"], 6),
        "min": round(rec["min"], 6),
        "max": round(rec["max"], 6),
        "avg": round(rec["sum"] / n, 6) if n else 0.0,
    }


def _snapshot_locked() -> dict:
    return {
        "counters": dict(_counters),
        "observations": {k: _obs_summary(v) for k, v in _observations.items()},
    }


def snapshot() -> dict:
    """Current metrics as a plain dict: {"counters": {...}, "observations": {...}}."""
    with _lock:
        return _snapshot_locked()


def _flush_line_locked() -> str:
    payload = _snapshot_locked()
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"))
    return "METRICS " + body


def flush_line() -> str:
    """Return one ASCII ``METRICS {json}`` line for the current window. Pure read
    -- does NOT drain the window (use flush_if_due for the periodic emit)."""
    with _lock:
        return _flush_line_locked()


def flush_if_due(logger: "logging.Logger | None" = None,
                 now: "float | None" = None) -> bool:
    """If at least OBS_FLUSH_SEC has elapsed since the last flush, log the METRICS
    line and DRAIN the window (so each periodic line is a fresh window). Returns
    True when it flushed. Safe to call on a hot path -- a not-due call is just a
    lock + time comparison."""
    global _last_flush_ts
    ts = time.time() if now is None else float(now)
    line = None
    with _lock:
        if ts - _last_flush_ts < OBS_FLUSH_SEC:
            return False
        line = _flush_line_locked()
        _counters.clear()
        _observations.clear()
        _last_flush_ts = ts
    (logger or log).info(line)
    return True


def reset() -> None:
    """Clear all metrics and reset the flush window. Used by tests and any
    caller that wants a clean window."""
    global _last_flush_ts
    with _lock:
        _counters.clear()
        _observations.clear()
        _last_flush_ts = time.time()


# ── Alerting ──────────────────────────────────────────────────────────────────
def alert(msg: str, level: str = "WARN") -> None:
    """Always log ``ALERT {level} {msg}``; additionally POST to Telegram when
    ALERT_TELEGRAM_BOT_TOKEN + ALERT_TELEGRAM_CHAT_ID are both set. The POST uses
    a 2s timeout and swallows EVERY error -- alerting must never break trading."""
    text = "ALERT %s %s" % (level, msg)
    try:
        log.warning(text)
    except Exception:  # pragma: no cover - logging must never break the caller
        pass

    token = os.getenv("ALERT_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("ALERT_TELEGRAM_CHAT_ID", "").strip()
    if not (token and chat) or requests is None:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % token,
            json={"chat_id": chat, "text": text},
            timeout=2,
        )
    except Exception:
        # Swallow ALL errors (network, timeout, malformed response, ...).
        pass
