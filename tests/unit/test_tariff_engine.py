"""
Unit Test Suite for Greek Commercial Electricity Tariff & Real-Time Cost Engine.

Comprehensive verification covering:
1. Contracts (Γ21, Γ22, Γ23), Tariff Colors (Green, Yellow, Dynamic), and TOU Schedules.
2. Green Tariff Fluctuation Mechanism (see docs/RULES_SOURCES.md) and Supply Rates.
3. Yellow & Dynamic Day-Ahead Market Spot Pricing.
4. Regulated Network Charges (DEDDIE, ADMIE, ETMEAR, YKO, EFK, DETE, 6% VAT).
5. Penalties: Power factor (cos φ < 0.85) multiplier and Contracted Capacity Excess.
6. Real-Time Running Cost (€/h), Incremental Packet Cost, Projected Peak Surcharge, and Daily Spend Tracking.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

ATHENS_TZ = ZoneInfo("Europe/Athens")

def athens_dt(*args, **kwargs) -> datetime:
    """Helper representing Greek market local timestamps."""
    return datetime(*args, **kwargs, tzinfo=ATHENS_TZ)

# Legacy alias for test backwards compatibility
utc_dt = athens_dt


from tariff_engine import (
    ADMIE_CAPACITY_RATE_EUR_KVA_YR,
    ADMIE_ENERGY_RATE_EUR_KWH,
    DEDDIE_CAPACITY_RATE_EUR_KVA_YR,
    DEDDIE_ENERGY_RATE_EUR_KWH,
    EFK_RATE_EUR_KWH,
    ETMEAR_LV_RATE_EUR_KWH,
    ETMEAR_MV_RATE_EUR_KWH,
    VAT_RATE,
    YKO_RATE_EUR_KWH,
    ContractProfile,
    DailySpendTracker,
    GreenTariffEngine,
    Season,
    TariffColor,
    TariffContract,
    YellowDynamicEngine,
    calculate_admie_capacity_charge,
    calculate_capacity_excess,
    calculate_deddie_capacity_charge,
    calculate_dynamic_tariff,
    calculate_fixed_capacity_charges,
    calculate_green_tariff_fluctuation,
    calculate_green_tariff_supply_rate,
    calculate_hourly_capacity_rate,
    calculate_power_factor_multiplier,
    calculate_power_factor_surcharge,
    calculate_projected_peak_penalty,
    calculate_realtime_cost,
    calculate_regulated_unit_rate,
    calculate_yellow_dynamic_supply_rate,
    calculate_yellow_tariff,
    get_greek_season,
    get_regulated_breakdown,
    get_remaining_peak_hours,
    is_capacity_exceeded,
    is_offpeak_window,
    is_peak_window,
    is_power_factor_penalized,
)

# --- 1. Contracts & Time-of-Use Schedules ---

class TestContractsAndTOU:
    def test_contract_enum_values_and_parsing(self):
        assert TariffContract.G21.value == "G21"
        assert TariffContract.G22.value == "G22"
        assert TariffContract.G23.value == "G23"

        # Parsing variations
        assert TariffContract.from_string("G21") == TariffContract.G21
        assert TariffContract.from_string("g22") == TariffContract.G22
        assert TariffContract.from_string("Γ21") == TariffContract.G21
        assert TariffContract.from_string("Γ23") == TariffContract.G23

        with pytest.raises(ValueError, match="Unknown tariff contract code"):
            TariffContract.from_string("INVALID")

    def test_color_enum_values_and_parsing(self):
        assert TariffColor.GREEN.value == "green"
        assert TariffColor.YELLOW.value == "yellow"
        assert TariffColor.DYNAMIC.value == "dynamic"

        assert TariffColor.from_string("GREEN") == TariffColor.GREEN
        assert TariffColor.from_string("Yellow") == TariffColor.YELLOW
        assert TariffColor.from_string("dynamic") == TariffColor.DYNAMIC

        with pytest.raises(ValueError, match="Unknown tariff color"):
            TariffColor.from_string("PURPLE")

    def test_contract_profile_initialization_and_validation(self):
        profile = ContractProfile(
            contract_type="G22",
            color="green",
            contracted_capacity_kva=50.0,
            base_rate_eur_per_kwh=0.160,
            prompt_discount_percent=5.0,
            fixed_monthly_fee_eur=4.50,
        )
        assert profile.contract_type == TariffContract.G22
        assert profile.color == TariffColor.GREEN
        assert profile.contracted_capacity_kva == 50.0
        assert profile.peak_threshold_kw == 42.5  # 85% of 50.0

        # Negative / Invalid checks
        with pytest.raises(ValueError, match="Contracted capacity must be positive"):
            ContractProfile(contracted_capacity_kva=-10.0)

        with pytest.raises(ValueError, match="Base rate cannot be negative"):
            ContractProfile(base_rate_eur_per_kwh=-0.05)

        with pytest.raises(ValueError, match="Prompt discount percent must be in"):
            ContractProfile(prompt_discount_percent=105.0)

        with pytest.raises(ValueError, match="Fixed monthly fee cannot be negative"):
            ContractProfile(fixed_monthly_fee_eur=-2.0)

    def test_season_detection(self):
        # Summer: May (5) to Oct (10)
        assert get_greek_season(utc_dt(2026, 5, 1, 12, 0)) == Season.SUMMER
        assert get_greek_season(utc_dt(2026, 8, 15, 12, 0)) == Season.SUMMER
        assert get_greek_season(utc_dt(2026, 10, 31, 23, 59)) == Season.SUMMER

        # Winter: Nov (11) to Apr (4)
        assert get_greek_season(utc_dt(2026, 11, 1, 0, 0)) == Season.WINTER
        assert get_greek_season(utc_dt(2026, 1, 15, 12, 0)) == Season.WINTER
        assert get_greek_season(utc_dt(2026, 4, 30, 23, 59)) == Season.WINTER

    def test_summer_peak_window_boundaries(self):
        # Wednesday in July 2026 (Summer: 14:00 - 17:00)
        dt_pre = utc_dt(2026, 7, 15, 13, 59, 59)
        dt_start = utc_dt(2026, 7, 15, 14, 0, 0)
        dt_mid = utc_dt(2026, 7, 15, 15, 30, 0)
        dt_end = utc_dt(2026, 7, 15, 16, 59, 59)
        dt_post = utc_dt(2026, 7, 15, 17, 0, 0)

        assert not is_peak_window(dt_pre)
        assert is_peak_window(dt_start)
        assert is_peak_window(dt_mid)
        assert is_peak_window(dt_end)
        assert not is_peak_window(dt_post)

    def test_winter_peak_window_boundaries(self):
        # Wednesday in January 2026 (Winter: 17:00 - 21:00)
        dt_pre = utc_dt(2026, 1, 14, 16, 59, 59)
        dt_start = utc_dt(2026, 1, 14, 17, 0, 0)
        dt_mid = utc_dt(2026, 1, 14, 19, 0, 0)
        dt_end = utc_dt(2026, 1, 14, 20, 59, 59)
        dt_post = utc_dt(2026, 1, 14, 21, 0, 0)

        assert not is_peak_window(dt_pre)
        assert is_peak_window(dt_start)
        assert is_peak_window(dt_mid)
        assert is_peak_window(dt_end)
        assert not is_peak_window(dt_post)

    def test_weekend_peak_exemption(self):
        # Saturday July 18, 2026 at 15:00
        dt_sat = utc_dt(2026, 7, 18, 15, 0, 0)
        assert dt_sat.weekday() == 5
        assert not is_peak_window(dt_sat)

        # Sunday January 18, 2026 at 18:00
        dt_sun = utc_dt(2026, 1, 18, 18, 0, 0)
        assert dt_sun.weekday() == 6
        assert not is_peak_window(dt_sun)

    def test_offpeak_window(self):
        assert is_offpeak_window(utc_dt(2026, 7, 15, 23, 0, 0))
        assert is_offpeak_window(utc_dt(2026, 7, 15, 2, 30, 0))
        assert is_offpeak_window(utc_dt(2026, 7, 15, 6, 59, 59))
        assert not is_offpeak_window(utc_dt(2026, 7, 15, 7, 0, 0))
        assert not is_offpeak_window(utc_dt(2026, 7, 15, 14, 0, 0))

    def test_remaining_peak_hours(self):
        # Summer peak ends at 17:00
        dt_1400 = utc_dt(2026, 7, 15, 14, 0, 0)
        assert pytest.approx(get_remaining_peak_hours(dt_1400), rel=1e-3) == 3.0

        dt_1530 = utc_dt(2026, 7, 15, 15, 30, 0)
        assert pytest.approx(get_remaining_peak_hours(dt_1530), rel=1e-3) == 1.5

        # Winter peak ends at 21:00
        dt_winter_1700 = utc_dt(2026, 1, 14, 17, 0, 0)
        assert pytest.approx(get_remaining_peak_hours(dt_winter_1700), rel=1e-3) == 4.0

        # Outside peak window or weekend
        dt_normal = utc_dt(2026, 7, 15, 10, 0, 0)
        assert get_remaining_peak_hours(dt_normal) == 0.0

        dt_weekend = utc_dt(2026, 7, 18, 15, 0, 0)
        assert get_remaining_peak_hours(dt_weekend) == 0.0


# --- 2. Green Tariff Fluctuation Mechanism (see docs/RULES_SOURCES.md) ---

class TestGreenTariff:
    def test_normal_band_no_fluctuation(self):
        # Ll=95, Lu=115, TEA=105 -> MD = 0.0
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=105.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0)
        assert md == 0.0

        # Exactly on boundaries
        assert calculate_green_tariff_fluctuation(tea_eur_mwh=95.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0) == 0.0
        assert calculate_green_tariff_fluctuation(tea_eur_mwh=115.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0) == 0.0

    def test_upper_breach_surcharge(self):
        # TEA=135, Lu=115, alpha=1.15 -> MD = 1.15 * (0.135 - 0.115) = 1.15 * 0.020 = 0.023 €/kWh
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=135.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15)
        assert pytest.approx(md, rel=1e-5) == 0.023

    def test_lower_breach_credit(self):
        # TEA=75, Ll=95, alpha=1.15 -> MD = 1.15 * (0.075 - 0.095) = 1.15 * (-0.020) = -0.023 €/kWh
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=75.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15)
        assert pytest.approx(md, rel=1e-5) == -0.023

    def test_historical_beta_factor_calculation(self):
        # TEA_{M-1} = 135, TEA_{M-2} = 115, alpha = 1.15
        # beta = 1.15 * (0.135 - 0.115) = 0.023
        # Upper breach: 1.15 * (0.135 - 0.115) + 0.023 = 0.023 + 0.023 = 0.046
        md = calculate_green_tariff_fluctuation(
            tea_eur_mwh=135.0,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
            tea_m2_eur_mwh=115.0,
        )
        assert pytest.approx(md, rel=1e-5) == 0.046

    def test_green_tariff_supply_rate_with_discounts(self):
        # Base: 0.155, Disc: 0.020, TEA: 135 (MD = 0.023) -> 0.155 - 0.020 + 0.023 = 0.158
        rate = calculate_green_tariff_supply_rate(
            p_base=0.155,
            e_disc=0.020,
            tea_eur_mwh=135.0,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        assert rate == 0.158

        # With prompt discount percentage: 10% on 0.155 = 0.0155
        rate_pct = calculate_green_tariff_supply_rate(
            p_base=0.155,
            tea_eur_mwh=105.0,  # MD = 0
            prompt_discount_percent=10.0,
        )
        assert rate_pct == round(0.155 - 0.0155, 5)

    def test_zero_floor_protection(self):
        # Extreme lower breach yielding negative supply rate -> floored at 0.0
        rate = calculate_green_tariff_supply_rate(
            p_base=0.050,
            e_disc=0.040,
            tea_eur_mwh=20.0,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        assert rate >= 0.0

    def test_green_tariff_engine_class(self):
        engine = GreenTariffEngine(p_base=0.150, prompt_discount_percent=5.0)
        fluct = engine.compute_fluctuation(tea_eur_mwh=105.0)
        assert fluct == 0.0
        rate = engine.compute_supply_rate(tea_eur_mwh=105.0)
        assert rate == round(0.150 * 0.95, 5)


# --- 3. Yellow & Dynamic Day-Ahead Market Spot Pricing ---

class TestYellowDynamicTariff:
    def test_yellow_tariff_calculation(self):
        # TEA: 120 €/MWh = 0.120 €/kWh, loss_factor: 0.135, margin: 0.015, base: 0.040
        # 0.120 * 1.135 + 0.015 + 0.040 = 0.1362 + 0.055 = 0.1912 €/kWh
        rate = calculate_yellow_tariff(tea_eur_mwh=120.0, loss_factor=0.135, margin_eur_kwh=0.015, p_base=0.040)
        assert rate == 0.1912

    def test_dynamic_tariff_calculation(self):
        # TEA: 120 €/MWh, loss_factor: 0.135, margin: 0.015, base: 0.020
        # 0.120 * 1.135 + 0.015 + 0.020 = 0.1362 + 0.035 = 0.1712 €/kWh
        rate = calculate_dynamic_tariff(tea_eur_mwh=120.0, loss_factor=0.135, margin_eur_kwh=0.015, p_base=0.020)
        assert rate == 0.1712

    def test_dynamic_extreme_price_spike(self):
        # TEA: 350 €/MWh = 0.350 €/kWh
        # 0.350 * 1.135 + 0.015 + 0.020 = 0.39725 + 0.035 = 0.43225 €/kWh
        rate = calculate_dynamic_tariff(tea_eur_mwh=350.0, loss_factor=0.135, margin_eur_kwh=0.015, p_base=0.020)
        assert rate == 0.43225

    def test_negative_wholesale_handling(self):
        # TEA: -10 €/MWh = -0.010 €/kWh
        # -0.010 * 1.135 + 0.015 + 0.020 = -0.01135 + 0.035 = 0.02365
        rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=-10.0, p_base=0.020)
        assert rate == 0.02365

        # Extreme negative with floor
        rate_floored = calculate_yellow_dynamic_supply_rate(
            tea_eur_mwh=-100.0, margin_eur_kwh=0.010, p_base=0.020, floor_at_zero=True
        )
        assert rate_floored == 0.0

    def test_yellow_dynamic_engine_class(self):
        engine = YellowDynamicEngine(loss_factor=0.10, margin_eur_kwh=0.010, yellow_base_eur_kwh=0.04)
        # TEA: 100 €/MWh = 0.100 €/kWh -> 0.100 * 1.10 + 0.010 + 0.040 = 0.160
        assert engine.compute_yellow(100.0) == 0.160


# --- 4. Regulated Network Charges ---

class TestRegulatedCharges:
    def test_regulated_constants(self):
        assert DEDDIE_CAPACITY_RATE_EUR_KVA_YR == 4.434
        assert DEDDIE_ENERGY_RATE_EUR_KWH == 0.01415
        assert ADMIE_CAPACITY_RATE_EUR_KVA_YR == 4.430
        assert ADMIE_ENERGY_RATE_EUR_KWH == 0.00560
        assert ETMEAR_LV_RATE_EUR_KWH == 0.01700
        assert ETMEAR_MV_RATE_EUR_KWH == 0.01200
        assert YKO_RATE_EUR_KWH == 0.00690
        assert EFK_RATE_EUR_KWH == 0.00220
        assert VAT_RATE == 0.06

    def test_regulated_unit_rate_low_voltage(self):
        # LV: DEDDIE(0.01415) + ADMIE(0.00560) + ETMEAR(0.01700) + YKO(0.00690) + EFK(0.00220) + DETE(0.00095)
        # = 0.04680 €/kWh
        rate = calculate_regulated_unit_rate(power_factor=0.98, is_lv=True)
        assert rate == 0.04680

    def test_regulated_unit_rate_medium_voltage(self):
        # MV: ETMEAR drops from 0.01700 to 0.01200 -> delta = -0.00500 -> 0.04180 €/kWh
        rate = calculate_regulated_unit_rate(power_factor=0.98, is_lv=False)
        assert rate == 0.04180

    def test_capacity_standing_charges(self):
        # 35 kVA over 365 days -> DEDDIE = 4.434 * 35 = 155.19 €
        deddie_annual = calculate_deddie_capacity_charge(contracted_kva=35.0, days=365)
        assert deddie_annual == 155.19

        admie_annual = calculate_admie_capacity_charge(contracted_kva=35.0, days=365)
        assert admie_annual == 155.05

        summary = calculate_fixed_capacity_charges(contracted_kva=35.0, days=365)
        assert summary["total_capacity_eur"] == 310.24

        # Hourly rate: (4.434 + 4.430) * 35 / (365 * 24) = 310.24 / 8760 = 0.03542 €/h
        hourly_rate = calculate_hourly_capacity_rate(35.0)
        assert pytest.approx(hourly_rate, rel=1e-3) == 0.03542

    def test_regulated_breakdown(self):
        breakdown = get_regulated_breakdown(contracted_kva=35.0, power_factor=0.98, is_lv=True)
        assert breakdown.deddie_energy == 0.01415
        assert breakdown.admie_energy == 0.00560
        assert breakdown.etmear == 0.01700
        assert breakdown.total_regulated_per_kwh == 0.04680
        assert breakdown.pf_multiplier == 1.0


# --- 5. Penalties: Power Factor and Capacity Excess ---

class TestPenalties:
    def test_power_factor_no_penalty(self):
        assert calculate_power_factor_multiplier(0.98) == 1.0
        assert calculate_power_factor_multiplier(0.85) == 1.0
        assert calculate_power_factor_multiplier(1.00) == 1.0
        assert not is_power_factor_penalized(0.85)
        assert not is_power_factor_penalized(0.95)

    def test_power_factor_penalty_scaling(self):
        # cos φ = 0.68 -> 0.85 / 0.68 = 1.25 (+25% surcharge)
        assert calculate_power_factor_multiplier(0.68) == 1.25
        assert is_power_factor_penalized(0.68)

        # cos φ = 0.50 -> 0.85 / 0.50 = 1.70 (+70% surcharge)
        assert calculate_power_factor_multiplier(0.50) == 1.70

        # Boundary test: 0.8499 vs 0.8500
        assert calculate_power_factor_multiplier(0.8499) > 1.0
        assert calculate_power_factor_multiplier(0.8500) == 1.0

    def test_power_factor_extreme_and_capacitive(self):
        # Extreme zero power factor clamped to 0.01
        assert calculate_power_factor_multiplier(0.0) == 85.0

        # Capacitive (negative) power factor cos φ = -0.70 evaluates |cos φ| = 0.70
        # 0.85 / 0.70 ≈ 1.2143
        assert pytest.approx(calculate_power_factor_multiplier(-0.70), rel=1e-3) == 1.2143

    def test_power_factor_surcharge_calculation(self):
        base_charge = 100.0  # 100 €
        # At cos φ = 0.68, multiplier is 1.25 -> surcharge = 25.0 €
        surcharge = calculate_power_factor_surcharge(base_charge, 0.68)
        assert surcharge == 25.0

    def test_capacity_excess_evaluation(self):
        # Normal within limits
        res_normal = calculate_capacity_excess(apparent_power_kva=30.0, contracted_kva=35.0)
        assert not res_normal.is_breached
        assert res_normal.excess_kva == 0.0
        assert res_normal.hourly_penalty_eur == 0.0
        assert not res_normal.breaker_trip_risk

        # Exact boundary
        res_exact = calculate_capacity_excess(apparent_power_kva=35.0, contracted_kva=35.0)
        assert not res_exact.is_breached
        assert res_exact.excess_kva == 0.0

        # Overload: 42 kVA on 35 kVA contract (7 kVA excess = +20%)
        res_overload = calculate_capacity_excess(apparent_power_kva=42.0, contracted_kva=35.0)
        assert res_overload.is_breached
        assert res_overload.excess_kva == 7.0
        assert res_overload.overload_percentage == 20.0
        assert res_overload.hourly_penalty_eur > 0.0
        assert res_overload.breaker_trip_risk  # > 15% overload

        with pytest.raises(ValueError, match="Contracted capacity must be > 0"):
            calculate_capacity_excess(30.0, 0.0)

    def test_is_capacity_exceeded_helper(self):
        assert not is_capacity_exceeded(35.0, 35.0)
        assert is_capacity_exceeded(35.1, 35.0)


# --- 6. Real-Time Cost & Penalty Projection Engine ---

class TestCostCalculator:
    def test_realtime_cost_zero_power(self):
        profile = ContractProfile(contract_type="G21", color="green", contracted_capacity_kva=25.0)
        dt = utc_dt(2026, 7, 15, 12, 0, 0)
        res = calculate_realtime_cost(0.0, 0.0, dt, profile)
        assert res.running_cost_eur_per_h == 0.00
        assert res.incremental_cost_eur == 0.00
        assert not res.is_excess_breach

    def test_realtime_cost_g21_single_rate(self):
        # G21 uniform commercial rate 24h
        profile = ContractProfile(contract_type="G21", color="green", contracted_capacity_kva=25.0)
        dt_day = utc_dt(2026, 7, 15, 14, 30, 0)
        dt_night = utc_dt(2026, 7, 15, 3, 30, 0)

        res_day = calculate_realtime_cost(10.0, 1.0, dt_day, profile)
        res_night = calculate_realtime_cost(10.0, 1.0, dt_night, profile)

        # In G21 single-rate contract, rate does not vary by time of day
        assert res_day.current_rate_eur_per_kwh == res_night.current_rate_eur_per_kwh
        assert res_day.running_cost_eur_per_h == res_night.running_cost_eur_per_h

    def test_realtime_cost_g22_dual_rate_modifiers(self):
        # G22 dual-rate contract: +25% peak surcharge, -30% night discount
        profile = ContractProfile(contract_type="G22", color="green", contracted_capacity_kva=50.0)
        dt_peak = utc_dt(2026, 7, 15, 15, 0, 0)
        dt_offpeak = utc_dt(2026, 7, 15, 3, 0, 0)

        res_peak = calculate_realtime_cost(20.0, 1.0, dt_peak, profile)
        res_offpeak = calculate_realtime_cost(20.0, 1.0, dt_offpeak, profile)

        assert res_peak.is_peak_window
        assert not res_offpeak.is_peak_window
        assert res_peak.current_rate_eur_per_kwh > res_offpeak.current_rate_eur_per_kwh
        assert res_peak.running_cost_eur_per_h > res_offpeak.running_cost_eur_per_h

    def test_peak_excess_breach_detection(self):
        # Contract with 25 kW threshold
        profile = ContractProfile(
            contract_type="G22",
            color="green",
            contracted_capacity_kva=35.0,
            peak_threshold_kw=22.0,
        )
        dt_peak = utc_dt(2026, 7, 15, 15, 0, 0)

        # Above threshold during peak -> breach
        res_breach = calculate_realtime_cost(28.0, 1.0, dt_peak, profile)
        assert res_breach.is_excess_breach
        assert res_breach.excess_power_kw == 6.0
        assert res_breach.projected_excess_penalty_eur > 0.0

        # Below threshold during peak -> no breach
        res_ok = calculate_realtime_cost(18.0, 1.0, dt_peak, profile)
        assert not res_ok.is_excess_breach
        assert res_ok.excess_power_kw == 0.0
        assert res_ok.projected_excess_penalty_eur == 0.0

        # Above threshold outside peak (e.g. 10:00 AM) -> no peak breach
        dt_normal = utc_dt(2026, 7, 15, 10, 0, 0)
        res_normal = calculate_realtime_cost(28.0, 1.0, dt_normal, profile)
        assert not res_normal.is_excess_breach
        assert res_normal.excess_power_kw == 0.0

    def test_projected_peak_penalty_pure_function(self):
        # 10 kW excess, 0.08 €/kWh rate difference, 2.0 remaining hours, 6% VAT
        # 10 * 0.08 * 2.0 * 1.06 = 1.60 * 1.06 = 1.696 ≈ 1.70 €
        penalty = calculate_projected_peak_penalty(
            excess_power_kw=10.0,
            rate_difference_eur_kwh=0.08,
            remaining_peak_hours=2.0,
            vat_rate=0.06,
        )
        assert penalty == 1.70

        # Non-positive parameters return 0.0
        assert calculate_projected_peak_penalty(0.0, 0.08, 2.0) == 0.0
        assert calculate_projected_peak_penalty(10.0, 0.0, 2.0) == 0.0
        assert calculate_projected_peak_penalty(10.0, 0.08, 0.0) == 0.0

    def test_low_power_factor_impact_on_cost(self):
        profile = ContractProfile(contract_type="G22", color="green", contracted_capacity_kva=35.0)
        dt = utc_dt(2026, 7, 15, 12, 0, 0)

        res_high_pf = calculate_realtime_cost(30.0, 1.0, dt, profile, power_factor=0.98)
        res_low_pf = calculate_realtime_cost(30.0, 1.0, dt, profile, power_factor=0.68)

        # Regulated rate is higher for low PF
        assert res_low_pf.regulated_rate_eur_per_kwh > res_high_pf.regulated_rate_eur_per_kwh
        assert res_low_pf.running_cost_eur_per_h > res_high_pf.running_cost_eur_per_h

    def test_daily_spend_tracker(self):
        tracker = DailySpendTracker()
        dt1 = utc_dt(2026, 9, 14, 10, 0, 0)
        dt2 = utc_dt(2026, 9, 14, 11, 0, 0)
        dt_next_day = utc_dt(2026, 9, 15, 8, 0, 0)

        tracker.record_reading(dt1, delta_kwh=5.0, cost_eur=1.20)
        tracker.record_reading(dt2, delta_kwh=6.0, cost_eur=1.45)
        tracker.record_reading(dt_next_day, delta_kwh=10.0, cost_eur=2.50)

        # Sep 14 checks
        assert tracker.get_daily_spend(dt1.date()) == 2.65
        assert tracker.get_daily_kwh(dt1.date()) == 11.0

        # Sep 15 checks
        assert tracker.get_daily_spend(dt_next_day.date()) == 2.50
        assert tracker.get_daily_kwh(dt_next_day.date()) == 10.0

        summary = tracker.get_summary()
        assert summary["total_spend_eur"] == 5.15
        assert summary["total_kwh"] == 21.0
        assert summary["days_tracked"] == 2

        # Reset single day
        tracker.reset(dt1.date())
        assert tracker.get_daily_spend(dt1.date()) == 0.00
        assert tracker.get_daily_spend(dt_next_day.date()) == 2.50

        # Reset all
        tracker.reset()
        assert tracker.get_summary()["days_tracked"] == 0

    def test_realtime_cost_yellow_and_dynamic_profiles(self):
        yellow_profile = ContractProfile(contract_type=TariffContract.G21, color=TariffColor.YELLOW, contracted_capacity_kva=25.0)
        dynamic_profile = ContractProfile(contract_type=TariffContract.G22, color=TariffColor.DYNAMIC, contracted_capacity_kva=50.0)
        dt = utc_dt(2026, 7, 15, 12, 0, 0)

        res_yellow = calculate_realtime_cost(15.0, 1.0, dt, yellow_profile, tea_eur_mwh=130.0)
        assert res_yellow.current_rate_eur_per_kwh > 0.0
        assert res_yellow.running_cost_eur_per_h > 0.0

        res_dynamic = calculate_realtime_cost(20.0, 1.0, dt, dynamic_profile, tea_eur_mwh=130.0)
        assert res_dynamic.current_rate_eur_per_kwh > 0.0
        assert res_dynamic.running_cost_eur_per_h > 0.0

    def test_realtime_cost_g23_medium_voltage(self):
        mv_profile = ContractProfile(contract_type=TariffContract.G23, color=TariffColor.GREEN, contracted_capacity_kva=300.0)
        dt_peak = utc_dt(2026, 7, 15, 15, 0, 0)
        res_mv = calculate_realtime_cost(100.0, 5.0, dt_peak, mv_profile)
        assert res_mv.is_peak_window
        assert res_mv.current_rate_eur_per_kwh > 0.0

    def test_realtime_cost_with_hourly_capacity_rate(self):
        profile = ContractProfile(contract_type=TariffContract.G22, color=TariffColor.GREEN, contracted_capacity_kva=50.0)
        dt = utc_dt(2026, 7, 15, 12, 0, 0)
        res_without = calculate_realtime_cost(20.0, 1.0, dt, profile, include_hourly_capacity_rate=False)
        res_with = calculate_realtime_cost(20.0, 1.0, dt, profile, include_hourly_capacity_rate=True)
        assert res_with.hourly_capacity_rate_eur_h > 0.0
        assert res_with.running_cost_eur_per_h > res_without.running_cost_eur_per_h

    def test_contracts_edge_cases_and_season_parameters(self):
        # Passing enums to from_string
        assert TariffContract.from_string(TariffContract.G21) == TariffContract.G21
        assert TariffColor.from_string(TariffColor.GREEN) == TariffColor.GREEN

        dt_summer = utc_dt(2026, 7, 15, 15, 0, 0)
        assert is_peak_window(dt_summer, season="summer")
        assert not is_peak_window(dt_summer, season="winter")
        assert is_peak_window(dt_summer, season=Season.SUMMER)

        with pytest.raises(ValueError, match="Unknown season"):
            is_peak_window(dt_summer, season="autumn")

        with pytest.raises(TypeError, match="Unknown season type"):
            is_peak_window(dt_summer, season=123)



        # get_remaining_peak_hours with explicit seasons
        assert pytest.approx(get_remaining_peak_hours(dt_summer, season="summer"), rel=1e-3) == 2.0
        assert get_remaining_peak_hours(dt_summer, season="winter") == 0.0

        dt_winter = utc_dt(2026, 1, 14, 18, 0, 0)
        assert pytest.approx(get_remaining_peak_hours(dt_winter, season="winter"), rel=1e-3) == 3.0

    def test_normalizing_to_kwh_edge_cases(self):
        # Inputs <= 1.0 passed directly in €/kWh
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=0.105, ll_eur_mwh=0.095, lu_eur_mwh=0.115, unit="EUR_KWH")
        assert md == 0.0

        rate_yellow = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.120, p_base=0.040, unit="EUR_KWH")
        assert rate_yellow == 0.1912

        # YellowDynamicEngine dynamic computation
        engine = YellowDynamicEngine()
        dyn_rate = engine.compute_dynamic(120.0)
        assert dyn_rate > 0.0

        # GreenTariffEngine with monthly kWh and prompt discount
        g_engine = GreenTariffEngine(p_base=0.160, prompt_discount_percent=5.0, fixed_monthly_fee_eur=4.0)
        rate_g = g_engine.compute_supply_rate(tea_eur_mwh=105.0, monthly_kwh=1000.0)
        # 0.160 * 0.95 + 0.0 + (4.0 / 1000) = 0.152 + 0.004 = 0.156
        assert rate_g == 0.156

    def test_duck_typed_profile_and_winter_enum(self):
        # Object with raw string attributes and no peak_threshold_kw
        class DummyFacility:
            contract_code = "G21"
            tariff_color = "green"
            contracted_kva = 25.0

        dummy = DummyFacility()
        dt = utc_dt(2026, 1, 14, 18, 0, 0)
        res = calculate_realtime_cost(10.0, 1.0, dt, dummy)
        assert res.excess_power_kw == 0.0
        assert res.current_rate_eur_per_kwh > 0.0

        # Season.WINTER enum passed directly
        assert is_peak_window(dt, season=Season.WINTER)


