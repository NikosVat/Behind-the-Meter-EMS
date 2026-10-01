"""Security dependencies and authentication utilities for Greek Commercial EMS."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from backend.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Annotated[str | None, Security(api_key_header)] = None) -> bool:
    """Verify incoming API key if API_KEY enforcement is configured.

    If settings.API_KEY is None or empty string, authentication is open (development/test mode).
    If settings.API_KEY is configured, requests must supply a matching 'X-API-Key' header.
    """
    if not settings.API_KEY:
        return True

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing required API key header 'X-API-Key'",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    # Constant-time comparison prevents timing attacks
    if not hmac.compare_digest(api_key.encode("utf-8"), settings.API_KEY.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return True


async def verify_dashboard_access(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> bool:
    """Allow the login shell; protect every dashboard data/configuration route."""
    if request.url.path == "/dashboard":
        return True
    return await verify_api_key(api_key)


async def verify_viber_access(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header)] = None,
) -> bool:
    """Viber callbacks authenticate with HMAC; management routes use the API key."""
    if request.url.path != f"{settings.API_V1_STR}/viber/webhook":
        return await verify_api_key(api_key)
    token = settings.VIBER_BOT_TOKEN or settings.VIBER_AUTH_TOKEN
    if token:
        signature = request.headers.get("X-Viber-Content-Signature", "")
        expected = hmac.new(token.encode("utf-8"), await request.body(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=403, detail="Invalid or missing Viber signature")
    elif settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=503, detail="Viber webhook authentication is not configured")
    return True
