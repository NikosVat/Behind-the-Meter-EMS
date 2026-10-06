"""
Unit Test Suite for European Market Adapters (Requirement R4).

Validates:
1. BaseMarketAdapter abstract interface and data structures.
2. GreekMarketAdapter (GR / HEnEx / DEDDIE / ADMIE).
3. MarketAdapterRegistry dynamic registration, aliasing, and error handling.
4. Optimization Engine C_t vector compatibility with SciPy HiGHS MILP solver.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest
from scipy.optimize import LinearConstraint, milp

from tariff_engine.adapters.base import (
    BaseMarketAdapter,
    DemandCapacityLimit,
    HourlyPriceVector,
    MarketMetadata,
)
from tariff_engine.adapters.greek import GreekMarketAdapter
from tariff_engine.adapters.registry import (
    MarketAdapterRegistry,
    get_market_adapter,
    register_default_adapters,
)
from tariff_engine.contracts import ATHENS_TZ, ContractProfile, TariffContract


def utc_dt(*args, **kwargs) -> datetime:
    """Helper to build timezone-aware UTC datetime."""
    return datetime(*args, **kwargs, tzinfo=timezone.utc)


# =============================================================================
# 1. Base Market Adapter Interface Tests
# =============================================================================

class TestBaseMarketAdapterInterface:
    def test_abstract_class_cannot_be_instantiated(self) -> None:
        """Verifies that BaseMarketAdapter cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseMarketAdapter()  # type: ignore

    def test_concrete_subclass_implements_interface(self) -> None:
        """Verifies that a compliant mock subclass implements all abstract methods."""
        class MockAdapter(BaseMarketAdapter):
            def get_market_metadata(self) -> MarketMetadata:
                return MarketMetadata(
                    bidding_zone="MOCK",
                    country_code="MC",
                    country_name="Mockland",
                    currency="EUR",
                    regulatory_body="MockReg",
                    default_vat_rate=0.20,
                    wholesale_market="MockSpot",
                )

            def get_hourly_price_vector(
                self,
                start_dt: datetime,
                horizon_hours: int = 24,
                facility_contract: Any = None,
                spot_prices: list[float] | None = None,
            ) -> list[HourlyPriceVector]:
                return [
                    HourlyPriceVector(
                        timestamp=start_dt,
                        interval_index=i,
                        energy_rate_eur_kwh=0.10,
                        grid_distribution_rate=0.05,
                        grid_transmission_rate=0.02,
                        taxes_and_levies_rate=0.03,
                        vat_rate=0.20,
                        total_rate_ex_vat=0.20,
                        total_rate_inc_vat=0.24,
                        is_peak_window=(14 <= i < 18),
                    )
                    for i in range(horizon_hours)
                ]

            def calculate_instantaneous_cost(self, power_kw: float, energy_kwh_delta: float, timestamp: datetime, facility_contract: Any, **kwargs: Any) -> Any:
                return None

            def calculate_periodic_bill(self, bill_input: Any) -> Any:
                return None

            def get_demand_capacity_limits(self, timestamp: datetime, facility_contract: Any) -> DemandCapacityLimit:
                return DemandCapacityLimit(50.0, 45.0)

        adapter = MockAdapter()
        meta = adapter.get_market_metadata()
        assert meta.bidding_zone == "MOCK"

        c_t = adapter.get_c_t_vector(utc_dt(2026, 7, 15, 0, 0), horizon_hours=24)
        assert len(c_t) == 24
        assert all(c == 0.24 for c in c_t)

        vectors = adapter.get_hourly_price_vector(utc_dt(2026, 7, 15, 0, 0), horizon_hours=24)
        summary = adapter.format_vector_summary(vectors)
        assert summary["bidding_zone"] == "MOCK"
        assert summary["horizon_hours"] == 24
        assert summary["min_rate_eur_kwh"] == 0.24
        assert summary["max_rate_eur_kwh"] == 0.24
        assert summary["peak_hours_count"] == 4

    def test_demand_capacity_limit_exceeded(self) -> None:
        """Verifies capacity limit breach checking."""
        limit = DemandCapacityLimit(contracted_capacity_kw=50.0, peak_demand_threshold_kw=45.0)
        assert limit.is_capacity_exceeded(40.0) is False
        assert limit.is_capacity_exceeded(45.0) is False
        assert limit.is_capacity_exceeded(45.1) is True


# =============================================================================
# 2. Greek Market Adapter Tests (Requirement R4 / F6)
# =============================================================================

class TestGreekMarketAdapter:
    @pytest.fixture
    def adapter(self) -> GreekMarketAdapter:
        return GreekMarketAdapter()

    def test_metadata(self, adapter: GreekMarketAdapter) -> None:
        meta = adapter.get_market_metadata()
        assert meta.bidding_zone == "GR"
        assert meta.country_name == "Greece"
        assert meta.regulatory_body == "RAAEY"
        assert meta.wholesale_market == "HEnEx"
        assert meta.default_vat_rate == 0.06

    def test_hourly_price_vector_24h(self, adapter: GreekMarketAdapter) -> None:
        """Tests that 24 hourly price vectors are generated with valid structure."""
        start = utc_dt(2026, 7, 15, 0, 0)  # Summer Wednesday
        vectors = adapter.get_hourly_price_vector(start, horizon_hours=24)
        assert len(vectors) == 24
        for h, v in enumerate(vectors):
            assert v.interval_index == h
            assert v.total_rate_inc_vat > 0.0
            assert v.vat_rate == 0.06

    def test_greek_peak_window_summer_and_weekend(self, adapter: GreekMarketAdapter) -> None:
        """Verifies Summer peak window (Mon-Fri 14:00-17:00) and weekend exemption."""
        # Summer Wednesday in Athens local civil time: hours 14, 15, 16 are peak
        summer_wed = datetime(2026, 7, 15, 0, 0, tzinfo=ATHENS_TZ)
        vectors_wed = adapter.get_hourly_price_vector(summer_wed, horizon_hours=24)
        for h, v in enumerate(vectors_wed):
            if 14 <= h < 17:
                assert v.is_peak_window is True
            else:
                assert v.is_peak_window is False

        # Summer Saturday: all hours exempt from peak
        summer_sat = datetime(2026, 7, 18, 0, 0, tzinfo=ATHENS_TZ)
        vectors_sat = adapter.get_hourly_price_vector(summer_sat, horizon_hours=24)
        assert all(v.is_peak_window is False for v in vectors_sat)

    def test_greek_peak_window_winter(self, adapter: GreekMarketAdapter) -> None:
        """Verifies Winter peak window (Mon-Fri 17:00-21:00)."""
        winter_tue = datetime(2026, 1, 13, 0, 0, tzinfo=ATHENS_TZ)
        vectors = adapter.get_hourly_price_vector(winter_tue, horizon_hours=24)
        for h, v in enumerate(vectors):
            if 17 <= h < 21:
                assert v.is_peak_window is True
            else:
                assert v.is_peak_window is False

    def test_greek_power_factor_penalty(self, adapter: GreekMarketAdapter) -> None:
        """Verifies low power factor (0.68) increases DEDDIE distribution rate by +25%."""
        start = utc_dt(2026, 7, 15, 0, 0)

        contract_normal = type("FacilityContract", (), {
            "contract_type": TariffContract.G22,
            "contracted_capacity_kva": 50.0,
            "power_factor": 0.95,
        })()
        vectors_normal = adapter.get_hourly_price_vector(start, horizon_hours=24, facility_contract=contract_normal)

        contract_low_pf = type("FacilityContract", (), {
            "contract_type": TariffContract.G22,
            "contracted_capacity_kva": 50.0,
            "power_factor": 0.68,
        })()
        vectors_low_pf = adapter.get_hourly_price_vector(start, horizon_hours=24, facility_contract=contract_low_pf)

        # Base DEDDIE rate = 0.01415. With pf=0.68, mult=1.25 => 0.01769
        base_dist = vectors_normal[0].grid_distribution_rate
        penalized_dist = vectors_low_pf[0].grid_distribution_rate
        assert penalized_dist > base_dist
        assert abs(penalized_dist - round(base_dist * 1.25, 5)) < 1e-4

    def test_calculate_instantaneous_cost(self, adapter: GreekMarketAdapter) -> None:
        """Verifies delegation to calculate_realtime_cost."""
        ts = utc_dt(2026, 7, 15, 12, 0)
        contract = ContractProfile(contract_type=TariffContract.G21)
        res = adapter.calculate_instantaneous_cost(
            power_kw=20.0,
            energy_kwh_delta=0.5,
            timestamp=ts,
            facility_contract=contract,
            tea_eur_mwh=115.0,
        )
        assert res.running_cost_eur_per_h > 0.0
        assert res.incremental_cost_eur > 0.0

    def test_demand_capacity_limits(self, adapter: GreekMarketAdapter) -> None:
        contract = ContractProfile(contract_type=TariffContract.G22, contracted_capacity_kva=50.0)
        limits = adapter.get_demand_capacity_limits(utc_dt(2026, 7, 15, 12, 0), contract)
        assert limits.contracted_capacity_kw == 50.0
        assert limits.peak_demand_threshold_kw == round(50.0 * 0.85, 2)
        assert limits.allows_power_factor_penalty is True



# =============================================================================
# 3. Market Adapter Registry Tests (Requirement R4 / F7)
# =============================================================================

class TestMarketAdapterRegistry:
    def setup_method(self) -> None:
        register_default_adapters()

    def test_supported_zones(self) -> None:
        zones = MarketAdapterRegistry.list_supported_zones()
        assert "GR" in zones

    def test_resolve_by_canonical_and_alias(self) -> None:
        # Greek aliases
        ad_gr = MarketAdapterRegistry.get_adapter("GR")
        assert isinstance(ad_gr, GreekMarketAdapter)
        assert isinstance(MarketAdapterRegistry.get_adapter("GREECE"), GreekMarketAdapter)
        assert isinstance(MarketAdapterRegistry.get_adapter("gr"), GreekMarketAdapter)

    def test_unsupported_zone_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unsupported electricity bidding zone 'FR'"):
            MarketAdapterRegistry.get_adapter("FR")

    def test_is_zone_supported(self) -> None:
        assert MarketAdapterRegistry.is_zone_supported("GR") is True
        assert MarketAdapterRegistry.is_zone_supported("gr") is True
        assert MarketAdapterRegistry.is_zone_supported("XYZ") is False

    def test_get_metadata_without_instance(self) -> None:
        meta = MarketAdapterRegistry.get_metadata("GR")
        assert meta.country_code == "GR"
        assert meta.regulatory_body == "RAAEY"


# =============================================================================
# 4. Optimization Engine C_t Vector & SciPy MILP Solver Tests
# =============================================================================

class TestOptimizerIntegration:
    @pytest.mark.parametrize("zone", ["GR"])
    def test_c_t_vector_properties(self, zone: str) -> None:
        """Verifies that all adapters generate clean 24-element finite positive C_t vectors."""
        adapter = get_market_adapter(zone)
        start = utc_dt(2026, 7, 15, 0, 0)
        c_t = adapter.get_c_t_vector(start, horizon_hours=24)

        assert len(c_t) == 24
        assert all(isinstance(c, float) for c in c_t)
        assert all(c > 0.0 for c in c_t)
        assert all(not np.isnan(c) and not np.isinf(c) for c in c_t)

    @pytest.mark.parametrize("zone", ["GR"])
    def test_highs_milp_solver_optimization(self, zone: str) -> None:
        """
        Solves a 24-hour constrained load scheduling problem using SciPy HiGHS MILP solver:
            min sum_{t=0}^{23} C_t * P_t * dt
            s.t. sum_{t=0}^{23} P_t * dt = E_daily (120 kWh)
                 0 <= P_t <= P_max (15 kW)
        """
        adapter = get_market_adapter(zone)
        start = utc_dt(2026, 7, 15, 0, 0)
        c_t = adapter.get_c_t_vector(start, horizon_hours=24)

        T = 24
        dt = 1.0
        E_target = 120.0
        P_max = 15.0

        # Objective coefficients c = C_t * dt
        c = np.array(c_t) * dt

        # Equality constraint: sum P_t * dt = E_target
        A_eq = np.ones((1, T)) * dt
        b_l = np.array([E_target])
        b_u = np.array([E_target])
        constraints = LinearConstraint(A_eq, b_l, b_u)

        # Variable bounds: 0 <= P_t <= P_max
        integrality = np.zeros(T)  # Continuous variables
        bounds = (np.zeros(T), np.full(T, P_max))

        res = milp(c=c, integrality=integrality, bounds=bounds, constraints=constraints)

        assert res.success is True
        assert res.status == 0  # Optimal solution found
        assert abs(np.sum(res.x) - E_target) < 1e-4
        assert all(0.0 <= p <= P_max + 1e-5 for p in res.x)
        assert res.fun > 0.0  # Optimal electricity cost > 0
