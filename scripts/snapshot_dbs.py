#!/usr/bin/env python3
"""Manual DB snapshot. Run on Pi before risky operations."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.config.settings import load_settings  # noqa: E402
from app.shared.snapshots.runner import (  # noqa: E402
    prune_snapshots,
    snapshot_all_dbs,
)


if __name__ == "__main__":
    settings = load_settings()
    result = snapshot_all_dbs(settings, reason="manual")
    print(
        f"Snapshotted {result.db_count} DBs "
        f"({result.bytes_written} bytes) -> {result.path}"
    )
    if result.skipped:
        print(f"Skipped (not present): {result.skipped}")
    prune_result = prune_snapshots(settings)
    if prune_result.removed:
        print(
            f"Pruned {len(prune_result.removed)} old snapshots; "
            f"kept {prune_result.kept}"
        )
