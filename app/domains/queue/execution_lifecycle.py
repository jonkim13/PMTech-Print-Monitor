"""Queue execution lifecycle methods (mark_*, complete, fail, link)."""

from datetime import datetime, timezone
from typing import Optional


class QueueExecutionLifecycleMixin:
    """Lifecycle status transitions for queue_jobs.

    This mixin provides mark_*, complete_queue_job, fail_queue_job,
    link_* methods. It expects the host class to have:
    - _get_conn()
    - _get_queue_job_by_id(conn, queue_job_id)
    - _sync_job_status(conn, job_id)
    - _update_wo_status_from_items(conn, wo_id)
    """

    def _set_queue_job_status(self, conn, queue_job_id: int, status: str,
                              started_at: Optional[str] = None,
                              completed_at: Optional[str] = None) -> bool:
        job = self._get_queue_job_by_id(conn, queue_job_id)
        if not job:
            return False

        item_updates = ["status = ?"]
        item_params = [status]
        if started_at is not None:
            item_updates.append("started_at = ?")
            item_params.append(started_at)
        if completed_at is not None:
            item_updates.append("completed_at = ?")
            item_params.append(completed_at)
        else:
            item_updates.append("completed_at = NULL")

        conn.execute("""
            UPDATE queue_items
            SET {}
            WHERE queue_job_id = ?
              AND status NOT IN ('completed', 'cancelled')
        """.format(", ".join(item_updates)), item_params + [queue_job_id])

        job_updates = ["status = ?"]
        job_params = [status]
        if completed_at is not None:
            job_updates.append("completed_at = ?")
            job_params.append(completed_at)
        else:
            job_updates.append("completed_at = NULL")
        conn.execute("""
            UPDATE queue_jobs SET {}
            WHERE queue_job_id = ?
        """.format(", ".join(job_updates)), job_params + [queue_job_id])

        if job.get("job_id"):
            self._sync_job_status(conn, job["job_id"])
        self._update_wo_status_from_items(conn, job["wo_id"])
        return True

    def get_queue_job(self, queue_job_id: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            return self._get_queue_job_by_id(conn, queue_job_id)
        finally:
            conn.close()

    def get_active_queue_job_for_printer(self,
                                         printer_id: str) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT * FROM queue_jobs
                WHERE assigned_printer_id = ?
                  AND status = 'printing'
                ORDER BY queue_job_id DESC LIMIT 1
            """, (printer_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def find_printing_queue_job_by_filename(self, printer_id: str,
                                            filename: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM queue_jobs
            WHERE assigned_printer_id = ?
              AND gcode_file = ?
              AND status = 'printing'
            ORDER BY queue_job_id DESC LIMIT 1
        """, (printer_id, filename)).fetchone()

        if not row and filename:
            bare = filename.rsplit("/", 1)[-1] if "/" in filename else filename
            row = conn.execute("""
                SELECT * FROM queue_jobs
                WHERE assigned_printer_id = ?
                  AND (gcode_file = ? OR gcode_file LIKE ?)
                  AND status = 'printing'
                ORDER BY queue_job_id DESC LIMIT 1
            """, (printer_id, bare, "%" + bare)).fetchone()

        conn.close()
        return dict(row) if row else None

    def link_upload_session_to_queue_job(self, queue_job_id: int,
                                         upload_session_id: str) -> bool:
        conn = self._get_conn()
        job = conn.execute("""
            SELECT wo_id, job_id FROM queue_jobs WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        if not job:
            conn.close()
            return False

        conn.execute("""
            UPDATE queue_jobs SET upload_session_id = ?
            WHERE queue_job_id = ?
        """, (upload_session_id, queue_job_id))
        conn.execute("""
            UPDATE queue_items SET upload_session_id = ?
            WHERE queue_job_id = ?
        """, (upload_session_id, queue_job_id))
        conn.commit()
        conn.close()
        return True

    def link_print_job_to_queue_job(self, queue_job_id: int,
                                    print_job_id: int) -> None:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT job_id FROM queue_jobs WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        conn.execute("""
            UPDATE queue_jobs SET print_job_id = ?
            WHERE queue_job_id = ?
        """, (print_job_id, queue_job_id))
        conn.execute("""
            UPDATE queue_items SET print_job_id = ?
            WHERE queue_job_id = ?
        """, (print_job_id, queue_job_id))
        if row and row["job_id"]:
            conn.execute("""
                UPDATE jobs SET print_job_id = ?
                WHERE job_id = ?
            """, (print_job_id, row["job_id"]))
        conn.commit()
        conn.close()

    def mark_queue_job_uploading(self, queue_job_id: int,
                                 upload_session_id: str = None) -> bool:
        conn = self._get_conn()
        try:
            changed = self._set_queue_job_status(
                conn, queue_job_id, "uploading"
            )
            if changed and upload_session_id:
                conn.execute("""
                    UPDATE queue_jobs SET upload_session_id = ?
                    WHERE queue_job_id = ?
                """, (upload_session_id, queue_job_id))
                conn.execute("""
                    UPDATE queue_items SET upload_session_id = ?
                    WHERE queue_job_id = ?
                """, (upload_session_id, queue_job_id))
            if changed:
                conn.commit()
            else:
                conn.rollback()
            return changed
        finally:
            conn.close()

    def mark_queue_job_uploaded(self, queue_job_id: int) -> bool:
        conn = self._get_conn()
        try:
            changed = self._set_queue_job_status(
                conn, queue_job_id, "uploaded"
            )
            if changed:
                conn.commit()
            else:
                conn.rollback()
            return changed
        finally:
            conn.close()

    def mark_queue_job_starting(self, queue_job_id: int) -> bool:
        conn = self._get_conn()
        try:
            changed = self._set_queue_job_status(
                conn, queue_job_id, "starting"
            )
            if changed:
                conn.commit()
            else:
                conn.rollback()
            return changed
        finally:
            conn.close()

    def mark_queue_job_printing(self, queue_job_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            job = self._get_queue_job_by_id(conn, queue_job_id)
            changed = self._set_queue_job_status(
                conn, queue_job_id, "printing", started_at=now
            )
            if changed and job and job.get("job_id"):
                conn.execute("""
                    UPDATE jobs
                    SET started_at = COALESCE(started_at, ?)
                    WHERE job_id = ?
                """, (now, job["job_id"]))
            if changed:
                conn.commit()
            else:
                conn.rollback()
            return changed
        finally:
            conn.close()

    def mark_queue_job_upload_failed(self, queue_job_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            changed = self._set_queue_job_status(
                conn, queue_job_id, "upload_failed", completed_at=now
            )
            if changed:
                conn.commit()
            else:
                conn.rollback()
            return changed
        finally:
            conn.close()

    def mark_queue_job_start_failed(self, queue_job_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            changed = self._set_queue_job_status(
                conn, queue_job_id, "start_failed", completed_at=now
            )
            if changed:
                conn.commit()
            else:
                conn.rollback()
            return changed
        finally:
            conn.close()

    def complete_queue_job(self, queue_job_id: int,
                           print_job_id: Optional[int] = None) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        job = conn.execute(
            "SELECT wo_id, job_id FROM queue_jobs WHERE queue_job_id = ?",
            (queue_job_id,)
        ).fetchone()
        if not job:
            conn.close()
            return False

        conn.execute("""
            UPDATE queue_items
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_job_id = ? AND status = 'printing'
        """, (now, print_job_id, queue_job_id))

        conn.execute("""
            UPDATE queue_jobs
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_job_id = ?
        """, (now, print_job_id, queue_job_id))

        if job["job_id"]:
            conn.execute("""
                UPDATE jobs SET print_job_id = COALESCE(?, print_job_id)
                WHERE job_id = ?
            """, (print_job_id, job["job_id"]))
            self._sync_job_status(conn, job["job_id"])
        self._update_wo_status_from_items(conn, job["wo_id"])
        conn.commit()
        conn.close()
        return True

    def fail_queue_job(self, queue_job_id: int,
                       requeue_items: bool = False) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        job = conn.execute("""
            SELECT wo_id, job_id FROM queue_jobs WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        if not job:
            conn.close()
            return False

        if requeue_items:
            conn.execute("""
                UPDATE queue_items
                SET status = 'queued',
                    queue_job_id = NULL,
                    assigned_printer_id = NULL,
                    assigned_printer_name = NULL,
                    gcode_file = NULL,
                    upload_session_id = NULL,
                    print_job_id = NULL,
                    assigned_at = NULL,
                    started_at = NULL,
                    completed_at = NULL
                WHERE queue_job_id = ?
                  AND status IN ('uploading', 'uploaded', 'starting',
                                 'printing', 'upload_failed', 'start_failed')
            """, (queue_job_id,))
        else:
            conn.execute("""
                UPDATE queue_items
                SET status = 'failed',
                    completed_at = ?
                WHERE queue_job_id = ?
                  AND status IN ('uploading', 'uploaded', 'starting',
                                 'printing')
            """, (now, queue_job_id))

        conn.execute("""
            UPDATE queue_jobs SET status = 'failed', completed_at = ?
            WHERE queue_job_id = ?
        """, (now, queue_job_id))

        if job["job_id"]:
            self._sync_job_status(conn, job["job_id"])
        self._update_wo_status_from_items(conn, job["wo_id"])
        conn.commit()
        conn.close()
        return True

    def assign_queue_items(self, queue_ids, printer_id: str,
                           printer_name: str,
                           gcode_file: str,
                           operator_initials: Optional[str] = None,
                           job_id: Optional[int] = None) -> Optional[int]:
        try:
            result = self.start_queue_job_execution(
                queue_ids, printer_id, printer_name, gcode_file,
                operator_initials=operator_initials, job_id=job_id,
            )
            return result["queue_job_id"]
        except (LookupError, RuntimeError, ValueError):
            return None
