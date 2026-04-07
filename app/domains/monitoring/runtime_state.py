"""Runtime monitoring state containers and helpers."""

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


def normalize_print_filename(file_name):
    """Normalize filenames for pending print-start matching."""
    return os.path.basename(str(file_name or "")).strip().lower()


def build_filename_candidates(*names):
    """Return de-duplicated non-empty filename candidates."""
    candidates = []
    seen = set()
    for name in names:
        text = str(name or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        candidates.append(text)
    return candidates


def prune_pending_print_starts(pending_print_starts, now=None,
                               max_age=timedelta(hours=12)):
    """Drop stale pending print-start metadata in place."""
    cutoff = (now or datetime.now(timezone.utc)) - max_age
    empty_printers = []
    for printer_id, entries in pending_print_starts.items():
        fresh_entries = []
        for entry in entries:
            created_at = entry.get("created_at")
            try:
                created = datetime.fromisoformat(created_at)
            except (TypeError, ValueError):
                continue
            if created >= cutoff:
                fresh_entries.append(entry)
        if fresh_entries:
            pending_print_starts[printer_id] = fresh_entries
        else:
            empty_printers.append(printer_id)
    for printer_id in empty_printers:
        pending_print_starts.pop(printer_id, None)


def match_pending_print_start(pending_print_starts, printer_id,
                              file_name=None, upload_session_id=None):
    """Resolve the best pending start match for a printer."""
    entries = pending_print_starts.get(printer_id, [])
    if not entries:
        return None

    if upload_session_id:
        for entry in reversed(entries):
            if entry.get("upload_session_id") == upload_session_id:
                return dict(entry)

    normalized = normalize_print_filename(file_name)
    if normalized:
        for entry in reversed(entries):
            remote_name = normalize_print_filename(
                entry.get("remote_filename")
            )
            original_name = normalize_print_filename(
                entry.get("original_filename")
            )
            if normalized in (remote_name, original_name):
                return dict(entry)

    if len(entries) == 1:
        return dict(entries[-1])
    return None


def record_pending_print_start(pending_print_starts, printer_id,
                               upload_session_id, remote_filename,
                               original_filename, operator_initials,
                               queue_job_id=None, job_id=None, now=None):
    """Store structured start metadata until polling confirms printing."""
    normalized_remote = normalize_print_filename(remote_filename)
    normalized_original = normalize_print_filename(original_filename)
    initials = str(operator_initials or "").strip()
    if not printer_id or not upload_session_id or not initials:
        return

    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat() if isinstance(now, datetime) else str(now)
    prune_pending_print_starts(
        pending_print_starts,
        now=now if isinstance(now, datetime) else None,
    )

    entries = pending_print_starts.setdefault(printer_id, [])
    for entry in reversed(entries):
        if entry.get("upload_session_id") != upload_session_id:
            continue
        entry["created_at"] = now_iso
        entry["remote_filename"] = normalized_remote
        entry["original_filename"] = normalized_original
        entry["operator_initials"] = initials
        if queue_job_id is not None:
            entry["queue_job_id"] = queue_job_id
        if job_id is not None:
            entry["job_id"] = job_id
        return

    entries.append({
        "upload_session_id": upload_session_id,
        "remote_filename": normalized_remote,
        "original_filename": normalized_original,
        "operator_initials": initials,
        "queue_job_id": queue_job_id,
        "job_id": job_id,
        "created_at": now_iso,
    })


def clear_pending_print_start(pending_print_starts, printer_id,
                              upload_session_id=None,
                              remote_filename=None):
    """Remove pending print-start metadata when a start fails."""
    normalized_remote = normalize_print_filename(remote_filename)
    if not printer_id:
        return

    prune_pending_print_starts(pending_print_starts)
    entries = pending_print_starts.get(printer_id, [])
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if upload_session_id and (
            entry.get("upload_session_id") != upload_session_id
        ):
            continue
        if normalized_remote and (
            entry.get("remote_filename") != normalized_remote
        ):
            continue
        entries.pop(index)
        break
    if not entries:
        pending_print_starts.pop(printer_id, None)


@dataclass
class MonitoringRuntimeState:
    """Mutable runtime state used by monitoring orchestration."""

    print_start_times: dict = field(default_factory=dict)
    active_job_ids: dict = field(default_factory=dict)
    active_queue_job_ids: dict = field(default_factory=dict)
    pending_print_starts: dict = field(default_factory=dict)

    def prune_pending_print_starts(self):
        prune_pending_print_starts(self.pending_print_starts)

    def match_pending_print_start(self, printer_id, file_name=None,
                                  upload_session_id=None):
        return match_pending_print_start(
            self.pending_print_starts,
            printer_id,
            file_name=file_name,
            upload_session_id=upload_session_id,
        )

    def record_pending_print_start(self, printer_id, upload_session_id,
                                   remote_filename, original_filename,
                                   operator_initials, queue_job_id=None,
                                   job_id=None):
        record_pending_print_start(
            self.pending_print_starts,
            printer_id,
            upload_session_id,
            remote_filename,
            original_filename,
            operator_initials,
            queue_job_id=queue_job_id,
            job_id=job_id,
        )

    def clear_pending_print_start(self, printer_id, upload_session_id=None,
                                  remote_filename=None):
        clear_pending_print_start(
            self.pending_print_starts,
            printer_id,
            upload_session_id=upload_session_id,
            remote_filename=remote_filename,
        )