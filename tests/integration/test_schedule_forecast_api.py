from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import get_store
from backend.main import create_app
from backend.models.telemetry import TelemetryPayload


@pytest.fixture
def client(tmp_path):
    path = str(tmp_path / "forecast.db")
    with TestClient(create_app(path)) as c:
        yield c, get_store(path)


URL = "/api/v1/facilities/bakery-central-athens/schedules/preview"


def test_configured_asset_without_history_is_demo(client):
    c, _ = client
    c.post("/api/v1/facilities/bakery-central-athens/assets", json={
        "name": "oven", "rated_power_kw": 2, "required_runtime_minutes": 15})
    r = c.post(URL, json={"schedule_date": "2026-10-01"})
    assert r.status_code == 200
    assert r.json()["is_demo"] is True
    assert r.json()["input_source"] == "demo_profile"


def test_strict_telemetry_mode_does_not_silently_demo(client):
    c, _ = client
    assert c.post(URL, json={"data_mode": "telemetry"}).status_code == 422


@pytest.mark.parametrize("payload", [{"baseline_load_kw": [5] * 23}, {"schedule_date": "invalid"}])
def test_bad_inputs_are_client_errors(client, payload):
    c, _ = client
    assert c.post(URL, json=payload).status_code == 422


def test_telemetry_forecast_enters_schedule_and_save(client, valid_telemetry_dict):
    c, store = client
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    # Complete hourly observations including the previous local day. Independent DB only.
    for h in range(50 * 24):
        payload = dict(valid_telemetry_dict, timestamp=(start + timedelta(hours=h)).isoformat(),
                       cumulative_energy_kwh=200 + h * 17.9, power_measurement_method="meter_measured")
        store.store_telemetry(TelemetryPayload(**payload))
    r = c.post(URL, json={"schedule_date": "2026-09-19", "data_mode": "telemetry",
                         "tariff_rates_eur_kwh": [.2] * 24})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["input_source"] == "forecast"
    assert data["is_demo"] is False
    assert data["timeline_load"]["optimized_kw"] == pytest.approx([17.9] * 96, abs=.02)
    assert any("Forecast model:" in a for a in data["assumptions"])
    assert c.get("/api/v1/facilities/bakery-central-athens/load-forecast",
                 params={"schedule_date": "2026-09-19"}).status_code == 200


def test_invalid_weekdays_are_rejected_before_persistence(client):
    c, _ = client
    path = "/api/v1/facilities/bakery-central-athens/assets"
    r = c.post(path, json={"name": "oven", "rated_power_kw": 2,
                          "required_runtime_minutes": 15, "active_weekdays": [8]})
    assert r.status_code == 422
    assert c.get(path).json() == []
