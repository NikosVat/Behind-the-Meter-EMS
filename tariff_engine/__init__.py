"""
Greek Electricity Tariff & Real-Time Cost Engine.

Deterministic modeling for:
- Contracts: Γ21 (LV <= 25 kVA), Γ22 (LV dual-rate > 25 kVA), Γ23 (MV > 250 kVA).
- Retail Tariff Types: Green (Law 5068/2023 MD Formula), Yellow, Dynamic (HEnEx DAM spot).
- Regulated Charges: DEDDIE, ADMIE, ETMEAR, YKO, EFK, DETE, 6% VAT.
- Penalties: Low power factor (cos φ < 0.85), Contracted capacity excess.
- Metrics: Instantaneous €/h, incremental packet €, daily spend accumulator, projected peak breach surcharge.
"""

from __future__ import annotations

from .adapters import (
    BaseMarketAdapter,
    DemandCapacityLimit,
    GermanFacilityContract,
    GermanMarketAdapter,
    GreekMarketAdapter,
    HourlyPriceVector,
    MarketAdapterRegistry,
    MarketMetadata,
    SpanishFacilityContract,
    SpanishMarketAdapter,
    get_market_adapter,
    register_default_adapters,
)
from .benchmark_dataset import (
    BENCHMARK_BILLS,
    BenchmarkBillDefinition,
    get_all_benchmark_bills,
    get_benchmark_bill,
    list_benchmark_bill_ids,
)
from .billing import (
    LineItemCategory,
    UtilityBill,
    UtilityBillInput,
    UtilityBillLineItem,
    calculate_periodic_bill,
)
from .contracts import (
    ContractProfile,
    Season,
    TariffColor,
    TariffContract,
    get_greek_season,
    get_remaining_peak_hours,
    is_greek_offpeak_window,
    is_greek_peak_window,
    is_offpeak_window,
    is_peak_window,
)
from .cost_calculator import (
    CostCalculationResult,
    DailySpendTracker,
    calculate_projected_peak_penalty,
    calculate_realtime_cost,
)
from .green_tariff import (
    GreenTariffEngine,
    calculate_green_tariff_fluctuation,
    calculate_green_tariff_supply_rate,
)
from .market_feed import (
    BaseMarketFeed,
    resolve_effective_tea,
)
from .penalties import (
    POWER_FACTOR_THRESHOLD,
    CapacityExcessResult,
    calculate_capacity_excess,
    calculate_power_factor_multiplier,
    calculate_power_factor_surcharge,
    is_capacity_exceeded,
    is_power_factor_penalized,
)
from .regulated_charges import (
    ADMIE_CAPACITY_RATE_EUR_KVA_YR,
    ADMIE_ENERGY_RATE_EUR_KWH,
    DEDDIE_CAPACITY_RATE_EUR_KVA_YR,
    DEDDIE_ENERGY_RATE_EUR_KWH,
    DETE_FIXED_EQUIVALENT_EUR_KWH,
    DETE_PERCENT,
    EFK_RATE_EUR_KWH,
    ETMEAR_LV_RATE_EUR_KWH,
    ETMEAR_MV_RATE_EUR_KWH,
    VAT_RATE,
    YKO_RATE_EUR_KWH,
    RegulatedBreakdown,
    calculate_admie_capacity_charge,
    calculate_deddie_capacity_charge,
    calculate_fixed_capacity_charges,
    calculate_hourly_capacity_rate,
    calculate_regulated_unit_rate,
    get_regulated_breakdown,
)
from .validator import (
    BenchmarkSuiteValidationResult,
    BillValidationResult,
    LineItemComparison,
    compare_bill,
    validate_benchmark_suite,
)
from .units import (
    PriceUnit,
    to_kwh_rate,
)
from .yellow_dynamic import (
    YellowDynamicEngine,
    calculate_dynamic_tariff,
    calculate_yellow_dynamic_supply_rate,
    calculate_yellow_tariff,
)

__all__ = [
    "ADMIE_CAPACITY_RATE_EUR_KVA_YR",
    "ADMIE_ENERGY_RATE_EUR_KWH",
    "BENCHMARK_BILLS",
    "DEDDIE_CAPACITY_RATE_EUR_KVA_YR",
    "DEDDIE_ENERGY_RATE_EUR_KWH",
    "DETE_FIXED_EQUIVALENT_EUR_KWH",
    "DETE_PERCENT",
    "EFK_RATE_EUR_KWH",
    "ETMEAR_LV_RATE_EUR_KWH",
    "ETMEAR_MV_RATE_EUR_KWH",
    "POWER_FACTOR_THRESHOLD",
    "PriceUnit",
    "VAT_RATE",
    "YKO_RATE_EUR_KWH",
    "BaseMarketAdapter",
    "BaseMarketFeed",
    "BenchmarkBillDefinition",
    "BenchmarkSuiteValidationResult",
    "BillValidationResult",
    "CapacityExcessResult",
    "ContractProfile",
    # Real-Time Cost
    "CostCalculationResult",
    "DailySpendTracker",
    "DemandCapacityLimit",
    "GermanFacilityContract",
    "GermanMarketAdapter",
    "GreekMarketAdapter",
    "GreenTariffEngine",
    "HourlyPriceVector",
    "LineItemCategory",
    "LineItemComparison",
    "MarketAdapterRegistry",
    "MarketMetadata",
    "RegulatedBreakdown",
    "Season",
    "SpanishFacilityContract",
    "SpanishMarketAdapter",
    "TariffColor",
    # Contracts & TOU
    "TariffContract",
    "UtilityBill",
    "UtilityBillInput",
    "UtilityBillLineItem",
    "YellowDynamicEngine",
    "calculate_admie_capacity_charge",
    "calculate_capacity_excess",
    "calculate_deddie_capacity_charge",
    "calculate_dynamic_tariff",
    "calculate_fixed_capacity_charges",
    # Green Tariff
    "calculate_green_tariff_fluctuation",
    "calculate_green_tariff_supply_rate",
    "calculate_hourly_capacity_rate",
    "calculate_periodic_bill",
    # Penalties
    "calculate_power_factor_multiplier",
    "calculate_power_factor_surcharge",
    "calculate_projected_peak_penalty",
    "calculate_realtime_cost",
    # Regulated Charges
    "calculate_regulated_unit_rate",
    # Yellow & Dynamic
    "calculate_yellow_dynamic_supply_rate",
    "calculate_yellow_tariff",
    "compare_bill",
    "get_all_benchmark_bills",
    "get_benchmark_bill",
    "get_greek_season",
    "get_market_adapter",
    "get_regulated_breakdown",
    "get_remaining_peak_hours",
    "is_capacity_exceeded",
    "is_greek_offpeak_window",
    "is_greek_peak_window",
    "is_offpeak_window",
    "is_peak_window",
    "is_power_factor_penalized",
    "list_benchmark_bill_ids",
    "register_default_adapters",
    "resolve_effective_tea",
    "to_kwh_rate",
    "validate_benchmark_suite",
]

