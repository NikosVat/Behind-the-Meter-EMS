"""Data models and schemas for generic SME-friendly equipment scheduling.

Defines:
- GenericEquipmentAsset: Configurable arbitrary electrical assets (ovens, dishwashers, HVAC, EV chargers).
- ScheduleSettings: Facility-wide resolution (5/15/30/60m), max power limit, and objective preferences.
- GeneratedSchedule & ScheduleItem: Multi-resolution schedule results, timelines, and explanations.
- API request/response payloads with strict Pydantic v2 validation.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssetCategory(str, Enum):
    OVEN = "oven"
    DISHWASHER = "dishwasher"
    WATER_HEATER = "water_heater"
    DEFROST = "defrost"
    HVAC = "hvac"
    EV_CHARGER = "ev_charger"
    COMPRESSOR = "compressor"
    CUSTOM = "custom"


class ObjectiveMode(str, Enum):
    COST = "cost"
    PEAK = "peak"
    BALANCED = "balanced"


class ScheduleStatus(str, Enum):
    PREVIEW = "preview"
    SAVED = "saved"


def _validate_time_str(v: str) -> str:
    """Validate time string in 24-hour HH:MM format."""
    if not isinstance(v, str):
        raise TypeError("Time must be a string in HH:MM format")
    v = v.strip()
    match = re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", v)
    if not match:
        raise ValueError(f"Invalid time format '{v}'. Expected 24-hour HH:MM (e.g. '08:30', '23:15')")
    return v


class GenericEquipmentAsset(BaseModel):
    """Generic industrial/commercial electrical equipment asset."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "id" in data and "asset_id" not in data:
                data["asset_id"] = data.pop("id")
            if "is_must_run" in data and "must_run" not in data:
                data["must_run"] = data.pop("is_must_run")
            if "is_interruptible" in data and "interruptible" not in data:
                data["interruptible"] = data.pop("is_interruptible")
        return data

    asset_id: str = Field(default_factory=lambda: f"ast_{uuid.uuid4().hex[:8]}")
    facility_id: str
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="custom", max_length=50)
    rated_power_kw: float = Field(gt=0.0, description="Rated power draw in kW")
    required_runtime_minutes: int = Field(gt=0, le=1440, description="Total runtime needed within 24 hours")
    earliest_start: str = Field(default="00:00", description="Earliest permissible start time HH:MM")
    latest_finish: str = Field(default="23:59", description="Latest permissible finish time HH:MM")
    active_weekdays: list[int] = Field(
        default_factory=lambda: [0, 1, 2, 3, 4],
        description="Active days of week: 0=Monday, 6=Sunday"
    )
    must_run: bool = Field(default=True, description="Hard constraint: must complete runtime")
    interruptible: bool = Field(default=False, description="If True, runtime can be split across non-contiguous slots")
    priority: int = Field(default=3, ge=1, le=5, description="1=lowest, 5=critical operational priority")
    preferred_start: str | None = Field(default=None, description="Soft preferred start time HH:MM")
    enabled: bool = Field(default=True, description="Whether asset is included in optimization")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("earliest_start", "latest_finish")
    @classmethod
    def validate_time(cls, v: str, info) -> str:
        if v == "24:00" and info.field_name == "latest_finish":
            return v
        return _validate_time_str(v)

    @field_validator("preferred_start")
    @classmethod
    def validate_pref_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_time_str(v)

    @field_validator("active_weekdays")
    @classmethod
    def validate_weekdays(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("active_weekdays cannot be empty")
        for day in v:
            if day < 0 or day > 6:
                raise ValueError(f"Weekday must be in range 0..6, got {day}")
        return sorted(set(v))


class AssetCreateRequest(BaseModel):
    """Payload to add a new equipment asset."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=100)
    category: str = Field(default="custom", max_length=50)
    rated_power_kw: float = Field(gt=0.0)
    required_runtime_minutes: int = Field(gt=0, le=1440)
    earliest_start: str = Field(default="00:00")
    latest_finish: str = Field(default="23:59")
    active_weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    must_run: bool = True
    interruptible: bool = False
    priority: int = Field(default=3, ge=1, le=5)
    preferred_start: str | None = None
    enabled: bool = True

    @field_validator("active_weekdays")
    @classmethod
    def validate_weekdays(cls, v: list[int]) -> list[int]:
        if not v or any(day < 0 or day > 6 for day in v):
            raise ValueError("active_weekdays must contain weekdays in 0..6")
        return sorted(set(v))

    @field_validator("earliest_start", "latest_finish")
    @classmethod
    def validate_time(cls, v: str, info) -> str:
        if v == "24:00" and info.field_name == "latest_finish":
            return v
        return _validate_time_str(v)

    @field_validator("preferred_start")
    @classmethod
    def validate_pref_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_time_str(v)


class AssetUpdateRequest(BaseModel):
    """Payload to update an existing asset."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, max_length=50)
    rated_power_kw: float | None = Field(default=None, gt=0.0)
    required_runtime_minutes: int | None = Field(default=None, gt=0, le=1440)
    earliest_start: str | None = None
    latest_finish: str | None = None
    active_weekdays: list[int] | None = None
    must_run: bool | None = None
    interruptible: bool | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    preferred_start: str | None = None
    enabled: bool | None = None

    @field_validator("earliest_start", "latest_finish")
    @classmethod
    def validate_time(cls, v: str | None, info) -> str | None:
        if v == "24:00" and info.field_name == "latest_finish":
            return v
        if v is not None:
            return _validate_time_str(v)
        return v

    @field_validator("preferred_start")
    @classmethod
    def validate_pref_time(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return _validate_time_str(v)


class ScheduleSettings(BaseModel):
    """Facility-wide scheduling preferences."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = dict(data)
            if "conservative_margin" in data and "forecast_uncertainty_pct" not in data:
                val = data.pop("conservative_margin")
                data["forecast_uncertainty_pct"] = val * 100.0 if (isinstance(val, (int, float)) and val <= 1.0) else float(val)
        return data

    facility_id: str
    time_step_minutes: int = Field(default=15, description="Scheduling resolution (5, 15, 30, 60 min)")
    max_facility_power_kw: float = Field(default=25.0, gt=0.0, description="Contracted capacity limit in kW")
    objective_mode: str = Field(default="balanced", description="cost, peak, or balanced")
    timezone: str = Field(default="Europe/Athens")
    forecast_uncertainty_pct: float = Field(default=10.0, ge=0.0, le=100.0, description="Conservative baseline buffer %")
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("timezone must be an IANA timezone") from exc
        return value

    @field_validator("time_step_minutes")
    @classmethod
    def validate_resolution(cls, v: int) -> int:
        if v not in (5, 15, 30, 60):
            raise ValueError(f"Unsupported time_step_minutes: {v}. Must be one of 5, 15, 30, 60.")
        return v

    @field_validator("objective_mode")
    @classmethod
    def validate_objective(cls, v: str) -> str:
        v_clean = v.lower().strip()
        if v_clean not in ("cost", "peak", "balanced"):
            raise ValueError(f"Invalid objective_mode: {v}. Must be 'cost', 'peak', or 'balanced'.")
        return v_clean


class ScheduleSettingsUpdateRequest(BaseModel):
    """Payload to update scheduling preferences."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    time_step_minutes: int | None = None
    max_facility_power_kw: float | None = Field(default=None, gt=0.0)
    objective_mode: str | None = None
    timezone: str | None = None
    forecast_uncertainty_pct: float | None = Field(default=None, ge=0.0, le=100.0)

    @field_validator("time_step_minutes")
    @classmethod
    def validate_resolution(cls, v: int | None) -> int | None:
        if v is not None and v not in (5, 15, 30, 60):
            raise ValueError(f"Unsupported time_step_minutes: {v}. Must be one of 5, 15, 30, 60.")
        return v

    @field_validator("objective_mode")
    @classmethod
    def validate_objective(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.lower().strip()
            if v_clean not in ("cost", "peak", "balanced"):
                raise ValueError(f"Invalid objective_mode: {v}. Must be 'cost', 'peak', or 'balanced'.")
            return v_clean
        return v


class ScheduleItem(BaseModel):
    """Scheduled timing and load details for a single asset."""
    asset_id: str
    asset_name: str
    category: str
    rated_power_kw: float
    start_time: str | None = None  # HH:MM
    end_time: str | None = None    # HH:MM
    duration_minutes: int
    scheduled_slots: list[int] = Field(default_factory=list)
    scheduled_power_kw: float
    explanation: str
    preference_satisfied: bool = True
    is_omitted: bool = False

    @property
    def is_scheduled(self) -> bool:
        return not self.is_omitted

    @property
    def scheduled_duration_minutes(self) -> int:
        return self.duration_minutes

    @property
    def explanation_el(self) -> str:
        return self.explanation

    @property
    def active_slots(self) -> list[int]:
        return self.scheduled_slots


class TimelineLoad(BaseModel):
    """Time-series load profile at the configured resolution."""
    timestamps: list[str]             # List of HH:MM strings
    baseline_kw: list[float]
    conservative_baseline_kw: list[float]
    optimized_kw: list[float]
    max_power_limit_kw: float


class GeneratedSchedule(BaseModel):
    """Full 24-hour generated schedule result."""
    schedule_id: str = Field(default_factory=lambda: f"sch_{uuid.uuid4().hex[:10]}")
    facility_id: str
    schedule_date: str                 # YYYY-MM-DD
    status: str = "preview"           # preview or saved
    time_step_minutes: int = 15
    objective_mode: str = "balanced"
    input_source: str = "forecast"     # caller_supplied, measured_history, forecast, demo_profile
    is_demo: bool = False
    baseline_cost_eur: float
    optimized_cost_eur: float
    estimated_savings_eur: float
    baseline_peak_kw: float
    optimized_peak_kw: float
    uncertainty_margin_kw: float
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    items: list[ScheduleItem] = Field(default_factory=list)
    timeline_load: TimelineLoad
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def scheduled_items(self) -> list[ScheduleItem]:
        return self.items

    @property
    def timeline(self) -> list[float]:
        return self.timeline_load.optimized_kw


class SchedulePreviewRequest(BaseModel):
    """Payload to trigger a schedule preview."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schedule_date: str | None = None  # YYYY-MM-DD, defaults to today
    time_step_minutes: int | None = None
    max_facility_power_kw: float | None = Field(default=None, gt=0.0)
    objective_mode: str | None = None
    forecast_uncertainty_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    asset_overrides: list[GenericEquipmentAsset] | None = None
    baseline_load_kw: list[float] | None = None
    tariff_rates_eur_kwh: list[float] | None = None
    data_mode: Literal["auto", "demo", "telemetry"] = "auto"

    @field_validator("schedule_date")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is not None:
            return date.fromisoformat(value).isoformat()
        return value

    @field_validator("baseline_load_kw", "tariff_rates_eur_kwh")
    @classmethod
    def validate_profiles(cls, values: list[float] | None) -> list[float] | None:
        if values is not None and any(not math.isfinite(v) for v in values):
            raise ValueError("Profiles must contain finite numbers")
        return values

    @field_validator("time_step_minutes")
    @classmethod
    def validate_resolution(cls, v: int | None) -> int | None:
        if v is not None and v not in (5, 15, 30, 60):
            raise ValueError(f"Unsupported time_step_minutes: {v}. Must be one of 5, 15, 30, 60.")
        return v

    @field_validator("objective_mode")
    @classmethod
    def validate_objective(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.lower().strip()
            if v_clean not in ("cost", "peak", "balanced"):
                raise ValueError(f"Invalid objective_mode: {v}. Must be 'cost', 'peak', or 'balanced'.")
            return v_clean
        return v
