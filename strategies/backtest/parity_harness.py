"""parity_harness.py — per-trade comparison of sim output against live trades.

Phase 3 of docs/superpowers/specs/2026-08-12-5s-backtest-fidelity-design.md.

Aggregate statistics can pass while individual trades are wrong in offsetting
ways, which is exactly how a marginal book hides. So the deliverable here is a
per-trade diff that names WHICH mechanism diverged:

    matched   - live trade reproduced by the sim (with entry/exit/USD deltas)
    live_only - the sim MISSED a trade that really happened
    sim_only  - the sim INVENTED a trade that never happened

Everything in this module is pure: matching, deltas, attribution and the
tolerance verdict all run without a database, the box, or any market data. The
ground-truth loader lives separately so this stays testable offline.

The tolerance thresholds are PRE-REGISTERED in spec section 4.5 — fixed before
any result was seen, so the harness cannot be tuned until it flatters the sim.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

log = logging.getLogger(__name__)

# Sub-5-cent differences are quote noise, not a modelling error.
PX_EPS = 0.05


@dataclass(frozen=True)
class LiveTrade:
    """A real trade, assembled from Position + ENTRY Order + broker_deals."""
    strategy: str
    side: str
    entry_time: datetime
    entry_px: float
    exit_time: datetime
    exit_px: float
    outcome: str
    usd: float
    ticket: str = ""


@dataclass(frozen=True)
class SimTrade:
    """A simulated trade (projection of manager_sim_engine.TradeRecord)."""
    strategy: str
    side: str
    entry_time: datetime
    entry_px: float
    exit_time: datetime
    exit_px: float
    outcome: str
    usd: float


@dataclass(frozen=True)
class Pair:
    """One matched live/sim trade. Deltas are signed SIM MINUS LIVE."""
    live: LiveTrade
    sim: SimTrade

    @property
    def entry_delta(self) -> float:
        return self.sim.entry_px - self.live.entry_px

    @property
    def exit_delta(self) -> float:
        return self.sim.exit_px - self.live.exit_px

    @property
    def usd_delta(self) -> float:
        return self.sim.usd - self.live.usd

    @property
    def outcome_agrees(self) -> bool:
        return self.sim.outcome == self.live.outcome


@dataclass
class MatchResult:
    matched: list[Pair] = field(default_factory=list)
    live_only: list[LiveTrade] = field(default_factory=list)
    sim_only: list[SimTrade] = field(default_factory=list)


@dataclass(frozen=True)
class Tolerance:
    """Pre-registered acceptance thresholds (spec section 4.5)."""
    min_match_rate: float = 0.90
    max_entry_median: float = 0.15
    max_entry_p90: float = 0.40
    max_exit_median: float = 0.30
    max_exit_p90: float = 0.80
    min_outcome_agreement: float = 0.90
    max_usd_rel: float = 0.10


@dataclass
class Verdict:
    passed: bool
    failures: list[str]
    stats: dict


def match_trades(live: list[LiveTrade], sim: list[SimTrade],
                 window_s: float = 90.0) -> MatchResult:
    """Pair live and sim trades on (strategy, entry_time within window_s).

    Globally nearest-first greedy: every candidate pair is ranked by absolute
    time difference and consumed in that order, so a trade always claims its
    closest counterpart rather than whichever came first in input order. Each
    trade is used at most once; ties break deterministically on index so a rerun
    produces a byte-identical report.
    """
    candidates: list[tuple[float, int, int]] = []
    for i, lt in enumerate(live):
        for j, st in enumerate(sim):
            if lt.strategy != st.strategy:
                continue
            dt = abs((st.entry_time - lt.entry_time).total_seconds())
            if dt <= window_s:
                candidates.append((dt, i, j))
    candidates.sort()

    used_live: set[int] = set()
    used_sim: set[int] = set()
    result = MatchResult()
    for _dt, i, j in candidates:
        if i in used_live or j in used_sim:
            continue
        used_live.add(i)
        used_sim.add(j)
        result.matched.append(Pair(live=live[i], sim=sim[j]))

    result.live_only = [t for i, t in enumerate(live) if i not in used_live]
    result.sim_only = [t for j, t in enumerate(sim) if j not in used_sim]
    return result


def attribute(pair: Pair) -> str | None:
    """Name the dominant divergence mechanism for one matched pair.

    Ordered by severity: a flipped outcome dwarfs a price offset, and an entry
    offset propagates into the exit, so it is reported ahead of exit_fill.
    Returns None when the pair reproduces live within quote noise.
    """
    if not pair.outcome_agrees:
        return "intrabar_order"
    if abs(pair.entry_delta) > PX_EPS:
        return "entry_fill"
    if abs(pair.exit_delta) > PX_EPS:
        return "exit_fill"
    return None


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else 0.0


def summarise(res: MatchResult) -> dict:
    """Measured numbers behind the verdict — reported pass or fail."""
    n_matched = len(res.matched)
    n_live = n_matched + len(res.live_only)

    entry_abs = [abs(p.entry_delta) for p in res.matched]
    exit_abs = [abs(p.exit_delta) for p in res.matched]

    usd_live = sum(p.live.usd for p in res.matched)
    usd_sim = sum(p.sim.usd for p in res.matched)
    usd_rel = abs(usd_sim - usd_live) / abs(usd_live) if usd_live else 0.0

    agree = sum(1 for p in res.matched if p.outcome_agrees)

    mechanisms: dict[str, int] = {}
    for p in res.matched:
        label = attribute(p)
        if label:
            mechanisms[label] = mechanisms.get(label, 0) + 1

    return {
        "matched":           n_matched,
        "live_only":         len(res.live_only),
        "sim_only":          len(res.sim_only),
        "match_rate":        (n_matched / n_live) if n_live else 0.0,
        "entry_median":      float(np.median(entry_abs)) if entry_abs else 0.0,
        "entry_p90":         _pct(entry_abs, 90),
        "exit_median":       float(np.median(exit_abs)) if exit_abs else 0.0,
        "exit_p90":          _pct(exit_abs, 90),
        "outcome_agreement": (agree / n_matched) if n_matched else 0.0,
        "usd_live":          usd_live,
        "usd_sim":           usd_sim,
        "usd_rel":           usd_rel,
        "mechanisms":        mechanisms,
    }


def evaluate_tolerance(res: MatchResult,
                       tol: Tolerance = Tolerance()) -> Verdict:
    """Check the measured parity against the pre-registered thresholds."""
    s = summarise(res)
    failures: list[str] = []

    if s["match_rate"] < tol.min_match_rate:
        failures.append(
            f"match rate {s['match_rate']:.1%} < {tol.min_match_rate:.0%} "
            f"({s['live_only']} live trade(s) the sim never produced)")

    if s["entry_median"] > tol.max_entry_median:
        failures.append(f"median entry delta {s['entry_median']:.3f}pt > "
                        f"{tol.max_entry_median}pt")
    if s["entry_p90"] > tol.max_entry_p90:
        failures.append(f"p90 entry delta {s['entry_p90']:.3f}pt > "
                        f"{tol.max_entry_p90}pt")

    if s["exit_median"] > tol.max_exit_median:
        failures.append(f"median exit delta {s['exit_median']:.3f}pt > "
                        f"{tol.max_exit_median}pt")
    if s["exit_p90"] > tol.max_exit_p90:
        failures.append(f"p90 exit delta {s['exit_p90']:.3f}pt > "
                        f"{tol.max_exit_p90}pt")

    if s["outcome_agreement"] < tol.min_outcome_agreement:
        failures.append(
            f"outcome agreement {s['outcome_agreement']:.1%} < "
            f"{tol.min_outcome_agreement:.0%}")

    if s["usd_rel"] > tol.max_usd_rel:
        failures.append(
            f"aggregate USD off by {s['usd_rel']:.1%} "
            f"(sim {s['usd_sim']:+.2f} vs live {s['usd_live']:+.2f}) > "
            f"{tol.max_usd_rel:.0%}")

    return Verdict(passed=not failures, failures=failures, stats=s)
