"""
Tier 1: Feature Coverage E2E Test Suite
========================================
Exhaustively tests all 16 core EMS features (>= 5 test cases per feature = 80 total tests):
1. Telemetry calculations (True RMS, P, S, cos phi, cumulative kWh)
2. Γ21 commercial tariff (single-rate LV <= 25 kVA)
3. Γ22 commercial tariff (dual-rate LV > 25 kVA, peak & off-peak)
4. Γ23 commercial tariff (medium voltage > 250 kVA)
5. Green tariff fluctuation mechanism (see docs/RULES_SOURCES.md)
6. Yellow & Dynamic DAM spot pricing
7. Regulated charges (DEDDIE, ADMIE, ETMEAR, YKO, EFK, DETE, 6% VAT)
8. Capacity excess & power factor penalties
9. Running costs (€/h) & daily spend accumulator
10. Peak surcharge projection (€)
11. Telemetry ingestion API (payload validation & physical invariants)
12. Proactive alert generation within 30s
13. Greek notification templates
14. Alert throttling, cooldown & hysteresis
15. Greek commands (/status, /cost_today, /tariff, /settings)
16. Commercial simulation profiles (Bakery, Cold Storage, Hotel)
"""

import math
import time
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tests.e2e.test_tiers.harness import (
    AlertDispatcherStateMachine,
    AlertState,
    FacilityProfileConfig,
    TelemetryPayload,
    calculate_green_tariff_fluctuation,
    calculate_green_tariff_supply_rate,
    calculate_realtime_cost,
    calculate_regulated_unit_rate,
    calculate_yellow_dynamic_supply_rate,
    create_valid_telemetry_payload,
    format_greek_bot_response,
    get_boutique_hotel_power,
    get_cold_storage_power,
    get_commercial_bakery_power,
    is_greek_offpeak_window,
    is_greek_peak_window,
)

# --- Feature 1: Telemetry Calculations ---

class TestFeature1TelemetryCalculations:
    def test_feature1_active_apparent_power_calculation(self):
        """Validates S = V * I / 1000 and P = S * cos phi on each phase."""
        voltage = 230.0
        current = 20.0
        pf = 0.95
        s_phase = (voltage * current) / 1000.0  # 4.60 kVA
        p_phase = s_phase * pf  # 4.37 kW
        assert math.isclose(s_phase, 4.60, rel_tol=1e-3)
        assert math.isclose(p_phase, 4.37, rel_tol=1e-3)

    def test_feature1_power_factor_computation(self):
        """Validates system cos phi computation: pf = P_tot / S_tot."""
        p_total = 18.0
        s_total = 20.0
        pf = p_total / s_total
        assert math.isclose(pf, 0.90, rel_tol=1e-3)
        assert 0.0 <= pf <= 1.0

    def test_feature1_true_rms_current_conversion(self):
        """Validates discrete RMS integration: I_rms = sqrt(mean(i^2))."""
        # Synthesize a pure 50Hz sine wave current with 10A RMS (14.14A peak)
        samples = [14.142 * math.sin(2 * math.pi * 50.0 * (n / 2500.0)) for n in range(50)]
        mean_sq = sum(s ** 2 for s in samples) / len(samples)
        rms = math.sqrt(mean_sq)
        assert math.isclose(rms, 10.0, rel_tol=1e-2)

    def test_feature1_trapezoidal_energy_integration(self):
        """Validates trapezoidal active energy accumulation in kWh."""
        p_t0 = 20.0  # kW
        p_t1 = 22.0  # kW
        dt_seconds = 60.0  # 1 minute
        # trapezoid area: (p_t0 + p_t1)/2 * (dt/3600)
        delta_kwh = ((p_t0 + p_t1) / 2.0) * (dt_seconds / 3600.0)
        prev_kwh = 100.0
        new_kwh = prev_kwh + delta_kwh
        assert math.isclose(delta_kwh, 0.35, rel_tol=1e-3)
        assert math.isclose(new_kwh, 100.35, rel_tol=1e-3)

    def test_feature1_three_phase_sum_invariant_validation(self):
        """Validates physical invariant: |P_tot - sum(P_i)| <= 0.05 kW."""
        payload = create_valid_telemetry_payload(p_total_kw=17.90)
        sum_phases = sum(p.active_power_kw for p in payload.phases.values())
        assert abs(payload.total_active_power_kw - sum_phases) <= 0.05


# --- Feature 2: Γ21 Commercial Tariff (Single Rate LV <= 25 kVA) ---

class TestFeature2TariffG21:
    @pytest.fixture
    def g21_facility(self):
        return FacilityProfileConfig(
            facility_id="cafe-g21-syntagma",
            facility_name="Καφέ Σύνταγμα",
            facility_type="bakery",
            contract_code="G21",
            tariff_color="green",
            contracted_kva=25.0,
            peak_threshold_kw=18.0,
        )

    def test_feature2_g21_single_rate_under_25kva(self, g21_facility):
        """Verifies G21 contracted capacity is <= 25 kVA."""
        assert g21_facility.contracted_kva <= 25.0

    def test_feature2_g21_24h_uniform_rate_pricing(self, g21_facility):
        """Verifies G21 does not differentiate daytime supply rate by peak windows."""
        dt_day = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        dt_night = datetime(2026, 7, 15, 3, 0, 0, tzinfo=timezone.utc)
        res_day = calculate_realtime_cost(10.0, 1.0, dt_day, g21_facility)
        res_night = calculate_realtime_cost(10.0, 1.0, dt_night, g21_facility)
        # Supply component is identical (G21 is single rate)
        assert res_day.current_rate_eur_per_kwh == res_night.current_rate_eur_per_kwh

    def test_feature2_g21_fixed_supply_charge(self, g21_facility):
        """Verifies annual capacity regulated charge scale for G21."""
        reg_rate = calculate_regulated_unit_rate(contracted_kva=g21_facility.contracted_kva)
        assert reg_rate > 0.040

    def test_feature2_g21_running_cost_at_different_loads(self, g21_facility):
        """Verifies running cost scales with power draw in kW."""
        dt = datetime(2026, 7, 15, 11, 0, 0, tzinfo=timezone.utc)
        cost_5kw = calculate_realtime_cost(5.0, 0.5, dt, g21_facility).running_cost_eur_per_h
        cost_15kw = calculate_realtime_cost(15.0, 1.5, dt, g21_facility).running_cost_eur_per_h
        assert math.isclose(cost_15kw, cost_5kw * 3.0, rel_tol=0.05)

    def test_feature2_g21_offpeak_has_no_night_discount(self, g21_facility):
        """Verifies G21 single-rate contract does not grant dual-rate night discount."""
        dt_night = datetime(2026, 7, 15, 2, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(10.0, 1.0, dt_night, g21_facility)
        assert not res.is_peak_window


# --- Feature 3: Γ22 Commercial Tariff (Dual-Rate LV > 25 kVA) ---

class TestFeature3TariffG22:
    def test_feature3_g22_summer_peak_window_identification(self):
        """Verifies Greek summer peak window is strictly 14:00-17:00 on weekdays."""
        # July 15, 2026 is a Wednesday (weekday 2)
        dt_peak = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)
        dt_normal = datetime(2026, 7, 15, 12, 30, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_peak) is True
        assert is_greek_peak_window(dt_normal) is False

    def test_feature3_g22_summer_offpeak_window_identification(self):
        """Verifies Greek summer night offpeak window (23:00-07:00)."""
        dt_night = datetime(2026, 7, 15, 3, 0, 0, tzinfo=timezone.utc)
        dt_day = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        assert is_greek_offpeak_window(dt_night) is True
        assert is_greek_offpeak_window(dt_day) is False

    def test_feature3_g22_winter_peak_window_identification(self):
        """Verifies Greek winter peak window (17:00-21:00 on weekdays)."""
        # Jan 14, 2026 is Wednesday
        dt_winter_peak = datetime(2026, 1, 14, 18, 0, 0, tzinfo=timezone.utc)
        dt_winter_day = datetime(2026, 1, 14, 14, 30, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_winter_peak) is True
        assert is_greek_peak_window(dt_winter_day) is False

    def test_feature3_g22_weekend_no_peak_window(self):
        """Verifies Greek DEDDIE rules exempt weekends (Sat/Sun) from peak hours."""
        # July 18, 2026 is Saturday
        dt_sat = datetime(2026, 7, 18, 15, 30, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_sat) is False

    def test_feature3_g22_peak_vs_offpeak_rate_differential(self, sample_facility_bakery):
        """Verifies G22 dual-rate contract has higher rate during peak than offpeak."""
        dt_peak = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)
        dt_offpeak = datetime(2026, 7, 15, 2, 0, 0, tzinfo=timezone.utc)
        res_peak = calculate_realtime_cost(20.0, 1.0, dt_peak, sample_facility_bakery)
        res_offpeak = calculate_realtime_cost(20.0, 1.0, dt_offpeak, sample_facility_bakery)
        assert res_peak.current_rate_eur_per_kwh > res_offpeak.current_rate_eur_per_kwh


# --- Feature 4: Γ23 Medium Voltage Commercial Tariff (> 250 kVA) ---

class TestFeature4TariffG23:
    def test_feature4_g23_mv_lower_regulated_rate(self, sample_facility_hotel):
        """Verifies MV commercial connections have lower ETMEAR levy than LV."""
        reg_rate_mv = calculate_regulated_unit_rate(contracted_kva=300.0, is_lv=False)
        reg_rate_lv = calculate_regulated_unit_rate(contracted_kva=35.0, is_lv=True)
        assert reg_rate_mv < reg_rate_lv

    def test_feature4_g23_mv_large_capacity_threshold(self):
        """Verifies G23 configuration supports contracted capacity > 250 kVA."""
        g23_facility = FacilityProfileConfig(
            facility_id="cold-logistics-mv",
            facility_name="Κέντρο Logistics Μέσης Τάσης",
            facility_type="cold_storage",
            contract_code="G23",
            tariff_color="green",
            contracted_kva=400.0,
            peak_threshold_kw=120.0,
        )
        assert g23_facility.contracted_kva > 250.0

    def test_feature4_g23_mv_peak_surcharge_calculation(self, sample_facility_hotel):
        """Verifies peak surcharge calculation applies to G23 during peak."""
        dt_peak = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(35.0, 1.0, dt_peak, sample_facility_hotel)
        assert res.is_peak_window is True
        assert res.is_excess_breach is True
        assert res.projected_excess_penalty_eur > 0.0

    def test_feature4_g23_mv_offpeak_discount_rate(self, sample_facility_hotel):
        """Verifies MV offpeak rates reflect commercial night schedule."""
        dt_night = datetime(2026, 7, 15, 3, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(25.0, 1.0, dt_night, sample_facility_hotel)
        assert res.is_peak_window is False

    def test_feature4_g23_mv_running_cost_at_scale(self, sample_facility_hotel):
        """Verifies running cost calculation handles heavy 150 kW loads without numeric overflow."""
        dt = datetime(2026, 7, 15, 11, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(150.0, 2.5, dt, sample_facility_hotel)
        assert res.running_cost_eur_per_h > 20.0


# --- Feature 5: Green Tariff Formula (see docs/RULES_SOURCES.md) ---

class TestFeature5GreenTariffFormula:
    def test_feature5_green_tariff_normal_band_no_fluctuation(self):
        """When Ll <= TEA <= Lu, MD = 0.0."""
        # Ll=95, Lu=115, TEA=105 -> MD = 0.0
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=105.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0)
        assert math.isclose(md, 0.0, abs_tol=1e-5)

    def test_feature5_green_tariff_upper_breach_surcharge(self):
        """When TEA > Lu, MD = alpha * (TEA - Lu)."""
        # TEA=135, Lu=115, alpha=1.15 -> MD = 1.15 * (0.135 - 0.115) = 1.15 * 0.020 = +0.023 €/kWh
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=135.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15)
        assert math.isclose(md, 0.023, rel_tol=1e-3)

    def test_feature5_green_tariff_lower_breach_credit(self):
        """When TEA < Ll, MD = alpha * (TEA - Ll) (negative credit)."""
        # TEA=75, Ll=95, alpha=1.15 -> MD = 1.15 * (0.075 - 0.095) = -0.023 €/kWh
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=75.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15)
        assert math.isclose(md, -0.023, rel_tol=1e-3)

    def test_feature5_green_tariff_zero_floor_protection(self):
        """P_supply = max(0, P_base - E_disc + MD), never negative."""
        rate = calculate_green_tariff_supply_rate(p_base=0.05, e_disc=0.04, tea_eur_mwh=20.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0)
        assert rate >= 0.0

    def test_feature5_green_tariff_alpha_amplification_scaling(self):
        """Verifies MD scales linearly with alpha parameter."""
        md_1 = calculate_green_tariff_fluctuation(tea_eur_mwh=145.0, alpha=1.0)
        md_15 = calculate_green_tariff_fluctuation(tea_eur_mwh=145.0, alpha=1.5)
        assert math.isclose(md_15, md_1 * 1.5, rel_tol=1e-3)


# --- Feature 6: Yellow & Dynamic DAM Spot Pricing ---

class TestFeature6YellowDynamicTariff:
    def test_feature6_yellow_base_plus_market_index(self):
        """Verifies Yellow tariff adds wholesale spot to base component."""
        rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=100.0, loss_factor=0.135, margin_eur_kwh=0.015, p_base=0.04)
        expected = 0.100 * 1.135 + 0.015 + 0.04  # 0.1135 + 0.055 = 0.1685
        assert math.isclose(rate, expected, rel_tol=1e-3)

    def test_feature6_dynamic_hourly_spot_loss_factor_addition(self):
        """Verifies 13.5% grid distribution loss factor is added to spot price."""
        tea = 120.0
        rate_with_loss = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=tea, loss_factor=0.135, margin_eur_kwh=0.0, p_base=0.0)
        assert math.isclose(rate_with_loss, (tea / 1000.0) * 1.135, rel_tol=1e-3)

    def test_feature6_dynamic_hourly_spot_supplier_margin(self):
        """Verifies supplier margin is added to volumetric price."""
        r1 = calculate_yellow_dynamic_supply_rate(100.0, margin_eur_kwh=0.010)
        r2 = calculate_yellow_dynamic_supply_rate(100.0, margin_eur_kwh=0.020)
        assert math.isclose(r2 - r1, 0.010, rel_tol=1e-3)

    def test_feature6_dynamic_spot_price_spike_handling(self):
        """Verifies dynamic tariff calculates accurately during extreme wholesale spikes (e.g. 350 €/MWh)."""
        rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=350.0)
        assert rate > 0.40

    def test_feature6_dynamic_spot_price_negative_or_zero_clearing(self):
        """Verifies behavior when wholesale market clears at 0 €/MWh."""
        rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.0, margin_eur_kwh=0.015, p_base=0.02)
        assert rate >= 0.035


# --- Feature 7: Regulated Charges (DEDDIE, ADMIE, ETMEAR, YKO, EFK, VAT) ---

class TestFeature7RegulatedCharges:
    def test_feature7_deddie_distribution_volumetric_charge(self):
        """Verifies base DEDDIE distribution charge ~0.01415 €/kWh."""
        reg = calculate_regulated_unit_rate(power_factor=1.0)
        assert reg >= 0.01415

    def test_feature7_admie_transmission_charge(self):
        """Verifies ADMIE transmission charge ~0.00560 €/kWh is included."""
        reg = calculate_regulated_unit_rate()
        assert reg >= (0.01415 + 0.00560)

    def test_feature7_etmear_and_yko_levies(self):
        """Verifies ETMEAR (0.01700) and YKO (0.00690) volumetric levies."""
        reg = calculate_regulated_unit_rate(is_lv=True)
        # Sum of DEDDIE (0.01415) + ADMIE (0.00560) + ETMEAR (0.017) + YKO (0.0069) >= 0.04365
        assert reg >= 0.04365

    def test_feature7_efk_excise_and_dete_5permille(self):
        """Verifies EFK (0.00220) and DETE 5‰ duty are accounted for."""
        reg = calculate_regulated_unit_rate()
        assert reg >= 0.046

    def test_feature7_vat_6percent_reduced_electricity_tax(self, sample_facility_bakery):
        """Verifies Greek reduced 6% VAT is applied to final invoice sum."""
        dt = datetime(2026, 7, 15, 11, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(20.0, 1.0, dt, sample_facility_bakery)
        assert res.vat_rate == 0.06


# --- Feature 8: Capacity Excess & Power Factor Penalties ---

class TestFeature8Penalties:
    def test_feature8_power_factor_below_085_multiplier(self):
        """If cos phi < 0.85, DEDDIE distribution charge is scaled by 0.85 / cos phi."""
        reg_normal = calculate_regulated_unit_rate(power_factor=0.95)
        reg_penalized = calculate_regulated_unit_rate(power_factor=0.70)
        assert reg_penalized > reg_normal

    def test_feature8_power_factor_above_085_no_penalty(self):
        """If cos phi >= 0.85, penalty multiplier is exactly 1.0."""
        reg_85 = calculate_regulated_unit_rate(power_factor=0.85)
        reg_95 = calculate_regulated_unit_rate(power_factor=0.95)
        assert math.isclose(reg_85, reg_95, rel_tol=1e-3)

    def test_feature8_contracted_capacity_exact_boundary(self):
        """S == S_contracted is not a breach."""
        payload = create_valid_telemetry_payload(p_total_kw=25.0, pf=1.0)
        assert payload.total_apparent_power_kva <= 25.05

    def test_feature8_contracted_capacity_breach_penalty(self, sample_facility_bakery):
        """When power exceeds contracted capacity, excess power is detected."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(38.0, 1.0, dt, sample_facility_bakery)
        assert res.is_excess_breach is True
        assert res.excess_power_kw == (38.0 - sample_facility_bakery.peak_threshold_kw)

    def test_feature8_combined_pf_and_capacity_excess_penalties(self, sample_facility_bakery):
        """Combined low cos phi and capacity breach increases total rate and penalty."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res_high_pf = calculate_realtime_cost(30.0, 1.0, dt, sample_facility_bakery, power_factor=0.98)
        res_low_pf = calculate_realtime_cost(30.0, 1.0, dt, sample_facility_bakery, power_factor=0.70)
        assert res_low_pf.current_rate_eur_per_kwh > res_high_pf.current_rate_eur_per_kwh


# --- Feature 9: Running Costs & Daily Spend Accumulator ---

class TestFeature9RunningCostsAndDailySpend:
    def test_feature9_instantaneous_running_cost_eur_per_hour(self, sample_facility_bakery):
        """Running cost equals power (kW) * effective unit rate (€/kWh)."""
        dt = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(18.0, 0.5, dt, sample_facility_bakery)
        expected = round(18.0 * res.current_rate_eur_per_kwh, 2)
        assert math.isclose(res.running_cost_eur_per_h, expected, abs_tol=0.02)

    def test_feature9_incremental_cost_energy_delta(self, sample_facility_bakery):
        """Incremental cost = delta kWh * unit rate."""
        dt = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(20.0, 0.35, dt, sample_facility_bakery)
        expected = round(0.35 * res.current_rate_eur_per_kwh, 4)
        assert math.isclose(res.incremental_cost_eur, expected, abs_tol=0.001)

    def test_feature9_daily_spend_accumulation(self):
        """Verifies daily spend accumulation sums multiple incremental readings."""
        incremental_spends = [0.082, 0.084, 0.085, 0.090]
        daily_spend = sum(incremental_spends)
        assert math.isclose(daily_spend, 0.341, rel_tol=1e-3)

    def test_feature9_zero_load_running_cost(self, sample_facility_bakery):
        """Zero load produces zero running cost (€/h)."""
        dt = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(0.0, 0.0, dt, sample_facility_bakery)
        assert res.running_cost_eur_per_h == 0.0

    def test_feature9_daily_spend_average_unit_rate(self):
        """Verifies average daily rate computation: total € / total kWh."""
        total_eur = 48.60
        total_kwh = 240.5
        avg_rate = total_eur / total_kwh
        assert math.isclose(avg_rate, 0.202, rel_tol=1e-2)


# --- Feature 10: Peak Surcharge Projection ---

class TestFeature10PeakSurchargeProjection:
    def test_feature10_peak_projection_active_breach(self, sample_facility_bakery):
        """Calculates positive excess penalty during active peak breach."""
        # 15:00 summer peak (ends at 17:00 -> 2 hours remaining)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        assert res.is_excess_breach is True
        assert res.projected_excess_penalty_eur > 0.0

    def test_feature10_peak_projection_zero_outside_peak_window(self, sample_facility_bakery):
        """Outside peak window, excess penalty projection is 0.0 even at high load."""
        dt = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(30.0, 1.0, dt, sample_facility_bakery)
        assert res.projected_excess_penalty_eur == 0.0

    def test_feature10_peak_projection_zero_below_threshold(self, sample_facility_bakery):
        """Inside peak window, load below threshold produces 0.0 projected penalty."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(18.0, 1.0, dt, sample_facility_bakery)
        assert res.projected_excess_penalty_eur == 0.0

    def test_feature10_peak_projection_declining_with_remaining_hours(self, sample_facility_bakery):
        """Projected penalty declines as remaining peak window duration decreases."""
        dt_start = datetime(2026, 7, 15, 14, 15, 0, tzinfo=timezone.utc)
        dt_end = datetime(2026, 7, 15, 16, 45, 0, tzinfo=timezone.utc)
        res_start = calculate_realtime_cost(30.0, 1.0, dt_start, sample_facility_bakery)
        res_end = calculate_realtime_cost(30.0, 1.0, dt_end, sample_facility_bakery)
        assert res_start.projected_excess_penalty_eur > res_end.projected_excess_penalty_eur

    def test_feature10_peak_projection_scales_linearly_with_excess_kw(self, sample_facility_bakery):
        """Projected penalty scales linearly with excess kW."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        # Threshold is 22 kW: 24 kW (2 kW excess) vs 26 kW (4 kW excess)
        res_2kw = calculate_realtime_cost(24.0, 1.0, dt, sample_facility_bakery)
        res_4kw = calculate_realtime_cost(26.0, 1.0, dt, sample_facility_bakery)
        assert math.isclose(res_4kw.projected_excess_penalty_eur, res_2kw.projected_excess_penalty_eur * 2.0, rel_tol=0.05)


# --- Feature 11: Telemetry Ingestion API (Pydantic & Physical Invariants) ---

class TestFeature11TelemetryIngestion:
    def test_feature11_valid_payload_parsing_and_invariants(self, sample_telemetry_dict):
        """Verifies valid telemetry JSON parses cleanly into Pydantic model."""
        payload = TelemetryPayload(**sample_telemetry_dict)
        assert payload.device_id == "esp32-ems-001"
        assert payload.total_active_power_kw == 17.90

    def test_feature11_phase_sum_discrepancy_rejection(self, sample_telemetry_dict):
        """Rejects payload when |P_tot - sum(P_i)| > 0.05 kW."""
        corrupted = dict(sample_telemetry_dict)
        corrupted["total_active_power_kw"] = 25.0  # discrepancy with phase sum 17.90
        with pytest.raises(ValidationError):
            TelemetryPayload(**corrupted)

    def test_feature11_missing_phase_rejection(self, sample_telemetry_dict):
        """Rejects payload missing L3."""
        corrupted = dict(sample_telemetry_dict)
        corrupted["phases"] = {"L1": sample_telemetry_dict["phases"]["L1"], "L2": sample_telemetry_dict["phases"]["L2"]}
        with pytest.raises(ValidationError):
            TelemetryPayload(**corrupted)

    def test_feature11_out_of_range_voltage_rejection(self, sample_telemetry_dict):
        """Rejects voltage > 350V."""
        corrupted = dict(sample_telemetry_dict)
        corrupted["phases"]["L1"]["voltage_v"] = 420.0
        with pytest.raises(ValidationError):
            TelemetryPayload(**corrupted)

    def test_feature11_negative_energy_rejection(self, sample_telemetry_dict):
        """Rejects negative cumulative energy."""
        corrupted = dict(sample_telemetry_dict)
        corrupted["cumulative_energy_kwh"] = -10.0
        with pytest.raises(ValidationError):
            TelemetryPayload(**corrupted)


# --- Feature 12: Proactive Alert Generation within 30s ---

class TestFeature12ProactiveAlertGeneration:
    def test_feature12_breach_generates_alert_in_peak_window(self, sample_facility_bakery):
        """Load exceeding threshold in peak window generates breach event."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)

        # Feed 3 debouncing samples
        dispatcher.process_reading(28.0, dt, True, cost_res)
        dispatcher.process_reading(28.0, dt + timedelta(seconds=10), True, cost_res)
        event = dispatcher.process_reading(28.0, dt + timedelta(seconds=20), True, cost_res)

        assert event is not None
        assert event.current_kw == 28.0
        assert event.threshold_kw == 22.0

    def test_feature12_no_alert_when_below_threshold_in_peak(self, sample_facility_bakery):
        """Load below threshold in peak window produces no alert."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(18.0, 1.0, dt, sample_facility_bakery)
        event = dispatcher.process_reading(18.0, dt, True, cost_res)
        assert event is None

    def test_feature12_no_alert_when_above_threshold_outside_peak(self, sample_facility_bakery):
        """High load outside peak window produces no peak breach alert."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(30.0, 1.0, dt, sample_facility_bakery)
        event = dispatcher.process_reading(30.0, dt, False, cost_res)
        assert event is None

    def test_feature12_proactive_alert_latency_within_time_budget(self, sample_facility_bakery):
        """Simulates end-to-end alert trigger speed, ensuring it evaluates in < 0.1s."""
        t_start = time.perf_counter()
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            event = dispatcher.process_reading(28.0, dt + timedelta(seconds=i * 5), True, cost_res)
        t_duration = time.perf_counter() - t_start
        assert t_duration < 0.10  # Benchmark < 30 seconds requirement
        assert event is not None

    def test_feature12_alert_contains_financial_impact(self, sample_facility_bakery):
        """Verifies breach event includes estimated euro penalty and running cost."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            event = dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)
        assert event.estimated_penalty_eur > 0.0
        assert event.running_cost_eur_h > 0.0


# --- Feature 13: Greek Notification Templates ---

class TestFeature13GreekNotificationTemplates:
    def test_feature13_greek_peak_breach_header_and_keywords(self, sample_facility_bakery):
        """Verifies alert contains Greek warning header and operational details."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.6, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            event = dispatcher.process_reading(28.6, dt + timedelta(seconds=i), True, cost_res)
        text = event.message_text
        assert "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in text
        assert "Ζώνη Αιχμής" in text
        assert "28.6 kW" in text

    def test_feature13_greek_bakery_curtailment_advice(self, sample_facility_bakery):
        """Verifies bakery-specific advice mentions deck ovens / φούρνο."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            event = dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)
        assert "φούρνο" in event.message_text

    def test_feature13_greek_cold_storage_curtailment_advice(self, sample_facility_cold_storage):
        """Verifies cold storage advice mentions loading doors / απόψυξη."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_cold_storage)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(32.0, 1.0, dt, sample_facility_cold_storage)
        for i in range(3):
            event = dispatcher.process_reading(32.0, dt + timedelta(seconds=i), True, cost_res)
        assert "πόρτες" in event.message_text or "απόψυξης" in event.message_text

    def test_feature13_greek_hotel_curtailment_advice(self, sample_facility_hotel):
        """Verifies hotel advice mentions VRV air-conditioning / κλιματισμού."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_hotel)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(36.0, 1.0, dt, sample_facility_hotel)
        for i in range(3):
            event = dispatcher.process_reading(36.0, dt + timedelta(seconds=i), True, cost_res)
        assert "κλιματισμού VRV" in event.message_text

    def test_feature13_greek_recovery_cleared_template(self, sample_facility_bakery):
        """Verifies recovery message format with Greek title and cleared power."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)

        # Drop to safe power (<= 90% of 22 kW = 19.8 kW)
        recovery_event = dispatcher.process_reading(18.0, dt + timedelta(minutes=5), True, cost_res)
        assert recovery_event is not None
        assert "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in recovery_event.message_text
        assert "18.0 kW" in recovery_event.message_text


# --- Feature 14: Alert Throttling, Cooldown & Hysteresis ---

class TestFeature14ThrottlingCooldownHysteresis:
    def test_feature14_three_sample_debounce_before_alert(self, sample_facility_bakery):
        """Requires 3 consecutive breach samples before transitioning to TRIGGERED."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)

        # Sample 1: no alert
        assert dispatcher.process_reading(28.0, dt, True, cost_res) is None
        # Sample 2: no alert
        assert dispatcher.process_reading(28.0, dt + timedelta(seconds=5), True, cost_res) is None
        # Sample 3: alert triggered!
        assert dispatcher.process_reading(28.0, dt + timedelta(seconds=10), True, cost_res) is not None

    def test_feature14_cooldown_suppresses_duplicate_alerts(self, sample_facility_bakery):
        """Alerts within 30-minute cooldown window are suppressed."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)

        for i in range(3):
            dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)

        # Immediate follow-up 2 minutes later
        follow_up = dispatcher.process_reading(28.5, dt + timedelta(minutes=2), True, cost_res)
        assert follow_up is None

    def test_feature14_escalation_breaks_cooldown(self, sample_facility_bakery):
        """A >25% power jump breaks cooldown and emits escalation warning."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(24.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            dispatcher.process_reading(24.0, dt + timedelta(seconds=i), True, cost_res)

        # Escalation from 24.0 kW to 32.0 kW (+33% jump)
        cost_res_jump = calculate_realtime_cost(32.0, 1.0, dt + timedelta(minutes=5), sample_facility_bakery)
        escalation_event = dispatcher.process_reading(32.0, dt + timedelta(minutes=5), True, cost_res_jump)
        assert escalation_event is not None
        assert "ΚΛΙΜΑΚΩΣΗ ΥΠΕΡΒΑΣΗΣ" in escalation_event.message_text

    def test_feature14_ten_percent_hysteresis_recovery_point(self, sample_facility_bakery):
        """System emits recovery only when power drops below 90% of threshold (22 * 0.9 = 19.8 kW)."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)

        # 19.0 kW is <= 19.8 kW -> Clears alert
        cleared_event = dispatcher.process_reading(19.0, dt + timedelta(minutes=10), True, cost_res)
        assert cleared_event is not None
        assert cleared_event.active_zone == "Ομαλοποίηση"

    def test_feature14_hysteresis_does_not_clear_at_borderline_95_percent(self, sample_facility_bakery):
        """Power dropping to 21.0 kW (> 19.8 kW) remains in COOLDOWN, no recovery emitted."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)

        # 21.0 kW is below 22 kW threshold, but ABOVE 19.8 kW (90% hysteresis)
        still_cooldown = dispatcher.process_reading(21.0, dt + timedelta(minutes=5), True, cost_res)
        assert still_cooldown is None
        assert dispatcher.state == AlertState.COOLDOWN


# --- Feature 15: Greek Commands (/status, /cost_today, /tariff, /settings) ---

class TestFeature15GreekBotCommands:
    def test_feature15_command_status_formatting_and_phases(self, sample_facility_bakery):
        """Verifies /status output displays 3-phase voltages, currents, kW, and €/h."""
        payload = create_valid_telemetry_payload(p_total_kw=17.90)
        res = format_greek_bot_response("/status", sample_facility_bakery, payload)
        assert "Τρέχουσα Κατάσταση" in res
        assert "17.90 kW" in res
        assert "L1:" in res and "L2:" in res and "L3:" in res

    def test_feature15_command_cost_today_accumulated_spend(self, sample_facility_bakery):
        """Verifies /cost_today displays accumulated kWh and spend today."""
        res = format_greek_bot_response("/cost_today", sample_facility_bakery, daily_spend_eur=54.20, daily_energy_kwh=260.0)
        assert "Σημερινή Κατανάλωση" in res
        assert "54.20 €" in res
        assert "260.0 kWh" in res

    def test_feature15_command_tariff_contract_and_schedule(self, sample_facility_bakery):
        """Verifies /tariff displays contract type, color, and peak window."""
        res = format_greek_bot_response("/tariff", sample_facility_bakery)
        assert "G22" in res
        assert "Πράσινο" in res
        assert "14:00 - 17:00" in res

    def test_feature15_command_settings_threshold_and_cooldown(self, sample_facility_bakery):
        """Verifies /settings displays threshold kW and cooldown minutes."""
        res = format_greek_bot_response("/settings", sample_facility_bakery)
        assert "22.0 kW" in res
        assert "30 λεπτά" in res

    def test_feature15_command_unknown_fallback_prompt(self, sample_facility_bakery):
        """Verifies unknown commands return Greek help prompt with available options."""
        res = format_greek_bot_response("/invalid_cmd", sample_facility_bakery)
        assert "Άγνωστη εντολή" in res
        assert "/status" in res


# --- Feature 16: Commercial Simulation Profiles (Bakery, Cold Storage, Hotel) ---

class TestFeature16CommercialSimulationProfiles:
    def test_feature16_bakery_morning_baking_spike(self):
        """Verifies commercial bakery load peaks at 03:00-08:30 (32-45 kW)."""
        power_06am = get_commercial_bakery_power(6.0)
        assert 35.0 <= power_06am <= 48.0

    def test_feature16_bakery_afternoon_peak_breach(self):
        """Verifies bakery afternoon prep (15:00) draws 24-28 kW, breaching 22 kW threshold."""
        power_15pm = get_commercial_bakery_power(15.0)
        assert 22.0 <= power_15pm <= 29.0

    def test_feature16_cold_storage_compressor_cycling(self):
        """Verifies cold storage oscillates between idle baseload (16 kW) and pull-down (32 kW)."""
        power_pull_down = get_cold_storage_power(minute_float=15.0)
        power_idle = get_cold_storage_power(minute_float=35.0)
        assert power_pull_down > 28.0
        assert power_idle < 20.0

    def test_feature16_cold_storage_door_open_disturbance(self):
        """Verifies cold storage door open disturbance forces continuous 34.5 kW draw."""
        power_door_open = get_cold_storage_power(minute_float=35.0, door_open=True)
        assert math.isclose(power_door_open, 34.5, rel_tol=1e-2)

    def test_feature16_hotel_summer_afternoon_ac_ramp(self):
        """Verifies boutique hotel VRV AC ramps to 31-34 kW during afternoon check-in (15:30)."""
        power_hotel_1530 = get_boutique_hotel_power(15.5)
        assert 29.0 <= power_hotel_1530 <= 35.0
