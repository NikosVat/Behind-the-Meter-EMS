"""Adversarial stress-test suite for live Greek energy market & tariff ingestion.

Test scenarios:
1. Network timeouts (ConnectTimeout, ReadTimeout) and connection drops
2. Corrupt/malformed JSON & CSV payloads from market sources
3. NaN and extreme price spikes / negative prices (-500 to +3000 €/MWh boundary checks)
4. Greek Daylight Saving Time transitions (23-hour spring, 25-hour autumn)
5. 4-tier caching & fallback integrity under adverse conditions
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from backend.database.sqlite_store import SQLiteStore
from backend.market.clients import IMarketClient, MockMarketClient
from backend.market.fetcher_henex import (
    is_dst_transition_day,
    parse_henex_csv,
    parse_henex_json,
    validate_dam_prices,
)
from backend.market.models import DamHourlyPrice, GreenTariffAnnouncement
from backend.market.scraper_rae import (
    normalize_supplier,
    parse_float_gr,
    parse_rae_html,
    parse_rae_json,
)
from backend.market.service import MarketPriceService
from tariff_engine.cost_calculator import calculate_realtime_cost


# --- Helper Mock Clients for Adversarial Testing ---

class TimeoutMarketClient(IMarketClient):
    """Client simulating network timeouts (ConnectTimeout and ReadTimeout)."""

    def __init__(self, timeout_type: str = "connect") -> None:
        self.timeout_type = timeout_type

    async def fetch_dam_prices(self, target_date: str) -> list[DamHourlyPrice]:
        if self.timeout_type == "connect":
            raise httpx.ConnectTimeout(f"Connection timeout to HEnEx endpoint for {target_date}")
        raise httpx.ReadTimeout(f"Read timeout while downloading DAM curve for {target_date}")

    async def fetch_green_tariffs(self, month: str) -> list[GreenTariffAnnouncement]:
        if self.timeout_type == "connect":
            raise httpx.ConnectTimeout(f"Connection timeout to RAE endpoint for {month}")
        raise httpx.ReadTimeout(f"Read timeout while scraping RAE tariffs for {month}")


class DropMarketClient(IMarketClient):
    """Client simulating socket drops and protocol errors."""

    async def fetch_dam_prices(self, target_date: str) -> list[DamHourlyPrice]:
        raise httpx.RemoteProtocolError(f"Server disconnected during transfer for {target_date}")

    async def fetch_green_tariffs(self, month: str) -> list[GreenTariffAnnouncement]:
        raise httpx.ConnectError(f"Connection refused by remote host for {month}")


class CorruptPayloadMarketClient(IMarketClient):
    """Client returning malformed, corrupt, or unexpected payloads."""

    def __init__(self, payload_type: str) -> None:
        self.payload_type = payload_type

    async def fetch_dam_prices(self, target_date: str) -> list[DamHourlyPrice]:
        if self.payload_type == "null_prices":
            return parse_henex_json({"prices": None}, target_date)
        elif self.payload_type == "null_item_in_list":
            return parse_henex_json([None], target_date)  # type: ignore
        elif self.payload_type == "empty_csv":
            return parse_henex_csv("", target_date)
        elif self.payload_type == "nan_prices":
            return [
                DamHourlyPrice(date=target_date, hour=h, price_eur_mwh=float("nan"), price_eur_kwh=0.0)
                for h in range(24)
            ]
        elif self.payload_type == "excess_spike":
            return [
                DamHourlyPrice(date=target_date, hour=h, price_eur_mwh=9999.0, price_eur_kwh=9.999)
                for h in range(24)
            ]
        return []

    async def fetch_green_tariffs(self, month: str) -> list[GreenTariffAnnouncement]:
        if self.payload_type == "null_tariffs":
            return parse_rae_json({"tariffs": None}, month)
        elif self.payload_type == "null_supplier_name":
            return parse_rae_json([{"supplier_name": None, "p_base": 0.15}], month)
        return []


# --- Dimension 1: Network Timeouts & Offline Drops Stress Tests ---

class TestNetworkTimeoutsAndDrops:
    """Stress-test resilience against timeouts and network drops."""

    @pytest.mark.anyio
    async def test_connect_timeout_graceful_fallback(self, tmp_path):
        """Verify that ConnectTimeout triggers clean fallback to Tier 3 seed."""
        store = SQLiteStore(tmp_path / "timeout1.db")
        store.init_db()
        client = TimeoutMarketClient(timeout_type="connect")
        service = MarketPriceService(store=store, client=client)

        # DAM curve must not crash; must return L3 fallback seed
        summary = await service.get_dam_curve("2026-09-15")
        assert len(summary.prices) == 24
        assert service.dam_source_mode == "fallback_seed"

        # Green tariffs must also fall back to L3
        green = await service.get_green_tariffs("2026-09")
        assert len(green) >= 2
        assert service.green_source_mode == "fallback_seed"

    @pytest.mark.anyio
    async def test_read_timeout_graceful_fallback(self, tmp_path):
        """Verify that ReadTimeout triggers clean fallback to Tier 3 seed."""
        store = SQLiteStore(tmp_path / "timeout2.db")
        store.init_db()
        client = TimeoutMarketClient(timeout_type="read")
        service = MarketPriceService(store=store, client=client)

        summary = await service.get_dam_curve("2026-09-15")
        assert len(summary.prices) == 24
        assert service.dam_source_mode == "fallback_seed"

    @pytest.mark.anyio
    async def test_remote_disconnect_protocol_error_graceful_fallback(self, tmp_path):
        """Verify that RemoteProtocolError and ConnectError do not crash the service."""
        store = SQLiteStore(tmp_path / "drop.db")
        store.init_db()
        client = DropMarketClient()
        service = MarketPriceService(store=store, client=client)

        summary = await service.get_dam_curve("2026-09-15")
        assert len(summary.prices) == 24
        assert service.dam_source_mode == "fallback_seed"

        green = await service.get_green_tariffs("2026-09")
        assert len(green) >= 2
        assert service.green_source_mode == "fallback_seed"


# --- Dimension 2: Extreme Price Spikes, Negative Prices & Boundary Checks ---

class TestExtremePricesAndSanityBounds:
    """Stress-test zero, negative, and extreme market clearing prices."""

    def test_zero_dam_prices_validity_and_summary(self):
        """Zero wholesale price must be fully valid (solar midday surplus)."""
        prices = [
            DamHourlyPrice(date="2026-06-21", hour=h, price_eur_mwh=0.0, price_eur_kwh=0.0)
            for h in range(24)
        ]
        assert validate_dam_prices(prices, "2026-06-21") is True

    def test_eu_legal_floor_negative_500_accepted(self):
        """Price of exactly -500.0 €/MWh is the EU market clearing floor and must be accepted."""
        prices = [
            DamHourlyPrice(date="2026-05-01", hour=h, price_eur_mwh=-500.0, price_eur_kwh=-0.5)
            for h in range(24)
        ]
        assert validate_dam_prices(prices, "2026-05-01") is True

    def test_below_eu_legal_floor_rejected(self):
        """Price of -500.01 €/MWh breaches legal floor and must be rejected."""
        prices = [
            DamHourlyPrice(date="2026-05-01", hour=h, price_eur_mwh=-100.0, price_eur_kwh=-0.1)
            for h in range(24)
        ]
        prices[12] = DamHourlyPrice(date="2026-05-01", hour=12, price_eur_mwh=-500.01, price_eur_kwh=-0.50001)
        assert validate_dam_prices(prices, "2026-05-01") is False

    def test_eu_legal_cap_3000_accepted(self):
        """Price of exactly 3000.0 €/MWh is the EU market clearing cap and must be accepted."""
        prices = [
            DamHourlyPrice(date="2026-08-10", hour=h, price_eur_mwh=3000.0, price_eur_kwh=3.0)
            for h in range(24)
        ]
        assert validate_dam_prices(prices, "2026-08-10") is True

    def test_above_eu_legal_cap_rejected(self):
        """Price of 3000.01 €/MWh breaches legal cap and must be rejected."""
        prices = [
            DamHourlyPrice(date="2026-08-10", hour=h, price_eur_mwh=150.0, price_eur_kwh=0.15)
            for h in range(24)
        ]
        prices[19] = DamHourlyPrice(date="2026-08-10", hour=19, price_eur_mwh=3000.01, price_eur_kwh=3.00001)
        assert validate_dam_prices(prices, "2026-08-10") is False

    def test_extreme_price_spike_1000_eur_mwh_tariff_impact(self, tmp_path):
        """Verify dynamic running cost accurately reflects extreme 1000 €/MWh spot spike."""
        store = SQLiteStore(tmp_path / "spike.db")
        store.init_db()
        service = MarketPriceService(store=store)

        # 19:00 Greek civil time (hour 19 in DAM table) is 16:00 UTC in summer (EEST = UTC+3)
        dt = datetime(2026, 7, 20, 16, 0, tzinfo=timezone.utc)
        target_date = "2026-07-20"
        spike_prices = [
            DamHourlyPrice(
                date=target_date,
                hour=h,
                price_eur_mwh=1000.0 if h == 19 else 120.0,
                price_eur_kwh=1.0 if h == 19 else 0.12,
                source="henex_live",
            )
            for h in range(24)
        ]
        store.store_dam_hourly_prices(spike_prices)

        # Resolve TEA
        tea = service.get_effective_tea(dt, tariff_color="yellow", contract_type="G22")
        assert tea == 1000.0

        # Feed into cost calculator for 20 kW commercial load
        from types import SimpleNamespace
        facility = SimpleNamespace(contract_type="Γ22", tariff_color="yellow", contracted_kva=35.0, peak_threshold_kw=22.0)
        cost_normal = calculate_realtime_cost(20.0, 5.0, dt, facility, tea_eur_mwh=120.0)
        cost_spike = calculate_realtime_cost(20.0, 5.0, dt, facility, tea_eur_mwh=tea)

        # 1000 €/MWh spot price results in > 1.0 €/kWh retail rate vs ~0.20 normal
        assert cost_spike.current_rate_eur_per_kwh > 1.0
        assert cost_spike.running_cost_eur_per_h > (cost_normal.running_cost_eur_per_h + 20.0)


# --- Dimension 3: Greek Daylight Saving Time Transitions (23h & 25h) ---

class TestDaylightSavingTimeTransitions:
    """Stress-test Greek DST transitions: Spring 23-hour and Autumn 25-hour days."""

    def test_spring_dst_transition_detection(self):
        """Last Sunday of March 2026 (2026-03-29) must have 23 expected hours."""
        is_dst, expected = is_dst_transition_day("2026-03-29")
        assert is_dst is True
        assert expected == 23

    def test_autumn_dst_transition_detection(self):
        """Last Sunday of October 2026 (2026-10-25) must have 25 expected hours."""
        is_dst, expected = is_dst_transition_day("2026-10-25")
        assert is_dst is True
        assert expected == 25

    def test_normal_days_not_dst(self):
        """Normal weekdays and non-transition Sundays must have 24 hours."""
        assert is_dst_transition_day("2026-03-28") == (False, 24)
        assert is_dst_transition_day("2026-03-22") == (False, 24)  # Earlier Sunday in March
        assert is_dst_transition_day("2026-10-18") == (False, 24)  # Earlier Sunday in October
        assert is_dst_transition_day("2026-11-01") == (False, 24)

    def test_spring_23h_prices_validation(self):
        """Validate 23-hour series passes on spring DST date and rejects 24 hours."""
        p23 = [DamHourlyPrice(date="2026-03-29", hour=h, price_eur_mwh=100.0, price_eur_kwh=0.1) for h in range(23)]
        assert validate_dam_prices(p23, "2026-03-29") is True

        p24 = [DamHourlyPrice(date="2026-03-29", hour=h, price_eur_mwh=100.0, price_eur_kwh=0.1) for h in range(24)]
        assert validate_dam_prices(p24, "2026-03-29") is False

    def test_autumn_25h_prices_validation(self):
        """Validate 25-hour series passes on autumn DST date and rejects 24 hours."""
        p25 = [DamHourlyPrice(date="2026-10-25", hour=h, price_eur_mwh=100.0, price_eur_kwh=0.1) for h in range(25)]
        assert validate_dam_prices(p25, "2026-10-25") is True

        p24 = [DamHourlyPrice(date="2026-10-25", hour=h, price_eur_mwh=100.0, price_eur_kwh=0.1) for h in range(24)]
        assert validate_dam_prices(p24, "2026-10-25") is False

    @pytest.mark.anyio
    async def test_spring_dst_fallback_seed_generates_23_hours(self, tmp_path):
        """When offline on spring DST, L3 fallback seed must produce exactly 23 hours."""
        store = SQLiteStore(tmp_path / "dst_spring.db")
        store.init_db()
        client = MockMarketClient(should_fail_dam=True)
        service = MarketPriceService(store=store, client=client)

        summary = await service.get_dam_curve("2026-03-29")
        assert summary.count_hours == 23
        assert len(summary.prices) == 23
        assert service.dam_source_mode == "fallback_seed"

    @pytest.mark.anyio
    async def test_autumn_dst_fallback_seed_generates_25_hours(self, tmp_path):
        """When offline on autumn DST, L3 fallback seed must produce exactly 25 hours."""
        store = SQLiteStore(tmp_path / "dst_autumn.db")
        store.init_db()
        client = MockMarketClient(should_fail_dam=True)
        service = MarketPriceService(store=store, client=client)

        summary = await service.get_dam_curve("2026-10-25")
        assert summary.count_hours == 25
        assert len(summary.prices) == 25
        assert service.dam_source_mode == "fallback_seed"


# --- Dimension 4: Corrupt Payloads & Defect Demonstration ---

class TestCorruptPayloadsAndParserRobustness:
    """Stress-test how market parsers and service handle corrupt data feeds."""

    def test_parse_float_gr_resilience(self):
        """Number parser must safely handle European, Greek, and corrupt representations."""
        assert parse_float_gr("0,155") == 0.155
        assert parse_float_gr("1.234,56") == 1234.56
        assert parse_float_gr("invalid", default=-1.0) == -1.0
        assert parse_float_gr(None, default=0.0) == 0.0

    def test_parse_rae_html_with_empty_or_no_table(self):
        """HTML parser should return empty list when no table exists, without throwing."""
        assert parse_rae_html("<html><body>No tables here</body></html>", "2026-09") == []
        assert parse_rae_html("", "2026-09") == []

    def test_empty_csv_raises_index_error_in_parse_henex_csv(self):
        """Demonstrate that parse_henex_csv('') crashes with IndexError instead of returning []."""
        with pytest.raises(IndexError):
            parse_henex_csv("", "2026-09-15")

    def test_null_prices_raises_type_error_in_parse_henex_json(self):
        """Demonstrate that {'prices': None} crashes parse_henex_json with TypeError."""
        with pytest.raises(TypeError):
            parse_henex_json({"prices": None}, "2026-09-15")

    def test_null_item_in_list_raises_attribute_error_in_parse_henex_json(self):
        """Demonstrate that [None] crashes parse_henex_json with AttributeError."""
        with pytest.raises(AttributeError):
            parse_henex_json([None], "2026-09-15")  # type: ignore

    def test_null_tariffs_raises_type_error_in_parse_rae_json(self):
        """Demonstrate that {'tariffs': None} crashes parse_rae_json with TypeError."""
        with pytest.raises(TypeError):
            parse_rae_json({"tariffs": None}, "2026-09")

    def test_null_supplier_name_raises_attribute_error_in_parse_rae_json(self):
        """Demonstrate that {'supplier_name': None} crashes normalize_supplier with AttributeError."""
        with pytest.raises(AttributeError):
            parse_rae_json([{"supplier_name": None, "p_base": 0.15}], "2026-09")

    def test_nan_dam_prices_bypass_validation(self):
        """Demonstrate that NaN clearing prices pass validate_dam_prices because comparisons return False."""
        nan_prices = [
            DamHourlyPrice(date="2026-09-15", hour=h, price_eur_mwh=float("nan"), price_eur_kwh=0.0)
            for h in range(24)
        ]
        # In IEEE-754: nan < -500 is False, nan > 3000 is False.
        # Thus validate_dam_prices mistakenly returns True!
        assert validate_dam_prices(nan_prices, "2026-09-15") is True

    @pytest.mark.anyio
    async def test_corrupt_json_payload_escapes_service_fallback(self, tmp_path):
        """Demonstrate that when a live client returns {'prices': None},

        get_dam_curve crashes with TypeError instead of falling back to L3 seed,
        because service.py line 336 only catches (httpx.HTTPError, OSError, RuntimeError, ValueError).
        """
        store = SQLiteStore(tmp_path / "corrupt1.db")
        store.init_db()
        client = CorruptPayloadMarketClient("null_prices")
        service = MarketPriceService(store=store, client=client)

        with pytest.raises(TypeError):
            await service.get_dam_curve("2026-09-15")

    @pytest.mark.anyio
    async def test_nan_prices_cause_sqlite_integrity_error_in_service(self, tmp_path):
        """Demonstrate that when a live client returns NaN prices,

        service.get_dam_curve crashes with sqlite3.IntegrityError because SQLite column is NOT NULL,
        and service.py line 336 does not catch sqlite3.Error.
        """
        import sqlite3
        store = SQLiteStore(tmp_path / "corrupt_nan.db")
        store.init_db()
        client = CorruptPayloadMarketClient("nan_prices")
        service = MarketPriceService(store=store, client=client)

        with pytest.raises(sqlite3.IntegrityError):
            await service.get_dam_curve("2026-09-15")
