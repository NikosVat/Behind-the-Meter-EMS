"""
Greek Green Electricity Tariff (Ειδικό Τιμολόγιο) Calculation Engine.

Implements the official Fluctuation Mechanism (Μηχανισμός Διακύμανσης - MD)
pursuant to Greek Law 5068/2023 and Ministerial Decision ΥΠΕΝ/ΔΗΕ/120637/2107.

Fluctuation Mechanism Formulas:
- MD = α * (TEA_{M-1} - Lu) + β    if TEA_{M-1} > Lu
- MD = 0                            if Ll <= TEA_{M-1} <= Lu
- MD = α * (TEA_{M-1} - Ll) + β    if TEA_{M-1} < Ll
- β = α * (TEA_{M-1} - TEA_{M-2})

Final Green Supply Rate:
- P_supply = max(0, P_base - E_disc + MD + C_fixed_per_kwh)
"""

from __future__ import annotations

from dataclasses import dataclass


from typing import Literal

PriceUnit = Literal["EUR_MWH", "EUR_KWH"]


def to_kwh_rate(val: float, unit: PriceUnit = "EUR_MWH") -> float:
    """Converts price to €/kWh with strict unit validation."""
    if unit == "EUR_MWH":
        return val / 1000.0
    elif unit == "EUR_KWH":
        return val
    raise ValueError(f"Unsupported price unit '{unit}'. Must be 'EUR_MWH' or 'EUR_KWH'.")


def _normalize_to_kwh(val: float) -> float:
    """Deprecated: normalizes price to €/kWh assuming EUR_MWH."""
    return to_kwh_rate(val, "EUR_MWH")


def calculate_green_tariff_fluctuation(
    tea_eur_mwh: float,
    ll_eur_mwh: float = 95.0,
    lu_eur_mwh: float = 115.0,
    alpha: float = 1.15,
    beta: float = 0.0,
    tea_m2_eur_mwh: float | None = None,
    unit: PriceUnit = "EUR_MWH",
) -> float:
    """
    Computes Green Tariff Fluctuation Mechanism MD(M) per Law 5068/2023 & MD ΥΠΕΝ.

    Args:
        tea_eur_mwh: Mean Day-Ahead Market Clearing Price of month M-1 (€/MWh or €/kWh).
        ll_eur_mwh: Lower price tolerance band limit Ll (default: 95.0 €/MWh = 0.095 €/kWh).
        lu_eur_mwh: Upper price tolerance band limit Lu (default: 115.0 €/MWh = 0.115 €/kWh).
        alpha: Amplification/scaling factor α (default: 1.15, typically 1.15 to 1.40).
        beta: Historical adjustment factor β (€/kWh). If 0.0 and tea_m2_eur_mwh is supplied,
              calculated as α * (TEA_{M-1} - TEA_{M-2}).
        tea_m2_eur_mwh: Mean DAM Clearing Price of month M-2 (€/MWh or €/kWh).
        unit: Price unit of tea_eur_mwh ('EUR_MWH' or 'EUR_KWH', default 'EUR_MWH').

    Returns:
        Fluctuation Mechanism adjustment MD in €/kWh (can be positive, zero, or negative).
    """
    tea_kwh = to_kwh_rate(tea_eur_mwh, unit)
    ll_kwh = to_kwh_rate(ll_eur_mwh, unit)
    lu_kwh = to_kwh_rate(lu_eur_mwh, unit)

    # Compute beta if tea_m2 is provided and beta was not explicitly set
    effective_beta = beta
    if tea_m2_eur_mwh is not None and beta == 0.0:
        tea_m2_kwh = to_kwh_rate(tea_m2_eur_mwh, unit)
        effective_beta = alpha * (tea_kwh - tea_m2_kwh)

    if tea_kwh > lu_kwh:
        md = alpha * (tea_kwh - lu_kwh) + effective_beta
    elif tea_kwh < ll_kwh:
        md = alpha * (tea_kwh - ll_kwh) + effective_beta
    else:
        md = 0.0 + effective_beta

    return md


def calculate_green_tariff_supply_rate(
    p_base: float = 0.145,
    e_disc: float = 0.020,
    tea_eur_mwh: float = 135.0,
    ll_eur_mwh: float = 95.0,
    lu_eur_mwh: float = 115.0,
    alpha: float = 1.15,
    beta: float = 0.0,
    tea_m2_eur_mwh: float | None = None,
    prompt_discount_percent: float | None = None,
    fixed_monthly_fee_eur: float = 0.0,
    monthly_kwh: float = 0.0,
    unit: PriceUnit = "EUR_MWH",
) -> float:
    """
    Computes the total Green Tariff supply price:
    P_supply = max(0, P_base - E_disc + MD + C_fixed_per_kwh)

    Args:
        p_base: Base supply tariff set by provider (€/kWh).
        e_disc: Prompt payment discount in €/kWh (applied if prompt_discount_percent is None).
        tea_eur_mwh: Day-Ahead Market TEA_{M-1} (€/MWh).
        ll_eur_mwh: Lower price band Ll (€/MWh).
        lu_eur_mwh: Upper price band Lu (€/MWh).
        alpha: Amplification factor α.
        beta: Historical adjustment factor β (€/kWh).
        tea_m2_eur_mwh: Day-Ahead Market TEA_{M-2} (€/MWh).
        prompt_discount_percent: Optional prompt payment discount percentage (0-100%).
        fixed_monthly_fee_eur: Monthly fixed charge in € (capped at <= 5.00 €/mo).
        monthly_kwh: Estimated or actual monthly consumption in kWh to amortize fixed fee.
        unit: Price unit of tea_eur_mwh ('EUR_MWH' or 'EUR_KWH', default 'EUR_MWH').

    Returns:
        Final green supply rate in €/kWh rounded to 5 decimal places (floored at 0.0).
    """
    md = calculate_green_tariff_fluctuation(
        tea_eur_mwh=tea_eur_mwh,
        ll_eur_mwh=ll_eur_mwh,
        lu_eur_mwh=lu_eur_mwh,
        alpha=alpha,
        beta=beta,
        tea_m2_eur_mwh=tea_m2_eur_mwh,
        unit=unit,
    )

    # Calculate effective discount
    if prompt_discount_percent is not None:
        discount = p_base * (prompt_discount_percent / 100.0)
    else:
        discount = e_disc

    # Fixed charge per kWh amortization
    fixed_per_kwh = (fixed_monthly_fee_eur / monthly_kwh) if monthly_kwh > 0.0 else 0.0

    p_supply = max(0.0, p_base - discount + md + fixed_per_kwh)
    return round(p_supply, 5)


@dataclass
class GreenTariffEngine:
    """
    Stateful helper model for Green Tariff management.
    """
    p_base: float = 0.155
    ll_eur_mwh: float = 95.0
    lu_eur_mwh: float = 115.0
    alpha: float = 1.15
    prompt_discount_percent: float = 0.0
    prompt_discount_eur_kwh: float = 0.0
    fixed_monthly_fee_eur: float = 5.0

    def compute_fluctuation(
        self,
        tea_eur_mwh: float,
        beta: float = 0.0,
        tea_m2_eur_mwh: float | None = None,
        unit: PriceUnit = "EUR_MWH",
    ) -> float:
        return calculate_green_tariff_fluctuation(
            tea_eur_mwh=tea_eur_mwh,
            ll_eur_mwh=self.ll_eur_mwh,
            lu_eur_mwh=self.lu_eur_mwh,
            alpha=self.alpha,
            beta=beta,
            tea_m2_eur_mwh=tea_m2_eur_mwh,
            unit=unit,
        )

    def compute_supply_rate(
        self,
        tea_eur_mwh: float,
        beta: float = 0.0,
        tea_m2_eur_mwh: float | None = None,
        monthly_kwh: float = 0.0,
        unit: PriceUnit = "EUR_MWH",
    ) -> float:
        e_disc = self.prompt_discount_eur_kwh
        disc_pct = self.prompt_discount_percent if self.prompt_discount_percent > 0 else None
        return calculate_green_tariff_supply_rate(
            p_base=self.p_base,
            e_disc=e_disc,
            tea_eur_mwh=tea_eur_mwh,
            ll_eur_mwh=self.ll_eur_mwh,
            lu_eur_mwh=self.lu_eur_mwh,
            alpha=self.alpha,
            beta=beta,
            tea_m2_eur_mwh=tea_m2_eur_mwh,
            prompt_discount_percent=disc_pct,
            fixed_monthly_fee_eur=self.fixed_monthly_fee_eur,
            monthly_kwh=monthly_kwh,
            unit=unit,
        )

