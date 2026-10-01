"""Full system end-to-end integration pipeline tests for Greek Commercial EMS.

Validates the full pipeline:
- Telemetry generator -> REST Ingestion API -> Tariff Engine -> Alert Dispatcher -> MockTelegramClient.
- Scenarios:
  1. Commercial Bakery: Normal -> Peak breach -> 3-sample debounce -> Cooldown -> 10% Hysteresis recovery.
  2. Cold Storage Logistics: Refrigeration compressor & dock door surge with tailored Greek advice.
  3. Boutique Hotel: Afternoon VRV AC ramp breach with tailored Greek advice.
  4. Facility status, tariff, and cost reporting endpoints consistency.
  5. Multi-reading pipeline execution benchmark (< 3.5 seconds).
"""

from __future__ import annotations

import time
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import SQLiteStore, get_store
from backend.main import create_app
from backend.routes.telemetry import AlertDispatcher
from bot.telegram_client import MockTelegramClient
from simulator.generator import TelemetryGenerator


@pytest.fixture
def e2e_env(tmp_path) -> Generator[tuple[TestClient, MockTelegramClient, SQLiteStore, AlertDispatcher], None, None]:
    """Isolated E2E environment with temporary SQLite store and MockTelegramClient."""
    db_path = str(tmp_path / "e2e_pipeline.db")
    app = create_app(db_path=db_path)
    store = get_store(db_path)
    store.init_db()
    store.seed_default_facilities()

    mock_bot = MockTelegramClient()
    dispatcher = AlertDispatcher(telegram_client=mock_bot)
    app.state.dispatcher = dispatcher

    with TestClient(app) as client:
        yield client, mock_bot, store, dispatcher


@pytest.fixture
def summer_peak_time() -> datetime:
    """Wednesday 12:00 UTC / 15:00 EEST (Active Greek summer peak window: 14:00 - 17:00)."""
    return datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


class TestFullPipelineE2E:
    """End-to-end pipeline test suite covering commercial SMB workflows."""

    def test_full_pipeline_bakery_breach_cooldown_recovery(
        self,
        e2e_env: tuple[TestClient, MockTelegramClient, SQLiteStore, AlertDispatcher],
        summer_peak_time: datetime,
    ):
        client, mock_bot, _store, _dispatcher = e2e_env

        gen = TelemetryGenerator(
            profile="bakery",
            facility_id="bakery-central-athens",
            device_id="esp32-bakery-001",
            start_time=summer_peak_time,
            seed=101,
        )

        # 1. Normal baseline reading (14.0 kW < 22.0 kW threshold)
        gen.set_breach_mode(True, breach_power_kw=14.0)
        p1 = gen.step(step_seconds=10.0)
        r1 = client.post("/api/v1/telemetry", json=p1.model_dump(mode="json"))
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["alert_triggered"] is False
        assert d1["is_peak_window"] is True
        assert d1["is_excess_breach"] is False
        assert mock_bot.message_count() == 0

        # 2. Surge load to 32.5 kW (> 22.0 kW threshold)
        gen.set_breach_mode(True, breach_power_kw=32.5)

        # Breach sample 1: debounce 1/3 (no alert)
        p_b1 = gen.step(step_seconds=10.0)
        r_b1 = client.post("/api/v1/telemetry", json=p_b1.model_dump(mode="json")).json()
        assert r_b1["alert_triggered"] is False
        assert mock_bot.message_count() == 0

        # Breach sample 2: debounce 2/3 (no alert)
        p_b2 = gen.step(step_seconds=10.0)
        r_b2 = client.post("/api/v1/telemetry", json=p_b2.model_dump(mode="json")).json()
        assert r_b2["alert_triggered"] is False
        assert mock_bot.message_count() == 0

        # Breach sample 3: debounce 3/3 -> Alert TRIGGERED & DISPATCHED!
        p_b3 = gen.step(step_seconds=10.0)
        r_b3 = client.post("/api/v1/telemetry", json=p_b3.model_dump(mode="json")).json()
        assert r_b3["alert_triggered"] is True
        assert r_b3["alert_dispatched"] is True
        assert r_b3["projected_excess_penalty_eur"] > 0.0

        # Verify Greek Telegram alert message
        assert mock_bot.message_count() == 1
        breach_msg = mock_bot.get_last_message()
        assert breach_msg is not None
        assert breach_msg["chat_id"] == 999111222
        assert "ΠΡΟΣΟΧΗ: ΥΠΕΡΒΑΣΗ ΟΡΙΟΥ" in breach_msg["text"]
        assert "32.5 kW" in breach_msg["text"]
        assert "22.0 kW" in breach_msg["text"]
        assert "14:00–17:00" in breach_msg["text"]
        assert "φούρνο" in breach_msg["text"]

        # 3. 4th sample during cooldown at 32.5 kW -> Suppressed
        p_b4 = gen.step(step_seconds=10.0)
        r_b4 = client.post("/api/v1/telemetry", json=p_b4.model_dump(mode="json")).json()
        assert r_b4["alert_triggered"] is False
        assert mock_bot.message_count() == 1  # No duplicate alert sent

        # 4. Load drops below 90% threshold (22.0 * 0.90 = 19.8 kW) -> 15.0 kW
        gen.set_breach_mode(True, breach_power_kw=15.0)
        p_rec = gen.step(step_seconds=10.0)
        r_rec = client.post("/api/v1/telemetry", json=p_rec.model_dump(mode="json")).json()
        assert r_rec["alert_triggered"] is True
        assert r_rec["alert_dispatched"] is True

        # Verify Recovery alert
        assert mock_bot.message_count() == 2
        rec_msg = mock_bot.get_last_message()
        assert "ΟΜΑΛΟΠΟΙΗΣΗ ΚΑΤΑΝΑΛΩΣΗΣ" in rec_msg["text"]
        assert "15.0 kW" in rec_msg["text"]

    def test_full_pipeline_cold_storage_compressor_breach(
        self,
        e2e_env: tuple[TestClient, MockTelegramClient, SQLiteStore, AlertDispatcher],
        summer_peak_time: datetime,
    ):
        client, mock_bot, _store, _dispatcher = e2e_env

        gen = TelemetryGenerator(
            profile="cold_storage",
            facility_id="cold-storage-piraeus",
            device_id="esp32-cold-001",
            start_time=summer_peak_time,
            seed=202,
        )

        # Threshold for cold storage is 25.0 kW; surge to 34.5 kW
        gen.set_breach_mode(True, breach_power_kw=34.5)

        # Send 3 debounce samples
        for i in range(3):
            p = gen.step(step_seconds=10.0)
            res = client.post("/api/v1/telemetry", json=p.model_dump(mode="json"))
            assert res.status_code == 200

        assert mock_bot.message_count() == 1
        msg = mock_bot.get_last_message()
        assert msg["chat_id"] == 999222333
        assert "34.5 kW" in msg["text"]
        assert "πόρτες" in msg["text"] or "απόψυξης" in msg["text"]

    def test_full_pipeline_hotel_vrv_chiller_breach(
        self,
        e2e_env: tuple[TestClient, MockTelegramClient, SQLiteStore, AlertDispatcher],
        summer_peak_time: datetime,
    ):
        client, mock_bot, _store, _dispatcher = e2e_env

        gen = TelemetryGenerator(
            profile="boutique_hotel",
            facility_id="hotel-plaka-boutique",
            device_id="esp32-hotel-001",
            start_time=summer_peak_time,
            seed=303,
        )

        # Threshold for hotel is 25.0 kW; surge to 34.0 kW
        gen.set_breach_mode(True, breach_power_kw=34.0)

        # Send 3 debounce samples
        for i in range(3):
            p = gen.step(step_seconds=10.0)
            res = client.post("/api/v1/telemetry", json=p.model_dump(mode="json"))
            assert res.status_code == 200

        assert mock_bot.message_count() == 1
        msg = mock_bot.get_last_message()
        assert msg["chat_id"] == 999333444
        assert "34.0 kW" in msg["text"]
        assert "VRV" in msg["text"] or "πλυντήρια" in msg["text"]

    def test_full_pipeline_facility_endpoints_consistency(
        self,
        e2e_env: tuple[TestClient, MockTelegramClient, SQLiteStore, AlertDispatcher],
        summer_peak_time: datetime,
    ):
        client, _mock_bot, _store, _dispatcher = e2e_env

        gen = TelemetryGenerator(
            profile="bakery",
            facility_id="bakery-central-athens",
            start_time=summer_peak_time,
            seed=404,
        )

        # Ingest 3 consecutive readings
        for i in range(3):
            p = gen.step(step_seconds=10.0)
            res = client.post("/api/v1/telemetry", json=p.model_dump(mode="json"))
            assert res.status_code == 200

        # Check /status
        st_res = client.get("/api/v1/facilities/bakery-central-athens/status")
        assert st_res.status_code == 200
        st = st_res.json()
        assert st["facility_id"] == "bakery-central-athens"
        assert st["latest_telemetry"] is not None
        assert st["active_running_cost_eur_per_h"] > 0.0

        # Check /tariff
        tf_res = client.get("/api/v1/facilities/bakery-central-athens/tariff")
        assert tf_res.status_code == 200
        tf = tf_res.json()
        assert tf["contract_type"] == "Γ22"
        assert tf["peak_threshold_kw"] == 22.0

        # Check /cost-today with date
        target_date = summer_peak_time.strftime("%Y-%m-%d")
        ct_res = client.get(f"/api/v1/facilities/bakery-central-athens/cost-today?date={target_date}")
        assert ct_res.status_code == 200
        ct = ct_res.json()
        assert ct["total_kwh"] > 0.0
        assert ct["total_spend_eur"] >= 0.0

    def test_full_pipeline_execution_benchmark(
        self,
        e2e_env: tuple[TestClient, MockTelegramClient, SQLiteStore, AlertDispatcher],
        summer_peak_time: datetime,
    ):
        """Verify that a 15-reading end-to-end ingestion and evaluation executes under 3.5s."""
        client, _mock_bot, _store, _dispatcher = e2e_env

        gen = TelemetryGenerator(
            profile="bakery",
            facility_id="bakery-central-athens",
            start_time=summer_peak_time,
            seed=505,
        )

        t_start = time.perf_counter()
        for i in range(15):
            p = gen.step(step_seconds=10.0)
            res = client.post("/api/v1/telemetry", json=p.model_dump(mode="json"))
            assert res.status_code == 200

        duration_s = time.perf_counter() - t_start
        assert duration_s < 3.5, f"15-reading pipeline took {duration_s:.3f}s (budget: 3.5s)"
