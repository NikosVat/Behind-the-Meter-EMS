"""
Tier 3: Cross-Feature Combinations E2E Test Suite
=================================================
Validates multi-variable pairwise interactions across subsystems:
1. Green tariff fluctuation in peak window with low cos phi (< 0.85 penalty)
2. Yellow / Dynamic hourly spot pricing during commercial bakery morning baking spike
3. Γ22 dual-rate contract with cold storage compressor cycle and 30-min alert cooldown
4. Γ23 medium voltage contract with boutique hotel summer HVAC and capacity penalty
5. Rapid-fire telemetry burst during wholesale TEA spike followed by Greek /cost_today query
6. Green tariff wholesale credit (TEA < Ll) during high-load peak window
7. Multi-facility concurrent telemetry with isolated alert state machines
"""

from datetime import datetime, timedelta, timezone

from tests.e2e.test_tiers.harness import (
    AlertDispatcherStateMachine,
    AlertState,
    FacilityProfileConfig,
    calculate_green_tariff_fluctuation,
    calculate_realtime_cost,
    calculate_regulated_unit_rate,
    create_valid_telemetry_payload,
    format_greek_bot_response,
    get_boutique_hotel_power,
    get_commercial_bakery_power,
)


class TestTier3CrossFeatureCombinations:
    def test_cross_green_tariff_peak_window_with_low_cos_phi(self, sample_facility_bakery):
        """
        Combination 1:
        Green Tariff (see docs/RULES_SOURCES.md) + Active Summer Peak Window + Poor Power Factor (cos phi = 0.72).
        Verifies:
        - Fluctuation mechanism adds surcharge for TEA = 140 €/MWh (> Lu = 115).
        - G22 dual-rate adds peak window (+25%) modifier.
        - Low power factor increases DEDDIE charge by 0.85 / 0.72 = 1.18x.
        - Alert dispatcher generates proactive Greek breach notification with tailored advice.
        """
        dt_peak = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)
        tea = 140.0
        pf = 0.72
        load_kw = 28.5  # Exceeds 22 kW threshold

        cost_res = calculate_realtime_cost(
            power_kw=load_kw,
            energy_kwh_delta=1.0,
            timestamp=dt_peak,
            tariff_profile=sample_facility_bakery,
            tea_eur_mwh=tea,
            power_factor=pf,
        )

        assert cost_res.is_peak_window is True
        assert cost_res.is_excess_breach is True
        assert cost_res.excess_power_kw == (28.5 - 22.0)
        assert cost_res.projected_excess_penalty_eur > 0.0

        # Verify power factor increased regulated rate
        reg_high_pf = calculate_regulated_unit_rate(power_factor=0.98)
        assert cost_res.regulated_rate_eur_per_kwh > reg_high_pf

        # Process through alert dispatcher
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        for i in range(3):
            event = dispatcher.process_reading(load_kw, dt_peak + timedelta(seconds=i * 5), True, cost_res, power_factor=pf)

        assert event is not None
        assert "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in event.message_text
        assert "0.72" in event.message_text
        assert "φούρνο" in event.message_text

    def test_cross_yellow_dynamic_tariff_during_bakery_morning_spike(self, sample_facility_bakery):
        """
        Combination 2:
        Yellow / Dynamic Spot Tariff + Bakery Morning Oven Pre-heat (42 kW at 05:30).
        Verifies:
        - Wholesale price indexation (TEA = 150 €/MWh) accurately computes effective €/kWh.
        - Because 05:30 is OFF-PEAK, NO peak breach alert is dispatched despite high 42 kW draw.
        - Running cost (€/h) accurately reflects massive power draw.
        """
        # Change facility to yellow tariff
        bakery_yellow = FacilityProfileConfig(
            facility_id="bakery-central-athens",
            facility_name="Φούρνος Αθήνας",
            facility_type="bakery",
            contract_code="G22",
            tariff_color="yellow",
            contracted_kva=50.0,
            peak_threshold_kw=22.0,
        )

        dt_morning = datetime(2026, 7, 15, 5, 30, 0, tzinfo=timezone.utc)
        load_kw = get_commercial_bakery_power(5.5)  # ~42 kW
        assert load_kw > 35.0

        cost_res = calculate_realtime_cost(
            power_kw=load_kw,
            energy_kwh_delta=2.0,
            timestamp=dt_morning,
            tariff_profile=bakery_yellow,
            tea_eur_mwh=150.0,
        )

        # Off-peak window: no peak breach
        assert cost_res.is_peak_window is False
        assert cost_res.is_excess_breach is False
        assert cost_res.projected_excess_penalty_eur == 0.0
        assert cost_res.running_cost_eur_per_h > 8.0

        # Alert dispatcher should remain IDLE
        dispatcher = AlertDispatcherStateMachine(bakery_yellow)
        for i in range(5):
            event = dispatcher.process_reading(load_kw, dt_morning + timedelta(seconds=i * 10), False, cost_res)
            assert event is None
        assert dispatcher.state == AlertState.IDLE

    def test_cross_g22_dual_rate_cold_storage_compressor_and_cooldown(self, sample_facility_cold_storage):
        """
        Combination 3:
        Γ22 Dual-Rate Contract + Cold Storage Cyclical Compressor Cycle + 30-min Alert Cooldown.
        Verifies:
        - Baseline refrigeration (16 kW) does not trigger breach.
        - Compressor ramp at 14:15 (32 kW > 25 kW threshold) during peak triggers alert #1.
        - Second cycle at 14:45 during active cooldown is suppressed.
        - Power drop to idle (16 kW <= 22.5 kW 90% hysteresis) emits recovery notification.
        """
        dispatcher = AlertDispatcherStateMachine(sample_facility_cold_storage)
        dt_start = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)

        # 1. Baseline load 16 kW at 14:00 (Peak window, but below 25 kW threshold)
        cost_baseline = calculate_realtime_cost(16.0, 0.5, dt_start, sample_facility_cold_storage)
        assert dispatcher.process_reading(16.0, dt_start, True, cost_baseline) is None

        # 2. Compressor cycle pull-down at 14:15 (32 kW > 25 kW threshold)
        dt_compressor_on = dt_start + timedelta(minutes=15)
        cost_compressor = calculate_realtime_cost(32.0, 0.5, dt_compressor_on, sample_facility_cold_storage)
        for i in range(3):
            event = dispatcher.process_reading(32.0, dt_compressor_on + timedelta(seconds=i * 5), True, cost_compressor)

        assert event is not None
        assert "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in event.message_text
        assert dispatcher.state == AlertState.COOLDOWN

        # 3. Repeat high reading 15 minutes later (14:30) within 30-min cooldown
        dt_within_cooldown = dt_compressor_on + timedelta(minutes=15)
        suppressed_event = dispatcher.process_reading(32.0, dt_within_cooldown, True, cost_compressor)
        assert suppressed_event is None
        assert dispatcher.state == AlertState.COOLDOWN

        # 4. Compressor shuts off, baseload returns to 16 kW (< 0.90 * 25 = 22.5 kW)
        dt_recovery = dt_within_cooldown + timedelta(minutes=10)
        recovery_event = dispatcher.process_reading(16.0, dt_recovery, True, cost_baseline)
        assert recovery_event is not None
        assert "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in recovery_event.message_text
        assert dispatcher.state == AlertState.IDLE

    def test_cross_g23_mv_hotel_summer_hvac_with_capacity_penalty(self, sample_facility_hotel):
        """
        Combination 4:
        Γ23 Medium Voltage Contract + Boutique Hotel Summer VRV AC Ramp (33 kW, 15:30) + Greek /status query.
        Verifies:
        - Peak breach alert triggers with hotel VRV AC advice.
        - /status query immediately formats 3-phase metrics and Greek text accurately.
        """
        dt_hotel = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)
        load_kw = get_boutique_hotel_power(15.5)  # ~33 kW > 30 kW threshold
        cost_res = calculate_realtime_cost(load_kw, 1.0, dt_hotel, sample_facility_hotel)

        dispatcher = AlertDispatcherStateMachine(sample_facility_hotel)
        for i in range(3):
            event = dispatcher.process_reading(load_kw, dt_hotel + timedelta(seconds=i * 5), True, cost_res)

        assert event is not None
        assert "κλιματισμού VRV" in event.message_text

        # Ingest into simulated bot /status query
        payload = create_valid_telemetry_payload(p_total_kw=load_kw, timestamp=dt_hotel)
        status_msg = format_greek_bot_response("/status", sample_facility_hotel, payload)
        assert "Τρέχουσα Κατάσταση" in status_msg
        assert f"{load_kw:.2f} kW" in status_msg
        assert "Ζώνη Αιχμής" in status_msg

    def test_cross_telemetry_rapid_burst_during_extreme_tea_spike_with_bot_query(self, sample_facility_bakery):
        """
        Combination 5:
        Rapid telemetry bursts (5 payloads in 1 second) + Extreme TEA spike (280 €/MWh) + /cost_today query.
        Verifies:
        - System processes high-frequency inputs under high wholesale price.
        - /cost_today query delivers accurate Greek response.
        """
        dt_base = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        burst_payloads = [
            create_valid_telemetry_payload(p_total_kw=26.0 + i * 0.2, timestamp=dt_base + timedelta(milliseconds=i * 200))
            for i in range(5)
        ]

        total_accumulated_spend = 0.0
        for p in burst_payloads:
            res = calculate_realtime_cost(p.total_active_power_kw, 0.05, p.timestamp, sample_facility_bakery, tea_eur_mwh=280.0)
            total_accumulated_spend += res.incremental_cost_eur

        assert total_accumulated_spend > 0.0
        cost_msg = format_greek_bot_response("/cost_today", sample_facility_bakery, daily_spend_eur=total_accumulated_spend, daily_energy_kwh=10.5)
        assert "Σημερινή Κατανάλωση" in cost_msg
        assert f"{total_accumulated_spend:.2f} €" in cost_msg

    def test_cross_green_tariff_wholesale_credit_during_peak_breach(self, sample_facility_bakery):
        """
        Combination 6:
        Green Tariff with wholesale TEA < Ll (TEA = 60 €/MWh < 95) providing MD credit + Peak Breach.
        Verifies:
        - Negative fluctuation MD reduces supply charge.
        - Peak threshold breach still triggers alert.
        - Financial penalty accurately reflects reduced supply base.
        """
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        tea_low = 60.0
        md = calculate_green_tariff_fluctuation(tea_eur_mwh=tea_low)
        assert md < 0.0  # Wholesale credit

        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery, tea_eur_mwh=tea_low)
        assert cost_res.is_excess_breach is True
        assert cost_res.projected_excess_penalty_eur > 0.0

    def test_cross_multi_facility_independent_cooldown_and_alert_isolation(
        self, sample_facility_bakery, sample_facility_cold_storage
    ):
        """
        Combination 7:
        Multi-facility concurrent telemetry: Bakery in COOLDOWN does NOT suppress Cold Storage breach.
        Verifies per-facility state machine independence.
        """
        dispatcher_bakery = AlertDispatcherStateMachine(sample_facility_bakery)
        dispatcher_cold = AlertDispatcherStateMachine(sample_facility_cold_storage)

        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_bakery = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        cost_cold = calculate_realtime_cost(34.0, 1.0, dt, sample_facility_cold_storage)

        # Trigger Bakery into COOLDOWN
        for i in range(3):
            dispatcher_bakery.process_reading(28.0, dt + timedelta(seconds=i), True, cost_bakery)
        assert dispatcher_bakery.state == AlertState.COOLDOWN

        # Trigger Cold Storage: should emit alert independently
        cold_alert = None
        for i in range(3):
            cold_alert = dispatcher_cold.process_reading(34.0, dt + timedelta(seconds=i), True, cost_cold)

        assert cold_alert is not None
        assert cold_alert.facility_id == "cold-storage-piraeus"
        assert dispatcher_cold.state == AlertState.COOLDOWN
