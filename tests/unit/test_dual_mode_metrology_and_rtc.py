"""Unit tests for Dual-Mode Metrology Engine and Resilient RTC / Offline Timekeeping.

Covers:
1. Mode A (CT-Only Metrology):
   - Apparent power physical calculation from nominal voltage and True RMS current.
   - Active power estimation with configured baseline power factor.
   - Reactive power derivation: Q = sqrt(max(0, S^2 - P^2)).
   - Explicit provenance metadata: "estimated_nominal_voltage_pf", is_estimated = True.
   - Explicit verification that Mode A does NOT claim measured cos phi.
   - Input power factor boundary clamping.

2. Mode B (True RMS with Synchronized Voltage Sampling):
   - Instantaneous sampling: v(t) * i(t) True Active Power P = (1/N) * sum(v_k * i_k).
   - RMS Voltage V_rms = sqrt((1/N) * sum(v_k^2)).
   - RMS Current I_rms = sqrt((1/N) * sum(i_k^2)).
   - Apparent Power S = V_rms * I_rms / 1000.
   - Reactive Power Q = sqrt(max(0, S^2 - P^2)).
   - True Power Factor cos phi = P / S.
   - Provenance metadata: "meter_measured", is_estimated = False.
   - Waveform validation: Pure resistive (PF=1.0), inductive lag (30, 45, 60 deg), capacitive lead.
   - Harmonic distortion waveform handling (Parseval & IEEE 1459 active power vs apparent power).
   - Grid voltage fluctuation resilience (EN 50160 +/-10% deviation comparison: Mode A vs Mode B).
   - Idle / zero current power factor bounding.
   - Kirchhoff conservation invariant (|total_p - sum(p_phases)| <= 0.05 kW).
   - Pydantic TelemetryPayload validation for both Mode A and Mode B.

3. Hardware RTC & Offline Timekeeping Resilience:
   - DS3231 I2C RTC time recovery.
   - ESP32 NVS Flash timestamp persistence fallback.
   - Zero-Epoch (1970) immunization: system never starts at epoch 0.
   - Safe floor epoch constant (MIN_VALID_EPOCH >= 1770000000).
   - Greek commercial calendar conversion: Monday = 0 ... Sunday = 6, Hour = 0 ... 23.
   - Week rollover and leap year calendar math.
   - NVS flash wear-leveling / write rate limiting.
   - Authoritative NTP synchronization and state transition.
"""

import math
from datetime import datetime, timezone
import pytest
from backend.models.telemetry import TelemetryPayload, PhaseReading


# =====================================================================
# PYTHON REFERENCE IMPLEMENTATION OF C++ FIRMWARE ALGORITHMS
# =====================================================================

MIN_VALID_EPOCH = 1769904000  # 2026-02-01 00:00:00 UTC
NVS_WRITE_MIN_INTERVAL_SEC = 900  # 15 minutes


class PhasePowerResult:
    def __init__(self, voltage_v: float, current_a: float, active_power_kw: float,
                 apparent_power_kva: float, reactive_power_kvar: float,
                 power_factor: float, is_estimated: bool):
        self.voltage_v = voltage_v
        self.current_a = current_a
        self.active_power_kw = active_power_kw
        self.apparent_power_kva = apparent_power_kva
        self.reactive_power_kvar = reactive_power_kvar
        self.power_factor = power_factor
        self.is_estimated = is_estimated


def calculate_mode_a_estimated_power(nominal_voltage_v: float,
                                     current_rms_a: float,
                                     assumed_power_factor: float = 0.95) -> PhasePowerResult:
    """Mode A: CT-Only estimation matching PowerCalculator::calculateEstimatedPower."""
    v = max(0.0, float(nominal_voltage_v))
    i = max(0.0, float(current_rms_a))
    s_kva = (v * i) / 1000.0
    clamped_pf = max(-1.0, min(1.0, float(assumed_power_factor)))
    p_kw = s_kva * clamped_pf
    s2 = s_kva * s_kva
    p2 = p_kw * p_kw
    q_kvar = math.sqrt(max(0.0, s2 - p2))

    return PhasePowerResult(
        voltage_v=v,
        current_a=i,
        active_power_kw=p_kw,
        apparent_power_kva=s_kva,
        reactive_power_kvar=q_kvar,
        power_factor=clamped_pf,
        is_estimated=True
    )


def calculate_mode_b_instantaneous_power(v_samples: list[float],
                                         i_samples: list[float],
                                         v_cal: float = 1.0,
                                         i_cal: float = 1.0) -> PhasePowerResult:
    """Mode B: True RMS synchronized sampling matching PowerCalculator::calculateInstantaneousPower."""
    if not v_samples or not i_samples or len(v_samples) != len(i_samples) or len(v_samples) == 0:
        return PhasePowerResult(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, False)

    count = len(v_samples)
    sum_v_sq = 0.0
    sum_i_sq = 0.0
    sum_instant_p = 0.0
    valid_count = 0

    for k in range(count):
        v = v_samples[k] * v_cal
        i = i_samples[k] * i_cal
        if not math.isfinite(v) or not math.isfinite(i):
            continue
        sum_v_sq += v * v
        sum_i_sq += i * i
        sum_instant_p += (v * i)
        valid_count += 1

    if valid_count == 0:
        return PhasePowerResult(0.0, 0.0, 0.0, 0.0, 0.0, 1.0, False)

    v_rms = math.sqrt(sum_v_sq / valid_count)
    i_rms = math.sqrt(sum_i_sq / valid_count)
    s_kva = (v_rms * i_rms) / 1000.0
    p_kw = (sum_instant_p / valid_count) / 1000.0

    # Numerical precision guard: |P| <= S
    if p_kw > s_kva:
        p_kw = s_kva
    elif p_kw < -s_kva:
        p_kw = -s_kva

    s2 = s_kva * s_kva
    p2 = p_kw * p_kw
    q_kvar = math.sqrt(max(0.0, s2 - p2))

    if s_kva > 0.001:
        raw_pf = p_kw / s_kva
        pf = max(-1.0, min(1.0, raw_pf))
    else:
        pf = 1.0

    return PhasePowerResult(
        voltage_v=v_rms,
        current_a=i_rms,
        active_power_kw=p_kw,
        apparent_power_kva=s_kva,
        reactive_power_kvar=q_kvar,
        power_factor=pf,
        is_estimated=False
    )


def calendar_to_epoch(year: int, month: int, day: int, hour: int, minute: int, second: int) -> int:
    """Python reference matching RtcTimekeeper::calendarToEpoch."""
    days_before_month = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if year < 1970 or month < 1 or month > 12 or day < 1 or day > 31:
        return MIN_VALID_EPOCH

    y = year - 1970
    leap_days = (year - 1969) // 4 - (year - 1901) // 100 + (year - 1601) // 400
    is_leap = ((year % 4 == 0 and year % 100 != 0) or (year % 400 == 0))
    day_of_year = days_before_month[month - 1] + (day - 1)
    if is_leap and month > 2:
        day_of_year += 1

    total_days = (y * 365) + leap_days + day_of_year
    return (total_days * 86400) + (hour * 3600) + (minute * 60) + second


class TimeSyncSource:
    DEFAULT_BUILD_EPOCH = "DEFAULT_BUILD_EPOCH"
    NVS_FALLBACK = "NVS_FALLBACK"
    RTC_HARDWARE = "RTC_HARDWARE"
    NTP_SYNCED = "NTP_SYNCED"


class PyRtcTimekeeper:
    """Python reference model of ems::RtcTimekeeper state machine."""

    def __init__(self, ds3231_time: int | None = None, ds3231_osf: bool = False,
                 nvs_time: int | None = None):
        self.sync_source = TimeSyncSource.DEFAULT_BUILD_EPOCH
        self.current_epoch_base = MIN_VALID_EPOCH
        self.base_millis = 0
        self.last_nvs_write_epoch = 0
        self.ds3231_time = ds3231_time
        self.ds3231_osf = ds3231_osf
        self.nvs_time = nvs_time

    def begin(self, current_millis: int = 0) -> bool:
        self.base_millis = current_millis

        # 1. Probe DS3231
        if self.ds3231_time is not None and not self.ds3231_osf and self.ds3231_time >= MIN_VALID_EPOCH:
            self.current_epoch_base = self.ds3231_time
            self.sync_source = TimeSyncSource.RTC_HARDWARE
            return True

        # 2. Fallback to NVS Flash
        if self.nvs_time is not None and self.nvs_time >= MIN_VALID_EPOCH:
            self.current_epoch_base = self.nvs_time
            self.sync_source = TimeSyncSource.NVS_FALLBACK
            return False

        # 3. Fallback to compile-time safety floor
        self.current_epoch_base = MIN_VALID_EPOCH
        self.sync_source = TimeSyncSource.DEFAULT_BUILD_EPOCH
        return False

    def sync_with_ntp(self, ntp_epoch: int, current_millis: int):
        if ntp_epoch < MIN_VALID_EPOCH:
            return
        self.current_epoch_base = ntp_epoch
        self.base_millis = current_millis
        self.sync_source = TimeSyncSource.NTP_SYNCED
        self.ds3231_time = ntp_epoch
        self.ds3231_osf = False
        self.persist_to_nvs(ntp_epoch, force=True)

    def get_current_epoch(self, current_millis: int) -> int:
        # Unsigned 32-bit delta calculation handles millis wraparound seamlessly
        elapsed_ms = (current_millis - self.base_millis) & 0xFFFFFFFF
        elapsed_sec = elapsed_ms // 1000
        if elapsed_sec > 0:
            self.current_epoch_base += elapsed_sec
            self.base_millis = (self.base_millis + elapsed_sec * 1000) & 0xFFFFFFFF
        return self.current_epoch_base

    def persist_to_nvs(self, epoch: int, force: bool = False) -> bool:
        if epoch < MIN_VALID_EPOCH:
            return False
        if not force and self.last_nvs_write_epoch > 0 and (epoch - self.last_nvs_write_epoch < NVS_WRITE_MIN_INTERVAL_SEC):
            return False
        self.nvs_time = epoch
        self.last_nvs_write_epoch = epoch
        return True

    def get_time_info(self, current_millis: int, tz_offset_hours: int | None = None) -> tuple[int, int, int]:
        epoch = self.get_current_epoch(current_millis)
        hour, weekday = self.epoch_to_greek_calendar(epoch, tz_offset_hours)
        self.persist_to_nvs(epoch, force=False)
        return epoch, hour, weekday

    @staticmethod
    def get_greek_timezone_offset_hours(epoch: int) -> int:
        """Dynamic Greek timezone offset per EU Directive 2000/84/EC (UTC+2 EET / UTC+3 EEST)."""
        dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
        month = dt_utc.month
        day = dt_utc.day
        hour = dt_utc.hour
        wday = (dt_utc.weekday() + 1) % 7  # 0=Sun, 1=Mon, ..., 6=Sat

        if month < 3 or month > 10:
            return 2
        if month > 3 and month < 10:
            return 3

        days_to_31 = 31 - day
        wday_31 = (wday + days_to_31) % 7
        last_sunday = 31 - wday_31

        if month == 3:
            if day > last_sunday or (day == last_sunday and hour >= 1):
                return 3
            return 2
        else:
            if day < last_sunday or (day == last_sunday and hour < 1):
                return 3
            return 2

    @classmethod
    def epoch_to_greek_calendar(cls, epoch: int, tz_offset_hours: int | None = None) -> tuple[int, int]:
        effective_tz = cls.get_greek_timezone_offset_hours(epoch) if tz_offset_hours is None else tz_offset_hours
        local_epoch = epoch + (effective_tz * 3600)
        days_since_epoch = local_epoch // 86400
        seconds_in_day = local_epoch % 86400
        hour = seconds_in_day // 3600
        dow = (days_since_epoch + 3) % 7
        return hour, dow


# =====================================================================
# TEST SUITE 1: DUAL-MODE METROLOGY - MODE A (CT-ONLY ESTIMATION)
# =====================================================================

class TestModeACtOnlyMetrology:
    """Verify Mode A (CT-Only) physical equations, provenance, and assumptions."""

    def test_mode_a_apparent_power_calculation(self):
        """Mode A computes S = V_nominal * I_rms / 1000 with nominal 230V."""
        v_nom = 230.0
        i_rms = 40.0
        res = calculate_mode_a_estimated_power(v_nom, i_rms, assumed_power_factor=0.95)

        assert math.isclose(res.apparent_power_kva, 9.200, rel_tol=1e-4)
        assert math.isclose(res.active_power_kw, 9.200 * 0.95, rel_tol=1e-4)
        assert res.is_estimated is True
        assert res.voltage_v == 230.0
        assert res.current_a == 40.0

    def test_mode_a_reactive_power_balance(self):
        """Mode A derives Q = sqrt(max(0, S^2 - P^2)) satisfying S^2 == P^2 + Q^2."""
        res = calculate_mode_a_estimated_power(230.0, 25.0, assumed_power_factor=0.80)
        s = res.apparent_power_kva
        p = res.active_power_kw
        q = res.reactive_power_kvar

        assert math.isclose(s * s, (p * p) + (q * q), rel_tol=1e-4)
        # For cos phi = 0.80, sin phi = 0.60
        assert math.isclose(q, s * 0.60, rel_tol=1e-4)

    def test_mode_a_power_factor_clamping(self):
        """Assumed power factor outside [-1.0, 1.0] is clamped."""
        res_high = calculate_mode_a_estimated_power(230.0, 10.0, assumed_power_factor=1.50)
        assert res_high.power_factor == 1.0
        assert math.isclose(res_high.active_power_kw, res_high.apparent_power_kva, rel_tol=1e-4)

        res_low = calculate_mode_a_estimated_power(230.0, 10.0, assumed_power_factor=-1.20)
        assert res_low.power_factor == -1.0
        assert math.isclose(res_low.active_power_kw, -res_low.apparent_power_kva, rel_tol=1e-4)

    def test_mode_a_does_not_claim_measured_pf(self):
        """Verify Mode A flag is_estimated is True, indicating no hardware phase measurement."""
        res = calculate_mode_a_estimated_power(230.0, 15.0, assumed_power_factor=0.95)
        assert res.is_estimated is True, "Mode A must explicitly declare that active power/PF are estimated"

    def test_mode_a_payload_validates_with_pydantic_schema(self):
        """Construct full 3-phase Mode A payload and ensure it passes Pydantic TelemetryPayload."""
        l1 = calculate_mode_a_estimated_power(230.0, 20.0, 0.95)
        l2 = calculate_mode_a_estimated_power(230.0, 22.0, 0.95)
        l3 = calculate_mode_a_estimated_power(230.0, 18.0, 0.95)

        tot_p = round(l1.active_power_kw, 3) + round(l2.active_power_kw, 3) + round(l3.active_power_kw, 3)
        tot_s = round(l1.apparent_power_kva + l2.apparent_power_kva + l3.apparent_power_kva, 3)

        payload_dict = {
            "power_measurement_method": "estimated_nominal_voltage_pf",
            "device_id": "esp32-ems-001",
            "facility_id": "bakery-central-athens",
            "timestamp": "2026-09-22T14:30:00Z",
            "phases": {
                "L1": {
                    "voltage_v": round(l1.voltage_v, 1),
                    "current_a": round(l1.current_a, 2),
                    "active_power_kw": round(l1.active_power_kw, 3),
                    "apparent_power_kva": round(l1.apparent_power_kva, 3),
                    "power_factor": round(l1.power_factor, 2),
                },
                "L2": {
                    "voltage_v": round(l2.voltage_v, 1),
                    "current_a": round(l2.current_a, 2),
                    "active_power_kw": round(l2.active_power_kw, 3),
                    "apparent_power_kva": round(l2.apparent_power_kva, 3),
                    "power_factor": round(l2.power_factor, 2),
                },
                "L3": {
                    "voltage_v": round(l3.voltage_v, 1),
                    "current_a": round(l3.current_a, 2),
                    "active_power_kw": round(l3.active_power_kw, 3),
                    "apparent_power_kva": round(l3.apparent_power_kva, 3),
                    "power_factor": round(l3.power_factor, 2),
                },
            },
            "total_active_power_kw": round(tot_p, 3),
            "total_apparent_power_kva": tot_s,
            "system_power_factor": 0.95,
            "cumulative_energy_kwh": 55.40,
            "grid_frequency_hz": 50.0,
            "wifi_rssi_dbm": -68.0,
        }

        payload = TelemetryPayload.model_validate(payload_dict)
        assert payload.power_measurement_method == "estimated_nominal_voltage_pf"
        assert math.isclose(payload.total_active_power_kw, tot_p, abs_tol=1e-4)


# =====================================================================
# TEST SUITE 2: DUAL-MODE METROLOGY - MODE B (TRUE RMS SYNCHRONIZED)
# =====================================================================

class TestModeBTrueRmsMetrology:
    """Verify Mode B (Instantaneous v(t)*i(t) True RMS) equations and edge cases."""

    @staticmethod
    def generate_waveforms(v_rms: float, i_rms: float, phase_shift_deg: float,
                           freq_hz: float = 50.0, sample_rate_hz: float = 2500.0,
                           num_cycles: int = 10) -> tuple[list[float], list[float]]:
        """Generate synthetic synchronized voltage and current sample arrays."""
        v_peak = v_rms * math.sqrt(2.0)
        i_peak = i_rms * math.sqrt(2.0)
        phase_rad = math.radians(phase_shift_deg)
        total_samples = int(num_cycles * (sample_rate_hz / freq_hz))

        v_samples = []
        i_samples = []
        for n in range(total_samples):
            t = n / sample_rate_hz
            wt = 2.0 * math.pi * freq_hz * t
            v_samples.append(v_peak * math.sin(wt))
            i_samples.append(i_peak * math.sin(wt - phase_rad))

        return v_samples, i_samples

    def test_mode_b_pure_resistive_load(self):
        """Pure resistive load (0 deg phase shift): P == S, Q == 0, PF == 1.000."""
        v_rms_target = 230.0
        i_rms_target = 20.0
        v_samples, i_samples = self.generate_waveforms(v_rms_target, i_rms_target, phase_shift_deg=0.0)

        res = calculate_mode_b_instantaneous_power(v_samples, i_samples)

        assert math.isclose(res.voltage_v, 230.0, rel_tol=1e-3)
        assert math.isclose(res.current_a, 20.0, rel_tol=1e-3)
        assert math.isclose(res.apparent_power_kva, 4.600, rel_tol=1e-3)
        assert math.isclose(res.active_power_kw, 4.600, rel_tol=1e-3)
        assert math.isclose(res.reactive_power_kvar, 0.0, abs_tol=1e-3)
        assert math.isclose(res.power_factor, 1.0, rel_tol=1e-4)
        assert res.is_estimated is False

    @pytest.mark.parametrize("phase_deg, expected_pf", [
        (30.0, 0.866025),  # cos(30 deg)
        (45.0, 0.707107),  # cos(45 deg)
        (60.0, 0.500000),  # cos(60 deg)
    ])
    def test_mode_b_inductive_lagging_loads(self, phase_deg: float, expected_pf: float):
        """Inductive loads with current lagging voltage: verifies True Active and Reactive Power."""
        v_rms_target = 230.0
        i_rms_target = 30.0
        v_samples, i_samples = self.generate_waveforms(v_rms_target, i_rms_target, phase_shift_deg=phase_deg)

        res = calculate_mode_b_instantaneous_power(v_samples, i_samples)
        s_expected = (230.0 * 30.0) / 1000.0  # 6.90 kVA
        p_expected = s_expected * expected_pf
        q_expected = s_expected * math.sin(math.radians(phase_deg))

        assert math.isclose(res.apparent_power_kva, s_expected, rel_tol=1e-3)
        assert math.isclose(res.active_power_kw, p_expected, rel_tol=1e-3)
        assert math.isclose(res.reactive_power_kvar, q_expected, rel_tol=1e-3)
        assert math.isclose(res.power_factor, expected_pf, rel_tol=1e-3)
        assert res.is_estimated is False

    def test_mode_b_harmonic_distortion_waveforms(self):
        """Non-linear load (fundamental + 3rd + 5th harmonics).

        Parseval's theorem & IEEE 1459:
        True active power is the sum of fundamental and harmonic real powers:
        P = (1/1000) * (V1*I1*cos(phi1) + V3*I3*cos(phi3) + V5*I5*cos(phi5)).
        Apparent power S = V_rms * I_rms includes total harmonic distortion.
        """
        freq_hz = 50.0
        sample_rate_hz = 2500.0
        total_samples = 500  # 10 cycles

        v_samples = []
        i_samples = []
        for n in range(total_samples):
            t = n / sample_rate_hz
            w = 2.0 * math.pi * freq_hz
            # Voltage: pure 230V 50Hz
            v = 230.0 * math.sqrt(2) * math.sin(w * t)
            # Current: 20A fundamental (lag 30 deg) + 5A 3rd harmonic (lag 15 deg) + 2A 5th harmonic (in phase)
            i = (20.0 * math.sqrt(2) * math.sin(w * t - math.radians(30.0)) +
                 5.0 * math.sqrt(2) * math.sin(3.0 * w * t - math.radians(15.0)) +
                 2.0 * math.sqrt(2) * math.sin(5.0 * w * t))
            v_samples.append(v)
            i_samples.append(i)

        res = calculate_mode_b_instantaneous_power(v_samples, i_samples)

        # Expected fundamental real power: 230 * 20 * cos(30 deg) = 3983.7 W = 3.984 kW
        # Harmonics in current with pure fundamental voltage produce ZERO net active power:
        # integral(sin(wt) * sin(3wt)) = 0 over integer periods!
        expected_p_kw = (230.0 * 20.0 * math.cos(math.radians(30.0))) / 1000.0

        # Total RMS current: sqrt(20^2 + 5^2 + 2^2) = sqrt(429) = 20.712 A
        expected_i_rms = math.sqrt(20.0**2 + 5.0**2 + 2.0**2)
        expected_s_kva = (230.0 * expected_i_rms) / 1000.0

        assert math.isclose(res.active_power_kw, expected_p_kw, rel_tol=1e-2)
        assert math.isclose(res.current_a, expected_i_rms, rel_tol=1e-2)
        assert math.isclose(res.apparent_power_kva, expected_s_kva, rel_tol=1e-2)
        assert res.power_factor < math.cos(math.radians(30.0)), "THD lowers true power factor below displacement PF!"

    def test_grid_voltage_fluctuation_mode_a_vs_mode_b(self):
        """Greek grid standard EN 50160 allows +/- 10% voltage deviation (207V - 253V).

        Demonstrates why Mode B is superior to Mode A:
        - When voltage drops to 210V, Mode A assumes 230V -> 9.5% error!
        - Mode B measures true 210V -> <0.1% error!
        """
        actual_v_rms = 210.0
        i_rms = 25.0
        v_samples, i_samples = self.generate_waveforms(actual_v_rms, i_rms, phase_shift_deg=0.0)

        # Mode A computation (blindly assumes 230V)
        mode_a = calculate_mode_a_estimated_power(230.0, i_rms, assumed_power_factor=1.0)
        # Mode B computation (samples true v(t))
        mode_b = calculate_mode_b_instantaneous_power(v_samples, i_samples)

        true_power_kw = (actual_v_rms * i_rms) / 1000.0  # 5.25 kW

        mode_a_error_pct = abs(mode_a.active_power_kw - true_power_kw) / true_power_kw * 100.0
        mode_b_error_pct = abs(mode_b.active_power_kw - true_power_kw) / true_power_kw * 100.0

        assert mode_a_error_pct > 9.0, f"Mode A should have ~9.5% error, got {mode_a_error_pct:.2f}%"
        assert mode_b_error_pct < 0.2, f"Mode B should have <0.2% error, got {mode_b_error_pct:.2f}%"

    def test_mode_b_idle_zero_current_power_factor(self):
        """When facility is idle or CT is disconnected (0 Amperes), PF is safe 1.0."""
        v_samples = [230.0 * math.sqrt(2) * math.sin(2 * math.pi * 50 * (n / 2500)) for n in range(250)]
        i_samples = [0.0] * 250

        res = calculate_mode_b_instantaneous_power(v_samples, i_samples)
        assert res.current_a == 0.0
        assert res.active_power_kw == 0.0
        assert res.apparent_power_kva == 0.0
        assert res.power_factor == 1.0  # Safe fallback without division by zero

    def test_mode_b_payload_validates_with_meter_measured_metadata(self):
        """Mode B payload serializes with power_measurement_method='meter_measured'."""
        v_samples, i_samples = self.generate_waveforms(231.5, 18.2, phase_shift_deg=18.0)
        l1 = calculate_mode_b_instantaneous_power(v_samples, i_samples)
        l2 = calculate_mode_b_instantaneous_power(v_samples, i_samples)
        l3 = calculate_mode_b_instantaneous_power(v_samples, i_samples)

        tot_p = round(l1.active_power_kw, 3) * 3
        tot_s = round(l1.apparent_power_kva, 3) * 3

        payload_dict = {
            "power_measurement_method": "meter_measured",
            "device_id": "esp32-ems-001",
            "facility_id": "bakery-central-athens",
            "timestamp": "2026-09-22T15:00:00Z",
            "phases": {
                "L1": {
                    "voltage_v": round(l1.voltage_v, 1),
                    "current_a": round(l1.current_a, 2),
                    "active_power_kw": round(l1.active_power_kw, 3),
                    "apparent_power_kva": round(l1.apparent_power_kva, 3),
                    "power_factor": round(l1.power_factor, 2),
                },
                "L2": {
                    "voltage_v": round(l2.voltage_v, 1),
                    "current_a": round(l2.current_a, 2),
                    "active_power_kw": round(l2.active_power_kw, 3),
                    "apparent_power_kva": round(l2.apparent_power_kva, 3),
                    "power_factor": round(l2.power_factor, 2),
                },
                "L3": {
                    "voltage_v": round(l3.voltage_v, 1),
                    "current_a": round(l3.current_a, 2),
                    "active_power_kw": round(l3.active_power_kw, 3),
                    "apparent_power_kva": round(l3.apparent_power_kva, 3),
                    "power_factor": round(l3.power_factor, 2),
                },
            },
            "total_active_power_kw": round(tot_p, 3),
            "total_apparent_power_kva": round(tot_s, 3),
            "system_power_factor": round(l1.power_factor, 2),
            "cumulative_energy_kwh": 182.10,
            "grid_frequency_hz": 50.01,
            "wifi_rssi_dbm": -64.0,
        }

        payload = TelemetryPayload.model_validate(payload_dict)
        assert payload.power_measurement_method == "meter_measured"
        assert math.isclose(payload.total_active_power_kw, tot_p, abs_tol=1e-4)


# =====================================================================
# TEST SUITE 3: HARDWARE RTC & OFFLINE TIMEKEEPING RESILIENCE
# =====================================================================

class TestHardwareRtcAndOfflineTimekeeping:
    """Verify DS3231 RTC, NVS Flash fallback, and 1970 epoch immunization."""

    def test_boot_with_authoritative_ntp(self):
        """When NTP is available, timekeeper synchronizes, updates DS3231, and writes to NVS."""
        tk = PyRtcTimekeeper()
        tk.begin(current_millis=1000)

        ntp_epoch = 1789998000  # Valid future timestamp in 2026
        tk.sync_with_ntp(ntp_epoch, current_millis=5000)

        assert tk.sync_source == TimeSyncSource.NTP_SYNCED
        assert tk.get_current_epoch(5000) == ntp_epoch
        assert tk.ds3231_time == ntp_epoch
        assert tk.nvs_time == ntp_epoch

    def test_offline_boot_with_healthy_ds3231(self):
        """When Wi-Fi is offline at boot, DS3231 battery-backed RTC restores valid time."""
        # RTC has valid 2026 timestamp: 2026-09-22 14:00:00 UTC (1790085600)
        rtc_epoch = 1790085600
        tk = PyRtcTimekeeper(ds3231_time=rtc_epoch, ds3231_osf=False, nvs_time=None)
        res = tk.begin(current_millis=2000)

        assert res is True
        assert tk.sync_source == TimeSyncSource.RTC_HARDWARE
        assert tk.get_current_epoch(2000) == rtc_epoch

    def test_offline_boot_with_dead_ds3231_falls_back_to_nvs(self):
        """When DS3231 lost battery (OSF set or unreadable), timekeeper recovers from NVS."""
        # RTC oscillator stopped (OSF = True), but NVS has saved shutdown timestamp
        saved_nvs_epoch = 1790050000
        tk = PyRtcTimekeeper(ds3231_time=0, ds3231_osf=True, nvs_time=saved_nvs_epoch)
        res = tk.begin(current_millis=1000)

        assert res is False
        assert tk.sync_source == TimeSyncSource.NVS_FALLBACK
        assert tk.get_current_epoch(1000) == saved_nvs_epoch

    def test_zero_epoch_1970_immunization(self):
        """Under catastrophic failure (RTC dead, NVS erased), device NEVER boots at epoch 0 (1970).

        This prevents corrupting the on-device tinyML 7x24 seasonal matrix.
        """
        # Both DS3231 and NVS return 0 (Unix epoch 1970)
        tk = PyRtcTimekeeper(ds3231_time=0, ds3231_osf=True, nvs_time=0)
        tk.begin(current_millis=0)

        assert tk.sync_source == TimeSyncSource.DEFAULT_BUILD_EPOCH
        current_epoch = tk.get_current_epoch(0)
        assert current_epoch >= MIN_VALID_EPOCH, f"Epoch {current_epoch} must be >= {MIN_VALID_EPOCH}"

        # Verify year is at least 2026, not 1970!
        dt = datetime.fromtimestamp(current_epoch, tz=timezone.utc)
        assert dt.year >= 2026

    def test_offline_monotonic_progression_over_48_hours(self):
        """Device running completely offline advances time monotonically without drift error."""
        start_epoch = 1790000000
        tk = PyRtcTimekeeper(ds3231_time=start_epoch, ds3231_osf=False)
        tk.begin(current_millis=0)

        # 48 hours later: 48 * 3600 * 1000 = 172,800,000 ms
        millis_48h = 48 * 3600 * 1000
        epoch_48h = tk.get_current_epoch(millis_48h)

        assert epoch_48h == start_epoch + (48 * 3600)

    def test_greek_business_calendar_indexing(self):
        """Verify algorithmic mapping from epoch to Greek calendar hour (0-23) and weekday (0=Mon..6=Sun)."""
        # 2026-09-22 14:30:00 UTC
        # Tuesday: Greek weekday must be 1 (Monday = 0, Tuesday = 1)
        # Greece EET is UTC+2: local time is 16:30 -> hour 16
        dt = datetime(2026, 9, 22, 14, 30, 0, tzinfo=timezone.utc)
        epoch = int(dt.timestamp())

        hour, dow = PyRtcTimekeeper.epoch_to_greek_calendar(epoch, tz_offset_hours=2)
        assert hour == 16, f"Expected 16:00, got {hour}:00"
        assert dow == 1, f"Expected Tuesday (1), got {dow}"

    def test_greek_calendar_week_rollover(self):
        """Test rollover from Sunday 23:00 to Monday 01:00."""
        # 2026-09-20 is Sunday. At 21:30 UTC + 2h = 23:30 local (Sunday, dow=6)
        sun_dt = datetime(2026, 9, 20, 21, 30, 0, tzinfo=timezone.utc)
        sun_epoch = int(sun_dt.timestamp())
        sun_hour, sun_dow = PyRtcTimekeeper.epoch_to_greek_calendar(sun_epoch, tz_offset_hours=2)
        assert sun_dow == 6
        assert sun_hour == 23

        # 3 hours later -> Monday 02:30 local (Monday, dow=0)
        mon_epoch = sun_epoch + (3 * 3600)
        mon_hour, mon_dow = PyRtcTimekeeper.epoch_to_greek_calendar(mon_epoch, tz_offset_hours=2)
        assert mon_dow == 0
        assert mon_hour == 2

    def test_nvs_wear_leveling_rate_limiting(self):
        """Verify non-forced NVS writes are rate-limited to prevent flash wear."""
        tk = PyRtcTimekeeper()
        tk.begin(current_millis=0)

        start_epoch = 1790000000
        # First write succeeds
        w1 = tk.persist_to_nvs(start_epoch, force=False)
        assert w1 is True

        # Write 5 minutes later (300s < 900s limit) -> blocked
        w2 = tk.persist_to_nvs(start_epoch + 300, force=False)
        assert w2 is False

        # Forced write -> succeeds immediately
        w3 = tk.persist_to_nvs(start_epoch + 300, force=True)
        assert w3 is True

        # Write 20 minutes after last write (1200s > 900s) -> succeeds
        w4 = tk.persist_to_nvs(start_epoch + 1500, force=False)
        assert w4 is True

    def test_calendar_to_epoch_exactness(self):
        """Verify calendar_to_epoch accurately converts Gregorian dates, including leap years."""
        # Baseline start of Feb 2026: 2026-02-01 00:00:00 UTC
        epoch_feb2026 = calendar_to_epoch(2026, 2, 1, 0, 0, 0)
        assert epoch_feb2026 == 1769904000

        # Leap year leap day: 2024-02-29 12:00:00 UTC
        dt_leap = datetime(2024, 2, 29, 12, 0, 0, tzinfo=timezone.utc)
        assert calendar_to_epoch(2024, 2, 29, 12, 0, 0) == int(dt_leap.timestamp())

        # Post-leap-day date: 2024-03-01 00:00:00 UTC
        dt_mar = datetime(2024, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert calendar_to_epoch(2024, 3, 1, 0, 0, 0) == int(dt_mar.timestamp())

    def test_greek_dst_dynamic_transitions(self):
        """Verify dynamic Greek timezone transitions (EET UTC+2 <-> EEST UTC+3) per EU Directive 2000/84/EC."""
        # 2026: Last Sunday of March is March 29.
        # Before 01:00 UTC -> EET (+2)
        dt_before_dst = datetime(2026, 3, 29, 0, 59, 59, tzinfo=timezone.utc)
        assert PyRtcTimekeeper.get_greek_timezone_offset_hours(int(dt_before_dst.timestamp())) == 2

        # At and after 01:00 UTC -> EEST (+3)
        dt_after_dst = datetime(2026, 3, 29, 1, 0, 0, tzinfo=timezone.utc)
        assert PyRtcTimekeeper.get_greek_timezone_offset_hours(int(dt_after_dst.timestamp())) == 3

        # 2026: Last Sunday of October is October 25.
        # Before 01:00 UTC -> EEST (+3)
        dt_before_oct = datetime(2026, 10, 25, 0, 59, 59, tzinfo=timezone.utc)
        assert PyRtcTimekeeper.get_greek_timezone_offset_hours(int(dt_before_oct.timestamp())) == 3

        # At and after 01:00 UTC -> EET (+2)
        dt_after_oct = datetime(2026, 10, 25, 1, 0, 0, tzinfo=timezone.utc)
        assert PyRtcTimekeeper.get_greek_timezone_offset_hours(int(dt_after_oct.timestamp())) == 2

    def test_greek_business_calendar_with_auto_dst(self):
        """Verify automatic Greek timezone calculation: UTC+3 in summer (today: 2026-09-22), UTC+2 in winter."""
        # Summer (September 22, 2026): 14:30 UTC -> 17:30 Greek time (hour 17, EEST)
        summer_dt = datetime(2026, 9, 22, 14, 30, 0, tzinfo=timezone.utc)
        summer_epoch = int(summer_dt.timestamp())
        h_summer, dow_summer = PyRtcTimekeeper.epoch_to_greek_calendar(summer_epoch)  # auto DST
        assert h_summer == 17, f"Expected 17:00 (EEST UTC+3), got {h_summer}:00"
        assert dow_summer == 1  # Tuesday

        # Winter (January 15, 2026): 14:30 UTC -> 16:30 Greek time (hour 16, EET)
        winter_dt = datetime(2026, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
        winter_epoch = int(winter_dt.timestamp())
        h_winter, dow_winter = PyRtcTimekeeper.epoch_to_greek_calendar(winter_epoch)  # auto DST
        assert h_winter == 16, f"Expected 16:00 (EET UTC+2), got {h_winter}:00"
        assert dow_winter == 3  # Thursday

    def test_millis_wraparound_immunity(self):
        """Verify that 32-bit millis() overflow (~49.7 days) does not cause backwards time progression."""
        tk = PyRtcTimekeeper(ds3231_time=1790000000, ds3231_osf=False)
        # Set base_millis right before 32-bit overflow (e.g. 0xFFFFFF00 = 4,294,967,040 ms)
        tk.begin(current_millis=0xFFFFFF00)
        epoch_start = tk.get_current_epoch(0xFFFFFF00)
        assert epoch_start == 1790000000

        # Advance millis across the overflow boundary: 0xFFFFFF00 + 3000ms = 0x00000ABC
        # (3 seconds elapsed)
        overflow_millis = (0xFFFFFF00 + 3000) & 0xFFFFFFFF
        epoch_after = tk.get_current_epoch(overflow_millis)
        assert epoch_after == epoch_start + 3, f"Expected {epoch_start + 3}, got {epoch_after}"

    def test_mode_b_non_finite_sample_handling(self):
        """Verify Mode B gracefully skips NaN / Inf samples from noisy or corrupted ADC channels."""
        v_samples = [230.0 * math.sqrt(2) * math.sin(2 * math.pi * 50 * (n / 2500)) for n in range(100)]
        i_samples = [20.0 * math.sqrt(2) * math.sin(2 * math.pi * 50 * (n / 2500)) for n in range(100)]

        # Inject some NaN and Inf values
        v_samples[10] = float("nan")
        i_samples[25] = float("inf")
        i_samples[50] = float("-inf")

        res = calculate_mode_b_instantaneous_power(v_samples, i_samples)
        assert math.isfinite(res.voltage_v)
        assert math.isfinite(res.current_a)
        assert math.isfinite(res.active_power_kw)
        assert math.isfinite(res.apparent_power_kva)
        assert math.isfinite(res.power_factor)
        assert res.power_factor > 0.95
