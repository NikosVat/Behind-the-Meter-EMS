"""FastAPI REST routes for SME Schedule Studio & Equipment Optimization.

Exposes:
- Equipment Asset CRUD:
  * GET    /api/v1/facilities/{facility_id}/assets
  * POST   /api/v1/facilities/{facility_id}/assets
  * PUT    /api/v1/facilities/{facility_id}/assets/{asset_id}
  * DELETE /api/v1/facilities/{facility_id}/assets/{asset_id}
- Schedule Settings:
  * GET    /api/v1/facilities/{facility_id}/schedule-settings
  * PUT    /api/v1/facilities/{facility_id}/schedule-settings
- Multi-Resolution Optimization & Schedule Management:
  * POST   /api/v1/facilities/{facility_id}/schedules/preview
  * POST   /api/v1/facilities/{facility_id}/schedules/save
  * GET    /api/v1/facilities/{facility_id}/schedules
  * GET    /api/v1/facilities/{facility_id}/schedules/{schedule_id}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.database.sqlite_store import SQLiteStore
from backend.routes.telemetry import get_database_store
from optimization_engine.scheduling_models import (
    AssetCreateRequest,
    AssetUpdateRequest,
    GeneratedSchedule,
    GenericEquipmentAsset,
    SchedulePreviewRequest,
    ScheduleSettings,
    ScheduleSettingsUpdateRequest,
)
from optimization_engine.scheduling_service import EquipmentSchedulingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/facilities", tags=["Schedule Studio & Equipment Optimization"])


def _verify_facility_exists(facility_id: str, store: SQLiteStore) -> dict[str, Any]:
    """Verify facility exists in SQLite store or return 404."""
    facility = store.get_facility_config(facility_id)
    if not facility:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility '{facility_id}' does not exist.",
        )
    return facility


# =====================================================================
# 1. EQUIPMENT ASSET CRUD ENDPOINTS
# =====================================================================

@router.get(
    "/{facility_id}/assets",
    response_model=list[GenericEquipmentAsset],
    summary="List all equipment assets for a facility",
)
def list_equipment_assets(
    facility_id: str,
    enabled_only: bool = Query(default=False, description="Filter only enabled equipment"),
    store: SQLiteStore = Depends(get_database_store),
) -> list[GenericEquipmentAsset]:
    _verify_facility_exists(facility_id, store)
    raw_assets = store.get_equipment_assets(facility_id, enabled_only=enabled_only)
    return [GenericEquipmentAsset(**a) for a in raw_assets]


@router.post(
    "/{facility_id}/assets",
    response_model=GenericEquipmentAsset,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new equipment asset for a facility",
)
def create_equipment_asset(
    facility_id: str,
    payload: AssetCreateRequest,
    store: SQLiteStore = Depends(get_database_store),
) -> GenericEquipmentAsset:
    _verify_facility_exists(facility_id, store)

    # Validate timing window
    if payload.earliest_start == payload.latest_finish and payload.required_runtime_minutes > 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="earliest_start and latest_finish cannot be identical for equipment with positive runtime.",
        )

    asset_dict = payload.model_dump()
    asset_dict["facility_id"] = facility_id

    created = store.create_equipment_asset(asset_dict)
    return GenericEquipmentAsset(**created)


@router.get(
    "/{facility_id}/assets/{asset_id}",
    response_model=GenericEquipmentAsset,
    summary="Retrieve details of a specific equipment asset",
)
def get_equipment_asset(
    facility_id: str,
    asset_id: str,
    store: SQLiteStore = Depends(get_database_store),
) -> GenericEquipmentAsset:
    _verify_facility_exists(facility_id, store)
    asset = store.get_equipment_asset(facility_id, asset_id)
    if not asset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_id}' not found in facility '{facility_id}'.",
        )
    return GenericEquipmentAsset(**asset)


@router.put(
    "/{facility_id}/assets/{asset_id}",
    response_model=GenericEquipmentAsset,
    summary="Update an existing equipment asset",
)
def update_equipment_asset(
    facility_id: str,
    asset_id: str,
    payload: AssetUpdateRequest,
    store: SQLiteStore = Depends(get_database_store),
) -> GenericEquipmentAsset:
    _verify_facility_exists(facility_id, store)
    existing = store.get_equipment_asset(facility_id, asset_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_id}' not found in facility '{facility_id}'.",
        )

    updates = payload.model_dump(exclude_unset=True)
    updated = store.update_equipment_asset(facility_id, asset_id, updates)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_id}' could not be updated.",
        )
    return GenericEquipmentAsset(**updated)


@router.delete(
    "/{facility_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an equipment asset",
)
def delete_equipment_asset(
    facility_id: str,
    asset_id: str,
    store: SQLiteStore = Depends(get_database_store),
) -> None:
    _verify_facility_exists(facility_id, store)
    deleted = store.delete_equipment_asset(facility_id, asset_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Asset '{asset_id}' not found in facility '{facility_id}'.",
        )


# =====================================================================
# 2. SCHEDULE SETTINGS ENDPOINTS
# =====================================================================

@router.get(
    "/{facility_id}/schedule-settings",
    response_model=ScheduleSettings,
    summary="Retrieve scheduling preferences for a facility",
)
def get_schedule_settings(
    facility_id: str,
    store: SQLiteStore = Depends(get_database_store),
) -> ScheduleSettings:
    _verify_facility_exists(facility_id, store)
    settings_dict = store.get_schedule_settings(facility_id) or {}
    return ScheduleSettings(**settings_dict)


@router.put(
    "/{facility_id}/schedule-settings",
    response_model=ScheduleSettings,
    summary="Update scheduling preferences for a facility",
)
def update_schedule_settings(
    facility_id: str,
    payload: ScheduleSettingsUpdateRequest,
    store: SQLiteStore = Depends(get_database_store),
) -> ScheduleSettings:
    _verify_facility_exists(facility_id, store)
    updates = payload.model_dump(exclude_unset=True)
    updated = store.upsert_schedule_settings(facility_id, updates)
    return ScheduleSettings(**updated)


# =====================================================================
# 3. SCHEDULE PREVIEW & SAVE ENDPOINTS
# =====================================================================

def _build_and_solve_schedule(
    facility_id: str,
    payload: SchedulePreviewRequest,
    store: SQLiteStore,
    status_type: str = "preview",
) -> GeneratedSchedule:
    """Internal helper to construct scheduling service and execute optimization."""
    _verify_facility_exists(facility_id, store)

    # 1. Fetch or override settings
    raw_settings = store.get_schedule_settings(facility_id) or {}
    base_settings = ScheduleSettings(**raw_settings)
    active_settings = ScheduleSettings(
        facility_id=facility_id,
        time_step_minutes=payload.time_step_minutes or base_settings.time_step_minutes,
        max_facility_power_kw=payload.max_facility_power_kw or base_settings.max_facility_power_kw,
        objective_mode=payload.objective_mode or base_settings.objective_mode,
        timezone=base_settings.timezone,
        forecast_uncertainty_pct=(
            payload.forecast_uncertainty_pct
            if payload.forecast_uncertainty_pct is not None
            else base_settings.forecast_uncertainty_pct
        ),
    )

    # 2. Fetch or override assets
    if payload.asset_overrides is not None:
        assets = payload.asset_overrides
    else:
        raw_assets = store.get_equipment_assets(facility_id, enabled_only=True)
        assets = [GenericEquipmentAsset(**a) for a in raw_assets]

    # 3. Determine provenance
    input_source = "forecast"
    is_demo = False
    if payload.baseline_load_kw is not None:
        input_source = "caller_supplied"
    elif not assets:
        input_source = "demo_profile"
        is_demo = True

    # 4. Instantiate and solve
    service = EquipmentSchedulingService(
        settings=active_settings,
        assets=assets,
        schedule_date=payload.schedule_date,
        baseline_load_kw=payload.baseline_load_kw,
        tariff_rates_eur_kwh=payload.tariff_rates_eur_kwh,
        input_source=input_source,
        is_demo=is_demo,
    )

    try:
        schedule = service.solve()
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ex),
        )

    schedule.status = status_type
    return schedule


@router.post(
    "/{facility_id}/schedules/preview",
    response_model=GeneratedSchedule,
    summary="Generate an advisory schedule preview at configured resolution",
)
def preview_schedule(
    facility_id: str,
    payload: SchedulePreviewRequest,
    store: SQLiteStore = Depends(get_database_store),
) -> GeneratedSchedule:
    return _build_and_solve_schedule(facility_id, payload, store, status_type="preview")


@router.post(
    "/{facility_id}/schedules/save",
    response_model=GeneratedSchedule,
    status_code=status.HTTP_201_CREATED,
    summary="Solve and save an approved schedule to persistent storage",
)
def save_schedule(
    facility_id: str,
    payload: SchedulePreviewRequest,
    store: SQLiteStore = Depends(get_database_store),
) -> GeneratedSchedule:
    schedule = _build_and_solve_schedule(facility_id, payload, store, status_type="saved")
    saved = store.save_generated_schedule(schedule.model_dump())
    return GeneratedSchedule(**saved)


@router.get(
    "/{facility_id}/schedules",
    response_model=list[GeneratedSchedule],
    summary="List historical saved schedules for a facility",
)
def list_schedules(
    facility_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    store: SQLiteStore = Depends(get_database_store),
) -> list[GeneratedSchedule]:
    _verify_facility_exists(facility_id, store)
    raw_schedules = store.get_generated_schedules(facility_id, limit=limit)
    # Fetch full details for each schedule
    schedules = []
    for s in raw_schedules:
        full = store.get_generated_schedule(facility_id, s["schedule_id"])
        if full:
            schedules.append(GeneratedSchedule(**full))
    return schedules


@router.get(
    "/{facility_id}/schedules/{schedule_id}",
    response_model=GeneratedSchedule,
    summary="Retrieve details and timeline of a saved schedule",
)
def get_schedule(
    facility_id: str,
    schedule_id: str,
    store: SQLiteStore = Depends(get_database_store),
) -> GeneratedSchedule:
    _verify_facility_exists(facility_id, store)
    schedule = store.get_generated_schedule(facility_id, schedule_id)
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Schedule '{schedule_id}' not found in facility '{facility_id}'.",
        )
    return GeneratedSchedule(**schedule)
