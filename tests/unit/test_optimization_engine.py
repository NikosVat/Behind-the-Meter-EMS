"""Unit and integration tests for Constrained Load Optimization and Decision-Support Engine.

Validates:
1. Equipment operational constraint models (Defrost, HVAC deadbands, Batch ovens, BESS).
2. SciPy MILP HiGHS solver optimality, energy balance, and speed (<100ms).
3. Peak capacity breach avoidance and demand charge reduction.
4. DecisionSupportEngine actionable recommendation generation and Greek/English localization.
5. ClosedLoopVerifier post-intervention audit and accuracy classification.
6. FastAPI REST endpoints under /api/v1/optimization.
"""

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from optimization_engine.models import (
    ActionRecommendation,
    BESSLoad,
    DefrostLoad,
    HVACLoad,
    OptimizationProblem,
    PriorityLevel,
    ProductionBatchLoad,
    RecommendationCategory,
    ScheduleResult,
    VerificationStatus,
)
from optimization_engine.solver import ConstrainedLoadSolver
from optimization_engine.decision_support import ClosedLoopVerifier, DecisionSupportEngine


# --- 1. Equipment Constraint & Solver Tests ---

class TestConstrainedLoadSolver:
    """Mathematical validation of the SciPy HiGHS MILP scheduler."""

    def test_defrost_shift_to_cheaper_tariff_window(self):
        """Verify defrost cycle shifts from high-tariff nominal hour to low-tariff hour."""
        # High tariff at hour 14 (0.35 €/kWh), low tariff at hour 16 (0.10 €/kWh)
        tariffs = [0.15] * 24
        tariffs[14] = 0.40  # nominal start
        tariffs[16] = 0.10  # allowed shift target

        defrost = DefrostLoad(
            name="Freezer Rack A",
            nominal_start_hour=14,
            duration_hours=1,
            power_kw=8.0,
            max_shift_hours=2,  # can run in [12, 16]
        )
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[5.0] * 24,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=40.0,
            defrost_loads=[defrost],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        assert res.is_optimal is True
        assert res.device_schedules["Freezer Rack A"][16] == 8.0
        assert res.device_schedules["Freezer Rack A"][14] == 0.0
        assert res.savings_eur > 0.0

    def test_production_batch_oven_contiguous_running(self):
        """Verify bakery batch oven runs contiguously for duration_hours within allowed window."""
        tariffs = [0.20] * 24
        # Lower tariff window at hours 6-7
        tariffs[6] = 0.08
        tariffs[7] = 0.08

        batch = ProductionBatchLoad(
            name="Bakery Deck Oven",
            duration_hours=2,
            power_kw=15.0,
            earliest_start_hour=4,
            latest_start_hour=8,
        )
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[10.0] * 24,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=50.0,
            batch_loads=[batch],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        assert res.is_optimal is True
        schedule = res.device_schedules["Bakery Deck Oven"]
        active_hours = [t for t, p in enumerate(schedule) if p > 0.1]
        
        # Must run exactly 2 hours contiguously
        assert len(active_hours) == 2
        assert active_hours == [6, 7]
        assert active_hours[1] == active_hours[0] + 1

    def test_hvac_thermal_comfort_deadband_compliance(self):
        """Verify room temperature stays strictly within [T_min, T_max] comfort boundaries."""
        hvac = HVACLoad(
            name="Cold Room Chiller",
            initial_temp_c=22.0,
            temp_min_c=19.0,
            temp_max_c=23.5,
            thermal_loss_factor=0.10,
            cooling_power_factor=0.40,
            max_cooling_kw=8.0,
        )
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[8.0] * 24,
            tariff_rates_eur_kwh=[0.18] * 24,
            contracted_capacity_kw=30.0,
            hvac_loads=[hvac],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        assert res.is_optimal is True
        temps = res.hvac_temperatures["Cold Room Chiller"]
        assert len(temps) == 25  # T_0 to T_24
        for temp in temps:
            assert 18.99 <= temp <= 23.51, f"Temperature {temp} violated comfort bounds [19.0, 23.5]"

    def test_bess_arbitrage_and_soc_limits(self):
        """Verify BESS charges at off-peak rates, discharges during peak, and respects SOC limits."""
        tariffs = [0.20] * 24
        tariffs[2] = 0.05  # off-peak cheap night rate
        tariffs[14] = 0.35  # peak afternoon rate

        bess = BESSLoad(
            name="Storage BESS",
            capacity_kwh=20.0,
            max_charge_kw=4.0,
            max_discharge_kw=4.0,
            min_soc=0.20,
            max_soc=0.90,
            initial_soc=0.40,
            target_final_soc=0.40,
        )
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[12.0] * 24,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=40.0,
            bess=bess,
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        assert res.is_optimal is True
        net_profile = res.device_schedules["Storage BESS (Net)"]
        
        # BESS should charge at hour 2 (net > 0) and discharge at hour 14 (net < 0)
        assert net_profile[2] > 0.0
        assert net_profile[14] < 0.0
        
        # Check SOC limits
        for soc in res.bess_soc_history:
            assert 0.199 <= soc <= 0.901, f"SOC {soc} breached limits [0.20, 0.90]"

    def test_peak_capacity_breach_mitigation(self):
        """Verify solver successfully shaves load to avoid severe contracted capacity surcharge."""
        # Baseline load peaks at hour 14 (30 kW) but is lower at hour 16 (20 kW).
        # Defrost is 8 kW at hour 14 -> baseline total 38 kW breaches 35 kW capacity limit.
        baseline = [20.0] * 24
        baseline[14] = 30.0

        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=baseline,
            tariff_rates_eur_kwh=[0.15] * 24,
            contracted_capacity_kw=35.0,
            capacity_penalty_eur_per_kw=25.0,
            defrost_loads=[
                DefrostLoad(
                    name="Defrost Rack",
                    nominal_start_hour=14,
                    duration_hours=1,
                    power_kw=8.0,
                    max_shift_hours=3,
                )
            ],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        assert res.is_optimal is True
        assert res.capacity_breached_baseline is True
        # In baseline, 30 + 8 = 38 kW breaches 35 kW
        assert res.peak_baseline_kw == 38.0
        # Optimal schedule shifted defrost, maintaining peak <= 38.0 and reducing penalty
        assert res.savings_eur > 0.0

    def test_solver_speed_and_benchmark(self):
        """Verify MILP solve time is well under the 100ms real-time constraint."""
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[15.0] * 24,
            tariff_rates_eur_kwh=[0.16] * 24,
            contracted_capacity_kw=45.0,
            defrost_loads=[DefrostLoad()],
            batch_loads=[ProductionBatchLoad()],
            hvac_loads=[HVACLoad()],
            bess=BESSLoad(),
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        assert res.is_optimal is True
        assert res.solve_time_ms < 100.0, f"Solve time {res.solve_time_ms}ms exceeded 100ms budget"


# --- 2. Decision Support & Closed-Loop Verification Tests ---

class TestDecisionSupportAndClosedLoop:
    """Validation of actionable recommendation generation and post-intervention audit."""

    def test_recommendation_generation_and_greek_localization(self):
        """Verify DecisionSupportEngine generates prioritized Greek/English action cards."""
        tariffs = [0.15] * 24
        tariffs[14] = 0.38
        tariffs[16] = 0.11

        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[12.0] * 24,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            defrost_loads=[
                DefrostLoad(
                    name="Chiller A Defrost",
                    nominal_start_hour=14,
                    duration_hours=1,
                    power_kw=7.0,
                    max_shift_hours=2,
                )
            ],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()

        engine = DecisionSupportEngine(facility_id="fac_bakery_01")
        recs = engine.generate_recommendations(problem, res)

        assert len(recs) >= 1
        rec = recs[0]
        assert rec.category == RecommendationCategory.DEFROST_SHIFT
        assert rec.asset_name == "Chiller A Defrost"
        assert rec.peak_load_avoided_kw == 7.0
        assert rec.estimated_savings_eur > 0.0
        assert "Μετατόπιση απόψυξης" in rec.description_el
        assert "Shift defrost cycle" in rec.description_en
        assert rec.confidence_score is None

    def test_closed_loop_verification_success(self):
        """Verify successful post-intervention audit when actual load avoided matches target."""
        rec = ActionRecommendation(
            recommendation_id="rec_test_123",
            facility_id="fac_01",
            category=RecommendationCategory.DEFROST_SHIFT,
            priority=PriorityLevel.HIGH,
            title="Shift Defrost Test",
            description_el="Δοκιμή",
            description_en="Test",
            asset_name="Compressor 1",
            original_window="14:00-15:00",
            recommended_window="16:00-17:00",
            peak_load_avoided_kw=6.0,
            estimated_savings_eur=12.00,
            confidence_score=0.92,
            contracted_capacity_kw=35.0,
            projected_peak_kw=28.0,
        )

        verifier = ClosedLoopVerifier()
        # Baseline was 34 kW, measured load during intervention window was 28 kW -> 6 kW avoided
        record = verifier.verify_intervention(
            recommendation=rec,
            actual_measured_kw=28.0,
            counterfactual_baseline_kw=34.0,
            tariff_eur_kwh=0.25,
            duration_hours=1.0,
        )

        assert record.status == VerificationStatus.SUCCESS
        assert record.actual_load_avoided_kw == 6.0
        assert record.actual_savings_eur > 0.0
        assert "Επιτυχής παρέμβαση" in record.summary_el

    def test_closed_loop_verification_failure_on_operator_override(self):
        """Verify audit detects when operator overrode recommendation or load remained high."""
        rec = ActionRecommendation(
            recommendation_id="rec_test_456",
            facility_id="fac_01",
            category=RecommendationCategory.DEFROST_SHIFT,
            priority=PriorityLevel.HIGH,
            title="Shift Defrost Test",
            description_el="Δοκιμή",
            description_en="Test",
            asset_name="Compressor 1",
            original_window="14:00-15:00",
            recommended_window="16:00-17:00",
            peak_load_avoided_kw=6.0,
            estimated_savings_eur=12.00,
            confidence_score=0.92,
            contracted_capacity_kw=35.0,
            projected_peak_kw=28.0,
        )

        verifier = ClosedLoopVerifier()
        # Baseline was 34 kW, actual measured was 33.5 kW -> only 0.5 kW avoided (target 6.0 kW)
        record = verifier.verify_intervention(
            recommendation=rec,
            actual_measured_kw=33.5,
            counterfactual_baseline_kw=34.0,
            tariff_eur_kwh=0.25,
            duration_hours=1.0,
        )

        assert record.status == VerificationStatus.FAILED
        assert record.actual_load_avoided_kw == 0.5
        assert "Απόκλιση στόχου" in record.summary_el


# --- 3. REST API Integration Tests ---

class TestOptimizationRESTEndpoints:
    """Validation of FastAPI optimization endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        db_file = tmp_path / "test_opt_ems.db"
        app = create_app(db_path=str(db_file))
        return TestClient(app)

    def test_get_optimization_status(self, client):
        """GET /api/v1/optimization/status should return operational status."""
        resp = client.get("/api/v1/optimization/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "operational"
        assert data["solver_backend"] == "scipy_highs_milp"
        assert "flexible_refrigeration_defrost" in data["supported_constraints"]

    def test_post_optimization_solve(self, client):
        """POST /api/v1/optimization/solve should compute optimal schedule and recommendations."""
        payload = {
            "facility_id": "fac_bakery_01",
            "contracted_capacity_kw": 35.0,
            "include_defrost": True,
            "include_batch_ovens": True,
            "include_hvac": True,
            "include_bess": True,
        }
        resp = client.post("/api/v1/optimization/solve", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_optimal"] is True
        assert "savings_eur" in data
        assert "device_schedules" in data
        assert "recommendations" in data
        assert len(data["recommendations"]) > 0

    def test_get_recommendations_and_post_verify(self, client, valid_telemetry_dict):
        """Verify recommendation query and post-intervention telemetry verification."""
        # 1. Trigger solve to populate recommendations
        solve_resp = client.post("/api/v1/optimization/solve", json={"contracted_capacity_kw": 30.0})
        assert solve_resp.status_code == 200
        recs = solve_resp.json()["recommendations"]
        assert len(recs) > 0
        first_rec = recs[0]

        # 2. Query /api/v1/optimization/recommendations
        get_resp = client.get("/api/v1/optimization/recommendations")
        assert get_resp.status_code == 200
        assert len(get_resp.json()) >= 1

        # 3. Record an interval: 6 kWh over 15 minutes = 24 kW average.
        from backend.database.sqlite_store import get_store
        store = get_store(client.app.state.db_path)
        valid_telemetry_dict.update(facility_id=first_rec["facility_id"], timestamp="2026-09-22T10:00:00+00:00", cumulative_energy_kwh=100)
        start_id = store.store_telemetry(valid_telemetry_dict)
        valid_telemetry_dict.update(timestamp="2026-09-22T10:15:00+00:00", cumulative_energy_kwh=106)
        end_id = store.store_telemetry(valid_telemetry_dict)
        # 4. Post verification
        ver_payload = {
            "recommendation_id": first_rec["recommendation_id"],
            "start_reading_id": start_id,
            "end_reading_id": end_id,
            "counterfactual_baseline_kw": 32.0,
            "tariff_eur_kwh": 0.22,
        }
        ver_resp = client.post("/api/v1/optimization/verify", json=ver_payload)
        assert ver_resp.status_code == 200
        ver_data = ver_resp.json()
        assert ver_data["status"] in ["success", "partial", "failed"]
        assert ver_data["actual_load_avoided_kw"] == 8.0
        assert ver_data["duration_hours"] == .25
        assert ver_data["actual_savings_eur"] == .44
        assert ver_data["evidence_source"] == "stored_telemetry"
        assert ver_data["is_certified"] is False

    def test_post_verify_404_on_unknown_id(self, client):
        """POST /api/v1/optimization/verify with non-existent ID must return 404 Not Found."""
        ver_payload = {
            "recommendation_id": "non_existent_rec_9999",
            "start_reading_id": 1,
            "end_reading_id": 2,
            "tariff_eur_kwh": .22,
            "counterfactual_baseline_kw": 32.0,
        }
        resp = client.post("/api/v1/optimization/verify", json=ver_payload)
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_solve_422_on_invalid_horizon_length(self, client):
        """POST /api/v1/optimization/solve must reject baseline loads or tariffs not having 24 entries."""
        payload_bad_baseline = {"baseline_load_kw": [10.0] * 12}
        resp = client.post("/api/v1/optimization/solve", json=payload_bad_baseline)
        assert resp.status_code == 422

        payload_bad_tariffs = {"tariff_rates_eur_kwh": [0.15] * 20}
        resp2 = client.post("/api/v1/optimization/solve", json=payload_bad_tariffs)
        assert resp2.status_code == 422

    def test_multi_hour_contiguous_defrost(self):
        """Multi-hour defrost must run contiguously in optimal schedule."""
        defrost = DefrostLoad(
            name="2-Hour Freezer Defrost",
            nominal_start_hour=14,
            duration_hours=2,
            power_kw=7.5,
            max_shift_hours=3,
        )
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[10.0] * 24,
            tariff_rates_eur_kwh=[0.20] * 24,
            contracted_capacity_kw=40.0,
            defrost_loads=[defrost],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()
        assert res.is_optimal is True
        schedule = res.device_schedules["2-Hour Freezer Defrost"]
        active = [t for t, p in enumerate(schedule) if p > 0.1]
        assert len(active) == 2
        assert active[1] == active[0] + 1

    def test_hvac_soft_comfort_feasibility_on_freezing_day(self):
        """Solver must remain feasible on cold winter days when ambient is below comfort lower limit."""
        hvac = HVACLoad(
            name="Office HVAC",
            initial_temp_c=18.0,
            temp_min_c=20.0,
            temp_max_c=24.0,
            ambient_temp_forecast=[4.0] * 24,  # Freezing ambient temperature
        )
        problem = OptimizationProblem(
            horizon_hours=24,
            baseline_load_kw=[5.0] * 24,
            tariff_rates_eur_kwh=[0.15] * 24,
            contracted_capacity_kw=30.0,
            hvac_loads=[hvac],
        )
        solver = ConstrainedLoadSolver(problem)
        res = solver.solve()
        # Soft slack absorbs temperature excursion without solver crash
        assert res.is_optimal is True
