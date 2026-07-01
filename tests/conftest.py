"""conftest.py — put repo root on sys.path so pytest finds all packages."""
import sys
import os

# Ensure repo root is on sys.path so `import strategies.*` works from repo root.
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Also add strategies/ so that intra-package imports like
# `from backtest_strategies.base import ...` work when modules are imported
# via `strategies.backtest_strategies.*` from the repo root (mirrors the
# Docker /app working-dir layout where strategies/ is the CWD).
STRATEGIES_DIR = os.path.join(REPO_ROOT, "strategies")
if STRATEGIES_DIR not in sys.path:
    sys.path.insert(0, STRATEGIES_DIR)
