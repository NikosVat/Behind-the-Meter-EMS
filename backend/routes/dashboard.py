"""Interactive Real-Time Web Dashboard REST router and UI serving.

Provides:
- GET /dashboard: Renders the Greek Commercial EMS Single-Page Application (SPA)
- GET /api/v1/dashboard/metrics/{facility_id}: Aggregated live KPI metrics and timeline data
- POST /api/v1/dashboard/config/{facility_id}: Dynamic configuration update for thresholds & alert channels
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from backend.database.sqlite_store import SQLiteStore
from backend.routes.telemetry import AlertDispatcher, get_alert_dispatcher, get_database_store
from tariff_engine import is_greek_offpeak_window, is_greek_peak_window

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "dashboard.html"


# --- Pydantic Schemas ---

class DashboardTimelinePoint(BaseModel):
    timestamp: str
    power_kw: float
    running_cost_eur_h: float
    is_peak: bool


class DashboardMetricsResponse(BaseModel):
    power_measurement_method: str = "unknown"
    facility_id: str
    facility_name: str
    facility_type: str
    contract_type: str
    tariff_color: str
    contracted_kva: float
    peak_threshold_kw: float
    notification_channel: str
    chat_id: int | str | None = None
    viber_receiver_id: str | None = None
    total_active_power_kw: float
    total_apparent_power_kva: float
    system_power_factor: float
    grid_frequency_hz: float
    wifi_rssi_dbm: float
    phases: dict[str, Any]
    timestamp: str
    running_cost_eur_per_h: float
    current_rate_eur_per_kwh: float
    cost_today_eur: float
    kwh_today: float
    peak_surcharges_today_eur: float
    active_zone_name: str
    is_peak_window: bool
    is_offpeak_window: bool
    load_status: str
    timeline: list[DashboardTimelinePoint] = Field(default_factory=list)


class DashboardConfigUpdateRequest(BaseModel):
    peak_threshold_kw: float | None = Field(default=None, gt=0.0)
    notification_channel: str | None = Field(default=None)
    chat_id: int | None = Field(default=None)
    viber_receiver_id: str | None = Field(default=None)


# --- Endpoints ---

@router.get(
    "/dashboard",
    response_class=HTMLResponse,
    summary="Serve the Real-Time Behind-the-Meter EMS Web Dashboard",
)
def serve_dashboard() -> HTMLResponse:
    """Renders the self-contained HTML/Tailwind/Chart.js web dashboard."""
    if TEMPLATE_PATH.exists():
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
    else:
        content = "<h1>EMS Dashboard Template Not Found</h1>"
    return HTMLResponse(content=content, status_code=status.HTTP_200_OK)


@router.get(
    "/api/v1/dashboard/metrics/{facility_id}",
    response_model=DashboardMetricsResponse,
    summary="Get aggregated live dashboard metrics and load timeline for a commercial facility",
)
def get_dashboard_metrics(
    facility_id: str,
    store: Annotated[SQLiteStore, Depends(get_database_store)],
) -> DashboardMetricsResponse:
    """Returns real-time electrical telemetry, cost metrics, active DEDDIE zone, and recent timeline points."""
    config = store.get_facility_config(facility_id)
    if not config:
        # Fallback facility profile if not in store
        config = {
            "facility_id": facility_id,
            "name": facility_id,
            "facility_type": "commercial",
            "contract_type": "Γ22",
            "tariff_color": "green",
            "contracted_kva": 35.0,
            "peak_threshold_kw": 22.0,
            "notification_channel": "telegram",
            "chat_id": None,
            "viber_receiver_id": None,
        }

    latest = store.get_latest_telemetry(facility_id)
    daily_summary = store.get_daily_summary(facility_id)
    history = store.get_telemetry_history(facility_id, limit=40)

    now = datetime.now(timezone.utc)
    ts_obj = now
    if latest and latest.get("timestamp"):
        try:
            raw_ts = str(latest["timestamp"])
            ts_obj = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        except Exception:
            ts_obj = now

    is_peak = is_greek_peak_window(ts_obj)
    is_offpeak = is_greek_offpeak_window(ts_obj)
    if is_peak:
        zone_name = "Ζώνη Αιχμής (14:00 - 17:00 θερινό / 17:00 - 21:00 χειμερινό)"
    elif is_offpeak:
        zone_name = "Ζώνη Μειωμένης Χρέωσης (Νυχτερινό)"
    else:
        zone_name = "Κανονική Ζώνη"

    tot_kw = float(latest.get("total_active_power_kw", 0.0)) if latest else 0.0
    tot_kva = float(latest.get("total_apparent_power_kva", tot_kw)) if latest else 0.0
    sys_pf = float(latest.get("system_power_factor", 0.98)) if latest else 0.98
    freq = float(latest.get("grid_frequency_hz", 50.0)) if latest else 50.0
    rssi = float(latest.get("wifi_rssi_dbm", -60.0)) if latest else -60.0
    phases = latest.get("phases", {}) if latest else {}

    running_cost_h = float(latest.get("running_cost_eur_per_h", tot_kw * 0.245)) if latest else 0.0
    unit_rate = float(latest.get("current_rate_eur_per_kwh", 0.245)) if latest else 0.245

    threshold_kw = float(config.get("peak_threshold_kw", 22.0))
    warn_ratio = float(config.get("warning_threshold_ratio", 0.85))

    if tot_kw >= threshold_kw:
        load_status = "BREACH"
    elif tot_kw >= threshold_kw * warn_ratio:
        load_status = "WARNING"
    else:
        load_status = "NORMAL"

    # Build timeline chronologically
    timeline: list[DashboardTimelinePoint] = []
    for item in reversed(history):
        raw_t = str(item.get("timestamp", ""))
        try:
            pt_dt = datetime.fromisoformat(raw_t.replace("Z", "+00:00"))
            time_label = pt_dt.strftime("%H:%M")
        except Exception:
            time_label = raw_t[-8:-3] if len(raw_t) >= 8 else raw_t

        timeline.append(
            DashboardTimelinePoint(
                timestamp=time_label,
                power_kw=round(float(item.get("total_active_power_kw", 0.0)), 2),
                running_cost_eur_h=round(float(item.get("running_cost_eur_per_h", 0.0)), 2),
                is_peak=bool(item.get("is_peak_window", 0)),
            )
        )

    return DashboardMetricsResponse(
        power_measurement_method=latest.get("power_measurement_method", "unknown") if latest else "unknown",
        facility_id=str(config.get("facility_id", facility_id)),
        facility_name=str(config.get("name", config.get("facility_name", facility_id))),
        facility_type=str(config.get("facility_type", "commercial")),
        contract_type=str(config.get("contract_type", "Γ22")),
        tariff_color=str(config.get("tariff_color", "green")),
        contracted_kva=float(config.get("contracted_kva", 35.0)),
        peak_threshold_kw=threshold_kw,
        notification_channel=str(config.get("notification_channel", "telegram")),
        chat_id=config.get("chat_id"),
        viber_receiver_id=config.get("viber_receiver_id"),
        total_active_power_kw=round(tot_kw, 2),
        total_apparent_power_kva=round(tot_kva, 2),
        system_power_factor=round(sys_pf, 2),
        grid_frequency_hz=round(freq, 1),
        wifi_rssi_dbm=round(rssi, 1),
        phases=phases,
        timestamp=ts_obj.isoformat(),
        running_cost_eur_per_h=round(running_cost_h, 2),
        current_rate_eur_per_kwh=round(unit_rate, 4),
        cost_today_eur=round(float(daily_summary.get("total_spend_eur", 0.0)), 2),
        kwh_today=round(float(daily_summary.get("total_kwh", 0.0)), 1),
        peak_surcharges_today_eur=round(float(daily_summary.get("peak_surcharges_eur", 0.0)), 2),
        active_zone_name=zone_name,
        is_peak_window=is_peak,
        is_offpeak_window=is_offpeak,
        load_status=load_status,
        timeline=timeline,
    )


@router.post(
    "/api/v1/dashboard/config/{facility_id}",
    summary="Update facility threshold and notification channel configuration via Dashboard",
)
def update_dashboard_config(
    facility_id: str,
    payload: DashboardConfigUpdateRequest,
    store: Annotated[SQLiteStore, Depends(get_database_store)],
    dispatcher: Annotated[AlertDispatcher, Depends(get_alert_dispatcher)],
) -> dict[str, Any]:
    """Updates peak threshold (kW) and notification channel ('telegram', 'viber', 'both') for a facility."""
    existing = store.get_facility_config(facility_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility '{facility_id}' not found.",
        )

    updated_dict = dict(existing)
    if payload.peak_threshold_kw is not None:
        updated_dict["peak_threshold_kw"] = payload.peak_threshold_kw
    if payload.notification_channel is not None:
        updated_dict["notification_channel"] = payload.notification_channel.lower()
    if payload.chat_id is not None:
        updated_dict["chat_id"] = payload.chat_id
    if payload.viber_receiver_id is not None:
        updated_dict["viber_receiver_id"] = payload.viber_receiver_id

    # Persist in SQLite store
    store.store_facility_config(updated_dict)

    # Re-register or update dispatcher state machine
    try:
        from backend.models.alert import AlertThresholdConfig
        cfg_model = AlertThresholdConfig(
            facility_id=facility_id,
            peak_threshold_kw=float(updated_dict["peak_threshold_kw"]),
            chat_id=updated_dict.get("chat_id"),
            viber_receiver_id=updated_dict.get("viber_receiver_id"),
            notification_channel=updated_dict.get("notification_channel", "telegram"),
        )
        dispatcher.register_facility(cfg_model)
    except Exception as exc:
        logger.warning("Could not re-register facility on dispatcher: %s", exc)

    return {
        "status": "success",
        "message": f"Updated settings for facility {facility_id}",
        "config": updated_dict,
    }
