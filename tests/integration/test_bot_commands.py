"""Integration tests for Telegram bot Greek command handlers and formatting.

Verifies:
- format_greek_bot_response for /start, /status, /cost_today, /tariff, /settings, /help, and fallback.
- Dynamic telemetry formatting with 3-phase voltages, currents, power, and cos phi.
- Zone resolution across summer/winter peak and off-peak periods.
- Tariff color translation (Green, Yellow, Dynamic) and contract formatting (Γ21, Γ22, Γ23).
- BotCommandHandler lifecycle: update_telemetry, update_cost, handle_command.
- Async dispatch via MockTelegramClient recording sent messages.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.models.telemetry import TelemetryPayload
from bot.command_handlers import (
    BotCommandHandler,
    _format_contract_code,
    _format_tariff_color,
    format_greek_bot_response,
)
from bot.telegram_client import MockTelegramClient


@pytest.fixture
def bakery_facility_config() -> dict[str, Any]:
    return {
        "facility_id": "bakery-central-athens",
        "facility_name": "Κεντρικός Φούρνος Αθήνας",
        "facility_type": "bakery",
        "contract_type": "Γ22",
        "tariff_color": "green",
        "contracted_kva": 35.0,
        "peak_threshold_kw": 22.0,
        "cooldown_seconds": 1800,
        "hysteresis_factor": 0.90,
        "debounce_samples": 3,
        "chat_id": 999111222,
    }


@pytest.fixture
def cold_storage_facility_config() -> dict[str, Any]:
    return {
        "facility_id": "cold-storage-piraeus",
        "facility_name": "Ψυγεία Πειραιά Logistics",
        "facility_type": "cold_storage",
        "contract_type": "Γ22",
        "tariff_color": "yellow",
        "contracted_kva": 50.0,
        "peak_threshold_kw": 25.0,
        "cooldown_seconds": 1800,
        "hysteresis_factor": 0.90,
        "debounce_samples": 3,
        "chat_id": 999333444,
    }


@pytest.fixture
def hotel_facility_config() -> dict[str, Any]:
    return {
        "facility_id": "hotel-plaka-boutique",
        "facility_name": "Boutique Hotel Πλάκα",
        "facility_type": "boutique_hotel",
        "contract_type": "Γ23",
        "tariff_color": "dynamic",
        "contracted_kva": 100.0,
        "peak_threshold_kw": 30.0,
        "cooldown_seconds": 1800,
        "hysteresis_factor": 0.90,
        "debounce_samples": 3,
        "chat_id": 999555666,
    }


@pytest.fixture
def sample_telemetry(valid_telemetry_payload: TelemetryPayload) -> TelemetryPayload:
    return valid_telemetry_payload


class TestFormatGreekBotResponse:
    """Test format_greek_bot_response across all command types and scenarios."""

    def test_start_command(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/start", bakery_facility_config)
        assert "🇬🇷 Καλώς ήρθατε στο Commercial EMS Bot" in res
        assert "Κεντρικός Φούρνος Αθήνας" in res
        assert "Διαθέσιμες Εντολές:" in res
        assert "/status" in res
        assert "/cost_today" in res
        assert "/tariff" in res
        assert "/settings" in res
        assert "/help" in res

    def test_status_command_with_telemetry(
        self, bakery_facility_config: dict[str, Any], sample_telemetry: TelemetryPayload
    ):
        res = format_greek_bot_response(
            "/status", bakery_facility_config, latest_payload=sample_telemetry
        )
        assert "📊 Τρέχουσα Κατάσταση — Κεντρικός Φούρνος Αθήνας" in res
        assert "⚡ Συνολική Ισχύς: <b>17.90 kW</b>" in res
        assert "📈 Φαινόμενη Ισχύς: <b>18.27 kVA</b>" in res
        assert "🎯 Συντελεστής Ισχύος (cos φ): <b>0.98</b>" in res
        assert "💶 Τρέχον Κόστος:" in res
        assert "• L1: 230.2V" in res
        assert "• L2: 229.8V" in res
        assert "• L3: 231.0V" in res

    def test_status_command_without_telemetry(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/status", bakery_facility_config, latest_payload=None)
        assert "Δεν υπάρχουν διαθέσιμα δεδομένα τηλεμετρίας" in res

    def test_status_command_zone_peak(
        self, bakery_facility_config: dict[str, Any], sample_telemetry: TelemetryPayload
    ):
        # Wednesday 15:00 UTC (Summer peak)
        sample_telemetry.timestamp = datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc)
        res = format_greek_bot_response("/status", bakery_facility_config, latest_payload=sample_telemetry)
        assert "Ζώνη Αιχμής" in res

    def test_status_command_zone_offpeak(
        self, bakery_facility_config: dict[str, Any], sample_telemetry: TelemetryPayload
    ):
        # 02:00 UTC (Night off-peak)
        sample_telemetry.timestamp = datetime(2026, 7, 15, 2, 0, 0, tzinfo=timezone.utc)
        res = format_greek_bot_response("/status", bakery_facility_config, latest_payload=sample_telemetry)
        assert "Ζώνη Μειωμένης Χρέωσης (Νυχτερινό)" in res

    def test_cost_today_command_without_surcharges(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response(
            "/cost_today",
            bakery_facility_config,
            daily_spend_eur=48.60,
            daily_energy_kwh=240.5,
            peak_surcharge_eur=0.0,
        )
        assert "💰 Σημερινή Κατανάλωση & Κόστος" in res
        assert "240.5 kWh" in res
        assert "48.60 €" in res
        assert "δεν είναι διαθέσιμη" in res
        assert "εντός ορίων" not in res
        assert "άγνωστες" in res
        assert "Μέση Τιμή:" in res

    def test_cost_today_command_with_peak_surcharges(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response(
            "/cost_today",
            bakery_facility_config,
            daily_spend_eur=72.40,
            daily_energy_kwh=290.0,
            peak_surcharge_eur=18.50,
        )
        assert "18.50 €" in res
        assert "72.40 €" in res
        assert "290.0 kWh" in res

    def test_tariff_command_green_g22(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/tariff", bakery_facility_config)
        assert "📋 Στοιχεία Τιμολογίου" in res
        assert "Γ22" in res
        assert "Πράσινο" in res
        assert "35 kVA" in res
        assert "22.0 kW" in res
        assert "14:00 - 17:00" in res

    def test_tariff_command_yellow_cold_storage(self, cold_storage_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/tariff", cold_storage_facility_config)
        assert "Κίτρινο" in res
        assert "50 kVA" in res
        assert "25.0 kW" in res

    def test_tariff_command_dynamic_hotel(self, hotel_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/tariff", hotel_facility_config)
        assert "Γ23" in res
        assert "Πορτοκαλί" in res or "Δυναμικό" in res
        assert "100 kVA" in res
        assert "30.0 kW" in res

    def test_settings_command(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/settings", bakery_facility_config)
        assert "⚙️ Ρυθμίσεις Συστήματος" in res
        assert "22.0 kW" in res
        assert "30 λεπτά" in res
        assert "90%" in res
        assert "19.8 kW" in res
        assert "35 kVA" in res
        assert "999111222" in res

    def test_help_command(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/help", bakery_facility_config)
        assert "📖 Οδηγός Εντολών" in res
        assert "/status" in res
        assert "/cost_today" in res
        assert "/tariff" in res
        assert "/settings" in res
        assert "/start" in res

    def test_fallback_unrecognized_command(self, bakery_facility_config: dict[str, Any]):
        res = format_greek_bot_response("/foobar", bakery_facility_config)
        assert "Άγνωστη εντολή: /foobar" in res
        assert "/status" in res


class TestContractAndColorFormatting:
    """Test helper functions for contract code and tariff color formatting."""

    def test_format_contract_code(self):
        assert "Γ21" in _format_contract_code("G21")
        assert "Γ22" in _format_contract_code("Γ22")
        assert "Γ23" in _format_contract_code("G23")

    def test_format_tariff_color(self):
        assert "Πράσινο" in _format_tariff_color("green")
        assert "Κίτρινο" in _format_tariff_color("yellow")
        assert "Πορτοκαλί" in _format_tariff_color("dynamic")
        assert "Πορτοκαλί" in _format_tariff_color("orange")


class TestBotCommandHandlerLifecycle:
    """Test BotCommandHandler state updates, synchronous handle_command, and async dispatch."""

    def test_handle_command_synchronous(
        self, bakery_facility_config: dict[str, Any], sample_telemetry: TelemetryPayload
    ):
        handler = BotCommandHandler(
            facility_config=bakery_facility_config,
            latest_payload=sample_telemetry,
            daily_spend_eur=45.00,
            daily_energy_kwh=220.0,
        )
        # Check /status
        status_res = handler.handle_command("/status")
        assert "17.90 kW" in status_res

        # Check /cost_today
        cost_res = handler.handle_command("/cost_today")
        assert "45.00 €" in cost_res
        assert "220.0 kWh" in cost_res

    def test_update_telemetry_and_cost(
        self, bakery_facility_config: dict[str, Any], sample_telemetry: TelemetryPayload
    ):
        handler = BotCommandHandler(facility_config=bakery_facility_config)

        # Before update
        assert "Δεν υπάρχουν διαθέσιμα δεδομένα" in handler.handle_command("/status")

        # Update telemetry
        handler.update_telemetry(sample_telemetry)
        assert "17.90 kW" in handler.handle_command("/status")

        # Update cost
        handler.update_cost(daily_spend_eur=88.50, daily_energy_kwh=410.0, peak_surcharge_eur=12.0)
        cost_res = handler.handle_command("/cost_today")
        assert "88.50 €" in cost_res
        assert "410.0 kWh" in cost_res
        assert "12.00 €" in cost_res

    def test_async_dispatch_command_response(
        self, bakery_facility_config: dict[str, Any], sample_telemetry: TelemetryPayload
    ):
        async def _run():
            mock_client = MockTelegramClient()
            handler = BotCommandHandler(
                facility_config=bakery_facility_config,
                telegram_client=mock_client,
                latest_payload=sample_telemetry,
            )

            # Dispatch /status to chat 999111222
            resp = await handler.dispatch_command_response(
                chat_id=999111222,
                command_text="/status",
            )
            assert "17.90 kW" in resp
            assert mock_client.message_count() == 1
            sent = mock_client.get_last_message()
            assert sent["chat_id"] == 999111222
            assert "17.90 kW" in sent["text"]

            # Dispatch /tariff
            await handler.dispatch_command_response(
                chat_id=999111222,
                command_text="/tariff",
            )
            assert mock_client.message_count() == 2
            sent_tariff = mock_client.get_last_message()
            assert "Γ22" in sent_tariff["text"]
            assert "Πράσινο" in sent_tariff["text"]

        asyncio.run(_run())
