import sys
from pathlib import Path

# Make the Telegram_Bot modules (db_persist, etc.) importable without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
