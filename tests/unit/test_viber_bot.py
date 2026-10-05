"""Unit tests for Viber bot client adapters and unified multi-channel alert dispatching.

Verifies:
- MockViberClient queue recording, retrieval, filtering, and clear methods.
- LiveViberClient request construction, error handling, and parameter validation.
- Unified AlertDispatcher multi-channel alerting:
  * Channel 'telegram': Dispatches only to Telegram client.
  * Channel 'viber': Dispatches only to Viber client with stripped HTML tags.
  * Channel 'both': Dispatches simultaneously to both Telegram and Viber clients.
  * Fallbacks when one or both clients are missing or unconfigured.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.alert import AlertEvent, AlertSeverity, AlertThresholdConfig, AlertType
from bot.dispatcher import AlertDispatcher
from bot.telegram_client import MockTelegramClient
from bot.viber_client import LiveViberClient, MockViberClient

# --- MockViberClient Tests ---

@pytest.mark.anyio
async def test_mock_viber_client_send_and_inspect():
    client = MockViberClient()
    assert len(client.get_sent_messages()) == 0
    assert client.get_last_message() is None

    success = await client.send_message(
        receiver_id="viber_user_123",
        text="⚠️ Προσοχή: Υπέρβαση ορίου 24.5 kW",
        sender_name="EMS Monitor",
    )
    assert success is True
    assert len(client.get_sent_messages()) == 1

    last = client.get_last_message()
    assert last is not None
    assert last["receiver_id"] == "viber_user_123"
    assert "24.5 kW" in last["text"]
    assert last["sender_name"] == "EMS Monitor"

    # Filtered retrieval
    await client.send_message(receiver_id="viber_user_456", text="Second alert")
    assert len(client.get_sent_messages()) == 2
    assert len(client.get_sent_messages("viber_user_123")) == 1
    assert len(client.get_sent_messages("viber_user_456")) == 1
    assert client.get_last_message("viber_user_123")["text"].startswith("⚠️")

    client.clear()
    assert len(client.get_sent_messages()) == 0


# --- LiveViberClient Tests ---

@pytest.mark.anyio
async def test_live_viber_client_missing_params():
    client_no_token = LiveViberClient(auth_token="")
    sent = await client_no_token.send_message(receiver_id="user1", text="Hello")
    assert sent is False

    client_with_token = LiveViberClient(auth_token="dummy_token")
    sent = await client_with_token.send_message(receiver_id="", text="Hello")
    assert sent is False


@pytest.mark.anyio
async def test_live_viber_client_successful_send():
    client = LiveViberClient(
        auth_token="test_viber_token_123",
        sender_avatar="https://example.com/avatar.png",
    )

    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={"status": 0, "status_message": "ok", "message_token": 12345})

    with patch("httpx.AsyncClient.post", return_value=fake_response) as mock_post:
        success = await client.send_message(
            receiver_id="usr_abc",
            text="Ειδοποίηση EMS",
            sender_name="Custom Sender",
        )
        assert success is True
        mock_post.assert_called_once()
        _args, kwargs = mock_post.call_args
        assert kwargs["headers"]["X-Viber-Auth-Token"] == "test_viber_token_123"
        payload = kwargs["json"]
        assert payload["receiver"] == "usr_abc"
        assert payload["text"] == "Ειδοποίηση EMS"
        assert payload["sender"]["name"] == "Custom Sender"
        assert payload["sender"]["avatar"] == "https://example.com/avatar.png"


@pytest.mark.anyio
async def test_live_viber_client_api_error_status():
    client = LiveViberClient(auth_token="test_token")
    fake_response = AsyncMock()
    fake_response.status_code = 200
    fake_response.json = MagicMock(return_value={"status": 5, "status_message": "receiverNotRegistered"})

    with patch("httpx.AsyncClient.post", return_value=fake_response):
        success = await client.send_message(receiver_id="unknown_usr", text="Hi")
        assert success is False


@pytest.mark.anyio
async def test_live_viber_client_http_500():
    client = LiveViberClient(auth_token="test_token")
    fake_response = AsyncMock()
    fake_response.status_code = 500
    fake_response.text = "Internal Server Error"

    with patch("httpx.AsyncClient.post", return_value=fake_response):
        success = await client.send_message(receiver_id="usr1", text="Hi")
        assert success is False


# --- Multi-Channel Alert Dispatcher Tests ---

@pytest.fixture
def mock_clients():
    tg_client = MockTelegramClient()
    vb_client = MockViberClient()
    return tg_client, vb_client


@pytest.mark.anyio
async def test_dispatch_channel_telegram_only(mock_clients):
    tg_client, vb_client = mock_clients
    dispatcher = AlertDispatcher(telegram_client=tg_client, viber_client=vb_client)

    cfg = AlertThresholdConfig(
        facility_id="bakery_athens",
        peak_threshold_kw=20.0,
        chat_id=111222,
        viber_receiver_id="viber_bakery_1",
        notification_channel="telegram",
    )
    dispatcher.register_facility(cfg)

    alert = AlertEvent(
        facility_id="bakery_athens",
        alert_type=AlertType.PEAK_BREACH,
        severity=AlertSeverity.WARNING,
        timestamp=datetime.now(timezone.utc),
        current_power_kw=25.0,
        threshold_kw=20.0,
        excess_kw=5.0,
        message="<b>Προσοχή:</b> Υπέρβαση ορίου 25.0 kW",
    )

    dispatched = await dispatcher.dispatch_alert(alert)
    assert dispatched is True

    # Telegram received HTML message
    tg_msgs = tg_client.get_sent_messages(111222)
    assert len(tg_msgs) == 1
    assert "<b>Προσοχή:</b>" in tg_msgs[0]["text"]

    # Viber received nothing
    assert len(vb_client.get_sent_messages()) == 0


@pytest.mark.anyio
async def test_dispatch_channel_viber_only(mock_clients):
    tg_client, vb_client = mock_clients
    dispatcher = AlertDispatcher(telegram_client=tg_client, viber_client=vb_client)

    cfg = AlertThresholdConfig(
        facility_id="cold_piraeus",
        peak_threshold_kw=30.0,
        chat_id=333444,
        viber_receiver_id="viber_cold_1",
        notification_channel="viber",
    )
    dispatcher.register_facility(cfg)

    alert = AlertEvent(
        facility_id="cold_piraeus",
        alert_type=AlertType.PEAK_BREACH,
        severity=AlertSeverity.CRITICAL,
        timestamp=datetime.now(timezone.utc),
        current_power_kw=35.0,
        threshold_kw=30.0,
        excess_kw=5.0,
        message="<b>ΚΡΙΣΙΜΟ:</b> Υπέρβαση <code>35.0 kW</code> σε ζώνη αιχμής!",
    )

    dispatched = await dispatcher.dispatch_alert(alert)
    assert dispatched is True

    # Telegram received nothing
    assert len(tg_client.get_sent_messages()) == 0

    # Viber received clean plain-text message without HTML tags
    vb_msgs = vb_client.get_sent_messages("viber_cold_1")
    assert len(vb_msgs) == 1
    assert "<b>" not in vb_msgs[0]["text"]
    assert "<code>" not in vb_msgs[0]["text"]
    assert "ΚΡΙΣΙΜΟ: Υπέρβαση 35.0 kW σε ζώνη αιχμής!" in vb_msgs[0]["text"]


@pytest.mark.anyio
async def test_dispatch_channel_both(mock_clients):
    tg_client, vb_client = mock_clients
    dispatcher = AlertDispatcher(telegram_client=tg_client, viber_client=vb_client)

    cfg = AlertThresholdConfig(
        facility_id="hotel_rhodes",
        peak_threshold_kw=40.0,
        chat_id=555666,
        viber_receiver_id="viber_hotel_1",
        notification_channel="both",
    )
    dispatcher.register_facility(cfg)

    alert = AlertEvent(
        facility_id="hotel_rhodes",
        alert_type=AlertType.PEAK_BREACH,
        severity=AlertSeverity.WARNING,
        timestamp=datetime.now(timezone.utc),
        current_power_kw=45.0,
        threshold_kw=40.0,
        excess_kw=5.0,
        message="<b>Προειδοποίηση:</b> Υπέρβαση 45.0 kW",
    )

    dispatched = await dispatcher.dispatch_alert(alert)
    assert dispatched is True

    # Both channels dispatched successfully
    assert len(tg_client.get_sent_messages(555666)) == 1
    assert len(vb_client.get_sent_messages("viber_hotel_1")) == 1


@pytest.mark.anyio
async def test_dispatch_without_clients():
    dispatcher = AlertDispatcher(telegram_client=None, viber_client=None)
    cfg = AlertThresholdConfig(
        facility_id="fac_no_client",
        peak_threshold_kw=20.0,
        chat_id=123,
        viber_receiver_id="vb123",
        notification_channel="both",
    )
    dispatcher.register_facility(cfg)

    alert = AlertEvent(
        facility_id="fac_no_client",
        alert_type=AlertType.PEAK_BREACH,
        severity=AlertSeverity.WARNING,
        timestamp=datetime.now(timezone.utc),
        current_power_kw=25.0,
        threshold_kw=20.0,
        excess_kw=5.0,
        message="Test alert",
    )

    # In-memory dispatch completes without raising exceptions
    sent = await dispatcher.dispatch_alert(alert)
    assert sent is True
    assert alert.dispatched is True
