"""Strategy library — auto-registers all sNN_*.py modules."""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

STRATEGIES: list[ModuleType] = []

# Modules kept in the package as parent helpers for live strategies but NOT
# themselves tradeable. s10 is imported by s11/s12 but its M90_FADE strategy
# was retired after the 2026-05 backtest review.
_NON_TRADEABLE_MODULES = {"s10_90min_fade"}


def _load_all():
    """Import every sNN_*.py in this package and register modules with NAME/CONFIG/get_signal."""
    pkg = __name__
    pkg_path = __path__
    for _, mod_name, is_pkg in pkgutil.iter_modules(pkg_path):
        if is_pkg or not mod_name.startswith("s"):
            continue
        mod = importlib.import_module(f"{pkg}.{mod_name}")
        if mod_name in _NON_TRADEABLE_MODULES:
            continue
        if hasattr(mod, "NAME") and hasattr(mod, "CONFIG") and hasattr(mod, "get_signal"):
            STRATEGIES.append(mod)
    STRATEGIES.sort(key=lambda m: m.__name__)


_load_all()
