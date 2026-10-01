"""
Greek Electricity Commercial Contracts & Time-of-Use Schedules.

Implements contract types (Γ21, Γ22, Γ23), tariff color schemes (Green, Yellow, Dynamic),
contract profile models, and DEDDIE time-of-use peak/off-peak schedules with weekend exemptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

ATHENS_TZ = ZoneInfo("Europe/Athens")


def to_athens_time(dt: datetime) -> datetime:
    """Normalize datetime to Greek civil time (Europe/Athens)."""
    if dt.tzinfo is not None:
        return dt.astimezone(ATHENS_TZ)
    return dt.replace(tzinfo=ATHENS_TZ)


class TariffContract(str, Enum):
    """
    Greek commercial electricity contract classifications.

    - G21 (Γ21): Low Voltage <= 25 kVA, uniform 24h single-rate metering.
    - G22 (Γ22): Low Voltage > 25 kVA (up to 250 kVA), dual-rate (peak & off-peak).
    - G23 (Γ23): Medium Voltage > 250 kVA, interval-metered industrial/large commercial.
    """
    G21 = "G21"
    G22 = "G22"
    G23 = "G23"

    @classmethod
    def from_string(cls, val: str | TariffContract) -> TariffContract:
        if isinstance(val, cls):
            return val
        s = str(val).strip().upper()
        # Handle Greek uppercase Gamma Γ (U+0393)
        s = s.replace("Γ", "G")
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"Unknown tariff contract code: {val}. Valid options: {[m.value for m in cls]}")


class TariffColor(str, Enum):
    """
    Greek retail electricity pricing color coding:
    - GREEN: Special standardized monthly tariff with Fluctuation Mechanism (MD) per Law 5068/2023.
    - YELLOW: Variable tariff indexed to wholesale Day-Ahead Market.
    - DYNAMIC: Orange/dynamic hourly spot tariff indexed to HEnEx Day-Ahead Market clearing price.
    """
    GREEN = "green"
    YELLOW = "yellow"
    DYNAMIC = "dynamic"

    @classmethod
    def from_string(cls, val: str | TariffColor) -> TariffColor:
        if isinstance(val, cls):
            return val
        s = str(val).strip().lower()
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"Unknown tariff color: {val}. Valid options: {[m.value for m in cls]}")


class Season(str, Enum):
    """Greek grid tariff seasons."""
    SUMMER = "summer"
    WINTER = "winter"


@dataclass
class ContractProfile:
    """
    Commercial consumer contract configuration profile.

    Attributes:
        contract_type: TariffContract (G21, G22, G23).
        color: TariffColor (GREEN, YELLOW, DYNAMIC).
        contracted_capacity_kva: Agreed connection capacity in kVA.
        base_rate_eur_per_kwh: Base supply rate in €/kWh (excluding MD/margins).
        prompt_discount_percent: Prompt payment discount percentage (e.g. 5.0 for 5%).
        fixed_monthly_fee_eur: Regulated fixed monthly fee (capped at <= 5.00 €/month).
        peak_threshold_kw: Peak demand curtailment warning threshold in kW.
        cooldown_seconds: Telegram alert debounce/cooldown period in seconds.
    """
    contract_type: TariffContract = TariffContract.G21
    color: TariffColor = TariffColor.GREEN
    contracted_capacity_kva: float = 25.0
    base_rate_eur_per_kwh: float = 0.155
    prompt_discount_percent: float = 0.0
    fixed_monthly_fee_eur: float = 5.0
    peak_threshold_kw: float | None = None
    cooldown_seconds: int = 1800

    def __post_init__(self):
        if not isinstance(self.contract_type, TariffContract):
            self.contract_type = TariffContract.from_string(self.contract_type)
        if not isinstance(self.color, TariffColor):
            self.color = TariffColor.from_string(self.color)

        if self.contracted_capacity_kva <= 0.0:
            raise ValueError(f"Contracted capacity must be positive, got {self.contracted_capacity_kva}")
        if self.base_rate_eur_per_kwh < 0.0:
            raise ValueError(f"Base rate cannot be negative, got {self.base_rate_eur_per_kwh}")
        if self.prompt_discount_percent < 0.0 or self.prompt_discount_percent > 100.0:
            raise ValueError(f"Prompt discount percent must be in [0, 100], got {self.prompt_discount_percent}")
        if self.fixed_monthly_fee_eur < 0.0:
            raise ValueError(f"Fixed monthly fee cannot be negative, got {self.fixed_monthly_fee_eur}")

        if self.peak_threshold_kw is None:
            # Default to 85% of contracted capacity in kW (assuming cos phi = 1.0)
            self.peak_threshold_kw = round(self.contracted_capacity_kva * 0.85, 2)


def get_greek_season(dt: datetime) -> Season:
    """
    Determines Greek grid tariff season for a given datetime.
    - Summer: May 1 to October 31 (months 5 to 10 inclusive).
    - Winter: November 1 to April 30 (months 11 to 4 inclusive).
    """
    local_dt = to_athens_time(dt)
    month = local_dt.month
    if 5 <= month <= 10:
        return Season.SUMMER
    return Season.WINTER


def is_peak_window(dt: datetime, season: str | Season = "auto") -> bool:
    """
    Evaluates whether a given timestamp falls within Greek DEDDIE peak tariff hours.

    Rules:
    - Weekend Exemption: Saturday (5) and Sunday (6) have NO peak hours.
    - Summer Season (May 1 - Oct 31):
        Peak: 14:00 to 17:00 (14:00:00 to 16:59:59) Mon - Fri.
    - Winter Season (Nov 1 - Apr 30):
        Peak: 17:00 to 21:00 (17:00:00 to 20:59:59) Mon - Fri.
    """
    local_dt = to_athens_time(dt)

    # Weekend exemption
    if local_dt.weekday() >= 5:
        return False

    if isinstance(season, Season):
        active_season = season
    elif isinstance(season, str):
        if season.lower() == "auto":
            active_season = get_greek_season(local_dt)
        elif season.lower() == "summer":
            active_season = Season.SUMMER
        elif season.lower() == "winter":
            active_season = Season.WINTER
        else:
            raise ValueError(f"Unknown season '{season}'. Choose 'auto', 'summer', or 'winter'.")
    else:
        raise TypeError(f"Unknown season type: {type(season)}")

    hour = local_dt.hour

    if active_season == Season.SUMMER:
        # 14:00:00 to 16:59:59 is peak
        return 14 <= hour < 17
    else:
        # 17:00:00 to 20:59:59 is peak
        return 17 <= hour < 21


def is_offpeak_window(dt: datetime, season: str | Season = "auto") -> bool:
    """
    Evaluates whether a given timestamp falls within Greek off-peak / night tariff hours.

    Commercial night off-peak window: 23:00 to 07:00 (23:00:00 to 06:59:59).
    """
    local_dt = to_athens_time(dt)
    hour = local_dt.hour
    return hour >= 23 or hour < 7


def get_remaining_peak_hours(dt: datetime, season: str | Season = "auto") -> float:
    """
    Calculates remaining duration in hours for the active peak window.
    Returns 0.0 if dt is not in a peak window.
    """
    if not is_peak_window(dt, season=season):
        return 0.0

    local_dt = to_athens_time(dt)
    if isinstance(season, str) and season.lower() != "auto":
        is_summer = (season.lower() == "summer")
    else:
        is_summer = (get_greek_season(local_dt) == Season.SUMMER)

    end_hour = 17.0 if is_summer else 21.0
    current_time_hours = local_dt.hour + (local_dt.minute / 60.0) + (local_dt.second / 3600.0)
    remaining = end_hour - current_time_hours
    return max(0.0, remaining)



# Compatibility aliases matching test harness naming
is_greek_peak_window = is_peak_window
is_greek_offpeak_window = is_offpeak_window
