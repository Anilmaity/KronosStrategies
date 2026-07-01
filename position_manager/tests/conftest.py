"""Put the position_manager package dir on sys.path so its `shared.*` imports
resolve when pytest is run from the repo root or anywhere else."""
import sys
from pathlib import Path

_PM_DIR = Path(__file__).resolve().parent.parent
if str(_PM_DIR) not in sys.path:
    sys.path.insert(0, str(_PM_DIR))
