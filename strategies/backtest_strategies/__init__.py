"""Strategy library — auto-registers all sNN_*.py modules."""
from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

STRATEGIES: list[ModuleType] = []


def _load_all():
    """Import every sNN_*.py in this package and register modules with NAME/CONFIG/get_signal."""
    pkg = __name__
    pkg_path = __path__
    for _, mod_name, is_pkg in pkgutil.iter_modules(pkg_path):
        if is_pkg or not mod_name.startswith("s"):
            continue
        mod = importlib.import_module(f"{pkg}.{mod_name}")
        if hasattr(mod, "NAME") and hasattr(mod, "CONFIG") and hasattr(mod, "get_signal"):
            STRATEGIES.append(mod)
    STRATEGIES.sort(key=lambda m: m.__name__)


_load_all()
