"""Unit tests for backend/security.py API key authentication."""

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.security import verify_api_key


@pytest.mark.anyio
async def test_verify_api_key_open_only_with_explicit_dev_opt_in(monkeypatch):
    """No API_KEY: access is open only in development with ALLOW_OPEN_DEV_ACCESS."""
    monkeypatch.setattr(settings, "API_KEY", None)
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "ALLOW_OPEN_DEV_ACCESS", True)
    assert await verify_api_key(None) is True


@pytest.mark.anyio
@pytest.mark.parametrize("environment,allow_open", [
    ("production", False),
    ("production", True),
    ("development", False),
    ("staging", True),
])
async def test_verify_api_key_fails_closed_without_key(monkeypatch, environment, allow_open):
    """No API_KEY and no valid dev opt-in: raises 503, whatever header is sent."""
    monkeypatch.setattr(settings, "API_KEY", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "ALLOW_OPEN_DEV_ACCESS", allow_open)
    for header in (None, "some_arbitrary_key"):
        with pytest.raises(HTTPException) as exc_info:
            await verify_api_key(header)
        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "API key not configured"


@pytest.mark.anyio
async def test_verify_api_key_when_key_configured_success(monkeypatch):
    """When API_KEY is set and matches header, access is granted."""
    monkeypatch.setattr(settings, "API_KEY", "super-secret-key-12345")
    assert await verify_api_key("super-secret-key-12345") is True


@pytest.mark.anyio
async def test_verify_api_key_when_missing_header_raises_401(monkeypatch):
    """When API_KEY is set but header is missing, raises 401."""
    monkeypatch.setattr(settings, "API_KEY", "super-secret-key-12345")
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key(None)
    assert exc_info.value.status_code == 401
    assert "Missing required API key" in exc_info.value.detail


@pytest.mark.anyio
async def test_verify_api_key_when_invalid_key_raises_401(monkeypatch):
    """When API_KEY is set but header does not match, raises 401."""
    monkeypatch.setattr(settings, "API_KEY", "super-secret-key-12345")
    with pytest.raises(HTTPException) as exc_info:
        await verify_api_key("wrong-key")
    assert exc_info.value.status_code == 401
    assert "Invalid API key" in exc_info.value.detail
