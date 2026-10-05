"""Integration tests for Greek Commercial EMS backend telemetry ingestion and facility APIs.

Validates:
- Fast REST ingestion with Pydantic v2 validation and invariant enforcement (422)
- SQLite time-series persistence in WAL mode
- Real-time running cost (€/h) and daily spend accumulation via Greek tariff engine
- Status, cost breakdown, and tariff endpoints for commercial SMBs
- Time-series query filtering (limit, start_time, end_time)
- Proactive alert dispatching via Telegram client on peak load breaches
- Multi-facility tenant isolation (Bakery, Cold Storage, Boutique Hotel)
"""

from __future__ import annotations

import copy
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.routes.telemetry import AlertDispatcher
from bot.telegram_client import MockTelegramClient


@pytest.fixture
def mock_bot() -> MockTelegramClient:
    """Provide an isolated MockTelegramClient."""
    return MockTelegramClient()


@pytest.fixture
def test_client(tmp_path, mock_bot: MockTelegramClient) -> Generator[TestClient, None, None]:
    """Provide a TestClient connected to an isolated SQLite database."""
    db_path = str(tmp_path / "test_ems_integration.db")
    app = create_app(db_path=db_path)
    dispatcher = AlertDispatcher(telegram_client=mock_bot)
    app.state.dispatcher = dispatcher

    with TestClient(app) as client:
        yield client


class TestSystemEndpoints:
    """Test health and root system metadata endpoints."""

    def test_root_metadata_endpoint(self, test_client: TestClient):
        res = test_client.get("/")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "Greek Commercial" in data["name"]
        assert "version" in data

    def test_health_check_endpoint(self, test_client: TestClient):
        res = test_client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_default_facilities_seeded_on_startup(self, test_client: TestClient):
        res = test_client.get("/api/v1/facilities")
        assert res.status_code == 200
        facilities = res.json()
        facility_ids = [f["facility_id"] for f in facilities]
        assert "bakery-central-athens" in facility_ids
        assert "cold-storage-piraeus" in facility_ids
        assert "hotel-plaka-boutique" in facility_ids


class TestTelemetryIngestionAPI:
    """Test POST /api/v1/telemetry ingestion logic."""

    def test_valid_telemetry_ingestion_success(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        res = test_client.post("/api/v1/telemetry", json=valid_telemetry_dict)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["facility_id"] == "bakery-central-athens"
        assert data["total_active_power_kw"] == 17.90
        assert data["reading_id"] >= 1
        assert data["running_cost_eur_per_h"] > 0.0
        assert data["current_rate_eur_per_kwh"] > 0.0
        assert isinstance(data["is_peak_window"], bool)

    def test_telemetry_persisted_to_database(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        # Ingest reading
        post_res = test_client.post("/api/v1/telemetry", json=valid_telemetry_dict)
        assert post_res.status_code == 200

        # Verify reading is reflected in facility status
        status_res = test_client.get("/api/v1/facilities/bakery-central-athens/status")
        assert status_res.status_code == 200
        data = status_res.json()
        assert data["latest_telemetry"] is not None
        assert data["latest_telemetry"]["total_active_power_kw"] == 17.90
        assert "L1" in data["latest_telemetry"]["phases"]
        assert data["latest_telemetry"]["phases"]["L1"]["voltage_v"] == 230.2

    def test_cumulative_energy_delta_and_cost_aggregation(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        # First reading: cumulative 100 kWh
        reading1 = copy.deepcopy(valid_telemetry_dict)
        reading1["timestamp"] = "2026-09-14T10:00:00Z"
        reading1["cumulative_energy_kwh"] = 100.0
        res1 = test_client.post("/api/v1/telemetry", json=reading1)
        assert res1.status_code == 200
        assert res1.json()["incremental_cost_eur"] == 0.0  # initial packet

        # Second reading: cumulative 110 kWh (+10 kWh delta)
        reading2 = copy.deepcopy(valid_telemetry_dict)
        reading2["timestamp"] = "2026-09-14T10:15:00Z"
        reading2["cumulative_energy_kwh"] = 110.0
        res2 = test_client.post("/api/v1/telemetry", json=reading2)
        assert res2.status_code == 200
        inc_cost = res2.json()["incremental_cost_eur"]
        assert inc_cost > 0.0  # 10 kWh * unit_rate > 0

        # Check daily summary reflects the 10 kWh delta and cost
        ct_res = test_client.get(
            "/api/v1/facilities/bakery-central-athens/cost-today?date=2026-09-14"
        )
        assert ct_res.status_code == 200
        ct_data = ct_res.json()
        assert ct_data["total_kwh"] == 10.0
        assert ct_data["total_spend_eur"] > 0.0


class TestElectricalInvariantRejections422:
    """Verify strict rejection (HTTP 422) when physical or schema invariants are violated."""

    def test_reject_unbalanced_active_power_sum(
        self, test_client: TestClient, invalid_telemetry_unbalanced_active_power: dict[str, Any]
    ):
        res = test_client.post("/api/v1/telemetry", json=invalid_telemetry_unbalanced_active_power)
        assert res.status_code == 422
        assert "Electrical invariant violated" in str(res.json()["detail"])

    def test_reject_power_factor_out_of_range(
        self, test_client: TestClient, invalid_telemetry_bad_power_factor: dict[str, Any]
    ):
        res = test_client.post("/api/v1/telemetry", json=invalid_telemetry_bad_power_factor)
        assert res.status_code == 422

    def test_reject_negative_frequency(
        self, test_client: TestClient, invalid_telemetry_negative_frequency: dict[str, Any]
    ):
        res = test_client.post("/api/v1/telemetry", json=invalid_telemetry_negative_frequency)
        assert res.status_code == 422

    def test_reject_missing_phase(
        self, test_client: TestClient, invalid_telemetry_missing_phase: dict[str, Any]
    ):
        res = test_client.post("/api/v1/telemetry", json=invalid_telemetry_missing_phase)
        assert res.status_code == 422

    def test_reject_apparent_power_less_than_active_power(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        bad_data = copy.deepcopy(valid_telemetry_dict)
        # S = 10.0 kVA < P = 17.90 kW (violates S >= P)
        bad_data["total_apparent_power_kva"] = 10.0
        res = test_client.post("/api/v1/telemetry", json=bad_data)
        assert res.status_code == 422
        assert "Physical invariant violation" in str(res.json()["detail"])

    def test_reject_negative_energy(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        bad_data = copy.deepcopy(valid_telemetry_dict)
        bad_data["cumulative_energy_kwh"] = -5.0
        res = test_client.post("/api/v1/telemetry", json=bad_data)
        assert res.status_code == 422


class TestFacilityEndpoints:
    """Test GET /api/v1/facilities/{id}/status, /cost-today, /tariffs."""

    def test_get_facility_status_success(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        test_client.post("/api/v1/telemetry", json=valid_telemetry_dict)
        res = test_client.get("/api/v1/facilities/bakery-central-athens/status")
        assert res.status_code == 200
        data = res.json()
        assert data["facility_id"] == "bakery-central-athens"
        assert data["name"] == "Bakery Central Athens"
        assert data["contract_type"] == "Γ22"
        assert data["tariff_color"] == "green"
        assert data["peak_threshold_kw"] == 22.0
        assert data["active_running_cost_eur_per_h"] > 0.0
        assert "zone" in data["active_tariff"]

    def test_get_facility_status_404_on_unknown(self, test_client: TestClient):
        res = test_client.get("/api/v1/facilities/non-existent-facility/status")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_get_facility_cost_today_success(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        test_client.post("/api/v1/telemetry", json=valid_telemetry_dict)
        res = test_client.get("/api/v1/facilities/bakery-central-athens/cost-today")
        assert res.status_code == 200
        data = res.json()
        assert data["facility_id"] == "bakery-central-athens"
        assert "total_kwh" in data
        assert "total_spend_eur" in data
        assert "peak_surcharges_eur" in data

    def test_get_facility_cost_today_404_on_unknown(self, test_client: TestClient):
        res = test_client.get("/api/v1/facilities/unknown-facility/cost-today")
        assert res.status_code == 404

    def test_get_facility_tariffs_success(self, test_client: TestClient):
        res = test_client.get("/api/v1/facilities/bakery-central-athens/tariffs")
        assert res.status_code == 200
        data = res.json()
        assert data["contract_type"] == "Γ22"
        assert data["tariff_color"] == "green"
        assert data["contracted_kva"] == 35.0
        assert data["peak_threshold_kw"] == 22.0
        assert "summer_peak" in data["peak_window_schedule"]

    def test_get_facility_tariffs_singular_alias(self, test_client: TestClient):
        # Support /tariff alias
        res = test_client.get("/api/v1/facilities/bakery-central-athens/tariff")
        assert res.status_code == 200
        assert res.json()["contract_type"] == "Γ22"

    def test_get_facility_tariffs_404_on_unknown(self, test_client: TestClient):
        res = test_client.get("/api/v1/facilities/unknown-facility/tariffs")
        assert res.status_code == 404


class TestTelemetryHistoryQueryFiltering:
    """Test GET /api/v1/facilities/{id}/telemetry history querying and time slicing."""

    def test_telemetry_history_limit_and_order(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        # Ingest 5 chronological readings
        base_time = datetime(2026, 9, 14, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            d = copy.deepcopy(valid_telemetry_dict)
            d["timestamp"] = (base_time + timedelta(minutes=i * 5)).isoformat()
            d["cumulative_energy_kwh"] = 100.0 + i * 2.0
            res = test_client.post("/api/v1/telemetry", json=d)
            assert res.status_code == 200

        # Query with limit=3
        res = test_client.get(
            "/api/v1/facilities/bakery-central-athens/telemetry?limit=3"
        )
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 3
        # Most recent first
        assert items[0]["cumulative_energy_kwh"] == 108.0
        assert items[1]["cumulative_energy_kwh"] == 106.0
        assert items[2]["cumulative_energy_kwh"] == 104.0

    def test_telemetry_history_time_window_filtering(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        base_time = datetime(2026, 9, 14, 8, 0, 0, tzinfo=timezone.utc)
        for i in range(6):
            d = copy.deepcopy(valid_telemetry_dict)
            d["timestamp"] = (base_time + timedelta(hours=i)).isoformat()
            test_client.post("/api/v1/telemetry", json=d)

        # Filter between 09:30 and 11:30 (should capture 10:00 and 11:00)
        start = "2026-09-14T09:30:00Z"
        end = "2026-09-14T11:30:00Z"
        res = test_client.get(
            f"/api/v1/facilities/bakery-central-athens/telemetry?start_time={start}&end_time={end}"
        )
        assert res.status_code == 200
        items = res.json()
        assert len(items) == 2


class TestProactiveAlertingAndThrottling:
    """Test breach detection, 3-sample debounce, 30-min cooldown, and hysteresis recovery."""

    def test_alert_triggered_after_three_debounced_breaches(
        self,
        test_client: TestClient,
        mock_bot: MockTelegramClient,
        valid_telemetry_dict: dict[str, Any],
    ):
        # Summer peak window is 14:00 - 17:00 (Mon - Fri)
        # Threshold for bakery is 22.0 kW; send 28.0 kW breach load
        peak_time = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)  # Wednesday 15:00 EEST summer peak

        breach_payload = copy.deepcopy(valid_telemetry_dict)
        breach_payload["total_active_power_kw"] = 28.0
        breach_payload["total_apparent_power_kva"] = 28.57
        # Balance 3 phases to 28.0 kW (9.33, 9.33, 9.34)
        breach_payload["phases"]["L1"]["active_power_kw"] = 9.33
        breach_payload["phases"]["L1"]["apparent_power_kva"] = 9.52
        breach_payload["phases"]["L2"]["active_power_kw"] = 9.33
        breach_payload["phases"]["L2"]["apparent_power_kva"] = 9.52
        breach_payload["phases"]["L3"]["active_power_kw"] = 9.34
        breach_payload["phases"]["L3"]["apparent_power_kva"] = 9.53

        # Sample 1: debounce count 1/3 (no alert)
        breach_payload["timestamp"] = peak_time.isoformat()
        r1 = test_client.post("/api/v1/telemetry", json=breach_payload)
        assert r1.status_code == 200
        assert r1.json()["alert_triggered"] is False
        assert mock_bot.message_count() == 0

        # Sample 2: debounce count 2/3 (no alert)
        breach_payload["timestamp"] = (peak_time + timedelta(seconds=10)).isoformat()
        r2 = test_client.post("/api/v1/telemetry", json=breach_payload)
        assert r2.status_code == 200
        assert r2.json()["alert_triggered"] is False
        assert mock_bot.message_count() == 0

        # Sample 3: debounce count 3/3 -> Alert TRIGGERED!
        breach_payload["timestamp"] = (peak_time + timedelta(seconds=20)).isoformat()
        r3 = test_client.post("/api/v1/telemetry", json=breach_payload)
        assert r3.status_code == 200
        data3 = r3.json()
        assert data3["alert_triggered"] is True
        assert data3["alert_dispatched"] is True
        assert data3["projected_excess_penalty_eur"] > 0.0

        # Verify MockTelegramClient received the formatted Greek message
        assert mock_bot.message_count() == 1
        msg = mock_bot.get_last_message()
        assert msg is not None
        assert msg["chat_id"] == 999111222
        assert "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in msg["text"]
        assert "Bakery Central Athens" in msg["text"]
        assert "28.0 kW" in msg["text"]

    def test_alert_cooldown_and_hysteresis_clearance(
        self,
        test_client: TestClient,
        mock_bot: MockTelegramClient,
        valid_telemetry_dict: dict[str, Any],
    ):
        peak_time = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)  # Wednesday 15:00 EEST summer peak

        breach_payload = copy.deepcopy(valid_telemetry_dict)
        breach_payload["total_active_power_kw"] = 28.0
        breach_payload["total_apparent_power_kva"] = 28.57
        breach_payload["phases"]["L1"]["active_power_kw"] = 9.33
        breach_payload["phases"]["L1"]["apparent_power_kva"] = 9.52
        breach_payload["phases"]["L2"]["active_power_kw"] = 9.33
        breach_payload["phases"]["L2"]["apparent_power_kva"] = 9.52
        breach_payload["phases"]["L3"]["active_power_kw"] = 9.34
        breach_payload["phases"]["L3"]["apparent_power_kva"] = 9.53

        # Trigger alert with 3 samples
        for i in range(3):
            breach_payload["timestamp"] = (peak_time + timedelta(seconds=i * 10)).isoformat()
            test_client.post("/api/v1/telemetry", json=breach_payload)

        assert mock_bot.message_count() == 1

        # 4th sample 10 seconds later: power still 28 kW, but in COOLDOWN -> no second breach alert
        breach_payload["timestamp"] = (peak_time + timedelta(seconds=40)).isoformat()
        r4 = test_client.post("/api/v1/telemetry", json=breach_payload)
        assert r4.json()["alert_triggered"] is False
        assert mock_bot.message_count() == 1  # suppressed by cooldown

        # Now send safe load below 90% hysteresis (threshold 22 kW * 0.90 = 19.8 kW) -> 16.0 kW
        safe_payload = copy.deepcopy(valid_telemetry_dict)
        safe_payload["timestamp"] = (peak_time + timedelta(seconds=60)).isoformat()
        safe_payload["total_active_power_kw"] = 16.0
        safe_payload["total_apparent_power_kva"] = 16.33
        safe_payload["phases"]["L1"]["active_power_kw"] = 5.33
        safe_payload["phases"]["L1"]["apparent_power_kva"] = 5.44
        safe_payload["phases"]["L2"]["active_power_kw"] = 5.33
        safe_payload["phases"]["L2"]["apparent_power_kva"] = 5.44
        safe_payload["phases"]["L3"]["active_power_kw"] = 5.34
        safe_payload["phases"]["L3"]["apparent_power_kva"] = 5.45

        r_safe = test_client.post("/api/v1/telemetry", json=safe_payload)
        assert r_safe.status_code == 200
        # Recovery alert dispatched!
        assert mock_bot.message_count() == 2
        last_msg = mock_bot.get_last_message()
        assert "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in last_msg["text"]


class TestTenantMultiFacilityIsolation:
    """Verify multiple commercial facilities operate independently with zero cross-tenant contamination."""

    def test_multi_facility_status_isolation(
        self, test_client: TestClient, valid_telemetry_dict: dict[str, Any]
    ):
        # Post reading for Bakery
        bakery_payload = copy.deepcopy(valid_telemetry_dict)
        bakery_payload["facility_id"] = "bakery-central-athens"
        bakery_payload["total_active_power_kw"] = 18.0
        bakery_payload["total_apparent_power_kva"] = 18.37
        bakery_payload["phases"]["L1"]["active_power_kw"] = 6.0
        bakery_payload["phases"]["L1"]["apparent_power_kva"] = 6.12
        bakery_payload["phases"]["L2"]["active_power_kw"] = 6.0
        bakery_payload["phases"]["L2"]["apparent_power_kva"] = 6.12
        bakery_payload["phases"]["L3"]["active_power_kw"] = 6.0
        bakery_payload["phases"]["L3"]["apparent_power_kva"] = 6.13
        test_client.post("/api/v1/telemetry", json=bakery_payload)

        # Post reading for Cold Storage
        cold_payload = copy.deepcopy(valid_telemetry_dict)
        cold_payload["facility_id"] = "cold-storage-piraeus"
        cold_payload["total_active_power_kw"] = 28.5
        cold_payload["total_apparent_power_kva"] = 29.08
        cold_payload["phases"]["L1"]["active_power_kw"] = 9.5
        cold_payload["phases"]["L1"]["apparent_power_kva"] = 9.69
        cold_payload["phases"]["L2"]["active_power_kw"] = 9.5
        cold_payload["phases"]["L2"]["apparent_power_kva"] = 9.69
        cold_payload["phases"]["L3"]["active_power_kw"] = 9.5
        cold_payload["phases"]["L3"]["apparent_power_kva"] = 9.70
        test_client.post("/api/v1/telemetry", json=cold_payload)

        # Check Bakery status
        b_res = test_client.get("/api/v1/facilities/bakery-central-athens/status")
        assert b_res.status_code == 200
        assert b_res.json()["latest_telemetry"]["total_active_power_kw"] == 18.0

        # Check Cold Storage status
        c_res = test_client.get("/api/v1/facilities/cold-storage-piraeus/status")
        assert c_res.status_code == 200
        assert c_res.json()["latest_telemetry"]["total_active_power_kw"] == 28.5

        # Check Hotel status (has no readings yet)
        h_res = test_client.get("/api/v1/facilities/hotel-plaka-boutique/status")
        assert h_res.status_code == 200
        assert h_res.json()["latest_telemetry"] is None
        assert h_res.json()["active_running_cost_eur_per_h"] == 0.0
