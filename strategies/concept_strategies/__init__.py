"""
concept_strategies — 20 day-trading strategies built from
ConceptStrategies/20-day-trading-strategies-v2.md. All adapted to XAUUSD
(single-instrument cache); concepts that originally required other
instruments (ES/NQ/BANKNIFTY/EURUSD/DXY) have their *mechanic* ported.

Each module exports NAME, CONFIG, get_signal(w1m, w5m, w15m, now_utc) -> Signal | None
and is auto-discovered by file name prefix 'c'.
"""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

STRATEGIES: list[ModuleType] = []


def _load_all():
    pkg = __name__
    pkg_path = __path__
    for _, mod_name, is_pkg in pkgutil.iter_modules(pkg_path):
        if is_pkg or not mod_name.startswith("c"):
            continue
        try:
            mod = importlib.import_module(f"{pkg}.{mod_name}")
        except Exception as e:
            print(f"[concept_strategies] failed to import {mod_name}: {e}")
            continue
        if hasattr(mod, "NAME") and hasattr(mod, "CONFIG") and hasattr(mod, "get_signal"):
            STRATEGIES.append(mod)
    STRATEGIES.sort(key=lambda m: m.__name__)


_load_all()
