from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.database.sqlite_store import SQLiteStore
from backend.market.models import DamHourlyPrice
from backend.market.service import MarketPriceService
from tariff_engine.contracts import Season, get_greek_season, is_offpeak_window, is_peak_window, to_athens_time

ATHENS_TZ = ZoneInfo("Europe/Athens")

class TestTimezoneRegressions:
    def test_utc_and_athens_instant_parity_summer_peak(self):
        """2026-10-01 11:00:00 UTC is 14:00:00 EEST (Summer weekday peak). Both must return True."""
        dt_utc = datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc)
        dt_athens = datetime(2026, 10, 1, 14, 0, 0, tzinfo=ATHENS_TZ)
        assert is_peak_window(dt_utc) is True
        assert is_peak_window(dt_athens) is True

    def test_utc_timestamp_outside_peak_in_athens(self):
        """2026-10-01 14:00:00 UTC is 17:00:00 EEST (Summer peak ends at 17:00). Must return False."""
        dt_utc = datetime(2026, 10, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert is_peak_window(dt_utc) is False

    def test_winter_season_and_peak_hour(self):
        """2026-11-05 15:30:00 UTC is 17:30:00 EET (Winter weekday peak 17:00-21:00)."""
        dt_utc = datetime(2026, 11, 5, 15, 30, 0, tzinfo=timezone.utc)
        assert get_greek_season(dt_utc) == Season.WINTER
        assert is_peak_window(dt_utc) is True

    def test_month_transition_at_midnight_utc(self):
        """2026-10-31 22:30:00 UTC is 2026-11-01 00:30:00 EET. Must classify as WINTER, not SUMMER."""
        dt_utc = datetime(2026, 10, 31, 22, 30, 0, tzinfo=timezone.utc)
        assert get_greek_season(dt_utc) == Season.WINTER

    def test_offpeak_night_window_with_utc(self):
        """2026-10-01 21:30:00 UTC is 2026-10-02 00:30:00 EEST (Night off-peak 23:00-07:00)."""
        dt_utc = datetime(2026, 10, 1, 21, 30, 0, tzinfo=timezone.utc)
        assert is_offpeak_window(dt_utc) is True

    def test_to_athens_time_naive_and_aware(self):
        """Naive datetime is assigned Europe/Athens; aware is converted."""
        dt_naive = datetime(2026, 10, 1, 14, 0, 0)
        dt_norm = to_athens_time(dt_naive)
        assert dt_norm.tzinfo == ATHENS_TZ
        assert dt_norm.hour == 14

        dt_utc = datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc)
        dt_norm_utc = to_athens_time(dt_utc)
        assert dt_norm_utc.tzinfo == ATHENS_TZ
        assert dt_norm_utc.hour == 14

    def test_market_service_timezone_normalization(self, tmp_path):
        """MarketPriceService.get_effective_tea normalizes UTC timestamps to Europe/Athens."""
        db_path = str(tmp_path / "test_mkt_tz.db")
        store = SQLiteStore(db_path)
        store.init_db()
        service = MarketPriceService(store=store)

        # Preload cache for Athens date 2026-10-02 hour 1
        with service._lock:
            service._dam_cache["2026-10-02"] = [
                DamHourlyPrice(date="2026-10-02", hour=1, price_eur_mwh=155.0, price_eur_kwh=0.155, source="test")
            ]

        # 2026-10-01 22:30:00 UTC is 2026-10-02 01:30:00 EEST (Athens)
        dt_utc = datetime(2026, 10, 1, 22, 30, 0, tzinfo=timezone.utc)
        price = service.get_effective_tea(dt_utc, tariff_color="yellow")
        assert price == 155.0


from tariff_engine.green_tariff import calculate_green_tariff_fluctuation
from tariff_engine.yellow_dynamic import calculate_yellow_dynamic_supply_rate, to_kwh_rate


class TestPricingUnitsRegressions:
    def test_low_positive_tea_yellow_dynamic_rate(self):
        """TEA = 0.50 €/MWh must convert to 0.00050 €/kWh, yielding 0.06557 €/kWh, NOT 0.6325 €/kWh."""
        rate = calculate_yellow_dynamic_supply_rate(
            tea_eur_mwh=0.50,
            loss_factor=0.135,
            margin_eur_kwh=0.015,
            p_base=0.050,
        )
        assert rate == 0.06557

    def test_continuity_across_one_euro_boundary(self):
        """Ensure no 1000x jump between 0.99 €/MWh and 1.01 €/MWh."""
        rate_0_99 = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.99)
        rate_1_01 = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=1.01)
        assert abs(rate_1_01 - rate_0_99) < 0.0001

    def test_green_tariff_fluctuation_solar_surplus_rebate(self):
        """TEA = 0.50 €/MWh is far below Ll = 95.0 €/MWh, so MD must be a negative rebate."""
        md = calculate_green_tariff_fluctuation(
            tea_eur_mwh=0.50,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        # Expected: 1.15 * (0.00050 - 0.095) = -0.108675 €/kWh
        assert pytest.approx(md, rel=1e-4) == -0.108675

    def test_zero_and_negative_wholesale_prices(self):
        """Zero and negative wholesale prices calculate linearly without exception."""
        rate_zero = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.0, floor_at_zero=False)
        assert rate_zero == 0.06500

        rate_neg = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=-20.0, floor_at_zero=False)
        # -0.020 * 1.135 + 0.015 + 0.050 = -0.0227 + 0.065 = 0.04230
        assert rate_neg == 0.04230

    def test_explicit_price_unit_conversion(self):
        """Verify to_kwh_rate with EUR_MWH and EUR_KWH."""
        assert to_kwh_rate(100.0, "EUR_MWH") == 0.100
        assert to_kwh_rate(0.150, "EUR_KWH") == 0.150
        with pytest.raises(ValueError, match="Unsupported price unit"):
            to_kwh_rate(100.0, "INVALID")

    def test_units_module_centralization(self):
        """Verify tariff_engine.units exposes PriceUnit and to_kwh_rate with strict validation."""
        from tariff_engine.units import PriceUnit as UnitType  # noqa: F401
        from tariff_engine.units import to_kwh_rate as units_to_kwh_rate
        assert units_to_kwh_rate(250.0, "EUR_MWH") == 0.250
        assert units_to_kwh_rate(0.250, "EUR_KWH") == 0.250
        with pytest.raises(ValueError, match="Unsupported price unit"):
            units_to_kwh_rate(100.0, "BAD_UNIT")


from optimization_engine.decision_support import DecisionSupportEngine
from optimization_engine.models import (
    DefrostLoad,
    OptimizationProblem,
    PriorityLevel,
    ProductionBatchLoad,
    RecommendationCategory,
    ScheduleResult,
)


class TestDecisionSupportSavingsRegressions:
    def test_reproduce_finding_2_capacity_savings_not_overstated(self):
        """Scenario: 40 kW flat background, 35 kW capacity, 6.8 kW defrost shifted from hr 14 to hr 11.
        Baseline peak = 46.8 kW. Optimized peak = 46.8 kW (breach persists at other hours).
        Old code claimed EUR 127.16 savings by multiplying 6.8 kW * capacity_penalty_eur_per_kw.
        New code must claim ONLY the true energy shift savings (EUR 1.36) and avoid claiming false demand savings.
        """
        engine = DecisionSupportEngine(facility_id="fac_test_01")
        tariffs = [0.15] * 24
        tariffs[14] = 0.35  # expensive nominal defrost window
        tariffs[11] = 0.15  # cheap optimal defrost window

        problem = OptimizationProblem(
            baseline_load_kw=[40.0] * 24,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            capacity_penalty_eur_per_kw=18.50,
            defrost_loads=[DefrostLoad(name="Freezer Rack", nominal_start_hour=14, duration_hours=1, power_kw=6.8)],
        )

        dev_sched = [0.0] * 24
        dev_sched[11] = 6.8  # shifted to 11
        schedule = ScheduleResult(
            status="optimal",
            is_optimal=True,
            horizon_hours=24,
            baseline_total_load_kw=[40.0 + (6.8 if t == 14 else 0.0) for t in range(24)],
            optimized_total_load_kw=[40.0 + (6.8 if t == 11 else 0.0) for t in range(24)],
            baseline_cost_eur=146.36,
            optimized_cost_eur=145.00,
            savings_eur=1.36,
            savings_pct=0.93,
            peak_baseline_kw=46.8,
            peak_optimized_kw=46.8,
            peak_reduction_kw=0.0,
            capacity_breached_baseline=True,
            capacity_breached_optimized=True,
            device_schedules={"Freezer Rack": dev_sched},
            operationally_feasible=True,
        )

        recs = engine.generate_recommendations(problem, schedule)
        defrost_recs = [r for r in recs if r.category == RecommendationCategory.DEFROST_SHIFT]
        assert len(defrost_recs) == 1
        rec = defrost_recs[0]

        # Must report exact energy delta (EUR 1.36), NOT inflated penalty surcharge (EUR 127.16)
        assert rec.estimated_savings_eur == 1.36
        assert rec.peak_load_avoided_kw == 6.8

        # Must generate critical warning card for unmitigated breach
        warning_recs = [r for r in recs if r.priority == PriorityLevel.CRITICAL and "Capacity Breach" in r.title]
        assert len(warning_recs) >= 1
        assert "46.8" in warning_recs[0].description_en or "11.8" in warning_recs[0].description_en

    def test_genuine_demand_savings_with_contract_rate(self):
        """When the entire facility peak drops and contract demand rate is provided, credit demand savings."""
        engine = DecisionSupportEngine(facility_id="fac_test_01")
        tariffs = [0.20] * 24

        # Background load is 30 kW. Hour 14 has defrost (6.8 kW), totaling 36.8 kW (breaches 35 kW).
        # Shifting to hour 2 brings hour 14 to 30 kW and hour 2 to 36.8 kW? No, background at hour 2 is 20 kW!
        baseline_load = [20.0] * 24
        baseline_load[14] = 30.0  # hour 14 background 30 kW + 6.8 = 36.8 kW peak of day
        # When defrost moves to hr 2 (20 kW + 6.8 kW = 26.8 kW), new peak of day is 30.0 kW (at hr 14).
        # Daily peak dropped from 36.8 kW to 30.0 kW! Peak reduction = 6.8 kW.
        # Demand savings = 6.8 kW * contract_rate (e.g. 2.0 EUR/kW) = 13.60 EUR.
        problem = OptimizationProblem(
            baseline_load_kw=baseline_load,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            contracted_demand_rate_eur_per_kw=2.0,
            defrost_loads=[DefrostLoad(name="Freezer Rack", nominal_start_hour=14, duration_hours=1, power_kw=6.8)],
        )

        dev_sched = [0.0] * 24
        dev_sched[2] = 6.8
        base_tot = [baseline_load[t] + (6.8 if t == 14 else 0.0) for t in range(24)]
        opt_tot = [baseline_load[t] + (6.8 if t == 2 else 0.0) for t in range(24)]

        schedule = ScheduleResult(
            status="optimal",
            is_optimal=True,
            horizon_hours=24,
            baseline_total_load_kw=base_tot,
            optimized_total_load_kw=opt_tot,
            baseline_cost_eur=100.0,
            optimized_cost_eur=100.0,
            savings_eur=0.0,
            savings_pct=0.0,
            peak_baseline_kw=36.8,
            peak_optimized_kw=30.0,
            peak_reduction_kw=6.8,
            capacity_breached_baseline=True,
            capacity_breached_optimized=False,
            device_schedules={"Freezer Rack": dev_sched},
            operationally_feasible=True,
        )

        recs = engine.generate_recommendations(problem, schedule)
        defrost_recs = [r for r in recs if r.category == RecommendationCategory.DEFROST_SHIFT]
        assert len(defrost_recs) == 1
        # Energy delta = 0 (flat tariff), demand savings = min(6.8, 36.8 - 35.0) or min(6.8, 36.8 - 30.0) * 2.0
        assert defrost_recs[0].estimated_savings_eur > 0.0

    def test_batch_shift_demand_savings_and_no_inflated_penalty(self):
        """Batch load shift must not inflate savings with penalty weights and should credit contract demand savings."""
        engine = DecisionSupportEngine(facility_id="fac_test_01")
        tariffs = [0.15] * 24

        baseline_load = [20.0] * 24
        baseline_load[10] = 30.0  # hour 10 background 30 kW
        # Batch load is 10 kW, 2 hours, earliest 10 (so hours 10 and 11).
        # Baseline total at hr 10 = 30 + 10 = 40 kW (breaches 35 kW).
        # Optimized shifted to hr 14..16 where background is 20 kW -> peak 30 kW.
        problem = OptimizationProblem(
            baseline_load_kw=baseline_load,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            contracted_demand_rate_eur_per_kw=2.5,
            capacity_penalty_eur_per_kw=18.50,
            batch_loads=[ProductionBatchLoad(name="Oven 1", earliest_start_hour=10, latest_start_hour=18, duration_hours=2, power_kw=10.0)],
        )

        dev_sched = [0.0] * 24
        dev_sched[14] = 10.0
        dev_sched[15] = 10.0
        base_tot = [baseline_load[t] + (10.0 if t in (10, 11) else 0.0) for t in range(24)]
        opt_tot = [baseline_load[t] + (10.0 if t in (14, 15) else 0.0) for t in range(24)]

        schedule = ScheduleResult(
            status="optimal",
            is_optimal=True,
            horizon_hours=24,
            baseline_total_load_kw=base_tot,
            optimized_total_load_kw=opt_tot,
            baseline_cost_eur=100.0,
            optimized_cost_eur=100.0,
            savings_eur=0.0,
            savings_pct=0.0,
            peak_baseline_kw=40.0,
            peak_optimized_kw=30.0,
            peak_reduction_kw=10.0,
            capacity_breached_baseline=True,
            capacity_breached_optimized=False,
            device_schedules={"Oven 1": dev_sched},
            operationally_feasible=True,
        )

        recs = engine.generate_recommendations(problem, schedule)
        batch_recs = [r for r in recs if r.category == RecommendationCategory.BATCH_SCHEDULING]
        assert len(batch_recs) == 1
        # Net peak drop = 40.0 - max(35.0, 30.0) = 5.0 kW.
        # Demand savings = min(10.0, 5.0) * 2.5 = 12.50 EUR.
        # Old code would have added (min(10, 40-35) * 18.50) = 92.50 EUR.
        assert batch_recs[0].estimated_savings_eur == 12.50
        assert batch_recs[0].peak_load_avoided_kw == 10.0

    def test_peak_load_attribution_ties_defrost(self):
        """When multiple hours tie for peak_baseline_kw, defrost shift at any tied hour is credited demand savings."""
        engine = DecisionSupportEngine(facility_id="fac_test_tie")
        tariffs = [0.20] * 24

        baseline_load = [20.0] * 24
        baseline_load[5] = 36.8
        baseline_load[14] = 30.0

        problem = OptimizationProblem(
            baseline_load_kw=baseline_load,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            contracted_demand_rate_eur_per_kw=2.0,
            defrost_loads=[DefrostLoad(name="Freezer Tie", nominal_start_hour=14, duration_hours=1, power_kw=6.8)],
        )

        dev_sched = [0.0] * 24
        dev_sched[2] = 6.8
        base_tot = [baseline_load[t] + (6.8 if t == 14 else 0.0) for t in range(24)]
        opt_tot = [20.0] * 24
        opt_tot[14] = 30.0
        opt_tot[2] = 26.8
        opt_tot[5] = 30.0

        schedule = ScheduleResult(
            status="optimal",
            is_optimal=True,
            horizon_hours=24,
            baseline_total_load_kw=base_tot,
            optimized_total_load_kw=opt_tot,
            baseline_cost_eur=100.0,
            optimized_cost_eur=100.0,
            savings_eur=0.0,
            savings_pct=0.0,
            peak_baseline_kw=36.8,
            peak_optimized_kw=30.0,
            peak_reduction_kw=6.8,
            capacity_breached_baseline=True,
            capacity_breached_optimized=False,
            device_schedules={"Freezer Tie": dev_sched},
            operationally_feasible=True,
        )

        recs = engine.generate_recommendations(problem, schedule)
        defrost_recs = [r for r in recs if r.category == RecommendationCategory.DEFROST_SHIFT]
        assert len(defrost_recs) == 1
        assert defrost_recs[0].estimated_savings_eur > 0.0

    def test_peak_load_attribution_ties_batch(self):
        """When multiple hours tie for peak_baseline_kw, batch shift spanning any tied hour is credited demand savings."""
        engine = DecisionSupportEngine(facility_id="fac_test_tie_batch")
        tariffs = [0.20] * 24

        baseline_load = [20.0] * 24
        baseline_load[2] = 40.0
        baseline_load[10] = 30.0

        problem = OptimizationProblem(
            baseline_load_kw=baseline_load,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            contracted_demand_rate_eur_per_kw=2.5,
            batch_loads=[ProductionBatchLoad(name="Oven Tie", earliest_start_hour=10, latest_start_hour=18, duration_hours=2, power_kw=10.0)],
        )

        dev_sched = [0.0] * 24
        dev_sched[14] = 10.0
        dev_sched[15] = 10.0
        base_tot = [baseline_load[t] + (10.0 if t in (10, 11) else 0.0) for t in range(24)]
        opt_tot = [20.0] * 24
        opt_tot[2] = 30.0
        opt_tot[10] = 30.0
        opt_tot[14] = 30.0
        opt_tot[15] = 30.0

        schedule = ScheduleResult(
            status="optimal",
            is_optimal=True,
            horizon_hours=24,
            baseline_total_load_kw=base_tot,
            optimized_total_load_kw=opt_tot,
            baseline_cost_eur=100.0,
            optimized_cost_eur=100.0,
            savings_eur=0.0,
            savings_pct=0.0,
            peak_baseline_kw=40.0,
            peak_optimized_kw=30.0,
            peak_reduction_kw=10.0,
            capacity_breached_baseline=True,
            capacity_breached_optimized=False,
            device_schedules={"Oven Tie": dev_sched},
            operationally_feasible=True,
        )

        recs = engine.generate_recommendations(problem, schedule)
        batch_recs = [r for r in recs if r.category == RecommendationCategory.BATCH_SCHEDULING]
        assert len(batch_recs) == 1
        assert batch_recs[0].estimated_savings_eur == 12.50



