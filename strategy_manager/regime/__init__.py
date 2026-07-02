"""Regime Engine — pure market-regime classification for the Strategy Manager.

See docs/superpowers/specs/2026-07-02-strategy-manager-design.md §3.
"""

from regime.regime_engine import (  # noqa: F401
    RegimeSnapshot,
    compute_regime,
    fetch_regime_frames,
)
