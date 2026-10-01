"""Wholesale and retail price unit conversion utilities for Greek electricity tariffs."""
from typing import Literal

PriceUnit = Literal["EUR_MWH", "EUR_KWH"]

__all__ = ["PriceUnit", "to_kwh_rate"]


def to_kwh_rate(val: float, unit: PriceUnit = "EUR_MWH") -> float:
    """Converts wholesale or retail electricity price to €/kWh with strict unit validation."""
    if unit == "EUR_MWH":
        return val / 1000.0
    elif unit == "EUR_KWH":
        return val
    raise ValueError(f"Unsupported price unit '{unit}'. Must be 'EUR_MWH' or 'EUR_KWH'.")
