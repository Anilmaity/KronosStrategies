# tests/test_tree_sync.py
"""opt15 Task 6 - duplicated-tree sync guard + connection-pool right-sizing.

Two deliverables live here.

1. SYNC GUARD (Global Constraint #7): strategies/, position_manager/ and
   strategy_manager/ each carry their own copy of the shared layer because they
   are separate Docker build contexts (WORKDIR /app, imports are ``shared.*``).
   When a file that exists in more than one tree is edited, the identical edit
   MUST land in every copy. This test discovers those pairs dynamically and
   asserts byte-equality, with an explicit, reason-carrying allowlist for the
   handful of files that legitimately diverge for structural reasons. The
   companion allowlist-freshness test makes the allowlist impossible to rot: if
   an allowlisted pair is ever reconciled, its stale entry must be removed.

2. CONNECTION-POOL RIGHT-SIZING (report points #3, #8): ~8 single-threaded
   services each built a 60+10 pool against ONE managed Postgres (~560 possible
   connections). The engine is now env-tunable and defaults to 5/5 with
   pre-ping + a 1800s recycle. We prove (a) the strategies copy actually reads
   the new defaults / env overrides through create_engine, and (b) every
   models.py copy carries the same pool config line.

All offline: no DB, no network. The engine test monkeypatches create_engine so
no pool is ever constructed.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))).resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Canonical source tree; the other two mirror it.
CANONICAL = "strategies"
MIRROR_TREES = ("position_manager", "strategy_manager")
# Sub-trees that are duplicated across build contexts (per the brief).
SYNCED_SUBDIRS = ("shared", "regime", "strategy")

MODELS_COPIES = (
    "strategies/shared/models.py",
    "position_manager/shared/models.py",
    "strategy_manager/shared/models.py",
)


# ---------------------------------------------------------------------------
# Allowed divergences
# ---------------------------------------------------------------------------
# Key: (mirror_tree, relative_path_below_the_tree). Value: why it diverges from
# the strategies/ copy. Every entry is verified to be *actually* divergent by
# test_allowlisted_pairs_are_actually_divergent, so a future real reconciliation
# forces removal of the stale entry (the allowlist cannot silently rot).
ALLOWED_DIVERGENCES = {
    ("position_manager", "shared/models.py"): (
        "position_manager runs only the 1s exit loop and deliberately carries a "
        "trimmed ORM mirror: no StrategySignal, no RegimeSnapshot, no "
        "Position.archived column. Making it byte-identical would register unused "
        "mappers inside a live trading service for zero benefit. Pre-existing "
        "structural divergence; the engine-pool config line itself is kept in sync "
        "by hand (guarded by test_every_models_copy_has_pool_config)."
    ),
    ("position_manager", "shared/metaapi_client.py"): (
        "Structural, pre-existing: strategies/ exposes place_market_order as a "
        "MetaApiClient method (entry_manager calls _client.place_market_order), "
        "while position_manager/ exposes a module-level place_market_order fn plus "
        "a trimmed class (position_monitor imports only close_position_by_id + "
        "client_for_broker). Reconciling to byte-identical would change one side's "
        "public call contract. The Task-2 retry helpers are already identical in "
        "both copies; only the public order-placement shape differs."
    ),
}


# ---------------------------------------------------------------------------
# Pair discovery
# ---------------------------------------------------------------------------
def _discover_pairs():
    """Yield (rel, mirror_tree, canonical_path, mirror_path) for every file
    under strategies/{shared,regime,strategy} that also exists in a mirror tree.

    ``rel`` is the path below the tree root (e.g. ``shared/models.py``) with
    forward slashes so it is a stable, OS-independent allowlist key.
    """
    pairs = []
    for subdir in SYNCED_SUBDIRS:
        base = REPO_ROOT / CANONICAL / subdir
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO_ROOT / CANONICAL).as_posix()
            for tree in MIRROR_TREES:
                mirror = REPO_ROOT / tree / rel
                if mirror.is_file():
                    pairs.append((rel, tree, path, mirror))
    return pairs


def test_discovery_is_not_vacuous():
    """Guard: the dynamic discovery must actually find the known duplicated
    files, otherwise the sync assertion would pass vacuously."""
    pairs = _discover_pairs()
    rels = {(tree, rel) for rel, tree, _a, _b in pairs}
    # tsdb_reader / models are copied into both mirrors; regime_engine into
    # strategy_manager. If discovery finds none of these it is broken.
    assert ("position_manager", "shared/tsdb_reader.py") in rels
    assert ("strategy_manager", "shared/models.py") in rels
    assert ("strategy_manager", "regime/regime_engine.py") in rels
    assert len(pairs) >= 8, f"only discovered {len(pairs)} duplicated files"


def _content(path):
    """File bytes with line endings normalized. Windows checkouts (autocrlf)
    can rewrite one working copy of a duplicated pair to CRLF while its twin
    keeps LF; that is a checkout artifact, not content drift, and must not
    trip the sync guard."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def test_duplicated_trees_are_byte_identical():
    """Every duplicated file must be byte-identical across trees, except the
    explicitly allowlisted structural divergences."""
    divergent = []
    for rel, tree, canonical_path, mirror_path in _discover_pairs():
        if _content(canonical_path) == _content(mirror_path):
            continue
        if (tree, rel) in ALLOWED_DIVERGENCES:
            continue
        divergent.append(f"{tree}/{rel}")
    assert not divergent, (
        "Duplicated tree files drifted out of sync (Global Constraint #7): "
        + ", ".join(sorted(divergent))
        + ". Apply the identical edit to every copy, or add an allowlist entry "
        "with a reason in ALLOWED_DIVERGENCES."
    )


def test_allowlisted_pairs_are_actually_divergent():
    """Freshness guard: an allowlist entry that is no longer divergent (the
    files were reconciled) must be removed so the allowlist cannot rot."""
    stale = []
    for (tree, rel), _reason in ALLOWED_DIVERGENCES.items():
        canonical_path = REPO_ROOT / CANONICAL / rel
        mirror_path = REPO_ROOT / tree / rel
        assert canonical_path.is_file(), f"allowlist references missing {rel}"
        assert mirror_path.is_file(), f"allowlist references missing {tree}/{rel}"
        if _content(canonical_path) == _content(mirror_path):
            stale.append(f"{tree}/{rel}")
    assert not stale, (
        "Allowlisted files are now byte-identical - remove the stale "
        "ALLOWED_DIVERGENCES entries: " + ", ".join(sorted(stale))
    )


# ---------------------------------------------------------------------------
# Connection-pool right-sizing
# ---------------------------------------------------------------------------
@pytest.fixture()
def models_module(monkeypatch):
    """Import strategies.shared.models and yield it with create_engine stubbed
    so calling _get_engine() records kwargs instead of building a real pool."""
    from strategies.shared import models as m

    recorded = {}

    def _fake_create_engine(*args, **kwargs):
        recorded["args"] = args
        recorded["kwargs"] = kwargs
        return object()  # sentinel engine

    monkeypatch.setattr(m, "create_engine", _fake_create_engine)
    # Reset the lazily-cached singletons so _get_engine rebuilds via our stub.
    m._engine = None
    m._session_factory = None
    m._recorded = recorded
    try:
        yield m
    finally:
        m._engine = None
        m._session_factory = None


def test_engine_uses_new_pool_defaults(models_module, monkeypatch):
    """With no env set, the engine builds a 5/5 pool with pre-ping + recycle."""
    for var in ("DB_POOL_SIZE", "DB_MAX_OVERFLOW"):
        monkeypatch.delenv(var, raising=False)
    models_module._engine = None
    models_module._get_engine()
    kwargs = models_module._recorded["kwargs"]
    assert kwargs["pool_size"] == 5
    assert kwargs["max_overflow"] == 5
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 1800


def test_engine_pool_size_from_env(models_module, monkeypatch):
    """DB_POOL_SIZE / DB_MAX_OVERFLOW override the defaults at build time."""
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    models_module._engine = None
    models_module._get_engine()
    kwargs = models_module._recorded["kwargs"]
    assert kwargs["pool_size"] == 7
    assert kwargs["max_overflow"] == 3
    # pre-ping / recycle are not env-tunable - always on.
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 1800


def test_every_models_copy_has_pool_config():
    """All three models.py copies must carry the identical pool config, even the
    (allowlisted) trimmed position_manager copy."""
    patterns = [
        r'pool_size\s*=\s*int\(\s*os\.getenv\(\s*"DB_POOL_SIZE",\s*"5"\s*\)\s*\)',
        r'max_overflow\s*=\s*int\(\s*os\.getenv\(\s*"DB_MAX_OVERFLOW",\s*"5"\s*\)\s*\)',
        r'pool_pre_ping\s*=\s*True',
        r'pool_recycle\s*=\s*1800',
    ]
    for rel in MODELS_COPIES:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for pat in patterns:
            assert re.search(pat, text), f"{rel} missing pool config: /{pat}/"
