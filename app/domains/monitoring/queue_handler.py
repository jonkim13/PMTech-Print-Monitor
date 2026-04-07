"""Work-order queue side effects for print transitions."""

from app.domains.monitoring.runtime_state import normalize_print_filename
from app.shared.constants import QueueItemStatus


class QueueHandler:
    """Apply queue and work-order lifecycle side effects."""

    def __init__(self, work_order_db=None, runtime_state=None):
        self.work_order_db = work_order_db
        self.runtime_state = runtime_state

    def _active_job_ids(self):
        return self.runtime_state.active_job_ids if self.runtime_state else {}

    def _active_queue_job_ids(self):
        return (
            self.runtime_state.active_queue_job_ids
            if self.runtime_state else {}
        )

    def link_print_job_on_start(self, printer_id, state, job_id,
                                pending_start=None, upload_session=None):
        """Link a production print job to the matching queue job."""
        if not self.work_order_db:
            return

        queue_job = self._find_queue_job_on_start(
            printer_id, state, pending_start, upload_session
        )
        if queue_job:
            self.work_order_db.mark_queue_job_printing(
                queue_job["queue_job_id"]
            )
            self._active_queue_job_ids()[printer_id] = (
                queue_job["queue_job_id"]
            )
            self.work_order_db.link_print_job_to_queue_job(
                queue_job["queue_job_id"], job_id
            )
        else:
            self._active_queue_job_ids().pop(printer_id, None)

    def complete(self, printer_id, state):
        """Auto-complete a queue item when a print finishes."""
        if not self.work_order_db:
            return
        filename = state.get("job", {}).get("filename", "")
        queue_job_id = self._active_queue_job_ids().pop(printer_id, None)
        if queue_job_id and self._complete_known_queue_job(
                queue_job_id, filename):
            return
        if not filename:
            return
        try:
            queue_job = self.work_order_db.get_active_queue_job_for_printer(
                printer_id
            )
            if queue_job:
                self.work_order_db.complete_queue_job(
                    queue_job["queue_job_id"],
                    print_job_id=queue_job.get("print_job_id"),
                )
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"{QueueItemStatus.COMPLETED}")
                return

            queue_job = self.work_order_db.find_printing_queue_job_by_filename(
                printer_id, filename)
            if queue_job:
                self.work_order_db.complete_queue_job(
                    queue_job["queue_job_id"],
                    print_job_id=queue_job.get("print_job_id"),
                )
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"{QueueItemStatus.COMPLETED}")
                return

            queue_item = self.work_order_db.find_printing_item_by_filename(
                printer_id, filename)
            if queue_item:
                job_id = self._active_job_ids().get(printer_id)
                self.work_order_db.complete_queue_item(
                    queue_item["queue_id"], print_job_id=job_id)
                print(f"[WORKORDER] Queue item #{queue_item['queue_id']} "
                      f"completed ({queue_item['part_name']} "
                      f"{queue_item['sequence_number']}/"
                      f"{queue_item['total_quantity']} "
                      f"for {queue_item['customer_name']})")
        except Exception as exc:
            print(f"[WORKORDER] Error completing queue item: {exc}")

    def fail(self, printer_id, state):
        """Auto-fail a queue item when a printer errors or stops."""
        if not self.work_order_db:
            return
        filename = state.get("job", {}).get("filename", "")
        queue_job_id = self._active_queue_job_ids().pop(printer_id, None)
        if queue_job_id and self._fail_known_queue_job(queue_job_id, filename):
            return
        if not filename:
            return
        try:
            queue_job = self.work_order_db.get_active_queue_job_for_printer(
                printer_id
            )
            if queue_job:
                self.work_order_db.fail_queue_job(queue_job["queue_job_id"])
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"{QueueItemStatus.FAILED}")
                return

            queue_job = self.work_order_db.find_printing_queue_job_by_filename(
                printer_id, filename)
            if queue_job:
                self.work_order_db.fail_queue_job(queue_job["queue_job_id"])
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"{QueueItemStatus.FAILED}")
                return

            queue_item = self.work_order_db.find_printing_item_by_filename(
                printer_id, filename)
            if queue_item:
                self.work_order_db.fail_queue_item(queue_item["queue_id"])
                print(f"[WORKORDER] Queue item #{queue_item['queue_id']} "
                      f"failed ({queue_item['part_name']})")
        except Exception as exc:
            print(f"[WORKORDER] Error failing queue item: {exc}")

    def _find_queue_job_on_start(self, printer_id, state, pending_start,
                                 upload_session):
        queue_job = None
        pending_queue_job_id = (
            pending_start.get("queue_job_id") if pending_start else None
        )
        if pending_queue_job_id:
            queue_job = self.work_order_db.get_queue_job(pending_queue_job_id)
            if queue_job and queue_job.get("status") not in (
                QueueItemStatus.UPLOADING,
                QueueItemStatus.UPLOADED,
                QueueItemStatus.STARTING,
                QueueItemStatus.PRINTING,
            ):
                queue_job = None
        if not queue_job:
            queue_job = self.work_order_db.get_active_queue_job_for_printer(
                printer_id
            )
        if (not queue_job and upload_session
                and upload_session.get("queue_job_id")):
            queue_job = self.work_order_db.get_queue_job(
                upload_session["queue_job_id"]
            )
        if not queue_job:
            queue_job = self.work_order_db.find_printing_queue_job_by_filename(
                printer_id, state["job"]["filename"]
            )
        return queue_job

    def _complete_known_queue_job(self, queue_job_id, filename):
        try:
            queue_job = self.work_order_db.get_queue_job(queue_job_id)
            if self._matches_printing_queue_job(queue_job, filename):
                if self.work_order_db.complete_queue_job(queue_job_id):
                    print(f"[WORKORDER] Queue job #{queue_job_id} completed")
                    return True
        except Exception as exc:
            print(f"[WORKORDER] Error completing queue job: {exc}")
        return False

    def _fail_known_queue_job(self, queue_job_id, filename):
        try:
            queue_job = self.work_order_db.get_queue_job(queue_job_id)
            if self._matches_printing_queue_job(queue_job, filename):
                if self.work_order_db.fail_queue_job(queue_job_id):
                    print(f"[WORKORDER] Queue job #{queue_job_id} failed")
                    return True
        except Exception as exc:
            print(f"[WORKORDER] Error failing queue job: {exc}")
        return False

    @staticmethod
    def _matches_printing_queue_job(queue_job, filename):
        queued_file = normalize_print_filename(
            queue_job.get("gcode_file") if queue_job else ""
        )
        current_file = normalize_print_filename(filename)
        return (
            queue_job
            and queue_job.get("status") == QueueItemStatus.PRINTING
            and (not queued_file or not current_file
                 or queued_file == current_file)
        )
