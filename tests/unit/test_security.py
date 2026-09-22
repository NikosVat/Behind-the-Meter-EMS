"""Unit tests for backend/security.py API key authentication."""

import pytest
from fastapi import HTTPException

from backend.config import settings
from backend.security import verify_api_key


@pytest.mark.anyio
async def test_verify_api_key_when_no_key_configured(monkeypatch):
    """When API_KEY is None, access is granted without checking header."""
    monkeypatch.setattr(settings, "API_KEY", None)
    assert await verify_api_key(None) is True
    assert await verify_api_key("some_arbitrary_key") is True


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
