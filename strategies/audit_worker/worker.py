"""Manager Backtest worker: claim PENDING ManagerBacktestRun rows FIFO, run
the audit sim, write result JSON back. One job at a time; CPU-capped by
compose (cpus 0.5 / mem 700m).

Never imports shared.metaapi_client (directly or transitively) — this worker
structurally cannot place orders; tests/test_mbt_worker.py enforces it.
"""
from __future__ import annotations

import functools
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import text

import shared.models as models
from shared.models import ManagerBacktestRun

log = logging.getLogger("audit_worker")

CACHE_DIR = Path(os.getenv("AUDIT_CACHE_DIR", "/app/audit_cache"))
HEARTBEAT_FILE = os.getenv("HEARTBEAT_FILE", "/tmp/hb")
POLL_SEC = int(os.getenv("AUDIT_POLL_SEC", "10"))
PROGRESS_MIN_INTERVAL_SEC = 5.0
ERROR_MAX_LEN = 4000

# phase -> (progress floor, progress ceiling)
PHASES = {
    "fetching_bars": (0.0, 10.0),
    "replaying": (10.0, 80.0),
    "resolving_s5": (80.0, 90.0),
    "comparing": (90.0, 100.0),
}


class RunCancelled(Exception):
    """Raised inside a progress write when the row was flipped to CANCELLED."""


def _utcnow():
    return datetime.now(timezone.utc)


def _touch_heartbeat():
    try:
        Path(HEARTBEAT_FILE).write_text(str(int(time.time())))
    except OSError:
        pass


def fail_stale_running(session) -> int:
    """On startup: any RUNNING row is an orphan from a dead worker."""
    n = (session.query(ManagerBacktestRun)
         .filter(ManagerBacktestRun.status == "RUNNING")
         .update({"status": "FAILED", "error": "worker restarted",
                  "finished_at": _utcnow()}, synchronize_session=False))
    session.commit()
    return n


def claim_next(session):
    """Atomically claim the oldest PENDING row; None when queue is empty."""
    row = session.execute(text(
        "UPDATE apis_managerbacktestrun "
        "SET status='RUNNING', started_at=:now "
        "WHERE id = (SELECT id FROM apis_managerbacktestrun "
        "            WHERE status='PENDING' ORDER BY created_at LIMIT 1) "
        "  AND status='PENDING' "
        "RETURNING id"
    ), {"now": _utcnow()}).fetchone()
    session.commit()
    if row is None:
        return None
    rid = row[0]
    if isinstance(rid, str):        # SQLite RETURNING yields the hex string
        rid = uuid.UUID(rid)
    return session.get(ManagerBacktestRun, rid)


class ProgressWriter:
    """Throttled progress/phase writes; every write re-checks for CANCELLED."""

    def __init__(self, session, run_id):
        self.session = session
        self.run_id = run_id
        self._last_write = 0.0

    def _write(self, phase: str, pct: float, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_write < PROGRESS_MIN_INTERVAL_SEC:
            return
        self._last_write = now
        run = self.session.get(ManagerBacktestRun, self.run_id)
        self.session.refresh(run)
        if run.status == "CANCELLED":
            self.session.commit()
            raise RunCancelled()
        run.phase = phase
        run.progress_pct = round(min(100.0, max(0.0, pct)), 1)
        self.session.commit()

    def phase(self, phase: str):
        self._write(phase, PHASES[phase][0], force=True)

    def within(self, phase: str, frac: float, lo_frac: float = 0.0,
               hi_frac: float = 1.0):
        """Map frac in [0,1] (scaled into [lo_frac, hi_frac]) onto the
        phase's progress band."""
        lo, hi = PHASES[phase]
        scaled = lo_frac + (hi_frac - lo_frac) * frac
        self._write(phase, lo + (hi - lo) * scaled)


def process_run(session, run, cache_dir: Path = CACHE_DIR):
    """Execute one claimed run to a terminal state. Never raises for job
    errors — FAILED/CANCELLED are written to the row."""
    from audit_worker import bars, live_deltas, results, roster, s5_resolve
    from backtest.manager_sim_engine import SimConfig, load_frames, run_sim

    progress = ProgressWriter(session, run.id)
    try:
        p = run.params or {}
        start_utc = pd.Timestamp(run.period_start).tz_localize("UTC")
        end_utc = pd.Timestamp(run.period_end).tz_localize("UTC")
        include_ungated = bool(p.get("include_ungated"))

        progress.phase("fetching_bars")
        bars.ensure_frames(cache_dir, start_utc, end_utc)
        frames = load_frames(cache_dir, start_utc, end_utc)

        cfg = SimConfig(
            start=start_utc.to_pydatetime(), end=end_utc.to_pydatetime(),
            spread_pts=float(p.get("spread_pts", 0.30)),
            slippage_pts=float(p.get("slippage_pts", 0.10)),
            lots=float(p.get("lots", 0.02)),
            kill_switch_usd=float(p.get("kill_switch_usd", 150.0)),
            max_concurrent=int(p.get("max_concurrent", 3)),
            regime_cadence_min=int(p.get("regime_cadence_min", 5)),
            gated=True,
        )
        specs, notes = roster.build_specs(p.get("roster_snapshot") or [])

        progress.phase("replaying")
        hi = 0.5 if include_ungated else 1.0
        gated = run_sim(
            frames, cfg, specs=specs,
            progress_cb=lambda f: progress.within("replaying", f, 0.0, hi))
        ungated = None
        if include_ungated:
            from dataclasses import replace as dc_replace
            ungated = run_sim(
                frames, dc_replace(cfg, gated=False), specs=specs,
                progress_cb=lambda f: progress.within("replaying", f, 0.5, 1.0))

        progress.phase("resolving_s5")
        provider = functools.partial(bars.ensure_s5, cache_dir)
        resolved, s5_report = s5_resolve.resolve_ambiguous(
            gated.trades, frames["1m"], provider, cfg)
        gated.trades = resolved

        progress.phase("comparing")
        sim_map = results.sim_per_strategy(gated.trades, cfg)
        live_map = live_deltas.live_summary(
            session, [s.name for s in specs],
            start_utc.to_pydatetime(), end_utc.to_pydatetime())
        per_strategy = live_deltas.deltas(sim_map, live_map)

        csv_path = str(Path(cache_dir) / f"trades_{run.id}.csv")
        results.trades_frame(gated.trades).to_csv(csv_path, index=False)

        run = session.get(ManagerBacktestRun, run.id)
        run.result = results.assemble(gated, ungated, cfg, s5_report,
                                      per_strategy, notes, csv_path)
        run.status = "DONE"
        run.progress_pct = 100.0
        run.phase = "comparing"
        run.finished_at = _utcnow()
        session.commit()
        log.info("run %s DONE: %d trades", run.id, len(gated.trades))

    except RunCancelled:
        session.rollback()
        run = session.get(ManagerBacktestRun, run.id)
        run.finished_at = _utcnow()
        session.commit()
        log.info("run %s cancelled", run.id)

    except Exception as exc:  # noqa: BLE001 - job errors land on the row
        session.rollback()
        run = session.get(ManagerBacktestRun, run.id)
        run.status = "FAILED"
        run.error = repr(exc).encode("ascii", "replace").decode()[:ERROR_MAX_LEN]
        run.finished_at = _utcnow()
        session.commit()
        log.exception("run %s FAILED", run.id)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log.info("audit_worker starting (cache=%s poll=%ss)", CACHE_DIR, POLL_SEC)
    with models.Session() as session:
        n = fail_stale_running(session)
        if n:
            log.warning("marked %d stale RUNNING run(s) FAILED", n)
    while True:
        _touch_heartbeat()
        with models.Session() as session:
            run = claim_next(session)
            if run is not None:
                log.info("claimed run %s [%s..%s]", run.id,
                         run.period_start, run.period_end)
                process_run(session, run)
                continue
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
