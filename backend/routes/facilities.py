"""Commercial facilities monitoring and tariff status REST endpoints.

Provides:
- GET /api/v1/facilities: List all commercial facilities
- GET /api/v1/facilities/{id}/status: Real-time operational status, running cost, and today's spend
- GET /api/v1/facilities/{id}/cost-today: Granular breakdown of consumption, cost, and peak surcharges
- GET /api/v1/facilities/{id}/tariffs (and /tariff): Contract parameters and DEDDIE peak schedules
- GET /api/v1/facilities/{id}/telemetry: Time-series query filtering (start_time, end_time, limit)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from backend.database.sqlite_store import SQLiteStore
from backend.routes.telemetry import (
    AlertDispatcher,
    get_alert_dispatcher,
    get_database_store,
)
from tariff_engine import is_greek_offpeak_window, is_greek_peak_window

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/facilities", tags=["facilities"])


# --- Response Schemas ---

class FacilityStatusResponse(BaseModel):
    facility_id: str
    name: str
    facility_type: str
    contract_type: str
    tariff_color: str
    contracted_kva: float
    peak_threshold_kw: float
    latest_telemetry: dict[str, Any] | None = None
    active_running_cost_eur_per_h: float = 0.0
    active_tariff: dict[str, Any]
    cost_today_eur: float = 0.0
    kwh_today: float = 0.0
    peak_surcharges_today_eur: float = 0.0
    alert_status: str = "IDLE"


class CostTodayResponse(BaseModel):
    facility_id: str
    date: str
    total_kwh: float
    total_spend_eur: float
    peak_kwh: float
    offpeak_kwh: float
    peak_surcharges_eur: float
    average_rate_eur_per_kwh: float


class TariffConfigResponse(BaseModel):
    facility_id: str
    contract_type: str
    tariff_color: str
    contracted_kva: float
    peak_threshold_kw: float
    warning_threshold_ratio: float
    low_pf_threshold: float
    cooldown_seconds: int
    hysteresis_factor: float
    debounce_samples: int
    peak_window_schedule: dict[str, str]


# --- Endpoints ---

@router.get(
    "",
    response_model=list[dict[str, Any]],
    summary="List all configured facilities",
)
def list_facilities(
    store: SQLiteStore = Depends(get_database_store),
) -> list[dict[str, Any]]:
    """Return all registered commercial facilities."""
    return store.list_facility_configs()


@router.get(
    "/{id}/status",
    response_model=FacilityStatusResponse,
    summary="Get real-time facility operating status",
)
def get_facility_status(
    id: str,
    store: SQLiteStore = Depends(get_database_store),
    dispatcher: AlertDispatcher = Depends(get_alert_dispatcher),
) -> FacilityStatusResponse:
    """Returns the latest telemetry, active running cost (€/h), active Greek tariff

    window, today's accumulated spend (€), and current alert state.
    """
    config = store.get_facility_config(id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility '{id}' not found",
        )

    latest = store.get_latest_telemetry(id)
    summary = store.get_daily_summary(id)

    # Determine reference timestamp for zone calculation
    if latest and latest.get("timestamp"):
        try:
            ref_dt = datetime.fromisoformat(str(latest["timestamp"]).replace("Z", "+00:00"))
        except Exception:
            ref_dt = datetime.now(timezone.utc)
    else:
        ref_dt = datetime.now(timezone.utc)

    is_peak = is_greek_peak_window(ref_dt)
    is_offpeak = is_greek_offpeak_window(ref_dt)
    zone_name = "PEAK" if is_peak else ("OFF_PEAK" if is_offpeak else "NORMAL")

    running_cost = float(latest.get("running_cost_eur_per_h", 0.0)) if latest else 0.0
    current_rate = float(latest.get("current_rate_eur_per_kwh", 0.0)) if latest else 0.0
    alert_state = dispatcher.get_facility_state(id)

    return FacilityStatusResponse(
        facility_id=id,
        name=config.get("name", id),
        facility_type=config.get("facility_type", "bakery"),
        contract_type=config.get("contract_type", "Γ22"),
        tariff_color=config.get("tariff_color", "green"),
        contracted_kva=float(config.get("contracted_kva", 35.0)),
        peak_threshold_kw=float(config.get("peak_threshold_kw", 22.0)),
        latest_telemetry=latest,
        active_running_cost_eur_per_h=running_cost,
        active_tariff={
            "contract_type": config.get("contract_type", "Γ22"),
            "tariff_color": config.get("tariff_color", "green"),
            "zone": zone_name,
            "is_peak_window": is_peak,
            "is_offpeak_window": is_offpeak,
            "current_rate_eur_per_kwh": current_rate,
        },
        cost_today_eur=summary["total_spend_eur"],
        kwh_today=summary["total_kwh"],
        peak_surcharges_today_eur=summary["peak_surcharges_eur"],
        alert_status=alert_state,
    )


@router.get(
    "/{id}/cost-today",
    response_model=CostTodayResponse,
    summary="Get today's energy spend and peak surcharge breakdown",
)
def get_facility_cost_today(
    id: str,
    date: str | None = Query(None, description="Optional target date in YYYY-MM-DD format"),
    store: SQLiteStore = Depends(get_database_store),
) -> CostTodayResponse:
    """Returns detailed breakdown of today's (or specified date's) kWh, spend (€),

    peak consumption, off-peak consumption, and peak surcharges.
    """
    config = store.get_facility_config(id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility '{id}' not found",
        )

    summary = store.get_daily_summary(id, target_date=date)

    return CostTodayResponse(
        facility_id=id,
        date=summary["date"],
        total_kwh=summary["total_kwh"],
        total_spend_eur=summary["total_spend_eur"],
        peak_kwh=summary["peak_kwh"],
        offpeak_kwh=summary["offpeak_kwh"],
        peak_surcharges_eur=summary["peak_surcharges_eur"],
        average_rate_eur_per_kwh=summary["average_rate_eur_per_kwh"],
    )


@router.get(
    "/{id}/tariffs",
    response_model=TariffConfigResponse,
    summary="Get facility tariff configuration and peak schedules",
)
@router.get(
    "/{id}/tariff",
    response_model=TariffConfigResponse,
    include_in_schema=False,
)
def get_facility_tariffs(
    id: str,
    store: SQLiteStore = Depends(get_database_store),
) -> TariffConfigResponse:
    """Returns Greek commercial contract configuration, contracted capacity,

    alert thresholds, and peak window schedules.
    """
    config = store.get_facility_config(id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility '{id}' not found",
        )

    return TariffConfigResponse(
        facility_id=id,
        contract_type=config.get("contract_type", "Γ22"),
        tariff_color=config.get("tariff_color", "green"),
        contracted_kva=float(config.get("contracted_kva", 35.0)),
        peak_threshold_kw=float(config.get("peak_threshold_kw", 22.0)),
        warning_threshold_ratio=float(config.get("warning_threshold_ratio", 0.85)),
        low_pf_threshold=float(config.get("low_pf_threshold", 0.85)),
        cooldown_seconds=int(config.get("cooldown_seconds", 1800)),
        hysteresis_factor=float(config.get("hysteresis_factor", 0.90)),
        debounce_samples=int(config.get("debounce_samples", 3)),
        peak_window_schedule={
            "summer_peak": "14:00 - 17:00 (Mon - Fri, May 1 - Oct 31)",
            "winter_peak": "17:00 - 21:00 (Mon - Fri, Nov 1 - Apr 30)",
            "weekend": "No peak hours (standard normal / off-peak rates apply)",
        },
    )


@router.get(
    "/{id}/telemetry",
    response_model=list[dict[str, Any]],
    summary="Query historical telemetry records",
)
def get_facility_telemetry_history(
    id: str,
    start_time: str | None = Query(None, description="Start timestamp in ISO format"),
    end_time: str | None = Query(None, description="End timestamp in ISO format"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    store: SQLiteStore = Depends(get_database_store),
) -> list[dict[str, Any]]:
    """Retrieve historical telemetry time-series records for this facility."""
    config = store.get_facility_config(id)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility '{id}' not found",
        )

    return store.get_telemetry_history(
        facility_id=id,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
