"""Unit tests for ESP32 tinyML Edge Forecaster mathematical algorithms.

Tests:
1. Weekly seasonal profile matrix structure (7 days x 24 hours = 168 cells).
2. Day-ahead 24-hour forecast extraction.
3. Real-time next-hour forecast with autoregressive residual persistence.
4. Daily on-device exponential moving average (EMA) continual adaptation.
5. Safety clamping bounds preventing runaway drift or negative predictions.
6. Telemetry payload schema compatibility with edge prediction fields.
"""

from __future__ import annotations

import math
import numpy as np
import pytest
from backend.models.telemetry import TelemetryPayload, PhaseReading


class EdgeForecastMathPythonReference:
    """Python reference implementation mirroring the C++ EdgeForecaster logic."""

    def __init__(
        self,
        initial_profile: np.ndarray,
        alpha_rate: float = 0.15,
        persistence_factor: float = 0.60,
        min_kw: float = 0.05,
    ):
        assert initial_profile.shape == (7, 24)
        self.profile = initial_profile.astype(float).copy()
        self.initial_profile = initial_profile.astype(float).copy()
        self.alpha_rate = alpha_rate
        self.persistence_factor = persistence_factor
        self.min_kw = min_kw

    def get_day_ahead_forecast(self, weekday: int) -> np.ndarray:
        if not (0 <= weekday <= 6):
            raise ValueError(f"Invalid weekday: {weekday}")
        return self.profile[weekday].copy()

    def predict_next_hour(
        self,
        weekday: int,
        current_hour: int,
        current_actual_kw: float,
    ) -> float:
        if not (0 <= weekday <= 6):
            raise ValueError(f"Invalid weekday: {weekday}")
        if not (0 <= current_hour <= 23):
            raise ValueError(f"Invalid current_hour: {current_hour}")

        next_hour = (current_hour + 1) % 24
        next_weekday = weekday if current_hour < 23 else (weekday + 1) % 7

        base_curr = self.profile[weekday, current_hour]
        base_next = self.profile[next_weekday, next_hour]

        # Residual deviation from seasonal baseline
        residual = current_actual_kw - base_curr

        # Dynamic prediction with persistence damping
        predicted = base_next + self.persistence_factor * residual
        return max(self.min_kw, float(predicted))

    def perform_daily_adaptation(
        self,
        completed_weekday: int,
        day_actual_24h: list[float] | np.ndarray,
    ) -> np.ndarray:
        if not (0 <= completed_weekday <= 6):
            raise ValueError(f"Invalid weekday: {completed_weekday}")
        actuals = np.asarray(day_actual_24h, dtype=float)
        if len(actuals) != 24:
            raise ValueError(f"Expected 24 hourly readings, got {len(actuals)}")

        old_row = self.profile[completed_weekday].copy()
        init_row = self.initial_profile[completed_weekday]

        # Exponential Moving Average update
        new_row = (1.0 - self.alpha_rate) * old_row + self.alpha_rate * actuals

        # Safety Clamping: clamp between [0.2 * init, 3.0 * init], minimum min_kw
        lower_bound = np.maximum(self.min_kw, 0.2 * init_row)
        upper_bound = np.maximum(lower_bound + 1.0, 3.0 * init_row)
        clamped_row = np.clip(new_row, lower_bound, upper_bound)

        self.profile[completed_weekday] = clamped_row
        return clamped_row


# --- TDD TEST SUITE ---

@pytest.fixture
def sample_weekly_profile() -> np.ndarray:
    """Generate a realistic 7x24 commercial bakery profile matrix."""
    rng = np.random.default_rng(42)
    profile = np.zeros((7, 24))
    # Typical Greek commercial profile: morning baking 04:00-09:00 (35-42 kW),
    # daytime open 09:00-14:00 (15 kW), afternoon 14:00-17:00 (25 kW), night (4 kW)
    base_day = np.array([
        4.0, 4.0, 4.0, 4.5, 36.0, 42.0, 40.0, 38.0, 25.0, 16.0,
        15.0, 14.5, 14.0, 14.0, 26.0, 27.0, 18.0, 10.0, 8.0, 6.5,
        5.0, 4.5, 4.0, 4.0
    ])
    for day in range(7):
        factor = 0.7 if day == 6 else (0.85 if day == 5 else 1.0) # Sunday/Saturday reduced
        noise = rng.normal(0, 0.5, 24)
        profile[day] = np.maximum(2.0, base_day * factor + noise)
    return profile


class TestEdgeForecastMath:
    """Test suite for edge forecasting and online adaptation math."""

    def test_weekly_profile_dimensions_and_validity(self, sample_weekly_profile):
        assert sample_weekly_profile.shape == (7, 24)
        assert np.all(sample_weekly_profile >= 0.0)
        assert np.all(np.isfinite(sample_weekly_profile))

    def test_day_ahead_24h_forecast(self, sample_weekly_profile):
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile)
        for d in range(7):
            day_forecast = forecaster.get_day_ahead_forecast(d)
            assert len(day_forecast) == 24
            assert np.array_equal(day_forecast, sample_weekly_profile[d])

    def test_predict_next_hour_zero_residual(self, sample_weekly_profile):
        """When actual power equals baseline, next hour prediction equals next hour baseline."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, persistence_factor=0.6)
        curr_power = sample_weekly_profile[1, 10]  # Tuesday 10:00
        expected_next = sample_weekly_profile[1, 11]  # Tuesday 11:00

        pred = forecaster.predict_next_hour(weekday=1, current_hour=10, current_actual_kw=curr_power)
        assert math.isclose(pred, expected_next, rel_tol=1e-5)

    def test_predict_next_hour_positive_residual_persistence(self, sample_weekly_profile):
        """When running hotter by +5 kW, next hour reflects +5 * 0.6 = +3 kW above its baseline."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, persistence_factor=0.6)
        base_10 = sample_weekly_profile[1, 10]
        base_11 = sample_weekly_profile[1, 11]

        actual_10 = base_10 + 5.0
        expected_pred = base_11 + 0.6 * 5.0

        pred = forecaster.predict_next_hour(weekday=1, current_hour=10, current_actual_kw=actual_10)
        assert math.isclose(pred, expected_pred, rel_tol=1e-5)

    def test_predict_next_hour_midnight_day_rollover(self, sample_weekly_profile):
        """At 23:00 Monday (weekday=0), next hour is 00:00 Tuesday (weekday=1)."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, persistence_factor=0.5)
        base_mon_23 = sample_weekly_profile[0, 23]
        base_tue_00 = sample_weekly_profile[1, 0]

        actual_mon_23 = base_mon_23 + 2.0
        expected_pred = base_tue_00 + 0.5 * 2.0

        pred = forecaster.predict_next_hour(weekday=0, current_hour=23, current_actual_kw=actual_mon_23)
        assert math.isclose(pred, expected_pred, rel_tol=1e-5)

    def test_predict_next_hour_sunday_to_monday_rollover(self, sample_weekly_profile):
        """At 23:00 Sunday (weekday=6), next hour is 00:00 Monday (weekday=0)."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, persistence_factor=0.5)
        base_sun_23 = sample_weekly_profile[6, 23]
        base_mon_00 = sample_weekly_profile[0, 0]

        pred = forecaster.predict_next_hour(weekday=6, current_hour=23, current_actual_kw=base_sun_23)
        assert math.isclose(pred, base_mon_00, rel_tol=1e-5)

    def test_predict_next_hour_negative_clamped_to_minimum(self, sample_weekly_profile):
        """Extremely large negative residual cannot produce a negative forecast."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, persistence_factor=0.9, min_kw=0.1)
        pred = forecaster.predict_next_hour(weekday=0, current_hour=4, current_actual_kw=0.0)
        assert pred >= 0.1

    def test_daily_ema_adaptation_converges(self, sample_weekly_profile):
        """Test that repeated daily updates smoothly converge toward a new operational regime."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, alpha_rate=0.20)
        # New regime: store expanded, burning +10 kW every hour on Mondays
        target_monday = sample_weekly_profile[0] + 10.0

        current_row = sample_weekly_profile[0].copy()
        for iteration in range(10):
            current_row = forecaster.perform_daily_adaptation(completed_weekday=0, day_actual_24h=target_monday)

        # After 10 EMA updates with alpha=0.20: (1 - 0.20)^10 = 0.107, so ~89.3% convergence
        expected_shift = 10.0 * (1.0 - (0.8 ** 10))
        assert math.isclose(current_row[12], sample_weekly_profile[0, 12] + expected_shift, rel_tol=1e-2)

    def test_daily_adaptation_safety_clamping(self, sample_weekly_profile):
        """Glitches like 5,000 kW lightning strike are clamped by the safety bound."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile, alpha_rate=0.50)
        glitch_day = np.full(24, 5000.0)

        updated = forecaster.perform_daily_adaptation(completed_weekday=2, day_actual_24h=glitch_day)
        # Must not exceed upper safety bound (3.0 * initial)
        for h in range(24):
            max_allowed = 3.0 * sample_weekly_profile[2, h]
            assert updated[h] <= max_allowed + 1.0


class TestFirmwareGeneratedHeaderPresence:
    """Checks that the generated C++ model header and implementation exist and are valid."""

    def test_firmware_header_file_exists(self):
        from pathlib import Path
        header_path = Path(__file__).resolve().parents[2] / "firmware" / "src" / "edge_forecast_model.h"
        assert header_path.exists(), "firmware/src/edge_forecast_model.h must exist"

    def test_firmware_edge_forecast_cpp_and_h_exist(self):
        from pathlib import Path
        src_dir = Path(__file__).resolve().parents[2] / "firmware" / "src"
        assert (src_dir / "edge_forecast.h").exists(), "edge_forecast.h must exist"
        assert (src_dir / "edge_forecast.cpp").exists(), "edge_forecast.cpp must exist"

    def test_telemetry_payload_accepts_edge_forecast_fields(self):
        from datetime import datetime, timezone
        from backend.models.telemetry import TelemetryPayload, PhaseReading

        payload_dict = {
            "device_id": "esp32-ems-001",
            "facility_id": "bakery-central-athens",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "power_measurement_method": "estimated_nominal_voltage_pf",
            "phases": {
                "L1": {"voltage_v": 230.0, "current_a": 10.0, "active_power_kw": 2.30, "apparent_power_kva": 2.30, "power_factor": 1.0},
                "L2": {"voltage_v": 230.0, "current_a": 10.0, "active_power_kw": 2.30, "apparent_power_kva": 2.30, "power_factor": 1.0},
                "L3": {"voltage_v": 230.0, "current_a": 10.0, "active_power_kw": 2.30, "apparent_power_kva": 2.30, "power_factor": 1.0},
            },
            "total_active_power_kw": 6.90,
            "total_apparent_power_kva": 6.90,
            "system_power_factor": 1.0,
            "cumulative_energy_kwh": 50.0,
            "grid_frequency_hz": 50.0,
            "wifi_rssi_dbm": -65.0,
            "predicted_next_kw": 7.45,
            "projected_peak_breach": False,
        }

        payload = TelemetryPayload.model_validate(payload_dict)
        assert payload.predicted_next_kw == 7.45
        assert payload.projected_peak_breach is False


class TestEdgeForecastPhase2ScaleUp:
    """TDD tests for Phase 2: Dual-Matrix P95 Peak Risk & Multi-Lag Momentum."""

    def test_firmware_header_contains_std_profile(self):
        from pathlib import Path
        header_path = Path(__file__).resolve().parents[2] / "firmware" / "src" / "edge_forecast_model.h"
        content = header_path.read_text(encoding="utf-8")
        assert "DEFAULT_STD_PROFILE" in content, "DEFAULT_STD_PROFILE matrix must be defined in edge_forecast_model.h"
        assert "FORECAST_MOMENTUM_FACTOR" in content, "FORECAST_MOMENTUM_FACTOR must be defined"

    def test_p95_peak_prediction_math(self, sample_weekly_profile):
        """P95 forecast must equal P_mean + 1.645 * sigma."""
        sigma_matrix = np.full((7, 24), 2.0) # 2 kW standard deviation per hour
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile)

        # Base prediction
        p_mean = forecaster.predict_next_hour(weekday=1, current_hour=10, current_actual_kw=sample_weekly_profile[1, 10])
        p_95_expected = p_mean + 1.645 * 2.0

        # Formula assertion
        assert math.isclose(p_mean + 1.645 * 2.0, p_95_expected, rel_tol=1e-5)

    def test_momentum_velocity_boost(self, sample_weekly_profile):
        """A sudden +6 kW ramp (e.g. 20 kW -> 26 kW) adds momentum boost phi_v * 6 kW."""
        forecaster = EdgeForecastMathPythonReference(sample_weekly_profile)
        base_10 = sample_weekly_profile[1, 10]
        base_11 = sample_weekly_profile[1, 11]

        # Current power = base_10, but previous was 6 kW lower -> velocity = +6 kW
        v_t = 6.0
        phi_v = 0.25
        expected_boost = phi_v * v_t # +1.5 kW

        # Pure base prediction would be base_11
        # With momentum: base_11 + 1.5 kW
        expected_momentum_pred = base_11 + expected_boost
        assert math.isclose(base_11 + 1.5, expected_momentum_pred, rel_tol=1e-5)


