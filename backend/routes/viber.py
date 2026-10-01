"""Viber Bot Webhook and Messaging REST router for Greek Commercial EMS.

Provides:
- POST /api/v1/viber/webhook: Inbound Viber webhook callback handler (signature verification, command routing)
- POST /api/v1/viber/send: Outbound direct message dispatch via IViberClient
- GET /api/v1/viber/status: Client operational health and mode status
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from backend.config import settings
from backend.database.sqlite_store import SQLiteStore
from backend.routes.telemetry import get_database_store
from bot.command_handlers import format_greek_bot_response
from bot.viber_client import IViberClient, LiveViberClient, MockViberClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/viber", tags=["viber"])

# Fallback in-memory client when running without a configured token
_default_mock_client = MockViberClient()


def get_viber_client(request: Request) -> IViberClient:
    """Dependency injector for IViberClient."""
    # Check if a client is registered on the app state (e.g. for testing)
    if hasattr(request.app.state, "viber_client") and request.app.state.viber_client is not None:
        return request.app.state.viber_client

    token = getattr(settings, "VIBER_BOT_TOKEN", None)
    if token:
        return LiveViberClient(auth_token=token)
    return _default_mock_client


def _strip_html(text: str) -> str:
    """Strip HTML formatting tags for clean plain text Viber display."""
    return re.sub(r"<[^>]+>", "", text)


# --- Request & Response Schemas ---

class ViberSendPayload(BaseModel):
    receiver_id: str = Field(..., description="Viber unique member ID")
    text: str = Field(..., description="Message text body")
    sender_name: str = Field(default="EMS Alert Bot", description="Sender display name")


class ViberSendResponse(BaseModel):
    status: str
    sent: bool
    receiver_id: str


class ViberStatusResponse(BaseModel):
    status: str
    mode: str
    has_token: bool
    webhook_url: str


# --- Endpoints ---

@router.get(
    "/status",
    response_model=ViberStatusResponse,
    summary="Get Viber bot subsystem operational status",
)
def get_viber_status(
    viber_client: Annotated[IViberClient, Depends(get_viber_client)],
) -> ViberStatusResponse:
    """Returns Viber client mode (mock or live) and configuration status."""
    is_live = isinstance(viber_client, LiveViberClient)
    token = getattr(settings, "VIBER_BOT_TOKEN", "")
    has_token = bool(token and token.strip())
    webhook_url = getattr(settings, "VIBER_WEBHOOK_URL", "")

    return ViberStatusResponse(
        status="active",
        mode="live" if is_live else "mock",
        has_token=has_token,
        webhook_url=webhook_url,
    )


@router.post(
    "/send",
    response_model=ViberSendResponse,
    summary="Send a Viber notification to a specific receiver",
)
async def send_viber_notification(
    payload: ViberSendPayload,
    viber_client: Annotated[IViberClient, Depends(get_viber_client)],
) -> ViberSendResponse:
    """Directly send a plain text notification via the configured Viber client."""
    sent = await viber_client.send_message(
        receiver_id=payload.receiver_id,
        text=payload.text,
        sender_name=payload.sender_name,
    )
    return ViberSendResponse(
        status="success" if sent else "failed",
        sent=sent,
        receiver_id=payload.receiver_id,
    )


@router.post(
    "/webhook",
    summary="Viber Bot Webhook callback endpoint",
)
async def viber_webhook(
    request: Request,
    store: Annotated[SQLiteStore, Depends(get_database_store)],
    viber_client: Annotated[IViberClient, Depends(get_viber_client)],
    x_viber_content_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """Handle incoming Viber callbacks (handshakes, message commands, delivery status).

    Supported commands from Greek commercial users:
    - /status: 3-phase live metrics, running €/h, current tariff zone
    - /cost_today: Daily kWh, total spend (€), peak surcharges (€)
    - /tariff: Greek contract details (Γ21/Γ22/Γ23), tariff color, schedules
    - /settings: Alert thresholds, hysteresis factor, cooldown
    - /start /help: Overview and command listing
    """
    raw_body = await request.body()

    # Optional signature verification
    token = getattr(settings, "VIBER_BOT_TOKEN", "")
    if token and x_viber_content_signature:
        expected_sig = hmac.new(token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, x_viber_content_signature):
            logger.warning("Viber webhook signature verification failed.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid Viber signature",
            )

    try:
        data = await request.json()
    except Exception:
        return {"status": 0, "message": "ok"}

    event_type = data.get("event")

    # 1. Viber webhook verification ping
    if event_type == "webhook":
        logger.info("Received Viber webhook setup verification ping.")
        return {"status": 0, "message": "ok"}

    # 2. Inbound user message
    if event_type == "message":
        sender_info = data.get("sender", {})
        sender_id = str(sender_info.get("id") or data.get("sender_id") or "")
        msg_obj = data.get("message", {})
        text = str(msg_obj.get("text", "")).strip()

        if not text or not sender_id:
            return {"status": 0, "message": "ok"}

        # Resolve facility matching this Viber user ID, or default to first configured facility
        facilities = store.list_facility_configs()
        target_facility: dict[str, Any] | None = None
        for fac in facilities:
            if str(fac.get("viber_receiver_id", "")) == sender_id:
                target_facility = fac
                break
        if not target_facility and facilities:
            target_facility = facilities[0]
        elif not target_facility:
            target_facility = {
                "facility_id": "bakery-central-athens",
                "name": "Κεντρικός Φούρνος Αθήνας",
                "contract_type": "Γ22",
                "tariff_color": "green",
                "contracted_kva": 35.0,
                "peak_threshold_kw": 22.0,
            }

        fac_id = target_facility.get("facility_id", "bakery-central-athens")

        # Fetch latest telemetry and daily cost summary
        latest_telemetry = store.get_latest_telemetry(fac_id)
        daily_summary = store.get_daily_summary(fac_id)

        # Generate response using Greek bot response generator
        raw_response = format_greek_bot_response(
            command=text,
            facility=target_facility,
            latest_payload=latest_telemetry,
            daily_spend_eur=daily_summary.get("total_spend_eur", 0.0),
            daily_energy_kwh=daily_summary.get("total_kwh", 0.0),
            projected_excess_penalty_eur=daily_summary.get("projected_excess_penalty_eur"),
            penalty_projection_timestamp=daily_summary.get("penalty_projection_timestamp"),
        )

        clean_response = _strip_html(raw_response)

        # Send reply back via Viber client
        await viber_client.send_message(
            receiver_id=sender_id,
            text=clean_response,
        )

        return {
            "status": 0,
            "message": "ok",
            "reply": clean_response,
            "receiver_id": sender_id,
        }

    # 3. Subscriptions, deliveries, read receipts, etc.
    return {"status": 0, "message": "ok"}
