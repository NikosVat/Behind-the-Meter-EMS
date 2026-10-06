"""Shared pytest fixtures for Greek Commercial EMS testing suite."""

from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.config import settings
from backend.models.alert import (
    AlertEvent,
    AlertSeverity,
    AlertThresholdConfig,
    AlertType,
)
from backend.models.telemetry import (
    TelemetryPayload,
)
from bot.telegram_client import MockTelegramClient


@pytest.fixture(autouse=True)
def _open_dev_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keyless suites run in explicit open-dev mode; auth tests override these settings."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "ALLOW_OPEN_DEV_ACCESS", True)


@pytest.fixture
def mock_telegram_client() -> Generator[MockTelegramClient, None, None]:
    """Provide a clean in-memory MockTelegramClient for test verification."""
    client = MockTelegramClient()
    client.clear()
    yield client
    client.clear()


@pytest.fixture
def valid_telemetry_dict() -> dict[str, Any]:
    """Standard valid 3-phase commercial bakery telemetry payload dictionary.

    Conforms to telemetry interface specification:
    P_total = 17.90 kW, sum(phases) = 5.95 + 5.82 + 6.13 = 17.90 kW (|delta| = 0.00 <= 0.05).
    """
    return {
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


@pytest.fixture
def valid_telemetry_payload(valid_telemetry_dict: dict[str, Any]) -> TelemetryPayload:
    """Instantiated TelemetryPayload model object."""
    return TelemetryPayload.model_validate(valid_telemetry_dict)


@pytest.fixture
def invalid_telemetry_unbalanced_active_power(
    valid_telemetry_dict: dict[str, Any]
) -> dict[str, Any]:
    """Payload where total active power (25.0 kW) deviates from phase sum (17.90 kW) by > 0.05 kW."""
    data = dict(valid_telemetry_dict)
    data["total_active_power_kw"] = 25.0
    return data


@pytest.fixture
def invalid_telemetry_bad_power_factor(
    valid_telemetry_dict: dict[str, Any]
) -> dict[str, Any]:
    """Payload with system power factor > 1.0 (violating physical cos phi bound)."""
    data = dict(valid_telemetry_dict)
    data["system_power_factor"] = 1.25
    return data


@pytest.fixture
def invalid_telemetry_negative_frequency(
    valid_telemetry_dict: dict[str, Any]
) -> dict[str, Any]:
    """Payload with non-positive grid frequency."""
    data = dict(valid_telemetry_dict)
    data["grid_frequency_hz"] = -50.0
    return data


@pytest.fixture
def invalid_telemetry_missing_phase(
    valid_telemetry_dict: dict[str, Any]
) -> dict[str, Any]:
    """Payload missing required phase L3."""
    data = dict(valid_telemetry_dict)
    phases = dict(data["phases"])
    del phases["L3"]
    data["phases"] = phases
    return data


@pytest.fixture
def sample_bakery_facility_config() -> AlertThresholdConfig:
    """Pre-configured alert threshold settings for Commercial Bakery profile."""
    return AlertThresholdConfig(
        facility_id="bakery-central-athens",
        peak_threshold_kw=22.0,
        warning_threshold_ratio=0.85,
        low_pf_threshold=0.85,
        contracted_capacity_kva=35.0,
        cooldown_seconds=1800,
        hysteresis_factor=0.90,
        debounce_samples=3,
        chat_id=999111222,
    )


@pytest.fixture
def sample_cold_storage_facility_config() -> AlertThresholdConfig:
    """Pre-configured alert threshold settings for Cold Storage facility."""
    return AlertThresholdConfig(
        facility_id="cold-storage-piraeus",
        peak_threshold_kw=30.0,
        warning_threshold_ratio=0.85,
        low_pf_threshold=0.85,
        contracted_capacity_kva=50.0,
        cooldown_seconds=1800,
        hysteresis_factor=0.90,
        debounce_samples=3,
        chat_id=999222333,
    )


@pytest.fixture
def sample_boutique_hotel_facility_config() -> AlertThresholdConfig:
    """Pre-configured alert threshold settings for Boutique Hotel facility."""
    return AlertThresholdConfig(
        facility_id="hotel-plaka-boutique",
        peak_threshold_kw=25.0,
        warning_threshold_ratio=0.85,
        low_pf_threshold=0.85,
        contracted_capacity_kva=40.0,
        cooldown_seconds=1800,
        hysteresis_factor=0.90,
        debounce_samples=3,
        chat_id=999333444,
    )


@pytest.fixture
def sample_alert_event() -> AlertEvent:
    """Sample peak breach alert event."""
    return AlertEvent(
        id="alert-evt-001",
        facility_id="bakery-central-athens",
        alert_type=AlertType.PEAK_BREACH,
        severity=AlertSeverity.CRITICAL,
        timestamp=datetime.now(timezone.utc),
        message="🚨 <b>ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ ΣΕ ΖΩΝΗ ΑΙΧΜΗΣ</b>",
        current_power_kw=28.6,
        threshold_kw=22.0,
        excess_kw=6.6,
        active_zone="PEAK",
        estimated_penalty_eur=1.12,
        power_factor=0.98,
        metadata={"facility_type": "bakery", "recommendation": "turn off deck oven 2"},
        dispatched=True,
        dispatched_at=datetime.now(timezone.utc),
    )
