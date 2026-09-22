"""Commercial 3-phase telemetry generator.

Generates physically consistent 3-phase electrical readings complying with
backend electrical invariants:
- |P_tot - sum(P_i)| <= 0.05 kW
- Realistic phase voltages: 230V +/- 3V (227.0 - 233.0 V)
- Commercial displacement power factor (cos φ): 0.88 - 0.98
- Grid frequency: 50.0 Hz (+/- 0.05 Hz)
- Current calculated from apparent power: I = (S * 1000) / V
- Gaussian load noise (--noise)
- Automated peak breach injection (--trigger-breach) for sub-30s verification
- Cumulative active energy (kWh) numeric integration
"""

import random
from collections.abc import Iterator
from datetime import datetime, timezone

from backend.models.telemetry import PhaseReading, TelemetryPayload
from simulator.profiles import get_profile
from simulator.profiles.base import LoadProfile


class TelemetryGenerator:
    """Parametric 3-phase commercial telemetry load generator."""

    def __init__(
        self,
        profile: str | LoadProfile = "bakery",
        device_id: str | None = None,
        facility_id: str | None = None,
        noise_kw: float = 0.0,
        trigger_breach: bool = False,
        breach_power_kw: float | None = None,
        nominal_voltage: float = 230.0,
        voltage_jitter: float = 2.8,
        nominal_frequency: float = 50.0,
        initial_energy_kwh: float = 100.0,
        start_time: datetime | None = None,
        seed: int | None = None,
    ):
        if isinstance(profile, str):
            self.profile: LoadProfile = get_profile(profile)
        elif isinstance(profile, LoadProfile):
            self.profile = profile
        else:
            raise TypeError(f"profile must be str or LoadProfile, got {type(profile)}")

        self.device_id = device_id or f"esp32-{self.profile.name}-001"
        self.facility_id = facility_id or self.profile.default_facility_id
        self.noise_kw = max(0.0, float(noise_kw))
        self.trigger_breach = bool(trigger_breach)
        self.breach_power_kw = breach_power_kw
        self.nominal_voltage = nominal_voltage
        self.voltage_jitter = min(3.0, max(0.0, voltage_jitter))
        self.nominal_frequency = nominal_frequency
        self.cumulative_energy_kwh = max(0.0, float(initial_energy_kwh))

        if start_time is not None:
            if start_time.tzinfo is None:
                self.current_time = start_time.replace(tzinfo=timezone.utc)
            else:
                self.current_time = start_time
        else:
            self.current_time = datetime.now(timezone.utc)

        self.elapsed_seconds: float = 0.0
        self._rng = random.Random(seed)

    def set_breach_mode(self, enabled: bool, breach_power_kw: float | None = None) -> None:
        """Enable or disable automated peak breach injection."""
        self.trigger_breach = enabled
        if breach_power_kw is not None:
            self.breach_power_kw = breach_power_kw

    def _determine_breach_power(self) -> float:
        """Calculate active power for high-rate peak breach state."""
        if self.breach_power_kw is not None:
            return float(self.breach_power_kw)

        # Force well above threshold depending on profile
        threshold = self.profile.default_peak_threshold_kw
        if self.profile.name == "bakery":
            # Threshold 22 kW -> surge to 32.5 kW
            return 32.5
        elif self.profile.name == "cold_storage":
            # Threshold 25 kW -> surge to 36.5 kW
            return 36.5
        elif self.profile.name == "boutique_hotel":
            # Threshold 30 kW -> surge to 38.5 kW
            return 38.5
        else:
            return round(threshold * 1.35, 2)

    def generate_reading(
        self,
        dt: datetime | None = None,
        elapsed_seconds: float | None = None,
        step_seconds: float = 0.0,
    ) -> TelemetryPayload:
        """Generate a single physically invariant 3-phase TelemetryPayload.

        Args:
            dt: Optional measurement datetime (defaults to current_time).
            elapsed_seconds: Optional elapsed seconds (defaults to self.elapsed_seconds).
            step_seconds: Elapsed time since prior reading to integrate energy.

        Returns:
            Validated TelemetryPayload instance.
        """
        timestamp = dt if dt is not None else self.current_time
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        elapsed = elapsed_seconds if elapsed_seconds is not None else self.elapsed_seconds

        # 1. Base power determination
        if self.trigger_breach:
            raw_power = self._determine_breach_power()
        else:
            raw_power = self.profile.get_power_kw(dt=timestamp, elapsed_seconds=elapsed)

        # 2. Gaussian noise injection
        if self.noise_kw > 0.0:
            noise = self._rng.gauss(0.0, self.noise_kw)
            total_active_kw = max(0.5, raw_power + noise)
        else:
            total_active_kw = max(0.5, raw_power)

        total_active_kw = round(total_active_kw, 2)

        # 3. 3-Phase partition with strict invariant: sum(P_i) == total_active_kw
        # Slight realistic imbalance across phases (~34% / 33% / 33% +/- jitter)
        ratio_jitter = self._rng.uniform(-0.015, 0.015)
        ratio_l1 = 0.34 + ratio_jitter
        ratio_l2 = 0.33 - (ratio_jitter / 2.0)
        p_l1 = round(total_active_kw * ratio_l1, 2)
        p_l2 = round(total_active_kw * ratio_l2, 2)
        p_l3 = round(total_active_kw - p_l1 - p_l2, 2)

        # Prevent negative phase power on edge cases
        if p_l3 < 0.0:
            p_l1 = round(total_active_kw / 3.0, 2)
            p_l2 = round(total_active_kw / 3.0, 2)
            p_l3 = round(total_active_kw - p_l1 - p_l2, 2)

        # 4. Realistic voltages: 230V +/- 3V (227.0 - 233.0 V)
        v_l1 = round(self.nominal_voltage + self._rng.uniform(-self.voltage_jitter, self.voltage_jitter), 1)
        v_l2 = round(self.nominal_voltage + self._rng.uniform(-self.voltage_jitter, self.voltage_jitter), 1)
        v_l3 = round(self.nominal_voltage + self._rng.uniform(-self.voltage_jitter, self.voltage_jitter), 1)

        # 5. Realistic power factors cos phi: 0.88 - 0.98
        pf_l1 = round(self._rng.uniform(0.88, 0.98), 2)
        pf_l2 = round(self._rng.uniform(0.88, 0.98), 2)
        pf_l3 = round(self._rng.uniform(0.88, 0.98), 2)

        # 6. Apparent power per phase [kVA] with invariant S >= P
        s_l1 = round(p_l1 / pf_l1, 2)
        s_l2 = round(p_l2 / pf_l2, 2)
        s_l3 = round(p_l3 / pf_l3, 2)

        s_l1 = max(s_l1, p_l1)
        s_l2 = max(s_l2, p_l2)
        s_l3 = max(s_l3, p_l3)

        # 7. Phase currents [A] = (S [kVA] * 1000) / V
        i_l1 = round((s_l1 * 1000.0) / v_l1, 1)
        i_l2 = round((s_l2 * 1000.0) / v_l2, 1)
        i_l3 = round((s_l3 * 1000.0) / v_l3, 1)

        # 8. Totals
        total_apparent_kva = round(s_l1 + s_l2 + s_l3, 2)
        total_apparent_kva = max(total_apparent_kva, total_active_kw)

        sys_pf = round(total_active_kw / total_apparent_kva, 2) if total_apparent_kva > 0.0 else 0.95
        sys_pf = max(0.85, min(0.99, sys_pf))

        # 9. Energy numeric integration
        if step_seconds > 0.0:
            delta_kwh = total_active_kw * (step_seconds / 3600.0)
            self.cumulative_energy_kwh += delta_kwh

        # 10. Frequency & RSSI
        grid_freq = round(self.nominal_frequency + self._rng.uniform(-0.04, 0.04), 2)
        wifi_rssi = round(self._rng.uniform(-68.0, -58.0), 1)

        phases = {
            "L1": PhaseReading(
                voltage_v=v_l1,
                current_a=i_l1,
                active_power_kw=p_l1,
                apparent_power_kva=s_l1,
                power_factor=pf_l1,
            ),
            "L2": PhaseReading(
                voltage_v=v_l2,
                current_a=i_l2,
                active_power_kw=p_l2,
                apparent_power_kva=s_l2,
                power_factor=pf_l2,
            ),
            "L3": PhaseReading(
                voltage_v=v_l3,
                current_a=i_l3,
                active_power_kw=p_l3,
                apparent_power_kva=s_l3,
                power_factor=pf_l3,
            ),
        }

        payload = TelemetryPayload(
            power_measurement_method="simulated",
            device_id=self.device_id,
            facility_id=self.facility_id,
            timestamp=timestamp,
            phases=phases,
            total_active_power_kw=total_active_kw,
            total_apparent_power_kva=total_apparent_kva,
            system_power_factor=sys_pf,
            cumulative_energy_kwh=round(self.cumulative_energy_kwh, 3),
            grid_frequency_hz=grid_freq,
            wifi_rssi_dbm=wifi_rssi,
        )

        return payload

    def step(self, step_seconds: float = 10.0) -> TelemetryPayload:
        """Advance the simulation time by step_seconds and return the new reading.

        Args:
            step_seconds: Virtual time in seconds to advance.

        Returns:
            New TelemetryPayload reading.
        """
        payload = self.generate_reading(
            dt=self.current_time,
            elapsed_seconds=self.elapsed_seconds,
            step_seconds=step_seconds,
        )
        self.elapsed_seconds += step_seconds
        from datetime import timedelta
        self.current_time += timedelta(seconds=step_seconds)
        return payload

    def stream(
        self,
        duration_seconds: float,
        step_seconds: float = 10.0,
    ) -> Iterator[TelemetryPayload]:
        """Stream telemetry payloads over a duration of virtual seconds.

        Args:
            duration_seconds: Total virtual seconds to simulate.
            step_seconds: Time interval between readings.

        Yields:
            TelemetryPayload readings.
        """
        remaining = duration_seconds
        while remaining > 0:
            current_step = min(remaining, step_seconds)
            payload = self.step(current_step)
            yield payload
            remaining -= current_step
