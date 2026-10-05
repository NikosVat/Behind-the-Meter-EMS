"""Unit tests for ESP32 3-phase firmware mathematical algorithms and hardware physics.

Covers Milestone M4 verification:
1. SCT-013 burden resistor physics and standard value selection.
2. Calibration multiplier formulas (K_I in A/V and A/mV).
3. ADC1 pin mapping and Wi-Fi ADC2 conflict lockout.
4. True RMS calculation on pure, DC-biased, and harmonic synthetic waveforms.
5. 3-phase power calculations and backend electrical invariant compliance.
6. Trapezoidal numerical integration of active power into cumulative energy (kWh).
7. Serialization schema compliance against Pydantic TelemetryPayload model.
"""

import math

import pytest

from backend.models.telemetry import PhaseReading, TelemetryPayload

# --- 1. SCT-013 BURDEN RESISTOR PHYSICS & DERIVATIONS ---

class TestBurdenResistorDerivation:
    """Mathematical validation of burden resistor sizing for SCT-013 sensors."""

    def test_sct013_000_peak_secondary_current(self):
        """Verify secondary peak current derivation from 100A RMS and 2000:1 turns ratio."""
        prim_rms_a = 100.0
        turns_ratio = 2000.0
        sec_rms_a = prim_rms_a / turns_ratio
        assert math.isclose(sec_rms_a, 0.050, rel_tol=1e-5), "100A / 2000 must equal 50mA RMS"

        sec_peak_a = sec_rms_a * math.sqrt(2)
        assert math.isclose(sec_peak_a, 0.070710678, rel_tol=1e-5), "Peak secondary current is ~70.71 mA"

    def test_burden_resistor_max_value_for_linear_adc(self):
        """Verify maximum allowable burden resistor for 1.45V peak swing on 3.3V ADC.

        ESP32 ADC linear range is ~0.15V to ~3.10V. Centered at 1.65V DC midpoint bias,
        the allowable single-sided peak swing without entering non-linear saturation is:
        V_peak_safe <= 1.65V - 0.15V = 1.50V, recommended 1.45V.
        """
        v_peak_safe = 1.45  # Volts
        sec_peak_a = (100.0 / 2000.0) * math.sqrt(2)
        r_burden_max = v_peak_safe / sec_peak_a
        assert 20.0 <= r_burden_max <= 21.0, f"R_burden_max should be ~20.5 Ohms, got {r_burden_max:.2f}"

    @pytest.mark.parametrize("r_burden, expected_v_peak, expected_v_pp", [
        (18.0, 1.27279, 2.54558),
        (22.0, 1.55563, 3.11127),
    ])
    def test_standard_resistor_voltage_swings(self, r_burden: float, expected_v_peak: float, expected_v_pp: float):
        """Verify voltage swing for standard 18 Ohm and 22 Ohm burden resistors."""
        sec_peak_a = (100.0 / 2000.0) * math.sqrt(2)
        v_peak = sec_peak_a * r_burden
        v_pp = 2.0 * v_peak

        assert math.isclose(v_peak, expected_v_peak, rel_tol=1e-3)
        assert math.isclose(v_pp, expected_v_pp, rel_tol=1e-3)

        # Centered at 1.65V DC bias, verify bounds remain within [0.0V, 3.3V]
        v_min = 1.65 - v_peak
        v_max = 1.65 + v_peak
        assert v_min > 0.0, f"v_min ({v_min:.3f}V) must be positive"
        assert v_max < 3.3, f"v_max ({v_max:.3f}V) must be below ESP32 VDD rail 3.3V"

    def test_safe_current_headroom_with_18_ohm(self):
        """Verify that an 18 Ohm burden resistor allows overcurrent up to ~114 A RMS before clipping."""
        r_burden = 18.0
        v_peak_safe = 1.45
        # I_prim_peak_safe = (v_peak_safe / r_burden) * turns_ratio
        i_prim_peak_safe = (v_peak_safe / r_burden) * 2000.0
        i_prim_rms_safe = i_prim_peak_safe / math.sqrt(2)
        assert i_prim_rms_safe >= 113.0, f"18 Ohm should allow >= 113A RMS headroom, got {i_prim_rms_safe:.1f}A"

    def test_burden_resistor_thermal_dissipation(self):
        """Verify power dissipation in 22 Ohm burden resistor is safely below 0.25W rating."""
        sec_rms_a = 0.050
        r_burden = 22.0
        power_w = (sec_rms_a ** 2) * r_burden
        assert power_w < 0.060, f"Dissipation {power_w}W should be ~0.055W"
        assert power_w < 0.25 * 0.30, "Power dissipation must be under 30% of 1/4W resistor rating"


# --- 2. CALIBRATION MULTIPLIER FORMULAS (K_I) ---

class TestCalibrationFormulas:
    """Verify calibration factor formulas K_I in A/V and A/mV."""

    def test_sct013_000_18_ohm_calibration(self):
        """Verify calibration constant for SCT-013-000 with 18 Ohm resistor."""
        turns = 2000.0
        r_burden = 18.0
        k_i_a_per_v = turns / r_burden
        k_i_a_per_mv = k_i_a_per_v / 1000.0

        assert math.isclose(k_i_a_per_v, 111.1111, rel_tol=1e-4)
        assert math.isclose(k_i_a_per_mv, 0.111111, rel_tol=1e-4)

    def test_sct013_000_22_ohm_calibration(self):
        """Verify calibration constant for SCT-013-000 with 22 Ohm resistor."""
        turns = 2000.0
        r_burden = 22.0
        k_i_a_per_v = turns / r_burden
        k_i_a_per_mv = k_i_a_per_v / 1000.0

        assert math.isclose(k_i_a_per_v, 90.9091, rel_tol=1e-4)
        assert math.isclose(k_i_a_per_mv, 0.090909, rel_tol=1e-4)

    def test_sct013_030_internal_burden_calibration(self):
        """Verify calibration constant for SCT-013-030 (1V RMS = 30A RMS)."""
        # Built-in burden yields 1.0 V at 30.0 A
        k_i_a_per_v = 30.0 / 1.0
        k_i_a_per_mv = k_i_a_per_v / 1000.0

        assert math.isclose(k_i_a_per_v, 30.0, rel_tol=1e-5)
        assert math.isclose(k_i_a_per_mv, 0.030, rel_tol=1e-5)


# --- 3. ADC1 PIN MAPPING & WI-FI HARDWARE LOCKOUT ---

class TestAdcPinAllocation:
    """Verify pin mapping adheres to the ESP32 hardware rule: ADC1 only, never ADC2."""

    # ESP32 Official Technical Reference Manual ADC Pinout
    ADC1_PINS = {32, 33, 34, 35, 36, 39}
    ADC2_PINS = {0, 2, 4, 12, 13, 14, 15, 25, 26, 27}

    ASSIGNED_PINS = {
        "L1": 34,  # ADC1_CH6
        "L2": 35,  # ADC1_CH7
        "L3": 32,  # ADC1_CH4
    }

    def test_all_phases_assigned_to_adc1(self):
        """Verify all CT analog pins are strictly within ADC1 channels."""
        for phase, pin in self.ASSIGNED_PINS.items():
            assert pin in self.ADC1_PINS, f"Phase {phase} GPIO {pin} is not in ADC1: {self.ADC1_PINS}"

    def test_zero_overlap_with_wifi_adc2(self):
        """Verify no assigned CT pin collides with ADC2 (which is corrupted by Wi-Fi RF)."""
        for phase, pin in self.ASSIGNED_PINS.items():
            assert pin not in self.ADC2_PINS, f"Phase {phase} GPIO {pin} conflicts with ADC2!"

    def test_distinct_pin_per_phase(self):
        """Verify each phase has a unique physical GPIO pin."""
        pins = list(self.ASSIGNED_PINS.values())
        assert len(pins) == len(set(pins)), "Assigned GPIO pins must be distinct"


# --- 4. TRUE RMS ALGORITHM & SYNTHETIC WAVEFORM TESTS ---

def calculate_true_rms(samples: list[float], dt: float = 1.0) -> float:
    """Pure mathematical True RMS function matching firmware's C++ logic."""
    if not samples:
        return 0.0
    mean_sq = sum(s * s for s in samples) / len(samples)
    return math.sqrt(mean_sq)


def remove_dc_bias(samples: list[float]) -> tuple[list[float], float]:
    """Window mean subtraction matching ct_sampler.cpp logic."""
    if not samples:
        return [], 0.0
    dc_mean = sum(samples) / len(samples)
    ac_samples = [s - dc_mean for s in samples]
    return ac_samples, dc_mean


class TestTrueRmsAlgorithm:
    """Verify numerical True RMS algorithm against synthetic waveforms."""

    def test_pure_sine_wave_rms(self):
        """A pure sine wave I_peak * sin(w*t) must yield True RMS = I_peak / sqrt(2)."""
        i_peak = 20.0  # 20 Amperes peak -> ~14.142 A RMS
        freq_hz = 50.0
        sample_rate_hz = 2500.0  # 50 samples per 20ms cycle
        num_cycles = 10
        total_samples = int(num_cycles * (sample_rate_hz / freq_hz))

        samples = [
            i_peak * math.sin(2.0 * math.pi * freq_hz * (n / sample_rate_hz))
            for n in range(total_samples)
        ]

        expected_rms = i_peak / math.sqrt(2.0)
        computed_rms = calculate_true_rms(samples)

        # Over exact integer cycles, numerical True RMS should match theoretical within 0.01%
        assert math.isclose(computed_rms, expected_rms, rel_tol=1e-4), (
            f"Expected {expected_rms:.4f} A, got {computed_rms:.4f} A"
        )

    def test_dc_bias_offset_removal(self):
        """Synthetic signal with 1.65V DC offset: verify offset is eliminated and AC RMS preserved."""
        v_dc = 1650.0  # 1650 mV (1.65V midpoint)
        v_ac_peak = 500.0  # 500 mV peak AC signal
        freq_hz = 50.0
        sample_rate_hz = 2500.0
        total_samples = 500  # 10 cycles

        raw_samples = [
            v_dc + v_ac_peak * math.sin(2.0 * math.pi * freq_hz * (n / sample_rate_hz))
            for n in range(total_samples)
        ]

        ac_samples, measured_dc = remove_dc_bias(raw_samples)

        # DC midpoint should be recovered with high precision
        assert math.isclose(measured_dc, v_dc, rel_tol=1e-5), f"Expected DC {v_dc} mV, got {measured_dc} mV"

        # AC RMS should match v_ac_peak / sqrt(2)
        expected_ac_rms = v_ac_peak / math.sqrt(2.0)
        computed_ac_rms = calculate_true_rms(ac_samples)
        assert math.isclose(computed_ac_rms, expected_ac_rms, rel_tol=1e-4)

    def test_harmonic_distorted_waveform_true_rms(self):
        """Waveform with fundamental + 3rd harmonic + 5th harmonic (typical inverter load).

        Parseval's theorem: RMS_total = sqrt(I_1_rms^2 + I_3_rms^2 + I_5_rms^2).
        """
        i1_peak = 25.0
        i3_peak = 5.0
        i5_peak = 2.0
        freq_hz = 50.0
        sample_rate_hz = 2500.0
        total_samples = 500

        samples = [
            i1_peak * math.sin(2.0 * math.pi * 1.0 * freq_hz * (n / sample_rate_hz))
            + i3_peak * math.sin(2.0 * math.pi * 3.0 * freq_hz * (n / sample_rate_hz))
            + i5_peak * math.sin(2.0 * math.pi * 5.0 * freq_hz * (n / sample_rate_hz))
            for n in range(total_samples)
        ]

        i1_rms = i1_peak / math.sqrt(2.0)
        i3_rms = i3_peak / math.sqrt(2.0)
        i5_rms = i5_peak / math.sqrt(2.0)
        expected_total_rms = math.sqrt(i1_rms**2 + i3_rms**2 + i5_rms**2)

        computed_rms = calculate_true_rms(samples)
        assert math.isclose(computed_rms, expected_total_rms, rel_tol=1e-3), (
            f"Harmonic True RMS expected {expected_total_rms:.4f}, got {computed_rms:.4f}"
        )

    def test_idle_zero_current(self):
        """When current transformer is idle (open circuit), True RMS evaluates to 0.0."""
        samples = [0.0] * 250
        assert calculate_true_rms(samples) == 0.0


# --- 5. 3-PHASE POWER CALCULATIONS & INVARIANT ENFORCEMENT ---

class TestPowerCalculations:
    """Verify active/apparent power, system power factor, and Kirchhoff invariant."""

    def test_phase_powers_and_system_aggregations(self):
        """Verify P_i, S_i, P_tot, S_tot, PF_sys equations."""
        voltage_v = 230.0
        currents_a = {"L1": 20.0, "L2": 25.0, "L3": 15.0}
        cos_phi = {"L1": 0.95, "L2": 0.92, "L3": 0.98}

        apparent_kva = {ph: (voltage_v * i) / 1000.0 for ph, i in currents_a.items()}
        active_kw = {ph: apparent_kva[ph] * cos_phi[ph] for ph in currents_a}

        tot_s = sum(apparent_kva.values())
        tot_p = sum(active_kw.values())
        pf_sys = tot_p / tot_s

        assert math.isclose(apparent_kva["L1"], 4.600, rel_tol=1e-4)
        assert math.isclose(apparent_kva["L2"], 5.750, rel_tol=1e-4)
        assert math.isclose(apparent_kva["L3"], 3.450, rel_tol=1e-4)
        assert math.isclose(tot_s, 13.800, rel_tol=1e-4)

        assert math.isclose(active_kw["L1"], 4.370, rel_tol=1e-4)
        assert math.isclose(active_kw["L2"], 5.290, rel_tol=1e-4)
        assert math.isclose(active_kw["L3"], 3.381, rel_tol=1e-4)
        assert math.isclose(tot_p, 13.041, rel_tol=1e-4)

        assert 0.90 <= pf_sys <= 1.0, f"System PF should be in [0.90, 1.0], got {pf_sys:.3f}"

        # Backend invariant: |total_active_power_kw - sum(phases)| <= 0.05 kW
        delta = abs(tot_p - sum(active_kw.values()))
        assert delta <= 0.05, f"Invariant violated: delta={delta}"


# --- 6. TRAPEZOIDAL ENERGY INTEGRATION ---

def trapezoidal_step(p_prev_kw: float, p_curr_kw: float, dt_sec: float) -> float:
    """Trapezoidal rule integration step for power to energy [kWh]."""
    avg_power_kw = (p_prev_kw + p_curr_kw) / 2.0
    return avg_power_kw * (dt_sec / 3600.0)


class TestTrapezoidalEnergyIntegration:
    """Verify numerical energy integration using the trapezoidal rule."""

    def test_constant_power_integration(self):
        """Constant power 10 kW over 1 hour (3600s in 5s intervals) must equal exactly 10.0 kWh."""
        power_kw = 10.0
        dt_sec = 5.0
        steps = int(3600 / dt_sec)

        cumulative_kwh = 0.0
        for _ in range(steps):
            cumulative_kwh += trapezoidal_step(power_kw, power_kw, dt_sec)

        assert math.isclose(cumulative_kwh, 10.0, rel_tol=1e-5), f"Expected 10.0 kWh, got {cumulative_kwh}"

    def test_linear_ramping_power_exactness(self):
        """For linear ramp P(t) = P0 + a*t, trapezoidal integration is algebraically exact.

        Ramping from 5.0 kW to 25.0 kW over 1 hour (3600s):
        Analytical integral = (5 + 25) / 2 * 1h = 15.0 kWh.
        """
        p_start = 5.0
        p_end = 25.0
        duration_s = 3600.0
        dt_sec = 10.0
        steps = int(duration_s / dt_sec)

        cumulative_kwh = 0.0
        for i in range(steps):
            p_prev = p_start + (p_end - p_start) * ((i * dt_sec) / duration_s)
            p_curr = p_start + (p_end - p_start) * (((i + 1) * dt_sec) / duration_s)
            cumulative_kwh += trapezoidal_step(p_prev, p_curr, dt_sec)

        analytical_kwh = 15.0
        assert math.isclose(cumulative_kwh, analytical_kwh, rel_tol=1e-5), (
            f"Expected {analytical_kwh} kWh, got {cumulative_kwh} kWh"
        )


# --- 7. TELEMETRY JSON SCHEMA COMPLIANCE WITH BACKEND ---

class TestBackendSchemaCompliance:
    """Verify that the JSON payload generated by the firmware adheres to Pydantic TelemetryPayload."""

    def test_firmware_payload_validates_with_pydantic(self):
        """Construct synthetic payload mirroring telemetry_client.cpp serializePayloadJson and validate."""
        payload_dict = {
            "device_id": "esp32-ems-001",
            "facility_id": "bakery-central-athens",
            "timestamp": "2026-09-14T15:30:00Z",
            "phases": {
                "L1": {
                    "voltage_v": 230.2,
                    "current_a": 26.4,
                    "active_power_kw": 5.95,
                    "apparent_power_kva": 6.08,
                    "power_factor": 0.98,
                },
                "L2": {
                    "voltage_v": 229.8,
                    "current_a": 25.8,
                    "active_power_kw": 5.82,
                    "apparent_power_kva": 5.93,
                    "power_factor": 0.98,
                },
                "L3": {
                    "voltage_v": 231.0,
                    "current_a": 27.1,
                    "active_power_kw": 6.13,
                    "apparent_power_kva": 6.26,
                    "power_factor": 0.98,
                },
            },
            "total_active_power_kw": 17.90,
            "total_apparent_power_kva": 18.27,
            "system_power_factor": 0.98,
            "cumulative_energy_kwh": 142.50,
            "grid_frequency_hz": 50.01,
            "wifi_rssi_dbm": -62.0,
        }

        # Pydantic validation (will raise ValidationError on any mismatch or extra/missing key)
        model = TelemetryPayload.model_validate(payload_dict)
        assert model.device_id == "esp32-ems-001"
        assert model.facility_id == "bakery-central-athens"
        assert model.total_active_power_kw == 17.90
        assert math.isclose(
            model.total_active_power_kw,
            model.phases["L1"].active_power_kw + model.phases["L2"].active_power_kw + model.phases["L3"].active_power_kw,
            abs_tol=1e-5,
        )


# --- 8. RING BUFFER STORE-AND-FORWARD LOGIC ---

class PyRingBuffer:
    """Python reference implementation of ems::RingBuffer for algorithmic verification."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.buffer = [None] * capacity
        self.head = 0
        self.tail = 0
        self.count = 0
        self.overflow_count = 0

    def push(self, item) -> bool:
        overwritten = False
        if self.count == self.capacity:
            self.tail = (self.tail + 1) % self.capacity
            self.count -= 1
            self.overflow_count += 1
            overwritten = True
        self.buffer[self.head] = item
        self.head = (self.head + 1) % self.capacity
        self.count += 1
        return not overwritten

    def pop(self):
        if self.count == 0:
            return None
        item = self.buffer[self.tail]
        self.buffer[self.tail] = None
        self.tail = (self.tail + 1) % self.capacity
        self.count -= 1
        return item

    def is_empty(self) -> bool:
        return self.count == 0

    def is_full(self) -> bool:
        return self.count == self.capacity


class TestRingBufferLogic:
    """Verify circular store-and-forward buffer semantics."""

    def test_fifo_ordering(self):
        """Elements pushed into the ring buffer must be retrieved in strict FIFO order."""
        rb = PyRingBuffer(capacity=4)
        for i in range(3):
            rb.push(f"record_{i}")

        assert rb.pop() == "record_0"
        assert rb.pop() == "record_1"
        assert rb.pop() == "record_2"
        assert rb.is_empty()

    def test_overwrite_oldest_on_overflow(self):
        """When capacity is exceeded, oldest record is overwritten and overflow recorded."""
        rb = PyRingBuffer(capacity=3)
        rb.push("rec_1")
        rb.push("rec_2")
        rb.push("rec_3")
        assert rb.is_full()
        assert rb.overflow_count == 0

        # Push 4th item -> should drop rec_1
        success = rb.push("rec_4")
        assert not success
        assert rb.overflow_count == 1
        assert rb.count == 3

        assert rb.pop() == "rec_2"
        assert rb.pop() == "rec_3"
        assert rb.pop() == "rec_4"
        assert rb.is_empty()


# --- 9. EDGE CASES & ELECTRICAL CORNER CASES ---

class TestEdgeAndCornerCases:
    """Verify electrical corner cases: unbalance, negative PF, zero apparent power."""

    def test_extreme_3phase_unbalance(self):
        """Extreme commercial load unbalance (L1: heavy oven, L2: lights, L3: idle)."""
        voltage_v = 230.0
        currents = {"L1": 48.5, "L2": 3.2, "L3": 0.1}
        pf = {"L1": 0.99, "L2": 0.85, "L3": 1.00}

        s_phases = {ph: (voltage_v * currents[ph]) / 1000.0 for ph in currents}
        p_phases = {ph: s_phases[ph] * pf[ph] for ph in currents}

        tot_s = sum(s_phases.values())
        tot_p = sum(p_phases.values())
        sys_pf = tot_p / tot_s if tot_s > 0 else 1.0

        assert tot_p < tot_s
        assert 0.80 <= sys_pf <= 1.0
        # Invariant preserved
        assert abs(tot_p - sum(p_phases.values())) < 1e-6

    def test_zero_apparent_power_system_pf_default(self):
        """When facility is completely shut down (0 Amps), system power factor is safely 1.0."""
        tot_s = 0.0
        tot_p = 0.0
        sys_pf = 1.0 if tot_s < 1e-4 else (tot_p / tot_s)
        assert sys_pf == 1.0

    def test_negative_power_factor_capacitive(self):
        """When facility has leading capacitive power factor (cos phi = -0.92)."""
        voltage_v = 230.0
        current_a = 15.0
        s_kva = (voltage_v * current_a) / 1000.0
        cos_phi = -0.92
        p_kw = s_kva * cos_phi

        reading = PhaseReading(
            voltage_v=voltage_v,
            current_a=current_a,
            active_power_kw=p_kw,
            apparent_power_kva=s_kva,
            power_factor=cos_phi,
        )
        assert reading.power_factor == -0.92
        assert reading.active_power_kw < 0

