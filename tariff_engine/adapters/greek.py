"""
Greek Market Adapter implementing Greek HEnEx and RAAEY market regulations (Requirement R4 / F6).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from tariff_engine.adapters.base import (
    BaseMarketAdapter,
    DemandCapacityLimit,
    HourlyPriceVector,
    MarketMetadata,
)
from tariff_engine.contracts import (
    is_greek_offpeak_window,
    is_greek_peak_window,
)
from tariff_engine.cost_calculator import CostCalculationResult, calculate_realtime_cost
from tariff_engine.green_tariff import calculate_green_tariff_supply_rate
from tariff_engine.regulated_charges import (
    ADMIE_ENERGY_RATE_EUR_KWH,
    DEDDIE_ENERGY_RATE_EUR_KWH,
    DETE_FIXED_EQUIVALENT_EUR_KWH,
    EFK_RATE_EUR_KWH,
    ETMEAR_LV_RATE_EUR_KWH,
    ETMEAR_MV_RATE_EUR_KWH,
    VAT_RATE,
    YKO_RATE_EUR_KWH,
    calculate_hourly_capacity_rate,
)
from tariff_engine.yellow_dynamic import calculate_yellow_dynamic_supply_rate


class GreekMarketAdapter(BaseMarketAdapter):
    """
    Market adapter for Greek wholesale and retail electricity market (HEnEx / RAAEY).
    Supports Γ21, Γ22, Γ23 contracts, Green/Yellow/Dynamic retail structures,
    DEDDIE/ADMIE network charges, low cos phi penalties, and capacity surcharges.
    """

    def __init__(self) -> None:
        self._metadata = MarketMetadata(
            bidding_zone="GR",
            country_code="GR",
            country_name="Greece",
            currency="EUR",
            regulatory_body="RAAEY",
            default_vat_rate=VAT_RATE,
            wholesale_market="HEnEx",
            supports_dynamic_dam=True,
            supports_periodic_billing=True,
            notes="Governed by RAAEY tariff regulations and Greek Law 5068/2023.",
        )

    def get_market_metadata(self) -> MarketMetadata:
        return self._metadata

    def get_hourly_price_vector(
        self,
        start_dt: datetime,
        horizon_hours: int = 24,
        facility_contract: Any = None,
        spot_prices: list[float] | None = None,
    ) -> list[HourlyPriceVector]:
        # Extract contract configuration
        contract_code = "G22"
        tariff_color = "green"
        contracted_kva = 35.0
        power_factor = 0.98

        if facility_contract is not None:
            raw_code = getattr(facility_contract, "contract_code", None) or getattr(facility_contract, "contract_type", "G22")
            if raw_code is not None:
                contract_code = raw_code.value if hasattr(raw_code, "value") else str(raw_code).strip().upper().replace("Γ", "G")
            raw_color = getattr(facility_contract, "tariff_color", None) or getattr(facility_contract, "color", "green")
            if raw_color is not None:
                tariff_color = raw_color.value if hasattr(raw_color, "value") else str(raw_color).strip().lower()
            raw_kva = getattr(facility_contract, "contracted_capacity_kva", None) or getattr(facility_contract, "contracted_kva", 35.0)
            contracted_kva = float(raw_kva) if raw_kva is not None else 35.0
            power_factor = float(getattr(facility_contract, "power_factor", 0.98) or 0.98)

        is_lv = (contract_code != "G23")
        etmear_rate = ETMEAR_LV_RATE_EUR_KWH if is_lv else ETMEAR_MV_RATE_EUR_KWH
        taxes_rate = etmear_rate + YKO_RATE_EUR_KWH + EFK_RATE_EUR_KWH + DETE_FIXED_EQUIVALENT_EUR_KWH

        # Compute power factor multiplier for DEDDIE
        pf_mult = 1.0
        if 0.0 < power_factor < 0.85:
            pf_mult = 0.85 / power_factor

        grid_dist_rate = round(DEDDIE_ENERGY_RATE_EUR_KWH * pf_mult, 5)
        grid_trans_rate = round(ADMIE_ENERGY_RATE_EUR_KWH, 5)
        standing_hourly = calculate_hourly_capacity_rate(contracted_kva)

        vectors: list[HourlyPriceVector] = []
        for h in range(horizon_hours):
            interval_dt = start_dt + timedelta(hours=h)
            is_peak = is_greek_peak_window(interval_dt)
            is_offpeak = is_greek_offpeak_window(interval_dt)

            tea = spot_prices[h] if (spot_prices and h < len(spot_prices)) else 120.0

            # Supply rate calculation
            if tariff_color == "green":
                p_base = 0.155 if contract_code == "G21" else 0.165
                supply_rate = calculate_green_tariff_supply_rate(p_base=p_base, tea_eur_mwh=tea)
            elif tariff_color == "yellow":
                supply_rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=tea, p_base=0.040)
            else:  # dynamic
                supply_rate = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=tea, p_base=0.020)

            # TOU modifiers for G22/G23
            if contract_code in ("G22", "G23"):
                if is_peak:
                    supply_rate *= 1.25
                elif is_offpeak:
                    supply_rate *= 0.70

            supply_rate = round(supply_rate, 5)
            pretax_total = round(supply_rate + grid_dist_rate + grid_trans_rate + taxes_rate, 5)
            total_inc_vat = round(pretax_total * (1.0 + VAT_RATE), 5)

            vectors.append(
                HourlyPriceVector(
                    timestamp=interval_dt,
                    interval_index=h,
                    energy_rate_eur_kwh=supply_rate,
                    grid_distribution_rate=grid_dist_rate,
                    grid_transmission_rate=grid_trans_rate,
                    taxes_and_levies_rate=round(taxes_rate, 5),
                    vat_rate=VAT_RATE,
                    total_rate_ex_vat=pretax_total,
                    total_rate_inc_vat=total_inc_vat,
                    is_peak_window=is_peak,
                    is_critical_peak=is_peak,
                    standing_capacity_rate_eur_h=standing_hourly,
                )
            )

        return vectors

    def calculate_instantaneous_cost(
        self,
        power_kw: float,
        energy_kwh_delta: float,
        timestamp: datetime,
        facility_contract: Any,
        **kwargs: Any,
    ) -> CostCalculationResult:
        tea = kwargs.get("tea_eur_mwh", 120.0)
        pf = kwargs.get("power_factor", getattr(facility_contract, "power_factor", 0.98))
        contracted_kva = kwargs.get("contracted_kva", getattr(facility_contract, "contracted_capacity_kva", 35.0))
        include_cap = kwargs.get("include_hourly_capacity_rate", False)

        return calculate_realtime_cost(
            power_kw=power_kw,
            energy_kwh_delta=energy_kwh_delta,
            timestamp=timestamp,
            tariff_profile=facility_contract,
            tea_eur_mwh=tea,
            power_factor=pf,
            contracted_kva=contracted_kva,
            include_hourly_capacity_rate=include_cap,
        )

    def calculate_periodic_bill(self, bill_input: Any) -> Any:
        from tariff_engine.billing import calculate_periodic_bill
        return calculate_periodic_bill(bill_input)

    def get_demand_capacity_limits(
        self,
        timestamp: datetime,
        facility_contract: Any,
    ) -> DemandCapacityLimit:
        raw_kva = getattr(facility_contract, "contracted_capacity_kva", None) or getattr(facility_contract, "contracted_kva", 35.0)
        contracted_kva = float(raw_kva) if raw_kva is not None else 35.0
        raw_peak = getattr(facility_contract, "peak_threshold_kw", None)
        peak_threshold = float(raw_peak) if raw_peak is not None else round(contracted_kva * 0.85, 2)
        return DemandCapacityLimit(
            contracted_capacity_kw=contracted_kva,
            peak_demand_threshold_kw=peak_threshold,
            capacity_penalty_rate_eur_kw=round((4.434 / (365 * 24)) * 2.5, 6),
            allows_power_factor_penalty=True,
        )
