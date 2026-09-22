"""Telemetry ingestion REST endpoint and real-time alert dispatching.

Handles:
- POST /api/v1/telemetry
- Pydantic v2 validation and electrical invariant enforcement
- SQLite time-series storage with WAL mode
- Deterministic real-time cost calculation via tariff_engine
- Debounced, throttled proactive alert dispatching via AlertDispatcher
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from backend.database.sqlite_store import SQLiteStore, get_store
from backend.models.telemetry import TelemetryPayload
from tariff_engine import (
    calculate_realtime_cost,
    resolve_effective_tea,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telemetry"])


# --- Alert Dispatcher (Harmonized with bot.dispatcher) ---
from bot.dispatcher import AlertDispatcher

# Global singleton dispatcher
_default_dispatcher = AlertDispatcher()


def get_alert_dispatcher(request: Request) -> AlertDispatcher:
    """Dependency injector for AlertDispatcher."""
    if hasattr(request.app.state, "dispatcher") and request.app.state.dispatcher is not None:
        return request.app.state.dispatcher
    return _default_dispatcher


def get_database_store(request: Request) -> SQLiteStore:
    """Dependency injector for SQLiteStore."""
    db_path = getattr(request.app.state, "db_path", None)
    return get_store(db_path)


# --- Ingestion Response Schema ---

class IngestionResponse(BaseModel):
    power_measurement_method: str = "unknown"
    status: str = "success"
    reading_id: int
    facility_id: str
    device_id: str
    timestamp: datetime
    total_active_power_kw: float
    total_apparent_power_kva: float
    system_power_factor: float
    running_cost_eur_per_h: float
    current_rate_eur_per_kwh: float
    incremental_cost_eur: float
    is_peak_window: bool
    is_excess_breach: bool
    excess_power_kw: float
    projected_excess_penalty_eur: float
    alert_triggered: bool = False
    alert_dispatched: bool = False
    alert_event: dict[str, Any] | None = None


# --- Ingestion Endpoint ---

@router.post(
    "/telemetry",
    response_model=IngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest multi-phase electrical telemetry reading",
)
async def ingest_telemetry(
    payload: TelemetryPayload,
    store: Annotated[SQLiteStore, Depends(get_database_store)],
    dispatcher: Annotated[AlertDispatcher, Depends(get_alert_dispatcher)],
    request: Request,
) -> IngestionResponse:
    """Ingests 3-phase CT-clamp telemetry from ESP32 or simulator:

    1. Validates schema and Kirchhoff electrical invariants (|P_tot - sum(P_i)| <= 0.05 kW).
    2. Calculates incremental energy delta and instantaneous cost via Greek tariff engine.
    3. Persists time-series reading and daily aggregates in SQLite WAL storage.
    4. Evaluates peak-demand breaches and dispatches throttled Greek alerts via Telegram.
    """
    # Enforce apparent vs active power physical relationship
    if payload.total_apparent_power_kva < payload.total_active_power_kw - 0.051:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Physical invariant violation: total_apparent_power_kva "
                f"({payload.total_apparent_power_kva}) cannot be less than "
                f"total_active_power_kw ({payload.total_active_power_kw})"
            ),
        )

    # Resolve facility profile
    facility_config = store.get_facility_config(payload.facility_id)
    if not facility_config:
        # Auto-provision standard commercial configuration if not yet registered
        facility_config = {
            "facility_id": payload.facility_id,
            "name": payload.facility_id,
            "facility_type": "commercial",
            "contract_type": "Γ22",
            "tariff_color": "green",
            "contracted_kva": 35.0,
            "peak_threshold_kw": 22.0,
            "warning_threshold_ratio": 0.85,
            "low_pf_threshold": 0.85,
            "cooldown_seconds": 1800,
            "hysteresis_factor": 0.90,
            "debounce_samples": 3,
        }
        store.store_facility_config(facility_config)

    # Compute energy delta from previous reading
    prev_reading = store.get_latest_telemetry(payload.facility_id)
    if prev_reading and prev_reading.get("cumulative_energy_kwh") is not None:
        energy_delta = max(
            0.0, payload.cumulative_energy_kwh - prev_reading["cumulative_energy_kwh"]
        )
    else:
        energy_delta = 0.0

    # Resolve dynamic market rate via market feed adapter
    market_svc = None
    if request is not None and hasattr(request, "app") and hasattr(request.app.state, "market_service"):
        market_svc = request.app.state.market_service
    if market_svc is None:
        from backend.market.service import get_market_service
        market_svc = get_market_service(store=store)

    effective_tea = resolve_effective_tea(
        timestamp=payload.timestamp,
        tariff_color=facility_config.get("tariff_color", "green"),
        contract_type=facility_config.get("contract_type", "G22"),
        supplier_id=facility_config.get("supplier_id", "dei"),
        market_service=market_svc,
    )

    # Calculate real-time electricity cost via tariff engine
    profile_obj = SimpleNamespace(**facility_config)
    cost_res = calculate_realtime_cost(
        power_kw=payload.total_active_power_kw,
        energy_kwh_delta=energy_delta,
        timestamp=payload.timestamp,
        tariff_profile=profile_obj,
        tea_eur_mwh=effective_tea,
        power_factor=payload.system_power_factor,
        contracted_kva=facility_config.get("contracted_kva", 35.0),
    )

    # Store telemetry in SQLite WAL store
    reading_id = store.store_telemetry(payload, cost_res)

    # Process alert evaluation
    alert_event = await dispatcher.process_telemetry(payload, cost_res, facility_config)

    return IngestionResponse(
        power_measurement_method=payload.power_measurement_method,
        status="success",
        reading_id=reading_id,
        facility_id=payload.facility_id,
        device_id=payload.device_id,
        timestamp=payload.timestamp,
        total_active_power_kw=payload.total_active_power_kw,
        total_apparent_power_kva=payload.total_apparent_power_kva,
        system_power_factor=payload.system_power_factor,
        running_cost_eur_per_h=cost_res.running_cost_eur_per_h,
        current_rate_eur_per_kwh=cost_res.current_rate_eur_per_kwh,
        incremental_cost_eur=cost_res.incremental_cost_eur,
        is_peak_window=cost_res.is_peak_window,
        is_excess_breach=cost_res.is_excess_breach,
        excess_power_kw=cost_res.excess_power_kw,
        projected_excess_penalty_eur=cost_res.projected_excess_penalty_eur,
        alert_triggered=alert_event is not None,
        alert_dispatched=alert_event.dispatched if alert_event else False,
        alert_event=alert_event.model_dump() if alert_event else None,
    )
