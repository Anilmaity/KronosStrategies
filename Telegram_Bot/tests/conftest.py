"""Put the Telegram_Bot package dir on sys.path so its flat-module imports
(`import metaapi_orders`, `import live_trader`, …) resolve when pytest is run
from the repo root or anywhere else."""
import sys
from pathlib import Path

_BOT_DIR = Path(__file__).resolve().parent.parent
if str(_BOT_DIR) not in sys.path:
    sys.path.insert(0, str(_BOT_DIR))
