"""Compatibility wrapper for the extracted execution service."""

from app.domains.execution.service import ExecutionService, UploadWorkflowService

__all__ = ["ExecutionService", "UploadWorkflowService"]
