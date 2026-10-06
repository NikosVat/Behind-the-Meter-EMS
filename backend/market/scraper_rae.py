"""RAE / energycost.gr monthly Special Green Tariff (Ειδικό Τιμολόγιο) scraper.

Automates the ingestion, parsing, and legal formula validation of monthly Green Tariff
announcements mandated by the special (green) tariff rules; see docs/RULES_SOURCES.md.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from backend.market.models import GreenTariffAnnouncement
from tariff_engine.green_tariff import calculate_green_tariff_supply_rate

logger = logging.getLogger(__name__)

# Default RAE / energycost.gr endpoints
RAE_MONTHLY_PRICES_URL = "https://energycost.gr/api/v1/tariffs/green-monthly"
RAE_PUBLIC_COMPARISON_URL = "https://energycost.gr/commercial-green-tariffs"

# Normalized Greek Supplier Dictionary
SUPPLIER_MAPPING: dict[str, tuple[str, str]] = {
    "ΔΕΗ": ("dei", "ΔΕΗ (Public Power Corporation)"),
    "DEI": ("dei", "ΔΕΗ (Public Power Corporation)"),
    "PPC": ("dei", "ΔΕΗ (Public Power Corporation)"),
    "PROTERGIA": ("protergia", "Protergia (Metlen Energy & Metals)"),
    "ΠΡΟΤΕΡΓΙΑ": ("protergia", "Protergia (Metlen Energy & Metals)"),
    "METLEN": ("protergia", "Protergia (Metlen Energy & Metals)"),
    "ELPEDISON": ("elpedison", "Elpedison"),
    "ΕΛΠΕΔΙΣΟΝ": ("elpedison", "Elpedison"),
    "HERON": ("heron", "Ήρων (Heron)"),
    "ΗΡΩΝ": ("heron", "Ήρων (Heron)"),
    "ZENITH": ("zenith", "ZeniΘ"),
    "ZENIΘ": ("zenith", "ZeniΘ"),
    "ZENI": ("zenith", "ZeniΘ"),
    "ΖΕΝΙΘ": ("zenith", "ZeniΘ"),
    "NRG": ("nrg", "NRG Supply and Trading"),
    "VOLTON": ("volton", "Volton Hellenic Energy"),
    "ΦΥΣΙΚΟ ΑΕΡΙΟ": ("fysiko_aerio", "Φυσικό Αέριο Ελληνική Εταιρεία Ενέργειας"),
}


def normalize_supplier(raw_name: str) -> tuple[str, str]:
    """Normalize raw Greek supplier text to (supplier_id, display_name)."""
    cleaned = raw_name.strip().upper()
    for key, val in SUPPLIER_MAPPING.items():
        if key in cleaned:
            return val
    # Fallback slug
    slug = re.sub(r"[^a-z0-9]", "_", raw_name.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_") or "generic_supplier"
    return slug, raw_name.strip()


def parse_float_gr(val: Any, default: float = 0.0) -> float:
    """Safely parse float supporting both Greek comma (0,155) and dot (0.155) decimals."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("€", "").replace("/kWh", "").replace("/MWh", "").replace("%", "").strip()
    if not s:
        return default
    # Handle European format: 1.234,56 or 0,155
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return default


def verify_green_tariff_formula(
    tariff: GreenTariffAnnouncement,
    tolerance_eur_kwh: float = 0.005,
) -> tuple[bool, float, float]:
    """Cross-validate published rate against the green tariff MD formula (see docs/RULES_SOURCES.md).

    Returns:
        tuple[is_valid, calculated_rate_eur_kwh, discrepancy_eur_kwh]
    """
    tea_val = tariff.tea_m1_eur_mwh if tariff.tea_m1_eur_mwh is not None else 120.0
    calc_rate = calculate_green_tariff_supply_rate(
        p_base=tariff.p_base,
        e_disc=tariff.e_disc,
        tea_eur_mwh=tea_val,
        ll_eur_mwh=tariff.ll_eur_mwh,
        lu_eur_mwh=tariff.lu_eur_mwh,
        alpha=tariff.alpha,
        beta=tariff.beta,
    )
    discrepancy = abs(calc_rate - tariff.published_final_rate_eur_per_kwh)
    is_valid = discrepancy <= tolerance_eur_kwh
    return is_valid, round(calc_rate, 5), round(discrepancy, 5)


def parse_rae_html(html_content: str, target_month: str) -> list[GreenTariffAnnouncement]:
    """Parse HTML table or cards from RAE / energycost.gr publication."""
    soup = BeautifulSoup(html_content, "html.parser")
    results: list[GreenTariffAnnouncement] = []

    # Look for standard HTML table
    table = soup.find("table")
    if table:
        rows = table.find_all("tr")
        for row in rows[1:]:  # skip header
            cols = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if len(cols) < 4:
                continue

            # Expected columns: Supplier, Contract, P_base, Discount, Final Rate, [optional: α, Lu, Ll, β, Fee, TEA]
            raw_supplier = cols[0]
            raw_contract = cols[1].upper()
            contract_type = "G22" if ("G22" in raw_contract or "Γ22" in raw_contract) else "G21"

            supplier_id, supplier_name = normalize_supplier(raw_supplier)

            p_base = parse_float_gr(cols[2])
            e_disc = parse_float_gr(cols[3]) if len(cols) > 3 else 0.0
            pub_final = parse_float_gr(cols[4]) if len(cols) > 4 else p_base - e_disc

            # Optional detailed formula columns
            alpha = parse_float_gr(cols[5], default=1.15) if len(cols) > 5 else 1.15
            lu = parse_float_gr(cols[6], default=115.0) if len(cols) > 6 else 115.0
            ll = parse_float_gr(cols[7], default=95.0) if len(cols) > 7 else 95.0
            beta = parse_float_gr(cols[8], default=0.0) if len(cols) > 8 else 0.0
            fee = parse_float_gr(cols[9], default=5.0) if len(cols) > 9 else 5.0
            tea_m1 = parse_float_gr(cols[10], default=120.0) if len(cols) > 10 else 120.0

            announcement = GreenTariffAnnouncement(
                month=target_month,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                contract_type=contract_type,
                p_base=p_base,
                e_disc=e_disc,
                prompt_discount_percent=round((e_disc / p_base * 100.0) if p_base > 0 else 0.0, 2),
                alpha=alpha,
                lu_eur_mwh=lu,
                ll_eur_mwh=ll,
                beta=beta,
                fixed_monthly_fee_eur=fee,
                published_final_rate_eur_per_kwh=pub_final,
                tea_m1_eur_mwh=tea_m1,
                source="energycost_live",
            )
            results.append(announcement)

    return results


def parse_rae_json(data: list[dict[str, Any]] | dict[str, Any], target_month: str) -> list[GreenTariffAnnouncement]:
    """Parse JSON feed response from RAE API."""
    items = data.get("tariffs", []) if isinstance(data, dict) else data
    results: list[GreenTariffAnnouncement] = []

    for item in items:
        raw_supplier = item.get("supplier_name", item.get("supplier", ""))
        supplier_id, supplier_name = normalize_supplier(raw_supplier)
        contract = item.get("contract_type", item.get("contract", "G21")).upper().replace("Γ", "G")
        p_base = parse_float_gr(item.get("p_base", 0.155))
        e_disc = parse_float_gr(item.get("e_disc", 0.0))
        pub_final = parse_float_gr(item.get("published_final_rate_eur_per_kwh", item.get("final_rate", p_base - e_disc)))

        announcement = GreenTariffAnnouncement(
            month=target_month,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            contract_type=contract,
            p_base=p_base,
            e_disc=e_disc,
            prompt_discount_percent=parse_float_gr(item.get("prompt_discount_percent", 0.0)),
            alpha=parse_float_gr(item.get("alpha", 1.15)),
            lu_eur_mwh=parse_float_gr(item.get("lu_eur_mwh", 115.0)),
            ll_eur_mwh=parse_float_gr(item.get("ll_eur_mwh", 95.0)),
            beta=parse_float_gr(item.get("beta", 0.0)),
            fixed_monthly_fee_eur=parse_float_gr(item.get("fixed_monthly_fee_eur", 5.0)),
            published_final_rate_eur_per_kwh=pub_final,
            tea_m1_eur_mwh=parse_float_gr(item.get("tea_m1_eur_mwh", 120.0)),
            source="rae_feed",
        )
        results.append(announcement)

    return results


async def scrape_monthly_green_tariffs(
    month: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> list[GreenTariffAnnouncement]:
    """Fetch and scrape monthly Green Tariff announcements from RAE / energycost.gr.

    If remote endpoints are unavailable, raises an HTTP exception so callers can
    fall back through the 4-tier caching progression.
    """
    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    client = http_client or httpx.AsyncClient(timeout=10.0)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Greek-EMS/2.0 MarketIngestion",
        "Accept": "application/json, text/html, */*",
    }

    should_close = http_client is None
    try:
        # Try JSON endpoint first
        response = await client.get(
            f"{RAE_MONTHLY_PRICES_URL}?month={target_month}",
            headers=headers,
        )
        if response.status_code == 200:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                return parse_rae_json(response.json(), target_month)
            return parse_rae_html(response.text, target_month)

        # Fallback to HTML table scraper
        html_response = await client.get(
            f"{RAE_PUBLIC_COMPARISON_URL}?month={target_month}",
            headers=headers,
        )
        if html_response.status_code == 200:
            return parse_rae_html(html_response.text, target_month)

        raise httpx.HTTPStatusError(
            f"RAE monthly tariffs unavailable (status {response.status_code})",
            request=response.request,
            response=response,
        )
    finally:
        if should_close:
            await client.aclose()
