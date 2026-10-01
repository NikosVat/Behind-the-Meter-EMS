"""MarketPriceService: 4-tier caching & offline fallback energy market engine.

Tier Hierarchy:
- L1: Fast in-memory cache (<0.1ms) for high-frequency telemetry lookups
- L2: SQLite persistence tables (market_dam_hourly_prices, market_green_tariffs)
- L3: Bundled JSON fallback seed with realistic Greek benchmark rates
- L4: Pure deterministic algorithmic fallbacks (TEA = 120.0 €/MWh)
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import httpx

from backend.database.sqlite_store import SQLiteStore, get_store
from backend.market.clients import IMarketClient, LiveMarketClient
from backend.market.fetcher_henex import is_dst_transition_day
from backend.market.models import (
    DamDaySummary,
    DamHourlyPrice,
    GreenTariffAnnouncement,
    MarketStatus,
)
from backend.market.scraper_rae import verify_green_tariff_formula
from tariff_engine.contracts import to_athens_time

logger = logging.getLogger(__name__)

DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "market_fallback_seed.json"


class MarketPriceService:
    """Orchestrates market price ingestion, 4-tier caching, and offline fallbacks."""

    def __init__(
        self,
        store: SQLiteStore | None = None,
        client: IMarketClient | None = None,
        seed_path: str | Path | None = None,
    ) -> None:
        self.store = store or get_store()
        self.client = client or LiveMarketClient()
        self.seed_path = Path(seed_path or DEFAULT_SEED_PATH)

        # Tier 1: In-memory cache
        self._dam_cache: dict[str, list[DamHourlyPrice]] = {}
        self._green_cache: dict[str, list[GreenTariffAnnouncement]] = {}
        self._lock = Lock()

        # Operational metrics
        self.dam_last_fetched: datetime | None = None
        self.green_last_fetched: datetime | None = None
        self.dam_source_mode: str = "live"
        self.green_source_mode: str = "live"

    def clear_l1_cache(self) -> None:
        """Clear L1 memory cache (useful for tier progression testing)."""
        with self._lock:
            self._dam_cache.clear()
            self._green_cache.clear()

    # -------------------------------------------------------------------------
    # Tier 3 (L3) & Tier 4 (L4) Fallback Helpers
    # -------------------------------------------------------------------------

    def _load_seed_json(self) -> dict[str, Any]:
        """Load bundled fallback seed JSON."""
        try:
            if self.seed_path.exists():
                with open(self.seed_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Could not read market seed JSON from %s: %s", self.seed_path, e)
        return {}

    def _get_l3_dam_prices(self, target_date: str) -> list[DamHourlyPrice]:
        """Generate DAM curve from L3 bundled seasonal seed profiles."""
        seed_data = self._load_seed_json()
        seasonal = seed_data.get("dam_seasonal_profiles", {})

        try:
            dt = date.fromisoformat(target_date)
            month = dt.month
        except ValueError:
            month = 9

        # Select season
        if month in (6, 7, 8, 9):
            profile = seasonal.get("summer", [])
        elif month in (12, 1, 2):
            profile = seasonal.get("winter", [])
        else:
            profile = seasonal.get("shoulder", [])

        if not profile:
            return []

        _is_dst, expected_hours = is_dst_transition_day(target_date)
        if expected_hours == 23 and len(profile) >= 24:
            curve = profile[:3] + profile[4:24]
        elif expected_hours == 25 and len(profile) >= 24:
            curve = profile[:3] + [profile[2]] + profile[3:24]
        else:
            curve = profile[:expected_hours]

        records: list[DamHourlyPrice] = []
        for h, price in enumerate(curve):
            records.append(
                DamHourlyPrice(
                    date=target_date,
                    hour=h,
                    price_eur_mwh=round(float(price), 2),
                    price_eur_kwh=round(float(price) / 1000.0, 5),
                    source="fallback_seed",
                )
            )
        return records

    def _get_l4_dam_prices(self, target_date: str) -> list[DamHourlyPrice]:
        """Generate deterministic synthetic DAM curve normalized to TEA = 120.0 €/MWh."""
        _is_dst, expected_hours = is_dst_transition_day(target_date)
        base = [
            92.0, 85.0, 80.0, 78.0, 82.0, 90.0,
            105.0, 125.0, 148.0, 145.0, 122.0, 110.0,
            102.0, 98.0, 104.0, 115.0, 128.0, 142.0,
            175.0, 190.0, 178.0, 161.0, 124.0, 101.0,
        ]
        if expected_hours == 23:
            curve = base[:3] + base[4:24]
        elif expected_hours == 25:
            curve = base[:3] + [base[2]] + base[3:24]
        else:
            curve = base

        records: list[DamHourlyPrice] = []
        for h, price in enumerate(curve):
            records.append(
                DamHourlyPrice(
                    date=target_date,
                    hour=h,
                    price_eur_mwh=round(float(price), 2),
                    price_eur_kwh=round(float(price) / 1000.0, 5),
                    source="fallback_synthetic",
                )
            )
        return records

    def _get_l3_green_tariffs(self, target_month: str) -> list[GreenTariffAnnouncement]:
        """Load Green Tariffs from L3 bundled seed."""
        seed_data = self._load_seed_json()
        all_tariffs = seed_data.get("green_tariffs", [])
        matched = [t for t in all_tariffs if t.get("month") == target_month]
        if not matched and all_tariffs:
            # Fall back to latest month available in seed
            latest_month = max(t.get("month", "") for t in all_tariffs)
            matched = [t for t in all_tariffs if t.get("month") == latest_month]

        results: list[GreenTariffAnnouncement] = []
        for item in matched:
            tariff = GreenTariffAnnouncement(
                month=target_month,
                supplier_id=item["supplier_id"],
                supplier_name=item["supplier_name"],
                contract_type=item["contract_type"],
                p_base=item["p_base"],
                e_disc=item.get("e_disc", 0.0),
                prompt_discount_percent=item.get("prompt_discount_percent", 0.0),
                alpha=item.get("alpha", 1.15),
                lu_eur_mwh=item.get("lu_eur_mwh", 115.0),
                ll_eur_mwh=item.get("ll_eur_mwh", 95.0),
                beta=item.get("beta", 0.0),
                fixed_monthly_fee_eur=item.get("fixed_monthly_fee_eur", 5.0),
                published_final_rate_eur_per_kwh=item["published_final_rate_eur_per_kwh"],
                tea_m1_eur_mwh=item.get("tea_m1_eur_mwh", 120.0),
                source="fallback_seed",
            )
            results.append(tariff)
        return results

    def _get_l4_green_tariffs(self, target_month: str) -> list[GreenTariffAnnouncement]:
        """Deterministic algorithmic defaults matching Law 5068/2023 with TEA = 120.0."""
        return [
            GreenTariffAnnouncement(
                month=target_month,
                supplier_id="dei",
                supplier_name="ΔΕΗ (Public Power Corporation)",
                contract_type="G21",
                p_base=0.155,
                e_disc=0.020,
                prompt_discount_percent=12.9,
                alpha=1.15,
                lu_eur_mwh=115.0,
                ll_eur_mwh=95.0,
                beta=0.0,
                fixed_monthly_fee_eur=5.0,
                published_final_rate_eur_per_kwh=0.14075,
                tea_m1_eur_mwh=120.0,
                source="fallback_synthetic",
            ),
            GreenTariffAnnouncement(
                month=target_month,
                supplier_id="dei",
                supplier_name="ΔΕΗ (Public Power Corporation)",
                contract_type="G22",
                p_base=0.165,
                e_disc=0.020,
                prompt_discount_percent=12.1,
                alpha=1.15,
                lu_eur_mwh=115.0,
                ll_eur_mwh=95.0,
                beta=0.0,
                fixed_monthly_fee_eur=5.0,
                published_final_rate_eur_per_kwh=0.15075,
                tea_m1_eur_mwh=120.0,
                source="fallback_synthetic",
            ),
        ]

    # -------------------------------------------------------------------------
    # Synchronous Hot-Path Rate Resolution (<0.1ms via L1)
    # -------------------------------------------------------------------------

    def get_effective_tea(
        self,
        timestamp: datetime,
        tariff_color: str = "green",
        contract_type: str = "G22",
        supplier_id: str = "dei",
    ) -> float:
        """Resolve instantaneous TEA rate (€/MWh) across 4 tiers.

        Synchronous method designed for zero-latency lookups during high-frequency
        telemetry ingestion.
        """
        color = tariff_color.strip().lower()
        norm_contract = contract_type.strip().upper().replace("Γ", "G")
        norm_supplier = supplier_id.strip().lower()
        local_dt = to_athens_time(timestamp)

        # Dynamic / Yellow Tariffs: indexed to hourly DAM clearing rate
        if color in ("yellow", "dynamic"):
            date_str = local_dt.strftime("%Y-%m-%d")
            hour = local_dt.hour

            # L1 Check
            with self._lock:
                if date_str in self._dam_cache:
                    for p in self._dam_cache[date_str]:
                        if p.hour == hour:
                            return p.price_eur_mwh

            # L2 Check
            row = self.store.get_dam_hourly_price(date_str, hour)
            if row:
                return float(row["price_eur_mwh"])

            # L3 Check
            l3_prices = self._get_l3_dam_prices(date_str)
            if l3_prices:
                with self._lock:
                    self._dam_cache[date_str] = l3_prices
                for p in l3_prices:
                    if p.hour == hour:
                        return p.price_eur_mwh

            # L4 Default
            l4_prices = self._get_l4_dam_prices(date_str)
            for p in l4_prices:
                if p.hour == hour:
                    return p.price_eur_mwh
            return 120.0

        # Green Tariffs: indexed to monthly TEA_{M-1}
        month_str = local_dt.strftime("%Y-%m")

        # L1 Check
        with self._lock:
            if month_str in self._green_cache:
                for t in self._green_cache[month_str]:
                    if t.supplier_id == norm_supplier and t.contract_type == norm_contract:
                        return t.tea_m1_eur_mwh if t.tea_m1_eur_mwh is not None else 120.0

        # L2 Check
        row_g = self.store.get_green_tariff(month_str, norm_supplier, norm_contract)
        if row_g and row_g.get("tea_m1_eur_mwh") is not None:
            return float(row_g["tea_m1_eur_mwh"])

        # L3 Check
        l3_tariffs = self._get_l3_green_tariffs(month_str)
        if l3_tariffs:
            with self._lock:
                self._green_cache[month_str] = l3_tariffs
            for t in l3_tariffs:
                if t.supplier_id == norm_supplier and t.contract_type == norm_contract:
                    return t.tea_m1_eur_mwh if t.tea_m1_eur_mwh is not None else 120.0

        # L4 Default
        return 120.0

    # -------------------------------------------------------------------------
    # Asynchronous REST & Scheduled Methods
    # -------------------------------------------------------------------------

    async def get_dam_curve(self, target_date: str) -> DamDaySummary:
        """Fetch 24-hour DAM price curve with complete 4-tier fallback progression."""
        # 1. L1 Memory
        with self._lock:
            if target_date in self._dam_cache:
                prices = self._dam_cache[target_date]
                return self._build_dam_summary(target_date, prices)

        # 2. L2 SQLite
        rows = self.store.get_dam_hourly_prices(target_date)
        _is_dst, expected_hours = is_dst_transition_day(target_date)
        if len(rows) == expected_hours:
            prices = [DamHourlyPrice(**r) for r in rows]
            with self._lock:
                self._dam_cache[target_date] = prices
            self.dam_source_mode = "cached"
            return self._build_dam_summary(target_date, prices)

        # 3. Live Client
        try:
            live_prices = await self.client.fetch_dam_prices(target_date)
            if live_prices and len(live_prices) == expected_hours:
                self.store.store_dam_hourly_prices([p.model_dump() for p in live_prices])
                with self._lock:
                    self._dam_cache[target_date] = live_prices
                self.dam_last_fetched = datetime.now(timezone.utc)
                self.dam_source_mode = "live"
                return self._build_dam_summary(target_date, live_prices)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            logger.warning("Live DAM fetch failed for %s (%s). Falling back to L3 seed.", target_date, e)

        # 4. L3 Bundled JSON Seed
        l3_prices = self._get_l3_dam_prices(target_date)
        if l3_prices:
            self.store.store_dam_hourly_prices([p.model_dump() for p in l3_prices])
            with self._lock:
                self._dam_cache[target_date] = l3_prices
            self.dam_source_mode = "fallback_seed"
            return self._build_dam_summary(target_date, l3_prices)

        # 5. L4 Algorithmic synthetic
        l4_prices = self._get_l4_dam_prices(target_date)
        with self._lock:
            self._dam_cache[target_date] = l4_prices
        self.dam_source_mode = "fallback_synthetic"
        return self._build_dam_summary(target_date, l4_prices)

    def _build_dam_summary(self, target_date: str, prices: list[DamHourlyPrice]) -> DamDaySummary:
        """Compute metrics for DAM curve summary."""
        count = len(prices)
        mwh_vals = [p.price_eur_mwh for p in prices]
        min_p = min(mwh_vals) if mwh_vals else 0.0
        max_p = max(mwh_vals) if mwh_vals else 0.0
        avg_p = sum(mwh_vals) / count if count > 0 else 0.0

        # Peak hours: 09:00 - 21:00 (hours 9 through 20 inclusive)
        peak_vals = [p.price_eur_mwh for p in prices if 9 <= p.hour <= 20]
        peak_avg = sum(peak_vals) / len(peak_vals) if peak_vals else avg_p

        return DamDaySummary(
            date=target_date,
            count_hours=count,
            min_price_eur_mwh=round(min_p, 2),
            max_price_eur_mwh=round(max_p, 2),
            avg_price_eur_mwh=round(avg_p, 2),
            peak_avg_price_eur_mwh=round(peak_avg, 2),
            prices=prices,
        )

    async def get_dam_hourly_price(self, timestamp: datetime | str) -> DamHourlyPrice:
        """Fetch single hourly DAM price for a given timestamp."""
        if isinstance(timestamp, str):
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            dt = timestamp

        date_str = dt.strftime("%Y-%m-%d")
        summary = await self.get_dam_curve(date_str)
        for p in summary.prices:
            if p.hour == dt.hour:
                return p

        # Fallback single price
        return DamHourlyPrice(
            date=date_str,
            hour=dt.hour,
            price_eur_mwh=120.0,
            price_eur_kwh=0.120,
            source="fallback_synthetic",
        )

    async def get_green_tariffs(
        self,
        month: str | None = None,
        supplier_id: str | None = None,
    ) -> list[GreenTariffAnnouncement]:
        """Fetch Green Tariff announcements with full 4-tier caching & fallback."""
        target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        norm_supplier = supplier_id.strip().lower() if supplier_id else None

        # 1. L1 Memory
        with self._lock:
            if target_month in self._green_cache:
                cached = self._green_cache[target_month]
                if norm_supplier:
                    return [t for t in cached if t.supplier_id == norm_supplier]
                return cached

        # 2. L2 SQLite
        rows = self.store.get_green_tariffs(month=target_month, supplier_id=norm_supplier)
        if rows:
            tariffs = [GreenTariffAnnouncement(**r) for r in rows]
            with self._lock:
                self._green_cache[target_month] = tariffs
            self.green_source_mode = "cached"
            return tariffs

        # 3. Live Client
        try:
            live_tariffs = await self.client.fetch_green_tariffs(target_month)
            if live_tariffs:
                self.store.store_green_tariffs([t.model_dump() for t in live_tariffs])
                with self._lock:
                    self._green_cache[target_month] = live_tariffs
                self.green_last_fetched = datetime.now(timezone.utc)
                self.green_source_mode = "live"
                if norm_supplier:
                    return [t for t in live_tariffs if t.supplier_id == norm_supplier]
                return live_tariffs
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            logger.warning("Live Green Tariff fetch failed for %s (%s). Falling back to L3 seed.", target_month, e)

        # 4. L3 Bundled JSON Seed
        l3_tariffs = self._get_l3_green_tariffs(target_month)
        if l3_tariffs:
            self.store.store_green_tariffs([t.model_dump() for t in l3_tariffs])
            with self._lock:
                self._green_cache[target_month] = l3_tariffs
            self.green_source_mode = "fallback_seed"
            if norm_supplier:
                return [t for t in l3_tariffs if t.supplier_id == norm_supplier]
            return l3_tariffs

        # 5. L4 Algorithmic fallback
        l4_tariffs = self._get_l4_green_tariffs(target_month)
        with self._lock:
            self._green_cache[target_month] = l4_tariffs
        self.green_source_mode = "fallback_synthetic"
        if norm_supplier:
            return [t for t in l4_tariffs if t.supplier_id == norm_supplier]
        return l4_tariffs

    async def refresh_green_tariffs(self, month: str | None = None) -> list[GreenTariffAnnouncement]:
        """Force refresh of Green Tariffs from remote client, validating Law 5068/2023 formulas."""
        target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            tariffs = await self.client.fetch_green_tariffs(target_month)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            logger.warning("Live refresh failed (%s); seeding from L3 seed", e)
            tariffs = self._get_l3_green_tariffs(target_month)

        for t in tariffs:
            is_valid, calc_p, _disc = verify_green_tariff_formula(t)
            if not is_valid:
                logger.warning(
                    "Formula discrepancy for %s %s: declared=%.5f, calculated=%.5f",
                    t.supplier_name,
                    t.contract_type,
                    t.published_final_rate_eur_per_kwh,
                    calc_p,
                )

        self.store.store_green_tariffs([t.model_dump() for t in tariffs])
        with self._lock:
            self._green_cache[target_month] = tariffs
        self.green_last_fetched = datetime.now(timezone.utc)
        self.green_source_mode = "live"
        return tariffs

    async def fetch_dam_curve(self, target_date: str) -> list[DamHourlyPrice]:
        """Force fetch and persistence of 24h DAM curve for target date."""
        try:
            prices = await self.client.fetch_dam_prices(target_date)
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as e:
            logger.warning("Live DAM fetch failed (%s); generating from L3 profile", e)
            prices = self._get_l3_dam_prices(target_date)

        self.store.store_dam_hourly_prices([p.model_dump() for p in prices])
        with self._lock:
            self._dam_cache[target_date] = prices
        self.dam_last_fetched = datetime.now(timezone.utc)
        self.dam_source_mode = "live"
        return prices

    def get_market_status(self) -> MarketStatus:
        """Return operational health and cache statistics."""
        cached_dates = self.store.get_cached_dam_dates()
        latest_green = self.store.get_latest_green_month() or datetime.now(timezone.utc).strftime("%Y-%m")
        return MarketStatus(
            online=True,
            dam_last_fetched=self.dam_last_fetched,
            dam_cached_dates_count=len(cached_dates),
            green_last_fetched=self.green_last_fetched,
            green_active_month=latest_green,
            dam_source_mode=self.dam_source_mode,
            green_source_mode=self.green_source_mode,
        )


# Global singleton instance
_default_market_service: MarketPriceService | None = None
_service_lock = Lock()


def get_market_service(store: SQLiteStore | None = None) -> MarketPriceService:
    """Retrieve or initialize global MarketPriceService singleton."""
    global _default_market_service
    with _service_lock:
        if _default_market_service is None:
            _default_market_service = MarketPriceService(store=store)
        return _default_market_service
