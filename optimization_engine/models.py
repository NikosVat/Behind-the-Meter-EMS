"""Data models and schemas for constrained load optimization and decision support.

Defines:
- Equipment operational constraint models (defrost cycles, HVAC thermal comfort deadbands,
  bakery batch ovens, battery energy storage systems).
- Optimization problem formulation schemas.
- Mathematical optimization results and schedule profiles.
- Decision support recommendations and closed-loop verification records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class RecommendationCategory(str, Enum):
    DEFROST_SHIFT = "defrost_shift"
    HVAC_PRECOOLING = "hvac_precooling"
    BATCH_SCHEDULING = "batch_scheduling"
    BESS_ARBITRAGE = "bess_arbitrage"
    PEAK_SHAVING = "peak_shaving"


class PriorityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


class VerificationStatus(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    OVERRIDDEN = "overridden"


# --- Physical Asset Constraint Models ---

@dataclass
class DefrostLoad:
    """Flexible commercial refrigeration defrost cycle.
    
    In commercial cold storage or supermarket freezers, defrost heaters (electrical
    elements 3-10 kW) must run periodically to prevent evaporator icing. Defrost can
    be shifted forward or backward within a thermal safety window (e.g. +/- 60-120 min)
    to avoid high-tariff spot peaks or contracted capacity breaches without compromising
    stored food quality.
    """
    name: str = "Refrigeration Defrost Rack A"
    nominal_start_hour: int = 14
    duration_hours: int = 1
    power_kw: float = 6.8
    max_shift_hours: int = 2  # can run in [nominal - max_shift, nominal + max_shift]
    must_run: bool = True


@dataclass
class HVACLoad:
    """Commercial HVAC zone with thermal inertia and comfort deadband constraints.
    
    Models room temperature dynamics:
    T[t+1] = T[t] + alpha * (T_ambient[t] - T[t]) - beta * P_cooling[t]
    Subject to:
    T_min <= T[t] <= T_max (comfort deadband, e.g. 21.0C to 24.5C)
    """
    name: str = "HVAC Main Floor"
    initial_temp_c: float = 22.0
    temp_min_c: float = 20.0
    temp_max_c: float = 24.5
    ambient_temp_forecast: list[float] = field(default_factory=lambda: [
        22.0, 21.5, 21.0, 20.5, 20.5, 21.0, 23.0, 25.0,
        28.0, 31.0, 33.5, 35.0, 36.0, 36.5, 35.5, 34.0,
        32.0, 30.0, 28.0, 26.5, 25.0, 24.0, 23.0, 22.5
    ])
    thermal_loss_factor: float = 0.08      # alpha: rate of heat exchange with ambient
    cooling_power_factor: float = 0.35     # beta: degrees C reduction per kW per hour
    max_cooling_kw: float = 10.0
    cop: float = 3.2                       # Coefficient of Performance


@dataclass
class ProductionBatchLoad:
    """Industrial batch process with fixed duration and contiguous operation (e.g. bakery deck oven).
    
    Must run for exactly `duration_hours` contiguous hours starting within the permissible
    window [earliest_start_hour, latest_start_hour].
    """
    name: str = "Deck Oven Batch #2"
    duration_hours: int = 2
    power_kw: float = 14.5
    earliest_start_hour: int = 4
    latest_start_hour: int = 8
    must_run: bool = True


@dataclass
class BESSLoad:
    """Battery Energy Storage System (BESS) for peak shaving and price arbitrage.
    
    Tracks State of Charge (SOC) subject to power and capacity bounds:
    E[t+1] = E[t] + (eta_chg * P_chg[t] - (1/eta_dis) * P_dis[t]) * delta_t
    """
    name: str = "Commercial BESS 20kWh"
    capacity_kwh: float = 20.0
    max_charge_kw: float = 5.0
    max_discharge_kw: float = 5.0
    charge_efficiency: float = 0.94
    discharge_efficiency: float = 0.94
    min_soc: float = 0.15     # 15% minimum reserve
    max_soc: float = 0.95     # 95% maximum battery longevity limit
    initial_soc: float = 0.40
    target_final_soc: float = 0.40


@dataclass
class OptimizationProblem:
    """Complete 24-hour rolling horizon optimization problem specification."""
    horizon_hours: int = 24
    time_step_hours: float = 1.0
    baseline_load_kw: list[float] = field(default_factory=lambda: [10.0] * 24)
    tariff_rates_eur_kwh: list[float] = field(default_factory=lambda: [0.15] * 24)
    contracted_capacity_kw: float = 35.0
    contracted_demand_rate_eur_per_kw: float = 0.0
    capacity_penalty_eur_per_kw: float = 18.50  # DEDDIE capacity excess demand surcharge
    defrost_loads: list[DefrostLoad] = field(default_factory=list)
    hvac_loads: list[HVACLoad] = field(default_factory=list)
    batch_loads: list[ProductionBatchLoad] = field(default_factory=list)
    bess: BESSLoad | None = None


@dataclass
class ScheduleResult:
    """Results from SciPy MILP optimization solver."""
    status: str
    is_optimal: bool
    horizon_hours: int
    baseline_total_load_kw: list[float]
    optimized_total_load_kw: list[float]
    baseline_cost_eur: float
    optimized_cost_eur: float
    savings_eur: float
    savings_pct: float
    peak_baseline_kw: float
    peak_optimized_kw: float
    peak_reduction_kw: float
    capacity_breached_baseline: bool
    capacity_breached_optimized: bool
    device_schedules: dict[str, list[float]] = field(default_factory=dict)
    hvac_temperatures: dict[str, list[float]] = field(default_factory=dict)
    bess_soc_history: list[float] = field(default_factory=list)
    solve_time_ms: float = 0.0
    operationally_feasible: bool = True
    comfort_violation_c: float = 0.0


# --- Decision Support & Closed-Loop Verification Models ---

class ActionRecommendation(BaseModel):
    """Actionable operational recommendation generated by the decision support engine."""
    recommendation_id: str
    facility_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    category: RecommendationCategory
    priority: PriorityLevel
    status: RecommendationStatus = RecommendationStatus.PENDING
    title: str
    description_el: str
    description_en: str
    asset_name: str
    original_window: str
    recommended_window: str
    peak_load_avoided_kw: float
    estimated_savings_eur: float
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0,
                                             description="Unknown until calibrated against observed outcomes")
    contracted_capacity_kw: float
    projected_peak_kw: float


class VerificationRecord(BaseModel):
    """Post-intervention telemetry audit validating whether recommended savings were achieved."""
    verification_id: str
    recommendation_id: str
    facility_id: str
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: VerificationStatus
    baseline_counterfactual_kw: float
    actual_measured_kw: float
    actual_load_avoided_kw: float
    estimated_savings_eur: float
    actual_savings_eur: float
    accuracy_pct: float | None = None
    summary_el: str
    summary_en: str
    duration_hours: float = Field(gt=0, allow_inf_nan=False)
    is_certified: bool = False
    evidence_source: str = "caller_supplied"
    baseline_source: str = "caller_supplied_counterfactual"
    valuation_scope: str = "interval_energy_only; excludes demand charges and shifted-load rebound"
    power_measurement_method: str = "unknown"
    start_reading_id: int | None = None
    end_reading_id: int | None = None
