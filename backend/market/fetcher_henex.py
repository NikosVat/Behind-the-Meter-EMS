"""HEnEx (Hellenic Energy Exchange) 24-Hour Day-Ahead Market (DAM) fetcher.

Ingests Market Clearing Prices (MCP in €/MWh) for Greece (Bidding Zone: GR / 10YGR-HTSO-----8).
Handles Daylight Saving Time transitions (23-hour spring transition, 25-hour autumn transition),
negative prices, and validation.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Any

import httpx

from backend.market.models import DamHourlyPrice

logger = logging.getLogger(__name__)

# HEnEx / ENEX Group DAM endpoints
HENEX_DAM_RESULTS_URL = "https://www.enexgroup.gr/api/v1/dam-results"
ENTSOE_TRANSPARENCY_URL = "https://web-api.tp.entsoe.eu/api"
GREECE_BIDDING_ZONE = "10YGR-HTSO-----8"


def is_dst_transition_day(target_date: str) -> tuple[bool, int]:
    """Determine if a date in YYYY-MM-DD is a DST transition day in Europe/Athens.

    Returns:
        tuple[is_transition, expected_hours]
        - Last Sunday of March: (True, 23)
        - Last Sunday of October: (True, 25)
        - Normal days: (False, 24)
    """
    try:
        dt = date.fromisoformat(target_date)
    except ValueError:
        return False, 24

    # DST transitions happen on Sundays in March and October
    if dt.weekday() != 6:  # Sunday is 6
        return False, 24

    # Last Sunday of March: March 31 minus (March 31 weekday + 1) % 7
    if dt.month == 3 and dt.day >= 25:
        return True, 23
    elif dt.month == 10 and dt.day >= 25:
        return True, 25

    return False, 24


def validate_dam_prices(prices: list[DamHourlyPrice], target_date: str) -> bool:
    """Validate completeness and physical sanity of 24-hour DAM price series.

    Checks:
    - Correct hour count (24 normal, 23 spring DST, 25 autumn DST)
    - Sequential hour indices starting at 0
    - Price bounds within EU electricity market clearing rules (-500.0 to 3000.0 €/MWh)
    """
    if not prices:
        return False

    _is_transition, expected_hours = is_dst_transition_day(target_date)
    if len(prices) != expected_hours:
        logger.warning(
            "Hour count mismatch for %s: got %d, expected %d",
            target_date,
            len(prices),
            expected_hours,
        )
        return False

    # Check contiguous hours
    hours = [p.hour for p in prices]
    if hours != list(range(expected_hours)):
        logger.warning("Non-sequential or duplicated hours for %s: %s", target_date, hours)
        return False

    # Check EU market price limits (-500.0 €/MWh to 3000.0 €/MWh)
    for p in prices:
        if p.price_eur_mwh < -500.0 or p.price_eur_mwh > 3000.0:
            logger.warning("Price out of legal market bounds for hour %d: %.2f €/MWh", p.hour, p.price_eur_mwh)
            return False

    return True


def parse_henex_csv(csv_content: str, target_date: str) -> list[DamHourlyPrice]:
    """Parse CSV text containing hourly clearing prices from HEnEx publication.

    Expected CSV columns: Hour, MCP or Price (€/MWh).
    Handles both comma and semicolon delimiters.
    """
    delimiter = ";" if ";" in csv_content.splitlines()[0] else ","
    reader = csv.reader(io.StringIO(csv_content.strip()), delimiter=delimiter)

    records: list[DamHourlyPrice] = []
    header = next(reader, None)
    if not header:
        return []

    # Identify hour and price columns
    hour_idx = 0
    price_idx = 1
    for i, col in enumerate(header):
        cleaned = col.strip().lower()
        if "hour" in cleaned or "ώρα" in cleaned:
            hour_idx = i
        elif "mcp" in cleaned or "price" in cleaned or "τιμή" in cleaned or "eur" in cleaned:
            price_idx = i

    for row in reader:
        if not row or len(row) <= max(hour_idx, price_idx):
            continue
        try:
            raw_hour = row[hour_idx].strip()
            # If hour is 1-indexed (1..24), convert to 0..23; if already 0-indexed keep
            hour_num = int(raw_hour)
            if hour_num >= 1 and hour_num <= 24 and len(records) == hour_num - 1:
                h_idx = hour_num - 1
            else:
                h_idx = hour_num

            raw_price = row[price_idx].strip().replace("€", "").replace(",", ".")
            mcp = float(raw_price)

            records.append(
                DamHourlyPrice(
                    date=target_date,
                    hour=h_idx,
                    price_eur_mwh=round(mcp, 2),
                    price_eur_kwh=round(mcp / 1000.0, 5),
                    source="henex_live",
                )
            )
        except (ValueError, IndexError):
            continue

    return records


def parse_henex_json(data: dict[str, Any] | list[dict[str, Any]], target_date: str) -> list[DamHourlyPrice]:
    """Parse JSON payload from HEnEx or ENTSO-E market feed."""
    items = data.get("prices", data.get("data", [])) if isinstance(data, dict) else data
    records: list[DamHourlyPrice] = []

    for i, item in enumerate(items):  # type: ignore[arg-type]
        try:
            hour = int(item.get("hour", i))
            raw_price = item.get("price_eur_mwh", item.get("price", item.get("mcp", 0.0)))
            price = float(raw_price)
            records.append(
                DamHourlyPrice(
                    date=target_date,
                    hour=hour,
                    price_eur_mwh=round(price, 2),
                    price_eur_kwh=round(price / 1000.0, 5),
                    source="henex_live",
                )
            )
        except (ValueError, TypeError):
            continue

    return records


async def fetch_dam_hourly_prices(
    target_date: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[DamHourlyPrice]:
    """Fetch 24-hour DAM hourly prices from HEnEx or ENTSO-E endpoints.

    If remote endpoints fail or return invalid data, raises an HTTP exception
    so the 4-tier caching progression can activate cleanly.
    """
    client = http_client or httpx.AsyncClient(timeout=10.0)
    headers = {
        "User-Agent": "Greek-Commercial-EMS/2.0 MarketIngestion",
        "Accept": "application/json, text/csv, */*",
    }
    should_close = http_client is None

    try:
        url = f"{HENEX_DAM_RESULTS_URL}?date={target_date}&zone={GREECE_BIDDING_ZONE}"
        response = await client.get(url, headers=headers)
        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                prices = parse_henex_json(response.json(), target_date)
            else:
                prices = parse_henex_csv(response.text, target_date)

            if validate_dam_prices(prices, target_date):
                return prices

        raise httpx.HTTPStatusError(
            f"Failed to fetch valid DAM curve for {target_date} (status {response.status_code})",
            request=response.request,
            response=response,
        )
    finally:
        if should_close:
            await client.aclose()
