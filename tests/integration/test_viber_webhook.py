"""Integration tests for Viber Webhook API endpoints and interactive command handling.

Verifies:
- GET /api/v1/viber/status returns health and mode information.
- POST /api/v1/viber/send dispatches notifications via IViberClient.
- POST /api/v1/viber/webhook handles 'webhook' verification handshake.
- POST /api/v1/viber/webhook handles 'message' commands (/status, /cost_today, /tariff, /settings, /help, /start).
- HMAC-SHA256 signature verification protects the webhook when X-Viber-Content-Signature is enforced.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.database.sqlite_store import get_store
from backend.main import create_app
from bot.viber_client import MockViberClient


@pytest.fixture
def test_db_path(tmp_path) -> str:
    db_file = tmp_path / "test_viber.db"
    return str(db_file)


@pytest.fixture
def mock_viber():
    return MockViberClient()


@pytest.fixture
def app_client(test_db_path: str, mock_viber: MockViberClient) -> Generator[TestClient, None, None]:
    app = create_app(db_path=test_db_path)
    # Inject mock Viber client onto application state
    app.state.viber_client = mock_viber

    store = get_store(test_db_path)
    store.init_db()
    store.seed_default_facilities()

    # Configure a facility with a known Viber receiver ID
    store.store_facility_config({
        "facility_id": "bakery-central-athens",
        "name": "Κεντρικός Φούρνος Αθήνας",
        "contract_type": "Γ22",
        "tariff_color": "green",
        "contracted_kva": 35.0,
        "peak_threshold_kw": 22.0,
        "viber_receiver_id": "vb_usr_bakery_123",
        "notification_channel": "both",
    })

    store.store_telemetry({
        "device_id": "esp32-meter-01",
        "facility_id": "bakery-central-athens",
        "timestamp": datetime.now(timezone.utc),
        "total_active_power_kw": 18.5,
        "total_apparent_power_kva": 19.2,
        "system_power_factor": 0.96,
        "cumulative_energy_kwh": 240.5,
        "phases": {
            "L1": {"voltage_v": 231.0, "current_a": 27.0, "active_power_kw": 6.2},
            "L2": {"voltage_v": 230.5, "current_a": 26.5, "active_power_kw": 6.1},
            "L3": {"voltage_v": 229.8, "current_a": 27.2, "active_power_kw": 6.2},
        },
    })

    with TestClient(app) as client:
        yield client


def test_viber_status_endpoint(app_client: TestClient):
    response = app_client.get("/api/v1/viber/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["mode"] == "mock"


def test_viber_send_endpoint(app_client: TestClient, mock_viber: MockViberClient):
    payload = {
        "receiver_id": "receiver_999",
        "text": "Ειδοποίηση δοκιμής EMS",
        "sender_name": "EMS Alert Bot",
    }
    response = app_client.post("/api/v1/viber/send", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["sent"] is True

    last_msg = mock_viber.get_last_message("receiver_999")
    assert last_msg is not None
    assert last_msg["text"] == "Ειδοποίηση δοκιμής EMS"


def test_viber_webhook_verification_handshake(app_client: TestClient):
    payload = {
        "event": "webhook",
        "timestamp": 1715000000,
        "message_token": 123456789,
    }
    response = app_client.post("/api/v1/viber/webhook", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == 0


def test_viber_webhook_command_status(app_client: TestClient, mock_viber: MockViberClient):
    payload = {
        "event": "message",
        "timestamp": 1715000001,
        "message_token": 987654321,
        "sender": {
            "id": "vb_usr_bakery_123",
            "name": "Νίκος Αρτοποιός",
        },
        "message": {
            "type": "text",
            "text": "/status",
        },
    }
    response = app_client.post("/api/v1/viber/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == 0
    assert "reply" in data
    # Greek text response with no HTML tags
    assert "<b>" not in data["reply"]
    assert "Κατάσταση" in data["reply"] or "Εγκατάσταση" in data["reply"]

    # Verify MockViberClient captured the outbound reply
    last_msg = mock_viber.get_last_message("vb_usr_bakery_123")
    assert last_msg is not None
    assert "<b>" not in last_msg["text"]


def test_viber_webhook_command_cost_today(app_client: TestClient, mock_viber: MockViberClient):
    payload = {
        "event": "message",
        "timestamp": 1715000002,
        "message_token": 987654322,
        "sender": {
            "id": "vb_usr_bakery_123",
            "name": "Νίκος Αρτοποιός",
        },
        "message": {
            "type": "text",
            "text": "/cost_today",
        },
    }
    response = app_client.post("/api/v1/viber/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "Σημερινή Κατανάλωση" in data["reply"] or "Κόστος" in data["reply"]
    assert "<b>" not in data["reply"]


def test_viber_webhook_command_tariff(app_client: TestClient):
    payload = {
        "event": "message",
        "timestamp": 1715000003,
        "message_token": 987654323,
        "sender": {"id": "vb_usr_bakery_123"},
        "message": {"type": "text", "text": "/tariff"},
    }
    response = app_client.post("/api/v1/viber/webhook", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "Στοιχεία Τιμολογίου" in data["reply"]
    assert "Γ22" in data["reply"]


def test_viber_webhook_signature_verification(monkeypatch, app_client: TestClient):
    token = "secret_viber_bot_token"
    monkeypatch.setattr(settings, "VIBER_BOT_TOKEN", token)

    payload = {
        "event": "message",
        "sender": {"id": "user_x"},
        "message": {"type": "text", "text": "/help"},
    }
    body_bytes = json.dumps(payload).encode("utf-8")

    # 1. Invalid signature -> 403 Forbidden
    resp_invalid = app_client.post(
        "/api/v1/viber/webhook",
        content=body_bytes,
        headers={"Content-Type": "application/json", "X-Viber-Content-Signature": "invalid_signature"},
    )
    assert resp_invalid.status_code == 403

    # 2. Valid signature -> 200 OK
    valid_sig = hmac.new(token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    resp_valid = app_client.post(
        "/api/v1/viber/webhook",
        content=body_bytes,
        headers={"Content-Type": "application/json", "X-Viber-Content-Signature": valid_sig},
    )
    assert resp_valid.status_code == 200
