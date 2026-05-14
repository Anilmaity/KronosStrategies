import json
import logging
import os
import tempfile
from datetime import date

from ._exceptions import CacheReadError

log = logging.getLogger(__name__)


def cache_path(cache_dir: str, instrument: str, d: date) -> str:
    filename = f"{d.day}_{d.month}_{d.year}.json"
    return os.path.join(cache_dir, instrument, filename)


def load(path: str) -> list | None:
    """Return parsed cache data, None if file absent, raises CacheReadError if corrupt."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise CacheReadError(f"Corrupt cache file {path}: {exc}") from exc
    except OSError as exc:
        raise CacheReadError(f"Cannot read cache file {path}: {exc}") from exc


def save(path: str, data: list) -> None:
    """Atomically write data to path. Partial writes never leave a corrupt file."""
    dir_ = os.path.dirname(path)
    os.makedirs(dir_, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)   # atomic on POSIX; near-atomic on Windows (same volume)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
