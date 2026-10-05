"""
Fixtures specifically for the e2e test tiers.
"""

import pytest

from tests.e2e.test_tiers.harness import (
    FacilityProfileConfig,
)


@pytest.fixture
def sample_facility_bakery():
    """Commercial bakery facility profile fixture."""
    return FacilityProfileConfig(
        facility_id="bakery-central-athens",
        facility_name="Φούρνος Αθήνας",
        facility_type="bakery",
        contract_code="G22",
        tariff_color="green",
        contracted_kva=35.0,
        peak_threshold_kw=22.0,
        cooldown_seconds=1800,
        telegram_chat_id=999111222,
    )


@pytest.fixture
def sample_facility_cold_storage():
    """Cold storage logistics facility profile fixture."""
    return FacilityProfileConfig(
        facility_id="cold-storage-piraeus",
        facility_name="Ψυγεία Πειραιώς",
        facility_type="cold_storage",
        contract_code="G22",
        tariff_color="yellow",
        contracted_kva=50.0,
        peak_threshold_kw=25.0,
        cooldown_seconds=1800,
        telegram_chat_id=999333444,
    )


@pytest.fixture
def sample_facility_hotel():
    """Boutique hotel facility profile fixture."""
    return FacilityProfileConfig(
        facility_id="hotel-santorini-suites",
        facility_name="Ξενοδοχείο Σαντορίνη",
        facility_type="hotel",
        contract_code="G23",
        tariff_color="dynamic",
        contracted_kva=100.0,
        peak_threshold_kw=30.0,
        cooldown_seconds=1800,
        telegram_chat_id=999555666,
    )


@pytest.fixture
def sample_telemetry_dict():
    """Returns a valid 3-phase telemetry dictionary matching the telemetry schema."""
    return {
        "device_id": "esp32-ems-001",
        "facility_id": "bakery-central-athens",
        "timestamp": "2026-09-14T15:30:00Z",
        "phases": {
            "L1": {"voltage_v": 230.2, "current_a": 26.4, "active_power_kw": 5.95, "apparent_power_kva": 6.08, "power_factor": 0.98},
            "L2": {"voltage_v": 229.8, "current_a": 25.8, "active_power_kw": 5.82, "apparent_power_kva": 5.93, "power_factor": 0.98},
            "L3": {"voltage_v": 231.0, "current_a": 27.1, "active_power_kw": 6.13, "apparent_power_kva": 6.26, "power_factor": 0.98}
        },
        "total_active_power_kw": 17.90,
        "total_apparent_power_kva": 18.27,
        "system_power_factor": 0.98,
        "cumulative_energy_kwh": 142.50,
        "grid_frequency_hz": 50.01,
        "wifi_rssi_dbm": -62
    }
