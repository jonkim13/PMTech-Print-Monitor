"""Quality domain — Non-Conformance Reports (NCR) + Corrective Actions (CA).

Phase E1 (backend). Owns ``data/quality.db``. NCR↔job/WO relationships
are logical references resolved at the service layer — no cross-file
SQL joins (same convention as the production_log.db crossing in
WorkOrderService._attach_production_outcome).
"""
