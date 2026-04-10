"""Explicit application dependency construction."""

import threading
from dataclasses import dataclass

from database import PrintHistoryDB
from app.domains.assignments.repository import FilamentAssignmentDB
from app.domains.inventory.repository import FilamentInventoryDB
from drone import DroneController
from farm_manager import PrintFarmManager
from work_orders_db import WorkOrderDB

from ..domains.assignments.service import AssignmentService
from ..domains.execution import ExecutionService, UploadSessionRepository
from ..domains.inventory.service import InventoryService
from ..domains.monitoring.event_service import EventService
from ..domains.monitoring.runtime_state import MonitoringRuntimeState
from ..domains.monitoring.transition_handler import TransitionHandler
from ..domains.production.export_service import ExportService
from ..domains.production.job_repository import PrintJobRepository
from ..domains.production.machine_repository import MachineLogRepository
from ..domains.production.material_repository import MaterialUsageRepository
from ..domains.production.service import ProductionService

from .settings import AppSettings, load_settings


@dataclass(frozen=True)
class AppContainer:
    """Concrete application dependencies built from settings."""

    settings: AppSettings
    filament_db: FilamentInventoryDB
    history_db: PrintHistoryDB
    assignment_db: FilamentAssignmentDB
    job_repository: PrintJobRepository
    machine_repository: MachineLogRepository
    material_repository: MaterialUsageRepository
    production_service: ProductionService
    export_service: ExportService
    work_order_db: WorkOrderDB
    upload_session_repository: UploadSessionRepository
    event_service: EventService
    transition_handler: TransitionHandler
    farm_manager: PrintFarmManager
    drone_controller: DroneController
    execution_service: ExecutionService
    inventory_service: InventoryService
    assignment_service: AssignmentService

    @property
    def upload_session_db(self) -> UploadSessionRepository:
        return self.upload_session_repository

    @property
    def upload_workflow(self) -> ExecutionService:
        return self.execution_service


def build_container(settings: AppSettings = None) -> AppContainer:
    """Construct the current app's shared dependencies explicitly."""
    settings = settings or load_settings()

    filament_db = FilamentInventoryDB(settings.inventory_db_path)
    history_db = PrintHistoryDB(settings.history_db_path)
    assignment_db = FilamentAssignmentDB(settings.assignment_db_path)
    job_repository = PrintJobRepository(settings.production_db_path)
    machine_repository = MachineLogRepository(settings.production_db_path)
    material_repository = MaterialUsageRepository(
        settings.production_db_path
    )
    production_service = ProductionService(
        job_repository=job_repository,
        machine_repository=machine_repository,
        material_repository=material_repository,
    )
    export_service = ExportService(
        job_repository=job_repository,
        machine_repository=machine_repository,
        material_repository=material_repository,
    )
    work_order_db = WorkOrderDB(settings.work_order_db_path)
    upload_session_repository = UploadSessionRepository(
        settings.upload_session_db_path
    )
    event_service = EventService()
    runtime_state = MonitoringRuntimeState()
    state_lock = threading.Lock()
    transition_handler = TransitionHandler(
        history_db=history_db,
        job_repository=job_repository,
        machine_repository=machine_repository,
        material_repository=material_repository,
        work_order_db=work_order_db,
        filament_db=filament_db,
        assignment_db=assignment_db,
        upload_session_db=upload_session_repository,
        event_service=event_service,
        runtime_state=runtime_state,
        snapshots_dir=settings.snapshots_dir,
        state_lock=state_lock,
    )

    farm_manager = PrintFarmManager(
        settings.config,
        history_db,
        filament_db=filament_db,
        assignment_db=assignment_db,
        job_repository=job_repository,
        machine_repository=machine_repository,
        material_repository=material_repository,
        snapshots_dir=settings.snapshots_dir,
        data_dir=settings.data_dir,
        work_order_db=work_order_db,
        upload_session_db=upload_session_repository,
        event_service=event_service,
        transition_handler=transition_handler,
        runtime_state=runtime_state,
        state_lock=state_lock,
    )
    inventory_service = InventoryService(filament_db)

    def _resolve_printer_name(printer_id):
        printer_data = (farm_manager.printers or {}).get(printer_id, {})
        client = printer_data.get("client")
        name = getattr(client, "name", "") if client else ""
        if name and name != printer_id:
            return f"{name} ({printer_id})"
        return name if name else printer_id

    assignment_service = AssignmentService(
        assignment_db, filament_db,
        printer_name_resolver=_resolve_printer_name,
    )
    drone_controller = DroneController()
    execution_service = ExecutionService(
        settings.gcode_uploads_dir,
        upload_session_repository,
        farm_manager=farm_manager,
        work_order_db=work_order_db,
    )

    return AppContainer(
        settings=settings,
        filament_db=filament_db,
        history_db=history_db,
        assignment_db=assignment_db,
        job_repository=job_repository,
        machine_repository=machine_repository,
        material_repository=material_repository,
        production_service=production_service,
        export_service=export_service,
        work_order_db=work_order_db,
        upload_session_repository=upload_session_repository,
        event_service=event_service,
        transition_handler=transition_handler,
        farm_manager=farm_manager,
        drone_controller=drone_controller,
        execution_service=execution_service,
        inventory_service=inventory_service,
        assignment_service=assignment_service,
    )
