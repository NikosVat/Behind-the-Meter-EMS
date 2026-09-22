"""Security dependencies and authentication utilities for Greek Commercial EMS."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import HTTPException, Security, status
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
