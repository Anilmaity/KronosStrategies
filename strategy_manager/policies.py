"""
policies.py
-----------
Gating policies for the Kronos Strategy Manager (design spec 2026-07-02, §4).

Each policy is a pure function:

    policy(snap, params, now_utc) -> (desired_active: bool, reason: str)

`snap` is a regime snapshot (the RegimeSnapshot dataclass from
regime.regime_engine, or anything duck-typed with the same attributes:
d1_bias, h4_bias, vol_regime, trend_regime, session, market_closed).
`params` is the ManagedStrategy.policy_params JSON dict (may be empty —
every policy has sane spec defaults). `now_utc` is a tz-aware UTC datetime.

Global guards (market closed, kill-switch, max-concurrent) are applied by
the manager BEFORE these policies run — a policy only answers "does the
current regime suit this strategy?".

Registry: POLICIES maps ManagedStrategy.policy_key -> function.
"""

from __future__ import annotations

from datetime import datetime


# ──────────────────────────────────────────────────────────────────────────────
# Spec defaults (§4 table) — overridable per strategy via policy_params
# ──────────────────────────────────────────────────────────────────────────────

# session_vol: 06:45–10:00 & 13:15–16:00 UTC, as fractional-hour windows
SESSION_VOL_WINDOWS = [[6.75, 10.0], [13.25, 16.0]]
SESSION_VOL_REGIMES = ["NORMAL", "HIGH"]

# quiet_fade: 03:00–09:00 UTC
QUIET_FADE_WINDOW = [3.0, 9.0]
QUIET_FADE_VOL_REGIMES = ["LOW", "NORMAL"]

# quiet_mr (S98): 03:00-09:00 UTC, calm vol, NON-trending tape, bias-agnostic
QUIET_MR_WINDOW = [3.0, 9.0]
QUIET_MR_VOL_REGIMES = ["LOW", "NORMAL"]


def _hour_float(now_utc: datetime) -> float:
    """UTC wall-clock as a fractional hour (06:45 -> 6.75)."""
    return now_utc.hour + now_utc.minute / 60.0 + now_utc.second / 3600.0


def _in_windows(hf: float, windows) -> bool:
    return any(float(a) <= hf < float(b) for a, b in windows)


# ──────────────────────────────────────────────────────────────────────────────
# Policies
# ──────────────────────────────────────────────────────────────────────────────

def policy_always_on(snap, params: dict, now_utc: datetime) -> tuple[bool, str]:
    """Trend slot: always run; only the global guards ever pause it."""
    return True, "always_on: regime-independent"


def policy_session_vol(snap, params: dict, now_utc: datetime) -> tuple[bool, str]:
    """Session-breakout slot: London/NY opening windows + adequate volatility."""
    windows = params.get("windows", SESSION_VOL_WINDOWS)
    vol_ok_in = params.get("vol_regimes", SESSION_VOL_REGIMES)
    hf = _hour_float(now_utc)
    in_win = _in_windows(hf, windows)
    vol_ok = snap.vol_regime in vol_ok_in
    if in_win and vol_ok:
        return True, f"session_vol: in window ({hf:.2f}h UTC), vol={snap.vol_regime}"
    if not in_win:
        return False, f"session_vol: outside windows ({hf:.2f}h UTC)"
    return False, f"session_vol: vol={snap.vol_regime} not in {vol_ok_in}"


def policy_trending(snap, params: dict, now_utc: datetime) -> tuple[bool, str]:
    """Momentum slot: only when the tape is trending with an H4 direction."""
    if snap.trend_regime == "TRENDING" and snap.h4_bias != "neutral":
        return True, f"trending: trend={snap.trend_regime}, h4_bias={snap.h4_bias}"
    return False, (
        f"trending: trend={snap.trend_regime}, h4_bias={snap.h4_bias} "
        "(need TRENDING + directional h4)"
    )


def policy_quiet_fade(snap, params: dict, now_utc: datetime) -> tuple[bool, str]:
    """Scalper slot: quiet Asia/early-London tape with a directional D1 bias."""
    window = params.get("window", QUIET_FADE_WINDOW)
    vol_ok_in = params.get("vol_regimes", QUIET_FADE_VOL_REGIMES)
    hf = _hour_float(now_utc)
    in_win = float(window[0]) <= hf < float(window[1])
    vol_ok = snap.vol_regime in vol_ok_in
    # d1_bias is a structure label (bullish|bearish|ranging); 'neutral' is
    # guarded too in case an htf-bias value is ever routed here.
    directional = snap.d1_bias not in ("ranging", "neutral", None, "")
    if in_win and vol_ok and directional:
        return True, (
            f"quiet_fade: {hf:.2f}h UTC, vol={snap.vol_regime}, d1={snap.d1_bias}"
        )
    why = []
    if not in_win:
        why.append(f"{hf:.2f}h outside {window}")
    if not vol_ok:
        why.append(f"vol={snap.vol_regime} not in {vol_ok_in}")
    if not directional:
        why.append(f"d1={snap.d1_bias} non-directional")
    return False, "quiet_fade: " + "; ".join(why)


def policy_quiet_mr(snap, params: dict, now_utc: datetime) -> tuple[bool, str]:
    """Scalper slot (S98 z-score MR): quiet, non-trending Asia/early-London
    tape. Bias-agnostic -- mean reversion trades both directions; the
    efficiency-ratio trend gate replaces quiet_fade's d1-bias gate."""
    window = params.get("window", QUIET_MR_WINDOW)
    vol_ok_in = params.get("vol_regimes", QUIET_MR_VOL_REGIMES)
    hf = _hour_float(now_utc)
    in_win = float(window[0]) <= hf < float(window[1])
    vol_ok = snap.vol_regime in vol_ok_in
    not_trending = snap.trend_regime != "TRENDING"
    if in_win and vol_ok and not_trending:
        return True, (f"quiet_mr: {hf:.2f}h UTC, vol={snap.vol_regime}, "
                      f"trend={snap.trend_regime}")
    why = []
    if not in_win:
        why.append(f"{hf:.2f}h outside {window}")
    if not vol_ok:
        why.append(f"vol={snap.vol_regime} not in {vol_ok_in}")
    if not not_trending:
        why.append(f"trend={snap.trend_regime} is TRENDING")
    return False, "quiet_mr: " + "; ".join(why)


POLICIES = {
    "always_on": policy_always_on,
    "session_vol": policy_session_vol,
    "trending": policy_trending,
    "quiet_fade": policy_quiet_fade,
    "quiet_mr": policy_quiet_mr,
}
