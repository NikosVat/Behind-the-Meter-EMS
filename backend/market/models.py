"""Pydantic schemas and models for Greek Energy Market & Tariff Ingestion Engine.

Covers:
- Day-Ahead Market (DAM) hourly clearing prices (MCP)
- DAM daily summary statistics
- RAE Monthly Green Tariff (Ειδικό Τιμολόγιο) announcements
- Market service status and operational metrics
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DamHourlyPrice(BaseModel):
    """Hourly Day-Ahead Market clearing price record."""

    date: str = Field(..., description="Target market date in YYYY-MM-DD format")
    hour: int = Field(..., ge=0, le=24, description="Hour index 0..23 (or 24 for 25h autumn DST)")
    price_eur_mwh: float = Field(..., description="Market Clearing Price (MCP) in €/MWh")
    price_eur_kwh: float = Field(..., description="Market Clearing Price in €/kWh (price_eur_mwh / 1000.0)")
    source: str = Field("henex_live", description="Data source: henex_live, cached, fallback_seed, fallback_synthetic")


class DamDaySummary(BaseModel):
    """Daily summary and metrics for 24-hour DAM price curve."""

    date: str = Field(..., description="Target market date in YYYY-MM-DD format")
    count_hours: int = Field(..., description="Total hours in market curve (24, or 23/25 on DST transitions)")
    min_price_eur_mwh: float = Field(..., description="Lowest clearing price of the day in €/MWh")
    max_price_eur_mwh: float = Field(..., description="Highest clearing price of the day in €/MWh")
    avg_price_eur_mwh: float = Field(..., description="24-hour mean clearing price (TEA) in €/MWh")
    peak_avg_price_eur_mwh: float = Field(..., description="Peak hours (09:00-21:00) mean clearing price in €/MWh")
    prices: list[DamHourlyPrice] = Field(default_factory=list, description="Ordered hourly price records")


class GreenTariffAnnouncement(BaseModel):
    """Official monthly Green Tariff parameters per supplier & contract type."""

    month: str = Field(..., description="Announcement month in YYYY-MM format")
    supplier_id: str = Field(..., description="Normalized supplier ID (e.g. dei, protergia, elpedison, heron)")
    supplier_name: str = Field(..., description="Official Greek supplier name (e.g. ΔΕΗ, Protergia)")
    contract_type: str = Field(..., description="Contract type code: G21, G22, etc.")
    p_base: float = Field(..., description="Base supply energy rate P_base in €/kWh")
    e_disc: float = Field(0.0, description="Prompt payment discount rate E_disc in €/kWh")
    prompt_discount_percent: float = Field(0.0, description="Prompt payment discount percentage (%)")
    alpha: float = Field(1.15, description="Fluctuation mechanism multiplier α (see docs/RULES_SOURCES.md)")
    lu_eur_mwh: float = Field(115.0, description="Upper price tolerance threshold Lu in €/MWh")
    ll_eur_mwh: float = Field(95.0, description="Lower price tolerance threshold Ll in €/MWh")
    beta: float = Field(0.0, description="Historical shift factor β in €/kWh")
    fixed_monthly_fee_eur: float = Field(5.0, description="Monthly standing fee (πάγιο) in €")
    published_final_rate_eur_per_kwh: float = Field(..., description="Supplier's declared final rate in €/kWh")
    tea_m1_eur_mwh: float | None = Field(None, description="Reference wholesale TEA of month M-1 in €/MWh")
    source: str = Field("energycost_live", description="Source: energycost_live, rae_feed, cached, fallback_seed, fallback_synthetic")


class MarketStatus(BaseModel):
    """Operational status and cache metrics of the Market Ingestion Service."""

    online: bool = Field(..., description="True if remote market sources or caches are active")
    dam_last_fetched: datetime | None = Field(None, description="UTC timestamp of last DAM fetch")
    dam_cached_dates_count: int = Field(0, description="Count of dates cached in SQLite store")
    green_last_fetched: datetime | None = Field(None, description="UTC timestamp of last Green Tariff fetch")
    green_active_month: str = Field("", description="Currently active Green Tariff month (YYYY-MM)")
    dam_source_mode: str = Field("live", description="Active DAM source: live, cached, fallback_seed, fallback_synthetic")
    green_source_mode: str = Field("live", description="Active Green Tariff source: live, cached, fallback_seed, fallback_synthetic")


class RefreshGreenResponse(BaseModel):
    """Response payload for POST /api/v1/market/refresh-green."""

    status: str = "success"
    count: int = Field(..., description="Number of green tariffs refreshed")
    month: str = Field(..., description="Target month refreshed (YYYY-MM)")


class FetchDamResponse(BaseModel):
    """Response payload for POST /api/v1/market/fetch-dam."""

    status: str = "success"
    hours_fetched: int = Field(..., description="Number of hourly price records ingested")
    date: str = Field(..., description="Target date fetched (YYYY-MM-DD)")
