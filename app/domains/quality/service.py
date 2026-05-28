"""Quality business logic — NCR + Corrective Action workflow.

Validation and state-machine rules live here; the repository takes
what it is given. Two non-input errors are modelled as typed
exceptions so the route layer can map them to distinct HTTP codes:

    QualityValidationError (subclasses ValueError) -> 400
    QualityStateError                              -> 409
    LookupError (missing NCR/CA/job)               -> 404

Cross-DB join points (no SQL join across files):

- job existence is checked against work_orders.db via the injected
  JobRepository (``get_job``).
- after create/close NCR the work-order status is re-rolled through
  ``status_sync.sync_work_order_status`` with the quality repository
  passed in, so an open NCR gates the WO immediately and closing it
  releases the gate.
"""

from app.domains.work_orders import status_sync


class QualityValidationError(ValueError):
    """Bad input — maps to HTTP 400."""


class QualityStateError(Exception):
    """Illegal state transition / business-rule violation — HTTP 409."""


_NCR_CA_NEEDED = ("Y", "N")
_CA_STATUSES = ("open", "in_progress", "verified", "closed")
# Allowed CA status transitions. 'verified' is reachable from 'open'
# directly so the create→verify→close-NCR happy path works without an
# explicit in_progress hop; 'closed' is only reachable from 'verified'
# (open→closed must not skip verification).
_CA_TRANSITIONS = {
    "open": {"in_progress", "verified"},
    "in_progress": {"verified"},
    "verified": {"closed"},
    "closed": set(),
}


class QualityService:
    """Orchestrates NCR + Corrective Action lifecycle."""

    def __init__(self, *, quality_repository, job_repository,
                 work_order_repository):
        self.quality_repository = quality_repository
        self.job_repository = job_repository
        self.work_order_repository = work_order_repository

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_text(value, field):
        text = (value or "").strip() if isinstance(value, str) else value
        if not text:
            raise QualityValidationError("{} is required".format(field))
        return text

    def _resync_wo(self, wo_id: str) -> None:
        """Re-roll the WO status with the open-NCR gate applied.

        Opens a work_orders.db connection (same file the WO/jobs/queue
        live in) and passes the quality repository so the gate reads the
        open-NCR count. The NCR read itself happens on quality.db inside
        the repository — no cross-file SQL join.
        """
        conn = self.work_order_repository._get_conn()
        try:
            status_sync.sync_work_order_status(
                conn, wo_id, quality_repository=self.quality_repository
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Non-Conformances
    # ------------------------------------------------------------------

    def create_ncr(self, job_id: int, wo_id: str, description: str,
                   reported_by: str, affected_parts=None,
                   remedial_action=None,
                   corrective_action_needed: str = "N") -> dict:
        description = self._require_text(description, "description")
        reported_by = self._require_text(reported_by, "reported_by")
        wo_id = self._require_text(wo_id, "wo_id")
        if corrective_action_needed not in _NCR_CA_NEEDED:
            raise QualityValidationError(
                "corrective_action_needed must be one of {}; got {!r}".format(
                    _NCR_CA_NEEDED, corrective_action_needed
                )
            )
        if self.job_repository.get_job(job_id) is None:
            raise LookupError("Job not found")

        ncr = self.quality_repository.create_ncr(
            job_id=job_id, wo_id=wo_id, description=description,
            reported_by=reported_by, affected_parts=affected_parts,
            remedial_action=remedial_action,
            corrective_action_needed=corrective_action_needed,
        )
        # An open NCR must gate the WO immediately.
        self._resync_wo(wo_id)
        return ncr

    def get_ncr(self, ncr_id: int) -> dict:
        ncr = self.quality_repository.get_ncr(ncr_id)
        if ncr is None:
            raise LookupError("NCR not found")
        return ncr

    def get_ncr_with_cas(self, ncr_id: int) -> dict:
        ncr = self.get_ncr(ncr_id)
        ncr["corrective_actions"] = self.quality_repository.list_cas_for_ncr(
            ncr_id
        )
        return ncr

    def list_ncrs(self, wo_id=None, job_id=None) -> list:
        if wo_id is not None:
            return self.quality_repository.list_ncrs_for_wo(wo_id)
        if job_id is not None:
            return self.quality_repository.list_ncrs_for_job(job_id)
        return self.quality_repository.list_open_ncrs()

    def close_ncr(self, ncr_id: int) -> dict:
        ncr = self.get_ncr(ncr_id)
        cas = self.quality_repository.list_cas_for_ncr(ncr_id)
        unresolved = [c for c in cas
                      if c.get("status") not in ("verified", "closed")]
        if unresolved:
            raise QualityStateError(
                "Cannot close NCR {}: {} corrective action(s) not yet "
                "verified".format(ncr_id, len(unresolved))
            )
        closed = self.quality_repository.close_ncr(ncr_id)
        # The WO may have been held only by this NCR — release the gate.
        self._resync_wo(ncr["wo_id"])
        return closed

    # ------------------------------------------------------------------
    # Corrective Actions
    # ------------------------------------------------------------------

    def create_ca(self, ncr_id: int, root_cause_actions: str,
                  responsible_persons=None, resources_needed=None,
                  effectiveness_verification=None,
                  verifying_person=None) -> dict:
        ncr = self.get_ncr(ncr_id)
        if ncr.get("corrective_action_needed") != "Y":
            raise QualityStateError(
                "NCR {} does not require a corrective action".format(ncr_id)
            )
        root_cause_actions = self._require_text(
            root_cause_actions, "root_cause_actions"
        )
        return self.quality_repository.create_ca(
            ncr_id=ncr_id, root_cause_actions=root_cause_actions,
            responsible_persons=responsible_persons,
            resources_needed=resources_needed,
            effectiveness_verification=effectiveness_verification,
            verifying_person=verifying_person,
        )

    def get_ca(self, ca_id: int) -> dict:
        ca = self.quality_repository.get_ca(ca_id)
        if ca is None:
            raise LookupError("Corrective action not found")
        return ca

    def update_ca(self, ca_id: int, **fields) -> dict:
        self.get_ca(ca_id)  # existence check (raises LookupError)
        return self.quality_repository.update_ca(ca_id, **fields)

    def set_ca_status(self, ca_id: int, new_status: str,
                      verifying_person=None) -> dict:
        ca = self.get_ca(ca_id)
        if new_status not in _CA_STATUSES:
            raise QualityValidationError(
                "status must be one of {}; got {!r}".format(
                    _CA_STATUSES, new_status
                )
            )
        current = ca.get("status")
        if new_status not in _CA_TRANSITIONS.get(current, set()):
            raise QualityStateError(
                "Illegal corrective-action transition {!r} -> {!r}".format(
                    current, new_status
                )
            )
        return self.quality_repository.set_ca_status(
            ca_id, new_status, verifying_person=verifying_person
        )

    def verify_ca(self, ca_id: int, verifying_person: str) -> dict:
        verifying_person = self._require_text(
            verifying_person, "verifying_person"
        )
        return self.set_ca_status(
            ca_id, "verified", verifying_person=verifying_person
        )
