"""API authentication must guard actual mounted routes, not just a helper."""
import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from backend.config import settings
from backend.main import create_app


@pytest.fixture
def secured_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "competition-secret")
    monkeypatch.setattr(settings, "VIBER_BOT_TOKEN", "viber-secret")
    with TestClient(create_app(str(tmp_path / "auth.db"))) as client:
        yield client


SENSITIVE_ROUTES = pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/v1/facilities", None),
    ("POST", "/api/v1/telemetry", {}),
    ("GET", "/api/v1/market/dam/today", None),
    ("GET", "/api/v1/dashboard/metrics/bakery-central-athens", None),
    ("POST", "/api/v1/dashboard/config/bakery-central-athens", {}),
    ("GET", "/api/v1/optimization/status", None),
    ("GET", "/api/v1/viber/status", None),
    ("POST", "/api/v1/viber/send", {"receiver_id": "person", "text": "test"}),
    ("GET", "/api/v1/facilities/bakery-central-athens/assets", None),
])


@SENSITIVE_ROUTES
@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_mounted_sensitive_routes_reject_missing_or_wrong_key(secured_client, method, path, body, headers):
    response = secured_client.request(method, path, json=body, headers=headers)
    assert response.status_code == 401, response.text


@SENSITIVE_ROUTES
def test_protected_routes_fail_closed_when_api_key_unset_in_production(
    secured_client, monkeypatch, method, path, body
):
    # Startup refuses production without API_KEY, so unset it after the app is up.
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "API_KEY", None)
    for headers in ({}, {"X-API-Key": "competition-secret"}):
        response = secured_client.request(method, path, json=body, headers=headers)
        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "API key not configured"


def test_correct_key_and_public_health_and_dashboard_shell(secured_client):
    assert secured_client.get("/api/v1/facilities", headers={"X-API-Key": "competition-secret"}).status_code == 200
    assert secured_client.get("/health").status_code == 200
    assert secured_client.get("/dashboard").status_code == 200


def test_viber_valid_signature_without_api_key(secured_client):
    body = b'{"event":"webhook"}'
    signature = hmac.new(b"viber-secret", body, hashlib.sha256).hexdigest()
    assert secured_client.post("/api/v1/viber/webhook", content=body,
        headers={"X-Viber-Content-Signature": signature}).status_code == 200


def test_viber_missing_signature_cannot_bypass_auth(secured_client):
    assert secured_client.post("/api/v1/viber/webhook", json={"event": "webhook"}).status_code == 403


def test_viber_invalid_signature_is_rejected(secured_client):
    assert secured_client.post("/api/v1/viber/webhook", json={"event": "webhook"},
        headers={"X-Viber-Content-Signature": "invalid"}).status_code == 403


def test_production_startup_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "API_KEY", None)
    with pytest.raises(RuntimeError, match="API_KEY"), TestClient(create_app(str(tmp_path / "production.db"))):
        pass
