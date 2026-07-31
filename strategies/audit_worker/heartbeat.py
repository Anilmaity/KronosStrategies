"""Container-healthcheck heartbeat, shared by the worker loop and the
long-running phases (bars fetch, S5 resolution) so multi-minute work never
lets /tmp/hb go stale."""
from __future__ import annotations

import os
import time
from pathlib import Path

HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/hb")


def touch():
    try:
        Path(HEARTBEAT_FILE).write_text(str(int(time.time())))
    except OSError:
        pass
