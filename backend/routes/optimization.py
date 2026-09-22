"""FastAPI REST routes for constrained load optimization and closed-loop decision support.

Exposes:
- POST /api/v1/optimization/solve: Solve rolling 24-hour MILP schedule.
- GET /api/v1/optimization/recommendations: Retrieve prioritized operational action recommendations.
- POST /api/v1/optimization/verify: Certify real telemetry against baseline counterfactual.
- GET /api/v1/optimization/status: Engine operational status and solver capability metadata.
- GET /api/v1/optimization/verifications: Retrieve audit log of verified interventions.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.database.sqlite_store import SQLiteStore
from backend.routes.telemetry import get_database_store
from optimization_engine.decision_support import (
    ClosedLoopVerifier,
    DecisionSupportEngine,
)
from optimization_engine.models import (
    ActionRecommendation,
    BESSLoad,
    DefrostLoad,
    HVACLoad,
    OptimizationProblem,
    ProductionBatchLoad,
    VerificationRecord,
)
from optimization_engine.solver import ConstrainedLoadSolver

router = APIRouter(prefix="/optimization", tags=["Optimization & Decision Support"])

# In-memory recommendation and verification registry partitioned by facility_id
_active_recommendations: dict[str, list[ActionRecommendation]] = {}
_active_verifications: dict[str, list[VerificationRecord]] = {}


class SolveRequest(BaseModel):
    facility_id: str = "fac_bakery_01"
    contracted_capacity_kw: float = Field(default=35.0, gt=0.0)
    baseline_load_kw: list[float] | None = None
    tariff_rates_eur_kwh: list[float] | None = None
    include_defrost: bool = True
    include_batch_ovens: bool = True
    include_hvac: bool = True
    include_bess: bool = True

    @field_validator("baseline_load_kw")
    @classmethod
    def validate_baseline(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) != 24:
            raise ValueError(f"baseline_load_kw must have exactly 24 hourly entries, received {len(v)}")
        return v

    @field_validator("tariff_rates_eur_kwh")
    @classmethod
    def validate_tariffs(cls, v: list[float] | None) -> list[float] | None:
        if v is not None and len(v) != 24:
            raise ValueError(f"tariff_rates_eur_kwh must have exactly 24 hourly entries, received {len(v)}")
        return v


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation_id: str
    start_reading_id: int = Field(gt=0)
    end_reading_id: int = Field(gt=0)
    counterfactual_baseline_kw: float = Field(ge=0, allow_inf_nan=False)
    tariff_eur_kwh: float = Field(allow_inf_nan=False)


@router.get("/status")
def get_optimization_status():
    """Return optimization engine capabilities and health status."""
    total_recs = sum(len(recs) for recs in _active_recommendations.values())
    total_vers = sum(len(vers) for vers in _active_verifications.values())
    return {
        "status": "operational",
        "solver_backend": "scipy_highs_milp",
        "paradigm": "Measure -> Predict -> Optimize -> Act -> Verify",
        "supported_constraints": [
            "flexible_refrigeration_defrost",
            "production_batch_deck_ovens",
            "hvac_thermal_comfort_deadbands",
            "bess_soc_and_power_limits",
            "contracted_capacity_surcharge_avoidance",
        ],
        "active_recommendations_count": total_recs,
        "verified_interventions_count": total_vers,
    }


@router.post("/solve", response_model=dict)
def solve_schedule(req: SolveRequest):
    """Solve multi-period rolling constrained load scheduling problem."""
    # Build default commercial profile if not provided
    baseline = req.baseline_load_kw or [
        8.0, 7.5, 7.0, 6.8, 12.0, 18.5, 22.0, 24.5,
        26.0, 28.0, 27.5, 25.0, 24.0, 26.5, 29.0, 31.5,
        28.0, 22.0, 18.0, 15.0, 14.0, 12.5, 10.0, 8.5,
    ]

    # Default day-ahead spot / yellow tariff curve with afternoon peak
    tariffs = req.tariff_rates_eur_kwh or [
        0.095, 0.088, 0.082, 0.080, 0.085, 0.110, 0.145, 0.180,
        0.195, 0.210, 0.225, 0.240, 0.255, 0.285, 0.310, 0.290,
        0.245, 0.210, 0.190, 0.175, 0.150, 0.135, 0.115, 0.100,
    ]

    defrost_loads = [DefrostLoad()] if req.include_defrost else []
    batch_loads = [ProductionBatchLoad()] if req.include_batch_ovens else []
    hvac_loads = [HVACLoad()] if req.include_hvac else []
    bess = BESSLoad() if req.include_bess else None

    problem = OptimizationProblem(
        horizon_hours=24,
        time_step_hours=1.0,
        baseline_load_kw=baseline,
        tariff_rates_eur_kwh=tariffs,
        contracted_capacity_kw=req.contracted_capacity_kw,
        defrost_loads=defrost_loads,
        hvac_loads=hvac_loads,
        batch_loads=batch_loads,
        bess=bess,
    )

    solver = ConstrainedLoadSolver(problem)
    res = solver.solve()

    # Generate decision support recommendations
    engine = DecisionSupportEngine(facility_id=req.facility_id)
    recs = engine.generate_recommendations(problem, res)

    # Store active recommendations partitioned by facility_id
    _active_recommendations[req.facility_id] = recs

    return {
        "status": res.status,
        "is_optimal": res.is_optimal,
        "operationally_feasible": res.operationally_feasible,
        "comfort_violation_c": res.comfort_violation_c,
        "input_source": {
            "baseline": "caller_supplied" if req.baseline_load_kw is not None else "demo_profile",
            "tariff": "caller_supplied" if req.tariff_rates_eur_kwh is not None else "demo_profile",
        },
        "horizon_hours": res.horizon_hours,
        "baseline_cost_eur": res.baseline_cost_eur,
        "optimized_cost_eur": res.optimized_cost_eur,
        "savings_eur": res.savings_eur,
        "savings_pct": res.savings_pct,
        "peak_baseline_kw": res.peak_baseline_kw,
        "peak_optimized_kw": res.peak_optimized_kw,
        "peak_reduction_kw": res.peak_reduction_kw,
        "capacity_breached_baseline": res.capacity_breached_baseline,
        "capacity_breached_optimized": res.capacity_breached_optimized,
        "solve_time_ms": res.solve_time_ms,
        "device_schedules": res.device_schedules,
        "hvac_temperatures": res.hvac_temperatures,
        "bess_soc_history": res.bess_soc_history,
        "recommendations_count": len(recs),
        "recommendations": [r.model_dump() for r in recs],
    }


@router.get("/recommendations", response_model=list[ActionRecommendation])
def get_recommendations(facility_id: str | None = Query(None)):
    """Retrieve active decision-support recommendations with optional facility filtering."""
    if facility_id:
        return _active_recommendations.get(facility_id, [])
    return [rec for recs in _active_recommendations.values() for rec in recs]


@router.post("/verify", response_model=VerificationRecord)
def verify_intervention(req: VerificationRequest, store: SQLiteStore = Depends(get_database_store)):
    """Compare recorded interval energy with an explicitly unvalidated counterfactual.
    
    Raises 404 if the recommendation_id is not found in the active registry.
    """
    rec: ActionRecommendation | None = None
    for facility_recs in _active_recommendations.values():
        for r in facility_recs:
            if r.recommendation_id == req.recommendation_id:
                rec = r
                break
        if rec:
            break

    if not rec:
        raise HTTPException(
            status_code=404,
            detail=f"Recommendation '{req.recommendation_id}' not found. Cannot verify non-existent intervention.",
        )

    try:
        readings = store.get_telemetry_interval(rec.facility_id, req.start_reading_id, req.end_reading_id)
        first, last = readings[0], readings[-1]
        start = datetime.fromisoformat(first["timestamp"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Verification requires timezone-aware timestamps")
        duration = (end - start).total_seconds() / 3600
        if duration <= 0:
            raise ValueError("Verification interval must have positive duration")
        actual_kw = (last["cumulative_energy_kwh"] - first["cumulative_energy_kwh"]) / duration
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    verifier = ClosedLoopVerifier()
    record = verifier.verify_intervention(
        recommendation=rec,
        actual_measured_kw=actual_kw,
        counterfactual_baseline_kw=req.counterfactual_baseline_kw,
        tariff_eur_kwh=req.tariff_eur_kwh,
        duration_hours=duration,
    )
    record.evidence_source = "stored_telemetry"
    methods = {row["power_measurement_method"] for row in readings}
    record.power_measurement_method = methods.pop() if len(methods) == 1 else "mixed"
    record.start_reading_id = req.start_reading_id
    record.end_reading_id = req.end_reading_id

    _active_verifications.setdefault(rec.facility_id, []).append(record)
    return record


@router.get("/verifications", response_model=list[VerificationRecord])
def get_verifications(facility_id: str | None = Query(None)):
    """Retrieve history of certified closed-loop intervention audits."""
    if facility_id:
        return _active_verifications.get(facility_id, [])
    return [v for vers in _active_verifications.values() for v in vers]
