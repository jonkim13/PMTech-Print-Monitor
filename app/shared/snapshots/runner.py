"""Whole-fleet DB snapshot + retention.

Background
----------
The May 2026 inventory recovery incident showed that the per-DB
``.bak-<timestamp>`` files migration scripts produce are not enough on
their own: ``git checkout HEAD -- data/*.db`` overwrote three live DBs
with stale tracked snapshots and the partial loss went unnoticed for a
week. This module adds a one-way archive at ``data/recovery/`` that the
app populates on every startup and that migrations populate before any
write, so a known-good copy of every DB exists from the moment the
process comes up.

Two-layer design
----------------
``.bak-<timestamp>`` files (created by individual migration scripts) live
next to each DB and capture *one* DB just before that migration touches
it. The ``data/recovery/<timestamp>/`` folders (created here) capture
*every* DB at process-startup and at pre-migration time. They are
additive layers — one is per-DB and migration-local, the other is
whole-fleet and lifecycle-wide.

Boundaries
----------
- App code never reads from ``data/recovery/``.
- Nothing in this module imports from ``app.shared.migrations``. The
  dependency direction is one-way: migration scripts may import the
  snapshotter; the snapshotter knows nothing about migrations.
- The path list comes from :class:`AppSettings` — see
  :func:`_list_db_paths`. Do not hardcode DB names here.
- WAL-mode DBs are copied via :py:meth:`sqlite3.Connection.backup`, not
  a raw byte copy. A flat file copy can capture a torn checkpoint state.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

from app.config.settings import AppSettings


_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S"
_RECOVERY_DIRNAME = "recovery"
_REASON_FILENAME = "reason.txt"


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of a single :func:`snapshot_all_dbs` call."""

    path: Path
    db_count: int
    bytes_written: int
    skipped: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PruneResult:
    """Outcome of a single :func:`prune_snapshots` call."""

    removed: List[Path] = field(default_factory=list)
    kept: int = 0


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _recovery_root(settings: AppSettings) -> Path:
    return Path(settings.data_dir) / _RECOVERY_DIRNAME


def _list_db_paths(settings: AppSettings) -> List[Tuple[str, Path]]:
    """Return ``(filename, absolute_path)`` for every known DB.

    The filename is what the snapshot folder will store the copy under;
    we deliberately use ``basename(src)`` so the snapshot mirrors the
    real on-disk layout and a restore is a plain copy.
    """
    src_paths = [
        settings.inventory_db_path,
        settings.history_db_path,
        settings.assignment_db_path,
        settings.production_db_path,
        settings.work_order_db_path,
        settings.upload_session_db_path,
    ]
    return [(os.path.basename(p), Path(p)) for p in src_paths]


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

def _snapshot_one_db(src: Path, dst: Path) -> int:
    """Back up ``src`` to ``dst`` using the SQLite backup API.

    Returns the number of bytes in the resulting file. The backup API
    is required for WAL-mode DBs — a plain byte-level copy of a WAL
    database while the WAL has uncheckpointed pages produces a torn
    snapshot.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    return dst.stat().st_size


def snapshot_all_dbs(settings: AppSettings, reason: str) -> SnapshotResult:
    """Copy every known DB into ``data/recovery/<timestamp>/``.

    Args:
        settings: Resolved app settings — source of truth for DB paths.
        reason: Free-form label written verbatim into ``reason.txt``.
            Conventional values: ``"startup"``, ``"manual"``,
            ``"pre-migration-<NNN>"``.

    The snapshot is written to a ``<timestamp>.tmp`` directory and
    renamed atomically only after every backup completes. A crash
    mid-snapshot leaves a ``.tmp`` directory behind, which the pruner
    will eventually clear.
    """
    root = _recovery_root(settings)
    root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
    final_path = root / timestamp
    staging_path = root / f"{timestamp}.tmp"

    if staging_path.exists():
        shutil.rmtree(staging_path)
    staging_path.mkdir(parents=True, exist_ok=False)

    skipped: List[str] = []
    bytes_written = 0
    db_count = 0

    try:
        for filename, src in _list_db_paths(settings):
            if not src.exists():
                skipped.append(filename)
                continue
            bytes_written += _snapshot_one_db(src, staging_path / filename)
            db_count += 1

        (staging_path / _REASON_FILENAME).write_text(reason, encoding="utf-8")
        os.rename(staging_path, final_path)
    except BaseException:
        # Crash leaves the ``.tmp`` dir behind on purpose so the
        # operator can inspect it. The pruner removes stragglers.
        raise

    return SnapshotResult(
        path=final_path,
        db_count=db_count,
        bytes_written=bytes_written,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------

def _parse_snapshot_timestamp(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, _TIMESTAMP_FORMAT)
    except ValueError:
        return None


def prune_snapshots(
    settings: AppSettings,
    max_count: int = 100,
    max_age_days: int = 30,
) -> PruneResult:
    """Apply the rolling retention rule to ``data/recovery/``.

    A snapshot survives only if it satisfies BOTH constraints:
    it ranks among the ``max_count`` most recent AND its timestamp is
    within ``max_age_days`` of now. Anything else — plus any stale
    ``.tmp`` staging directory — is removed.
    """
    root = _recovery_root(settings)
    if not root.exists():
        return PruneResult(removed=[], kept=0)

    candidates: List[Tuple[datetime, Path]] = []
    removed: List[Path] = []

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        # Sweep stale staging dirs unconditionally — they exist only
        # if a previous snapshot crashed mid-write.
        if entry.name.endswith(".tmp"):
            shutil.rmtree(entry)
            removed.append(entry)
            continue
        ts = _parse_snapshot_timestamp(entry.name)
        if ts is None:
            # Unknown directory shape — leave it alone. Operator owns it.
            continue
        candidates.append((ts, entry))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    top_n = candidates[:max_count]
    cutoff = datetime.now() - timedelta(days=max_age_days)
    survivors = [(ts, path) for ts, path in top_n if ts >= cutoff]
    survivor_set = {path for _, path in survivors}

    for _, path in candidates:
        if path in survivor_set:
            continue
        shutil.rmtree(path)
        removed.append(path)

    return PruneResult(removed=removed, kept=len(survivors))
