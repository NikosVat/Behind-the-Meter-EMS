import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import create_app
from backend.models.telemetry import PhaseReading, TelemetryPayload
from backend.routes.telemetry import AlertDispatcher
from bot.telegram_client import MockTelegramClient


@pytest.fixture
def test_client(tmp_path):
    """Provide a TestClient connected to an isolated SQLite database."""
    db_path = str(tmp_path / "test_ems_nonfinite_regressions.db")
    app = create_app(db_path=db_path)
    dispatcher = AlertDispatcher(telegram_client=MockTelegramClient())
    app.state.dispatcher = dispatcher
    with TestClient(app) as client:
        yield client


class TestNonfiniteTelemetryRegressions:
    def test_phase_reading_rejects_nan_and_inf(self):
        """PhaseReading must reject float('nan') and float('inf')."""
        with pytest.raises(ValidationError):
            PhaseReading(
                voltage_v=230.0,
                current_a=10.0,
                active_power_kw=float("nan"),
                apparent_power_kva=2.3,
                power_factor=0.98,
            )

        with pytest.raises(ValidationError):
            PhaseReading(
                voltage_v=float("inf"),
                current_a=10.0,
                active_power_kw=2.3,
                apparent_power_kva=2.3,
                power_factor=0.98,
            )

    def test_phase_reading_rejects_string_nan(self):
        """PhaseReading must reject string 'NaN'."""
        with pytest.raises(ValidationError):
            PhaseReading.model_validate({
                "voltage_v": 230.0,
                "current_a": 10.0,
                "active_power_kw": "NaN",
                "apparent_power_kva": 2.3,
                "power_factor": 0.98,
            })

    def test_telemetry_payload_rejects_nan_active_power(self, valid_telemetry_dict):
        """TelemetryPayload must reject NaN in total_active_power_kw."""
        payload_data = dict(valid_telemetry_dict)
        payload_data["total_active_power_kw"] = float("nan")
        with pytest.raises(ValidationError):
            TelemetryPayload.model_validate(payload_data)

    def test_ingestion_api_rejects_nan_payload(self, test_client, valid_telemetry_dict):
        """POST /api/v1/telemetry must return 422 for NaN active power."""
        payload_data = dict(valid_telemetry_dict)
        payload_data["total_active_power_kw"] = "NaN"
        response = test_client.post("/api/v1/telemetry", json=payload_data)
        assert response.status_code == 422
