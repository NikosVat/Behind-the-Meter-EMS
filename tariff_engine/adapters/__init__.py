"""
Market Adapters Package.

Provides the Greek market adapter (GR / HEnEx / RAAEY).
"""

from __future__ import annotations

from .base import (
    BaseMarketAdapter,
    DemandCapacityLimit,
    HourlyPriceVector,
    MarketMetadata,
)
from .greek import (
    GreekMarketAdapter,
)
from .registry import (
    MarketAdapterRegistry,
    get_market_adapter,
    register_default_adapters,
)

__all__ = [
    "BaseMarketAdapter",
    "DemandCapacityLimit",
    "GreekMarketAdapter",
    "HourlyPriceVector",
    "MarketAdapterRegistry",
    "MarketMetadata",
    "get_market_adapter",
    "register_default_adapters",
]
