"""
Market Adapter Registry for dynamic registration and resolution by bidding zone (Requirement R4 / F7).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, ClassVar

from tariff_engine.adapters.base import BaseMarketAdapter, MarketMetadata

logger = logging.getLogger(__name__)


class MarketAdapterRegistry:
    """
    Thread-safe registry and factory for European energy market adapters.
    """

    _lock = threading.Lock()
    _adapters: ClassVar[dict[str, type[BaseMarketAdapter]]] = {}
    _aliases: ClassVar[dict[str, str]] = {}


    @classmethod
    def register(
        cls,
        bidding_zone: str,
        adapter_cls: type[BaseMarketAdapter],
        aliases: list[str] | None = None,
    ) -> None:
        """Registers a market adapter class under a canonical bidding zone code."""
        with cls._lock:
            zone = bidding_zone.strip().upper()
            cls._adapters[zone] = adapter_cls
            if aliases:
                for alias in aliases:
                    cls._aliases[alias.strip().upper()] = zone
            logger.debug("Registered market adapter %s for zone %s", adapter_cls.__name__, zone)

    @classmethod
    def get_adapter(
        cls,
        bidding_zone: str = "GR",
        **kwargs: Any,
    ) -> BaseMarketAdapter:
        """
        Instantiates and returns the market adapter for the requested bidding zone.
        Raises ValueError if the zone is unsupported.
        """
        zone = bidding_zone.strip().upper()
        with cls._lock:
            canonical_zone = cls._aliases.get(zone, zone)
            adapter_cls = cls._adapters.get(canonical_zone)

            if adapter_cls is None:
                supported = sorted(cls._adapters.keys())
                raise ValueError(
                    f"Unsupported electricity bidding zone '{bidding_zone}'. "
                    f"Registered bidding zones: {supported}"
                )

            return adapter_cls(**kwargs)

    @classmethod
    def list_supported_zones(cls) -> list[str]:
        """Returns sorted list of registered canonical bidding zones."""
        with cls._lock:
            return sorted(cls._adapters.keys())

    @classmethod
    def is_zone_supported(cls, bidding_zone: str) -> bool:
        """Checks whether a bidding zone or alias is registered."""
        zone = bidding_zone.strip().upper()
        with cls._lock:
            canonical_zone = cls._aliases.get(zone, zone)
            return canonical_zone in cls._adapters

    @classmethod
    def get_metadata(cls, bidding_zone: str) -> MarketMetadata:
        """Returns metadata for a registered market zone without holding adapter instance."""
        adapter = cls.get_adapter(bidding_zone)
        return adapter.get_market_metadata()

    @classmethod
    def clear(cls) -> None:
        """Clears all registered adapters (primarily for isolated test fixtures)."""
        with cls._lock:
            cls._adapters.clear()
            cls._aliases.clear()


def get_market_adapter(bidding_zone: str = "GR", **kwargs: Any) -> BaseMarketAdapter:
    """Convenience factory function."""
    return MarketAdapterRegistry.get_adapter(bidding_zone, **kwargs)


def register_default_adapters() -> None:
    """Registers the built-in Greek market adapter."""
    from tariff_engine.adapters.greek import GreekMarketAdapter

    MarketAdapterRegistry.register("GR", GreekMarketAdapter, aliases=["GREECE", "GR-EL"])


# Auto-register defaults on initial import
register_default_adapters()
