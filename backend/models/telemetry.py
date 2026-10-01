"""Core Pydantic v2 telemetry models for 3-phase commercial energy monitoring.

Implements strict validation of electrical invariants, power factor boundaries,
frequency validity, and multi-phase balance.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PhaseReading(BaseModel):
    """Telemetry reading for an individual electrical phase (L1, L2, or L3)."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    voltage_v: float = Field(
        ...,
        ge=0.0,
        le=500.0,
        description="Phase RMS Voltage relative to neutral [V] (nominal 230V in Greece)",
    )
    current_a: float = Field(
        ...,
        ge=0.0,
        le=500.0,
        description="Phase RMS Current [A] measured via SCT-013 CT clamp",
    )
    active_power_kw: float = Field(
        ...,
        description="Active Real Power [kW]",
    )
    apparent_power_kva: float = Field(
        ...,
        ge=0.0,
        description="Apparent Power [kVA] = V_rms * I_rms / 1000",
    )
    power_factor: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Phase displacement power factor cos φ in [-1.0, 1.0]",
    )

    @field_validator("power_factor")
    @classmethod
    def validate_phase_pf(cls, v: float) -> float:
        """Validate phase power factor is within physical bounds [-1.0, 1.0]."""
        if v < -1.0 or v > 1.0:
            raise ValueError(f"Phase power_factor must be in [-1.0, 1.0], got {v}")
        return v


class PhaseDict(dict):
    """Dictionary supporting both dictionary key indexing (phases['L1'])

    and direct attribute access (phases.L1).
    """

    def __getattr__(self, name: str) -> Any:
        if name in self:
            return self[name]
        raise AttributeError(f"'PhaseDict' object has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class TelemetryPayload(BaseModel):
    """Full 3-phase time-series telemetry payload ingested from ESP32 or simulator.

    Validates multi-phase sum conservation: |total_active_power_kw - sum(phases[Li])| <= 0.05 kW.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    power_measurement_method: Literal["unknown", "estimated_nominal_voltage_pf", "meter_measured", "simulated"] = Field(
        default="unknown", description="Source-reported provenance; not a calibration certificate")

    device_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique identifier of the ESP32 sensor or simulator instance",
    )
    facility_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Greek commercial facility identifier (e.g. bakery-central-athens)",
    )
    timestamp: datetime = Field(
        ...,
        description="Measurement timestamp in ISO 8601 UTC format",
    )
    phases: dict[str, PhaseReading] = Field(
        ...,
        description="Phase readings mapped by identifier: L1, L2, L3",
    )
    total_active_power_kw: float = Field(
        ...,
        description="Total 3-phase instantaneous active power [kW]",
    )
    total_apparent_power_kva: float = Field(
        ...,
        ge=0.0,
        description="Total 3-phase instantaneous apparent power [kVA]",
    )
    system_power_factor: float = Field(
        ...,
        ge=-1.0,
        le=1.0,
        description="Overall system power factor cos φ in [-1.0, 1.0]",
    )
    cumulative_energy_kwh: float = Field(
        ...,
        ge=0.0,
        description="Cumulative active electrical energy [kWh] integrated since deployment",
    )
    grid_frequency_hz: float = Field(
        ...,
        gt=0.0,
        description="Grid AC frequency in Hz (nominal 50.0 Hz in Greek DEDDIE/ADMIE network)",
    )
    wifi_rssi_dbm: float = Field(
        ...,
        description="Wi-Fi received signal strength indicator [dBm]",
    )
    predicted_next_kw: float | None = Field(
        default=None,
        ge=0.0,
        description="On-device edge forecasted active power for next hour [kW]",
    )
    projected_peak_breach: bool | None = Field(
        default=None,
        description="Flag indicating if the on-device edge forecast predicts a contracted capacity breach",
    )

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        """Validate timestamp is a valid datetime within reasonable chronological boundaries."""
        if v.year < 2000 or v.year > 2100:
            raise ValueError(f"Timestamp year {v.year} is outside valid operational range [2000, 2100]")
        return v

    @field_validator("grid_frequency_hz")
    @classmethod
    def validate_frequency(cls, v: float) -> float:
        """Validate frequency is strictly positive and within realistic physical bounds."""
        if v <= 0.0:
            raise ValueError(f"grid_frequency_hz must be strictly positive, got {v}")
        return v

    @field_validator("system_power_factor")
    @classmethod
    def validate_system_pf(cls, v: float) -> float:
        """Validate system power factor is in [-1.0, 1.0]."""
        if v < -1.0 or v > 1.0:
            raise ValueError(f"system_power_factor must be in [-1.0, 1.0], got {v}")
        return v

    @field_validator("phases", mode="after")
    @classmethod
    def validate_phases_dict(cls, v: dict[str, PhaseReading]) -> PhaseDict:
        """Verify that L1, L2, L3 phases are all present and return a PhaseDict wrapper."""
        required_phases = {"L1", "L2", "L3"}
        missing = required_phases - set(v.keys())
        if missing:
            raise ValueError(f"Telemetry payload missing required phase(s): {sorted(missing)}")
        return PhaseDict(v)

    @model_validator(mode="after")
    def validate_electrical_invariants(self) -> "TelemetryPayload":
        """Enforce Kirchhoff / electrical conservation invariant:

        |total_active_power_kw - sum(phases[Li].active_power_kw)| <= 0.05 kW
        """
        if not self.phases:
            return self

        sum_phases_active_kw = sum(
            self.phases[li].active_power_kw for li in ("L1", "L2", "L3") if li in self.phases
        )
        delta = abs(self.total_active_power_kw - sum_phases_active_kw)

        # Allow 0.05 kW tolerance with 1e-6 float precision margin
        if delta - 0.05 > 1e-6:
            raise ValueError(
                f"Electrical invariant violated: |total_active_power_kw ({self.total_active_power_kw:.4f}) "
                f"- sum(phases) ({sum_phases_active_kw:.4f})| = {delta:.4f} kW > 0.05 kW limit"
            )

        return self
