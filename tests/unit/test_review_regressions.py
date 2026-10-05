"""Regressions for scheduling, evidence provenance, and interval valuation."""
import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import SQLiteStore
from backend.main import create_app
from backend.models.telemetry import TelemetryPayload
from optimization_engine.decision_support import ClosedLoopVerifier, DecisionSupportEngine
from optimization_engine.models import DefrostLoad, HVACLoad, OptimizationProblem, ProductionBatchLoad
from optimization_engine.solver import ConstrainedLoadSolver


@pytest.mark.parametrize("kind", ["batch", "defrost"])
def test_required_cycle_cannot_be_truncated_at_midnight(kind):
    kwargs = ({"batch_loads": [ProductionBatchLoad(earliest_start_hour=23, latest_start_hour=23)]}
              if kind == "batch" else
              {"defrost_loads": [DefrostLoad(nominal_start_hour=23, max_shift_hours=0, duration_hours=2)]})
    result = ConstrainedLoadSolver(OptimizationProblem(**kwargs)).solve()
    assert not result.is_optimal
    assert result.status == "FAILED"


def test_late_cycle_moves_earlier_to_complete():
    problem = OptimizationProblem(batch_loads=[ProductionBatchLoad(earliest_start_hour=22, latest_start_hour=23)])
    result = ConstrainedLoadSolver(problem).solve()
    assert result.is_optimal
    assert sum(x > 0 for x in result.device_schedules[problem.batch_loads[0].name]) == 2


def test_violating_comfort_blocks_operational_recommendations():
    problem = OptimizationProblem(hvac_loads=[HVACLoad(initial_temp_c=30, ambient_temp_forecast=[40]*24, max_cooling_kw=0)],
                                  batch_loads=[ProductionBatchLoad()], tariff_rates_eur_kwh=[.3]*6+[.1]*18)
    result = ConstrainedLoadSolver(problem).solve()
    assert result.status == "COMFORT_VIOLATION"
    assert result.comfort_violation_c > 10
    assert not result.operationally_feasible
    assert DecisionSupportEngine().generate_recommendations(problem, result) == []


def recommendation():
    problem = OptimizationProblem(batch_loads=[ProductionBatchLoad()], tariff_rates_eur_kwh=[.4]*6+[.1]*18)
    result = ConstrainedLoadSolver(problem).solve()
    return DecisionSupportEngine().generate_recommendations(problem, result)[0]


def test_confidence_is_unknown_without_calibration():
    assert recommendation().confidence_score is None


def test_quarter_hour_savings_and_no_repeated_demand_charge():
    result = ClosedLoopVerifier().verify_intervention(recommendation(), 28, 40, .2, duration_hours=.25)
    assert result.actual_savings_eur == .6  # 12 kW * 0.25 h * EUR 0.2/kWh
    assert result.duration_hours == .25
    assert result.is_certified is False
    assert result.accuracy_pct is None  # Interval energy is not comparable to whole-plan savings.


def test_missing_duration_is_not_assumed_to_be_one_hour():
    with pytest.raises(TypeError):
        ClosedLoopVerifier().verify_intervention(recommendation(), 28, 40, .2)


def test_estimated_power_provenance_survives_storage(tmp_path, valid_telemetry_dict):
    valid_telemetry_dict["power_measurement_method"] = "estimated_nominal_voltage_pf"
    payload = TelemetryPayload.model_validate(valid_telemetry_dict)
    store = SQLiteStore(str(tmp_path / "evidence.db"))
    store.store_telemetry(payload)
    assert store.get_latest_telemetry(payload.facility_id)["power_measurement_method"] == "estimated_nominal_voltage_pf"


def test_unattributed_legacy_readings_are_not_assumed_measured(tmp_path, valid_telemetry_dict):
    store = SQLiteStore(str(tmp_path / "legacy.db"))
    store.store_telemetry(valid_telemetry_dict)
    assert store.get_latest_telemetry(valid_telemetry_dict["facility_id"])["power_measurement_method"] == "unknown"


def test_verification_rejects_caller_invented_actual_power(tmp_path):
    with TestClient(create_app(str(tmp_path / "api.db"))) as client:
        rec = client.post("/api/v1/optimization/solve", json={}).json()["recommendations"][0]
        resp = client.post("/api/v1/optimization/verify", json={
            "recommendation_id": rec["recommendation_id"], "actual_measured_kw": 0,
            "counterfactual_baseline_kw": 40, "duration_hours": 1})
        assert resp.status_code == 422


@pytest.mark.parametrize("invalid", ["other_facility", "other_device", "reset", "reversed"])
def test_verification_interval_rejects_invalid_evidence(tmp_path, valid_telemetry_dict, invalid):
    store = SQLiteStore(str(tmp_path / "interval.db"))
    data = dict(valid_telemetry_dict, timestamp="2026-09-22T10:00:00+00:00", cumulative_energy_kwh=100)
    start = store.store_telemetry(data)
    data.update(timestamp="2026-09-22T10:15:00+00:00", cumulative_energy_kwh=101)
    if invalid == "other_facility":
        data["facility_id"] = "other"
    elif invalid == "other_device":
        data["device_id"] = "other"
    elif invalid == "reset":
        data["cumulative_energy_kwh"] = 1
    end = store.store_telemetry(data)
    if invalid == "reversed":
        start, end = end, start
    with pytest.raises(ValueError):
        store.get_telemetry_interval(valid_telemetry_dict["facility_id"], start, end)


def test_database_upgrade_preserves_existing_rows(tmp_path, valid_telemetry_dict):
    import sqlite3

    from backend.database.sqlite_store import SCHEMA_SQL
    path = str(tmp_path / "upgrade.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
    store = SQLiteStore(path)
    store.store_telemetry(valid_telemetry_dict)
    store.init_db()  # Repeated startup must be idempotent.
    assert store.get_latest_telemetry(valid_telemetry_dict["facility_id"])["power_measurement_method"] == "unknown"


@pytest.mark.parametrize("duration", [0, -1, float("nan"), float("inf")])
def test_invalid_interval_duration_is_rejected(duration):
    with pytest.raises(ValueError):
        ClosedLoopVerifier().verify_intervention(recommendation(), 28, 40, .2, duration_hours=duration)


def test_increased_energy_cost_is_not_hidden_as_zero_savings():
    record = ClosedLoopVerifier().verify_intervention(recommendation(), 42, 40, .2, duration_hours=.25)
    assert record.actual_savings_eur == -.1
