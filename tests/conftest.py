"""conftest.py — put repo root on sys.path so pytest finds all packages."""
import sys
import os

# Ensure repo root is on sys.path so `import strategies.*` works from repo root.
REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
