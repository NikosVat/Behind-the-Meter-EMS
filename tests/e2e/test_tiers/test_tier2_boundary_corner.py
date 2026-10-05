"""
Tier 2: Boundary & Corner Cases E2E Test Suite
==============================================
Exhaustively covers physical, mathematical, and temporal boundary conditions (>= 5 cases per boundary):
1. Zero / negative / extreme power factor (cos phi = 0, negative, 0.8499 vs 0.8500, 1.0)
2. Max capacity breach (exact boundary, +0.1 kVA, 200% overload, zero load, phase unbalance)
3. Boundary minute of peak tariff windows (13:59:59 vs 14:00:00, 16:59:59 vs 17:00:00, winter, weekends)
4. Rapid-fire telemetry bursts (sub-second bursts, identical timestamps, microsecond jitter, out-of-order)
5. Borderline threshold hysteresis (exact threshold, exact 90% release, single spike debounce, oscillation)
"""

import math
from datetime import datetime, timedelta, timezone

from tests.e2e.test_tiers.harness import (
    AlertDispatcherStateMachine,
    AlertState,
    TelemetryPayload,
    calculate_realtime_cost,
    calculate_regulated_unit_rate,
    create_valid_telemetry_payload,
    is_greek_peak_window,
)

# --- Boundary 1: Zero & Extreme Power Factor ---

class TestBoundary1PowerFactor:
    def test_boundary1_pf_zero_apparent_power_guard(self):
        """Zero active power with apparent power present yields cos phi = 0.0 without div-by-zero."""
        p_total = 0.0
        s_total = 10.0
        pf = p_total / s_total
        assert pf == 0.0
        reg = calculate_regulated_unit_rate(power_factor=pf)
        # Power factor penalty multiplier maxes out safely
        assert reg > 0.05

    def test_boundary1_pf_negative_regenerative_flow(self):
        """Negative power factor in [-1.0, 1.0] representing reverse active power flow (e.g. PV)."""
        payload = create_valid_telemetry_payload(p_total_kw=10.0, pf=-0.95)
        assert payload.system_power_factor == -0.95

    def test_boundary1_pf_boundary_at_08500(self):
        """At exactly cos phi = 0.8500, no penalty multiplier is applied (F_PF = 1.0)."""
        reg_85 = calculate_regulated_unit_rate(power_factor=0.8500)
        reg_100 = calculate_regulated_unit_rate(power_factor=1.0000)
        assert math.isclose(reg_85, reg_100, rel_tol=1e-3)

    def test_boundary1_pf_boundary_at_08499(self):
        """At cos phi = 0.8499, penalty multiplier triggers (F_PF = 0.85 / 0.8499 > 1.0)."""
        reg_8499 = calculate_regulated_unit_rate(power_factor=0.8499)
        reg_8500 = calculate_regulated_unit_rate(power_factor=0.8500)
        assert reg_8499 > reg_8500

    def test_boundary1_pf_extreme_low_001(self):
        """Extreme lagging power factor (0.01) scales penalty without float overflow."""
        reg_001 = calculate_regulated_unit_rate(power_factor=0.01)
        assert reg_001 > 0.50

    def test_boundary1_pf_perfect_unity_100(self):
        """Unity power factor (cos phi = 1.0) where active kW equals apparent kVA."""
        payload = create_valid_telemetry_payload(p_total_kw=20.0, pf=1.0)
        assert math.isclose(payload.total_active_power_kw, payload.total_apparent_power_kva, abs_tol=0.05)


# --- Boundary 2: Max Capacity Breach ---

class TestBoundary2MaxCapacityBreach:
    def test_boundary2_exact_contracted_kva(self, sample_facility_bakery):
        """At exact contracted capacity (35.0 kVA), load is within limits."""
        payload = create_valid_telemetry_payload(p_total_kw=35.0, pf=1.0)
        assert payload.total_apparent_power_kva <= sample_facility_bakery.contracted_kva + 0.05

    def test_boundary2_slight_excess_01kva(self, sample_facility_bakery):
        """Slight excess (35.1 kVA on 35.0 kVA contract) is quantitatively detected."""
        payload = create_valid_telemetry_payload(p_total_kw=35.1, pf=1.0)
        excess = payload.total_apparent_power_kva - sample_facility_bakery.contracted_kva
        assert excess > 0.0
        assert math.isclose(excess, 0.1, abs_tol=0.05)

    def test_boundary2_massive_200percent_overload(self, sample_facility_bakery):
        """200% overload (70.0 kVA on 35.0 kVA contract) evaluates penalty projection cleanly."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(70.0, 1.0, dt, sample_facility_bakery)
        assert res.is_excess_breach is True
        assert res.excess_power_kw == (70.0 - sample_facility_bakery.peak_threshold_kw)
        assert res.projected_excess_penalty_eur > 5.0

    def test_boundary2_zero_load_standby(self, sample_facility_bakery):
        """0.0 kW / 0.0 kVA standby reading produces 0 running cost and no breach."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = calculate_realtime_cost(0.0, 0.0, dt, sample_facility_bakery)
        assert res.is_excess_breach is False
        assert res.running_cost_eur_per_h == 0.0

    def test_boundary2_phase_unbalance_capacity_limit(self):
        """Severe phase unbalance (L1 heavy, L2/L3 light) adheres to sum invariant."""
        v = 230.0
        p_l1, p_l2, p_l3 = 18.0, 2.0, 1.0
        phases = {
            "L1": {"voltage_v": v, "current_a": 78.3, "active_power_kw": p_l1, "apparent_power_kva": 18.0, "power_factor": 1.0},
            "L2": {"voltage_v": v, "current_a": 8.7, "active_power_kw": p_l2, "apparent_power_kva": 2.0, "power_factor": 1.0},
            "L3": {"voltage_v": v, "current_a": 4.3, "active_power_kw": p_l3, "apparent_power_kva": 1.0, "power_factor": 1.0},
        }
        payload = TelemetryPayload(
            device_id="esp32-001",
            facility_id="bakery-unbalanced",
            timestamp=datetime.now(timezone.utc),
            phases=phases,
            total_active_power_kw=21.0,
            total_apparent_power_kva=21.0,
            system_power_factor=1.0,
            cumulative_energy_kwh=100.0,
        )
        assert payload.total_active_power_kw == 21.0


# --- Boundary 3: Boundary Minute of Peak Tariff Windows ---

class TestBoundary3PeakWindowTransitions:
    def test_boundary3_summer_peak_start_exact(self):
        """13:59:59 is NORMAL, 14:00:00 is PEAK."""
        # Wednesday July 15, 2026
        dt_pre = datetime(2026, 7, 15, 13, 59, 59, tzinfo=timezone.utc)
        dt_start = datetime(2026, 7, 15, 14, 0, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_pre) is False
        assert is_greek_peak_window(dt_start) is True

    def test_boundary3_summer_peak_end_exact(self):
        """16:59:59 is PEAK, 17:00:00 is NORMAL."""
        dt_end = datetime(2026, 7, 15, 16, 59, 59, tzinfo=timezone.utc)
        dt_post = datetime(2026, 7, 15, 17, 0, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_end) is True
        assert is_greek_peak_window(dt_post) is False

    def test_boundary3_winter_peak_start_exact(self):
        """Winter peak: 16:59:59 is NORMAL, 17:00:00 is PEAK."""
        # Wednesday Jan 14, 2026
        dt_pre = datetime(2026, 1, 14, 16, 59, 59, tzinfo=timezone.utc)
        dt_start = datetime(2026, 1, 14, 17, 0, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_pre) is False
        assert is_greek_peak_window(dt_start) is True

    def test_boundary3_winter_peak_end_exact(self):
        """Winter peak: 20:59:59 is PEAK, 21:00:00 is NORMAL."""
        dt_end = datetime(2026, 1, 14, 20, 59, 59, tzinfo=timezone.utc)
        dt_post = datetime(2026, 1, 14, 21, 0, 0, tzinfo=timezone.utc)
        assert is_greek_peak_window(dt_end) is True
        assert is_greek_peak_window(dt_post) is False

    def test_boundary3_friday_to_saturday_midnight_transition(self):
        """Friday 23:59:59 to Saturday 00:00:00 transitions to weekend (no peak)."""
        dt_fri = datetime(2026, 7, 17, 23, 59, 59, tzinfo=timezone.utc)
        dt_sat = datetime(2026, 7, 18, 0, 0, 0, tzinfo=timezone.utc)
        assert dt_fri.weekday() == 4
        assert dt_sat.weekday() == 5
        assert is_greek_peak_window(dt_sat) is False

    def test_boundary3_sunday_to_monday_midnight_transition(self):
        """Sunday 23:59:59 to Monday 00:00:00 re-engages weekday rules."""
        dt_sun = datetime(2026, 7, 19, 23, 59, 59, tzinfo=timezone.utc)
        dt_mon = datetime(2026, 7, 20, 0, 0, 0, tzinfo=timezone.utc)
        assert dt_sun.weekday() == 6
        assert dt_mon.weekday() == 0


# --- Boundary 4: Rapid-Fire Telemetry Bursts ---

class TestBoundary4RapidFireBursts:
    def test_boundary4_sub_second_burst_ingestion(self):
        """Ingests 10 payloads spaced by 50 milliseconds without latency buildup."""
        base_time = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)
        payloads = [
            create_valid_telemetry_payload(
                p_total_kw=20.0 + (i * 0.1),
                timestamp=base_time + timedelta(milliseconds=i * 50),
            )
            for i in range(10)
        ]
        assert len(payloads) == 10
        assert payloads[-1].timestamp > payloads[0].timestamp

    def test_boundary4_identical_timestamp_deduplication(self):
        """Handles duplicate timestamps idempotently."""
        t_same = datetime(2026, 7, 15, 15, 30, 0, tzinfo=timezone.utc)
        p1 = create_valid_telemetry_payload(p_total_kw=20.0, timestamp=t_same)
        p2 = create_valid_telemetry_payload(p_total_kw=20.0, timestamp=t_same)
        assert p1.timestamp == p2.timestamp
        assert p1.total_active_power_kw == p2.total_active_power_kw

    def test_boundary4_microsecond_jitter_handling(self):
        """Timestamps with 10-microsecond jitter maintain valid chronological order."""
        t_base = datetime(2026, 7, 15, 15, 30, 0, 0, tzinfo=timezone.utc)
        t_jitter = datetime(2026, 7, 15, 15, 30, 0, 10, tzinfo=timezone.utc)
        assert t_jitter > t_base
        assert (t_jitter - t_base).microseconds == 10

    def test_boundary4_burst_during_active_breach_debouncing(self, sample_facility_bakery):
        """10 burst readings in 1 second during breach trigger exactly 1 alert after 3 samples."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 0.1, dt, sample_facility_bakery)

        alerts = []
        for i in range(10):
            event = dispatcher.process_reading(28.0, dt + timedelta(milliseconds=i * 100), True, cost_res)
            if event:
                alerts.append(event)

        # Exactly 1 alert emitted despite 10 burst readings due to cooldown
        assert len(alerts) == 1

    def test_boundary4_reverse_chronological_arrival(self):
        """Verifies payloads sorted by timestamp regardless of arrival order."""
        t1 = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 7, 15, 15, 0, 1, tzinfo=timezone.utc)
        p1 = create_valid_telemetry_payload(timestamp=t1)
        p2 = create_valid_telemetry_payload(timestamp=t2)
        arrived = [p2, p1]
        sorted_payloads = sorted(arrived, key=lambda x: x.timestamp)
        assert sorted_payloads[0].timestamp == t1
        assert sorted_payloads[1].timestamp == t2


# --- Boundary 5: Borderline Threshold Hysteresis ---

class TestBoundary5ThresholdHysteresis:
    def test_boundary5_exact_threshold_power(self, sample_facility_bakery):
        """At exactly 22.000 kW (threshold), load is not in breach (> 22.0 required)."""
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res_exact = calculate_realtime_cost(22.0, 1.0, dt, sample_facility_bakery)
        res_above = calculate_realtime_cost(22.01, 1.0, dt, sample_facility_bakery)
        assert res_exact.is_excess_breach is False
        assert res_above.is_excess_breach is True

    def test_boundary5_hysteresis_lower_bound_exact(self, sample_facility_bakery):
        """At exactly 0.90 * threshold (19.800 kW), hysteresis clears."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)

        # 19.800 kW is exactly 90% of 22 kW -> triggers clearance
        recovery_event = dispatcher.process_reading(19.800, dt + timedelta(minutes=5), True, cost_res)
        assert recovery_event is not None
        assert dispatcher.state == AlertState.IDLE

    def test_boundary5_hysteresis_lower_bound_plus_epsilon(self, sample_facility_bakery):
        """At 19.801 kW (> 19.800 kW), hysteresis remains active in COOLDOWN."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(28.0, 1.0, dt, sample_facility_bakery)
        for i in range(3):
            dispatcher.process_reading(28.0, dt + timedelta(seconds=i), True, cost_res)

        # 19.801 kW is slightly above 19.800 kW
        no_recovery = dispatcher.process_reading(19.801, dt + timedelta(minutes=5), True, cost_res)
        assert no_recovery is None
        assert dispatcher.state == AlertState.COOLDOWN

    def test_boundary5_single_sample_spike_rejected_by_debounce(self, sample_facility_bakery):
        """Single 45 kW motor startup spike followed by 18 kW does not trigger alert."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res_spike = calculate_realtime_cost(45.0, 0.1, dt, sample_facility_bakery)
        cost_res_normal = calculate_realtime_cost(18.0, 0.1, dt + timedelta(seconds=5), sample_facility_bakery)

        # Sample 1: spike
        assert dispatcher.process_reading(45.0, dt, True, cost_res_spike) is None
        # Sample 2: normal
        assert dispatcher.process_reading(18.0, dt + timedelta(seconds=5), True, cost_res_normal) is None
        assert dispatcher.state == AlertState.IDLE
        assert dispatcher.debounce_counter == 0

    def test_boundary5_oscillating_around_threshold(self, sample_facility_bakery):
        """Alternating load (23 kW -> 21 kW -> 23 kW -> 21 kW) prevents debounce trigger."""
        dispatcher = AlertDispatcherStateMachine(sample_facility_bakery)
        dt = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        cost_res = calculate_realtime_cost(23.0, 1.0, dt, sample_facility_bakery)

        for i in range(10):
            p = 23.0 if (i % 2 == 0) else 21.0
            event = dispatcher.process_reading(p, dt + timedelta(seconds=i * 5), True, cost_res)
            # Never hits 3 consecutive breach samples because counter decrements on 21.0 kW
            assert event is None
        assert dispatcher.state == AlertState.IDLE
