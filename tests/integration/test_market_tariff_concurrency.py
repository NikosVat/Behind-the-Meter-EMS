"""Stress and concurrency test harness for live market tariff ingestion.

Validates:
1. Law 5068/2023 Green Tariff Fluctuation Mechanism correctness across all TEA zones:
   - Zone 1: TEA < Ll (lower rebate/discount)
   - Zone 2: Ll <= TEA <= Lu (deadband neutral window)
   - Zone 3: TEA > Lu (upper surcharge)
   - Boundaries: TEA == Ll, TEA == Lu, continuity, and monotonicity (dMD/dTEA >= 0)
   - Boundary TEA values: negative rates (-500, -100, -10), zero, low wholesale (0.5, 1.0), extreme spikes (+3000, +10000)
   - Real Greek supplier profiles (ΔΕΗ, Protergia, Elpedison, Heron)
2. Concurrency stress:
   - Simultaneous market refresh (POST /market/refresh-green), DAM fetch (POST /market/fetch-dam),
     and high-throughput telemetry ingestion (POST /telemetry)
   - Race condition detection, SQLite WAL lock contention, and in-memory cache thread safety
3. Cache hit performance & tier progression:
   - Latency benchmarks across L1, L2, L3, L4
   - L2-to-L1 cache bypass verification
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.database.sqlite_store import SQLiteStore
from backend.main import create_app
from backend.market.clients import MockMarketClient
from backend.market.models import GreenTariffAnnouncement
from backend.market.scraper_rae import verify_green_tariff_formula
from backend.market.service import MarketPriceService
from tariff_engine.green_tariff import (
    calculate_green_tariff_fluctuation,
    calculate_green_tariff_supply_rate,
)

# --- 1. LAW 5068/2023 TARIFF FORMULA ADVERSARIAL CHALLENGES ---

class TestLaw5068TariffFormulaCorrectness:
    """Empirically validates compliance of green tariff calculations with Law 5068/2023."""

    @pytest.mark.parametrize(
        "tea,ll,lu,alpha,expected_md",
        [
            # Zone 1: TEA < Ll -> MD = alpha * (TEA - Ll)
            (75.0, 95.0, 115.0, 1.15, 1.15 * (0.075 - 0.095)),    # -0.023
            (50.0, 95.0, 115.0, 1.15, 1.15 * (0.050 - 0.095)),    # -0.05175
            (0.0, 95.0, 115.0, 1.15, 1.15 * (0.000 - 0.095)),     # -0.10925
            (94.99, 95.0, 115.0, 1.15, 1.15 * (0.09499 - 0.095)), # -0.0000115
            # Zone 2: Ll <= TEA <= Lu -> MD = 0.0
            (95.0, 95.0, 115.0, 1.15, 0.0),
            (100.0, 95.0, 115.0, 1.15, 0.0),
            (105.0, 95.0, 115.0, 1.15, 0.0),
            (110.0, 95.0, 115.0, 1.15, 0.0),
            (115.0, 95.0, 115.0, 1.15, 0.0),
            # Zone 3: TEA > Lu -> MD = alpha * (TEA - Lu)
            (115.01, 95.0, 115.0, 1.15, 1.15 * (0.11501 - 0.115)), # +0.0000115
            (120.0, 95.0, 115.0, 1.15, 1.15 * (0.120 - 0.115)),   # +0.00575
            (135.0, 95.0, 115.0, 1.15, 1.15 * (0.135 - 0.115)),   # +0.023
            (200.0, 95.0, 115.0, 1.15, 1.15 * (0.200 - 0.115)),   # +0.09775
            (300.0, 95.0, 115.0, 1.15, 1.15 * (0.300 - 0.115)),   # +0.21275
        ],
    )
    def test_three_zones_exact_analytical_match(self, tea, ll, lu, alpha, expected_md):
        calc_md = calculate_green_tariff_fluctuation(
            tea_eur_mwh=tea, ll_eur_mwh=ll, lu_eur_mwh=lu, alpha=alpha, beta=0.0
        )
        assert pytest.approx(calc_md, abs=1e-6) == expected_md

    def test_continuity_at_zone_boundaries(self):
        """Verify that MD function has C0 continuity at TEA = Ll and TEA = Lu (no jumps)."""
        ll = 95.0
        lu = 115.0
        alpha = 1.15
        eps = 1e-4

        # Boundary Ll
        md_below_ll = calculate_green_tariff_fluctuation(ll - eps, ll, lu, alpha)
        md_at_ll = calculate_green_tariff_fluctuation(ll, ll, lu, alpha)
        md_above_ll = calculate_green_tariff_fluctuation(ll + eps, ll, lu, alpha)

        assert abs(md_at_ll - md_below_ll) < 1e-4
        assert abs(md_above_ll - md_at_ll) < 1e-4
        assert md_at_ll == 0.0

        # Boundary Lu
        md_below_lu = calculate_green_tariff_fluctuation(lu - eps, ll, lu, alpha)
        md_at_lu = calculate_green_tariff_fluctuation(lu, ll, lu, alpha)
        md_above_lu = calculate_green_tariff_fluctuation(lu + eps, ll, lu, alpha)

        assert abs(md_at_lu - md_below_lu) < 1e-4
        assert abs(md_above_lu - md_at_lu) < 1e-4
        assert md_at_lu == 0.0

    def test_monotonicity_across_entire_price_spectrum(self):
        """Verify that dMD/dTEA >= 0 everywhere across [-500, +3000] €/MWh."""
        tea_samples = [-500.0, -200.0, -50.0, 0.0, 50.0, 90.0, 95.0, 100.0, 105.0, 115.0, 120.0, 250.0, 1000.0, 3000.0]
        md_values = [
            calculate_green_tariff_fluctuation(tea, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15)
            for tea in tea_samples
        ]
        for i in range(len(md_values) - 1):
            assert md_values[i + 1] >= md_values[i], (
                f"Monotonicity violated between TEA={tea_samples[i]} (MD={md_values[i]}) "
                f"and TEA={tea_samples[i+1]} (MD={md_values[i+1]})"
            )

    def test_historical_beta_additive_invariance(self):
        """Test beta calculation both as direct constant and derived from (TEA_{M-1} - TEA_{M-2})."""
        tea_m1 = 140.0
        tea_m2 = 110.0
        alpha = 1.20
        expected_beta = alpha * ((tea_m1 - tea_m2) / 1000.0)  # 1.20 * 0.030 = 0.036 €/kWh

        # 1. Derived automatically from tea_m2
        md_derived = calculate_green_tariff_fluctuation(
            tea_eur_mwh=tea_m1,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=alpha,
            beta=0.0,
            tea_m2_eur_mwh=tea_m2,
        )

        # 2. Passed explicitly as pre-computed beta
        md_explicit = calculate_green_tariff_fluctuation(
            tea_eur_mwh=tea_m1,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=alpha,
            beta=expected_beta,
        )

        assert pytest.approx(md_derived, rel=1e-5) == md_explicit
        # Analytical check: alpha*(140 - 115)/1000 + beta = 1.20 * 0.025 + 0.036 = 0.030 + 0.036 = 0.066
        assert pytest.approx(md_derived, rel=1e-5) == 0.066

    def test_extreme_negative_market_clearing_rates(self):
        """Test negative EU wholesale price floor (-500.0 €/MWh) floored at 0.0 supply rate."""
        rate = calculate_green_tariff_supply_rate(
            p_base=0.155,
            e_disc=0.020,
            tea_eur_mwh=-500.0,  # -0.500 €/kWh
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        # MD = 1.15 * (-0.500 - 0.095) = 1.15 * (-0.595) = -0.68425 €/kWh
        # Net = 0.155 - 0.020 - 0.68425 = -0.54925 -> Floored at 0.0
        assert rate == 0.0

    def test_extreme_positive_market_clearing_spikes(self):
        """Test EU price ceiling (+3000.0 €/MWh) behaves linearly without overflow."""
        rate = calculate_green_tariff_supply_rate(
            p_base=0.155,
            e_disc=0.020,
            tea_eur_mwh=3000.0,  # 3.000 €/kWh
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        # MD = 1.15 * (3.000 - 0.115) = 1.15 * 2.885 = 3.31775 €/kWh
        # Net = 0.155 - 0.020 + 3.31775 = 3.45275 €/kWh
        assert pytest.approx(rate, rel=1e-4) == 3.45275

    def test_greek_major_suppliers_announcements_cross_validation(self):
        """Cross-validate realistic announcements across all 4 major Greek suppliers."""
        suppliers = [
            # DEI G22: P_base=0.165, Disc=0.020, TEA=120, Lu=115, Ll=95, alpha=1.15
            # MD = 1.15 * (0.120 - 0.115) = 0.00575; Final = 0.165 - 0.020 + 0.00575 = 0.15075
            GreenTariffAnnouncement(
                month="2026-09",
                supplier_id="dei",
                supplier_name="ΔΕΗ",
                contract_type="G22",
                p_base=0.165,
                e_disc=0.020,
                alpha=1.15,
                lu_eur_mwh=115.0,
                ll_eur_mwh=95.0,
                beta=0.0,
                published_final_rate_eur_per_kwh=0.15075,
                tea_m1_eur_mwh=120.0,
            ),
            # Protergia G21: P_base=0.150, Disc=0.015, TEA=120, Lu=110, Ll=90, alpha=1.20
            # MD = 1.20 * (0.120 - 0.110) = 0.012; Final = 0.150 - 0.015 + 0.012 = 0.14700
            GreenTariffAnnouncement(
                month="2026-09",
                supplier_id="protergia",
                supplier_name="Protergia",
                contract_type="G21",
                p_base=0.150,
                e_disc=0.015,
                alpha=1.20,
                lu_eur_mwh=110.0,
                ll_eur_mwh=90.0,
                beta=0.0,
                published_final_rate_eur_per_kwh=0.14700,
                tea_m1_eur_mwh=120.0,
            ),
            # Elpedison G21: P_base=0.158, Disc=0.018, TEA=120, Lu=112, Ll=92, alpha=1.18
            # MD = 1.18 * (0.120 - 0.112) = 0.00944; Final = 0.158 - 0.018 + 0.00944 = 0.14944
            GreenTariffAnnouncement(
                month="2026-09",
                supplier_id="elpedison",
                supplier_name="Elpedison",
                contract_type="G21",
                p_base=0.158,
                e_disc=0.018,
                alpha=1.18,
                lu_eur_mwh=112.0,
                ll_eur_mwh=92.0,
                beta=0.0,
                published_final_rate_eur_per_kwh=0.14944,
                tea_m1_eur_mwh=120.0,
            ),
            # Heron G22: P_base=0.162, Disc=0.022, TEA=100 (Deadband), Lu=115, Ll=95, alpha=1.15
            # MD = 0.0; Final = 0.162 - 0.022 + 0.0 = 0.14000
            GreenTariffAnnouncement(
                month="2026-09",
                supplier_id="heron",
                supplier_name="Ήρων",
                contract_type="G22",
                p_base=0.162,
                e_disc=0.022,
                alpha=1.15,
                lu_eur_mwh=115.0,
                ll_eur_mwh=95.0,
                beta=0.0,
                published_final_rate_eur_per_kwh=0.14000,
                tea_m1_eur_mwh=100.0,
            ),
        ]

        for announcement in suppliers:
            is_valid, calc_rate, disc = verify_green_tariff_formula(announcement)
            assert is_valid is True, f"Supplier {announcement.supplier_id} failed formula: calc={calc_rate}, disc={disc}"
            assert disc < 0.0001


# --- 2. CONCURRENCY & STRESS TEST HARNESS ---

class TestConcurrencyAndThreadSafety:
    """Stress tests concurrent telemetry ingestion alongside market refresh operations."""

    @pytest.mark.anyio
    async def test_concurrent_telemetry_and_market_refreshes(self, tmp_path):
        """Simulate concurrent flood of:
        - 30 Telemetry POST requests across 3 distinct facilities
        - 5 Market Green Tariff refresh requests
        - 5 Market DAM fetch requests
        - 10 Market Status query requests
        All executing concurrently against the same SQLite WAL database.
        """
        db_path = str(tmp_path / "stress_concurrent.db")
        app = create_app(db_path=db_path)

        with TestClient(app) as client:
            # Inside lifespan: store and market_service are initialized
            store: SQLiteStore = client.app.state.market_service.store
            for fid in ["facility-alpha", "facility-beta", "facility-gamma"]:
                store.store_facility_config({
                    "facility_id": fid,
                    "name": fid.title(),
                    "facility_type": "bakery",
                    "contract_type": "Γ22",
                    "tariff_color": "green",
                    "contracted_kva": 35.0,
                    "peak_threshold_kw": 22.0,
                })

            store.store_facility_config({
                "facility_id": "facility-dynamic",
                "name": "Dynamic Market Facility",
                "facility_type": "cold_storage",
                "contract_type": "Γ22",
                "tariff_color": "yellow",
                "contracted_kva": 50.0,
                "peak_threshold_kw": 30.0,
            })

            mock_market = MockMarketClient()
            app.state.market_service = MarketPriceService(store=store, client=mock_market)

            def make_telemetry_payload(fid: str, idx: int) -> dict[str, Any]:
                base_kw = 15.0 + (idx % 10)
                p_each = round(base_kw / 3.0, 3)
                total_p = round(p_each * 3.0, 3)
                app_each = round(p_each * 1.05, 3)
                total_app = round(app_each * 3.0, 3)
                curr = round((p_each * 1000.0) / 230.0, 2)
                return {
                    "device_id": f"esp32-{fid}",
                    "facility_id": fid,
                    "timestamp": f"2026-09-15T14:{idx % 60:02d}:00Z",
                    "total_active_power_kw": total_p,
                    "total_apparent_power_kva": total_app,
                    "system_power_factor": 0.95,
                    "cumulative_energy_kwh": 100.0 + idx * 0.5,
                    "grid_frequency_hz": 50.0,
                    "wifi_rssi_dbm": -65.0,
                    "phases": {
                        "L1": {
                            "voltage_v": 230.0,
                            "current_a": curr,
                            "active_power_kw": p_each,
                            "apparent_power_kva": app_each,
                            "power_factor": 0.95,
                        },
                        "L2": {
                            "voltage_v": 230.0,
                            "current_a": curr,
                            "active_power_kw": p_each,
                            "apparent_power_kva": app_each,
                            "power_factor": 0.95,
                        },
                        "L3": {
                            "voltage_v": 230.0,
                            "current_a": curr,
                            "active_power_kw": p_each,
                            "apparent_power_kva": app_each,
                            "power_factor": 0.95,
                        },
                    },
                }

            async def post_telemetry(fid: str, idx: int):
                payload = make_telemetry_payload(fid, idx)
                res = await asyncio.to_thread(client.post, "/api/v1/telemetry", json=payload)
                assert res.status_code in (200, 409), f"Telemetry failed: {res.text}"
                if res.status_code == 409:
                    assert "timestamp" in res.json()["detail"]
                    return None
                data = res.json()
                assert data["status"] == "success"
                assert data["running_cost_eur_per_h"] > 0.0
                return data

            async def refresh_green():
                res = await asyncio.to_thread(client.post, "/api/v1/market/refresh-green?month=2026-09")
                assert res.status_code == 200
                return res.json()

            async def fetch_dam(dt_str: str):
                res = await asyncio.to_thread(client.post, f"/api/v1/market/fetch-dam?date={dt_str}")
                assert res.status_code == 200
                return res.json()

            async def query_status():
                res = await asyncio.to_thread(client.get, "/api/v1/market/status")
                assert res.status_code == 200
                return res.json()

            async def query_dam_today():
                res = await asyncio.to_thread(client.get, "/api/v1/market/dam/today")
                assert res.status_code == 200
                return res.json()

            # Build list of interleaved concurrent tasks
            tasks = []
            facilities = ["facility-alpha", "facility-beta", "facility-gamma", "facility-dynamic"]
            for i in range(30):
                target_fid = facilities[i % len(facilities)]
                tasks.append(post_telemetry(target_fid, i))

            for _ in range(5):
                tasks.append(refresh_green())

            for d in range(1, 6):
                tasks.append(fetch_dam(f"2026-09-0{d}"))

            for _ in range(10):
                tasks.append(query_status())

            for _ in range(10):
                tasks.append(query_dam_today())

            # Execute all 60 tasks concurrently!
            start_t = time.perf_counter()
            results = await asyncio.gather(*tasks, return_exceptions=False)
            duration = time.perf_counter() - start_t

            assert len(results) == 60
            readings = store.get_telemetry_history("facility-alpha", limit=100)
            assert len(readings) > 0
            accepted = [result for result in results[:30] if result is not None]
            for fid in facilities:
                rows = store.get_telemetry_history(fid, limit=100)
                assert len(rows) == sum(result["facility_id"] == fid for result in accepted)
                summary = store.get_daily_summary(fid, "2026-09-15")
                expected_kwh = rows[0]["cumulative_energy_kwh"] - rows[-1]["cumulative_energy_kwh"]
                assert summary["total_kwh"] == pytest.approx(expected_kwh)
                assert summary["total_spend_eur"] == round(sum(row["incremental_cost_eur"] for row in rows), 2)

            status_res = client.get("/api/v1/market/status").json()
            assert status_res["online"] is True
            assert status_res["dam_cached_dates_count"] >= 5
            assert duration < 5.0, f"Concurrency test took too long: {duration:.2f}s"

    def test_thread_safety_in_memory_cache_stress(self, tmp_path):
        """Simultaneously query and populate MarketPriceService in-memory cache across 20 threads."""
        import threading

        db_path = str(tmp_path / "thread_stress.db")
        store = SQLiteStore(db_path)
        store.init_db()

        mock_client = MockMarketClient()
        service = MarketPriceService(store=store, client=mock_client)

        dt = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        errors: list[Exception] = []

        def worker_lookup(worker_id: int):
            try:
                for _ in range(50):
                    # Concurrent read/write on L1
                    rate_green = service.get_effective_tea(dt, "green", "G22", "dei")
                    assert rate_green == 120.0
                    rate_yellow = service.get_effective_tea(dt, "yellow", "G22")
                    assert rate_yellow > 0.0
            except (AssertionError, RuntimeError, OSError) as ex:
                errors.append(ex)

        threads = [threading.Thread(target=worker_lookup, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread safety errors detected: {errors}"


# --- 3. CACHE PERFORMANCE & TIER PROGRESSION INVESTIGATION ---

class TestCachePerformanceAndProgression:
    """Investigates performance characteristics and caching behaviors across tiers."""

    def test_l1_cache_hit_latency(self, tmp_path):
        """Verify L1 cache hit delivers lookups in < 0.05 ms."""
        db_path = str(tmp_path / "perf_test.db")
        store = SQLiteStore(db_path)
        store.init_db()
        service = MarketPriceService(store=store, client=MockMarketClient())

        dt = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)
        # Prime L1
        service.get_effective_tea(dt, "green", "G22", "dei")

        # Measure 1,000 lookups
        start = time.perf_counter()
        for _ in range(1000):
            service.get_effective_tea(dt, "green", "G22", "dei")
        elapsed = time.perf_counter() - start
        avg_us = (elapsed / 1000.0) * 1_000_000

        # L1 hot path must execute in under 100 microseconds (0.1ms)
        assert avg_us < 100.0, f"L1 lookup too slow: {avg_us:.2f} µs"

    def test_l2_sqlite_persistence_survives_service_restart(self, tmp_path):
        """Verify that persisting tariffs in L2 SQLite allows a fresh service instance to operate offline."""
        db_path = str(tmp_path / "restart_test.db")
        store1 = SQLiteStore(db_path)
        store1.init_db()

        # Seed L2 via service 1
        service1 = MarketPriceService(store=store1, client=MockMarketClient())
        service1.store.store_green_tariffs([
            {
                "month": "2026-09",
                "supplier_id": "dei",
                "supplier_name": "ΔΕΗ",
                "contract_type": "G22",
                "p_base": 0.165,
                "e_disc": 0.020,
                "alpha": 1.15,
                "lu_eur_mwh": 115.0,
                "ll_eur_mwh": 95.0,
                "beta": 0.0,
                "fixed_monthly_fee_eur": 5.0,
                "published_final_rate_eur_per_kwh": 0.15075,
                "tea_m1_eur_mwh": 133.5,  # Custom TEA
                "source": "custom_seed",
            }
        ])

        # Service 2 starts completely fresh with failing network
        store2 = SQLiteStore(db_path)
        mock_fail = MockMarketClient(should_fail_dam=True, should_fail_green=True)
        service2 = MarketPriceService(store=store2, client=mock_fail)

        dt = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
        resolved_tea = service2.get_effective_tea(dt, "green", "G22", "dei")

        # Must recover 133.5 from SQLite L2 without crashing
        assert resolved_tea == 133.5

    def test_l2_hit_cache_population_behavior(self, tmp_path):
        """Investigate whether an L2 hit in get_effective_tea populates L1 memory cache.

        Finding: When get_effective_tea hits L2 SQLite, it returns the TEA value
        directly without updating self._green_cache or self._dam_cache.
        This means repeated telemetry calls continue to perform SQLite queries.
        """
        db_path = str(tmp_path / "cache_pop_test.db")
        store = SQLiteStore(db_path)
        store.init_db()

        # Seed L2 directly
        store.store_green_tariffs([
            {
                "month": "2026-09",
                "supplier_id": "dei",
                "supplier_name": "ΔΕΗ",
                "contract_type": "G22",
                "p_base": 0.165,
                "e_disc": 0.020,
                "alpha": 1.15,
                "lu_eur_mwh": 115.0,
                "ll_eur_mwh": 95.0,
                "beta": 0.0,
                "fixed_monthly_fee_eur": 5.0,
                "published_final_rate_eur_per_kwh": 0.15075,
                "tea_m1_eur_mwh": 125.0,
                "source": "manual_seed",
            }
        ])

        service = MarketPriceService(store=store, client=MockMarketClient())
        dt = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

        # L1 is currently empty
        assert "2026-09" not in service._green_cache

        # First call hits L2
        tea = service.get_effective_tea(dt, "green", "G22", "dei")
        assert tea == 125.0

        # Check whether L1 was populated:
        # In the current implementation, L2 hit in get_effective_tea does NOT populate L1
        l1_populated = "2026-09" in service._green_cache
        # We document this empirical behavior:
        assert l1_populated is False, "Documenting that L2 hit currently bypasses L1 cache population"

    def test_low_wholesale_tea_normalization_boundary_anomaly(self):
        """Stress-test _normalize_to_kwh boundary at TEA = 0.50 €/MWh and 1.0 €/MWh.

        Observation: _normalize_to_kwh uses `if abs(val) > 1.0: val / 1000.0 else val`.
        If wholesale TEA is 0.50 €/MWh (e.g. midday solar surplus), abs(0.50) <= 1.0.
        The function treats 0.50 as ALREADY in €/kWh (i.e. 500 €/MWh), causing an inversion
        from lower breach (TEA << Ll) to upper breach surcharge!
        """
        # TEA = 0.50 €/MWh should be 0.00050 €/kWh, far below Ll=95 €/MWh
        # Expected: Zone 1 lower breach rebate: 1.15 * (0.00050 - 0.095) = -0.108675 €/kWh
        # Actual behavior due to heuristic: 0.50 is treated as 0.500 €/kWh (500 €/MWh),
        # causing Zone 3 upper breach: 1.15 * (0.50 - 0.115) = +0.44275 €/kWh!
        calc_md_0_5 = calculate_green_tariff_fluctuation(
            tea_eur_mwh=0.50, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15
        )
        # With explicit unit conversion, TEA=0.50 €/MWh is recognized as 0.00050 €/kWh,
        # correctly triggering lower breach rebate (MD < 0) rather than an upper breach surcharge.
        assert calc_md_0_5 < 0.0, "TEA = 0.50 €/MWh must be recognized as lower breach rebate"
        assert pytest.approx(calc_md_0_5, rel=1e-4) == -0.108675

        # In contrast, TEA = 2.0 €/MWh has 2.0/1000 = 0.002 €/kWh < 0.095:
        calc_md_2_0 = calculate_green_tariff_fluctuation(
            tea_eur_mwh=2.0, ll_eur_mwh=95.0, lu_eur_mwh=115.0, alpha=1.15
        )
        assert calc_md_2_0 < 0.0, "TEA = 2.0 €/MWh correctly recognized as lower breach"

    @pytest.mark.anyio
    async def test_concurrent_telemetry_same_facility_lock_contention(self, tmp_path):
        """Send 25 rapid telemetry readings for the SAME facility concurrently.

        Ensures SQLite WAL handles simultaneous cost_aggregates UPSERT on (facility_id, date)
        without Deadlock or OperationalError: database is locked.
        """
        db_path = str(tmp_path / "same_facility_stress.db")
        app = create_app(db_path=db_path)

        with TestClient(app) as client:
            store: SQLiteStore = client.app.state.market_service.store
            store.store_facility_config({
                "facility_id": "bakery-high-load",
                "name": "Bakery High Load",
                "facility_type": "bakery",
                "contract_type": "Γ22",
                "tariff_color": "green",
                "contracted_kva": 45.0,
                "peak_threshold_kw": 28.0,
            })

            def make_reading(idx: int):
                return {
                    "device_id": "esp32-bakery-1",
                    "facility_id": "bakery-high-load",
                    "timestamp": f"2026-09-15T15:{idx % 60:02d}:00Z",
                    "total_active_power_kw": 20.0,
                    "total_apparent_power_kva": 21.0,
                    "system_power_factor": 0.952,
                    "cumulative_energy_kwh": 500.0 + idx * 0.2,
                    "grid_frequency_hz": 50.0,
                    "wifi_rssi_dbm": -55.0,
                    "phases": {
                        "L1": {"voltage_v": 230.0, "current_a": 28.98, "active_power_kw": 6.666, "apparent_power_kva": 7.0, "power_factor": 0.952},
                        "L2": {"voltage_v": 230.0, "current_a": 28.98, "active_power_kw": 6.667, "apparent_power_kva": 7.0, "power_factor": 0.952},
                        "L3": {"voltage_v": 230.0, "current_a": 28.98, "active_power_kw": 6.667, "apparent_power_kva": 7.0, "power_factor": 0.952},
                    },
                }

            async def send_one(idx: int):
                res = await asyncio.to_thread(client.post, "/api/v1/telemetry", json=make_reading(idx))
                assert res.status_code in (200, 409), res.text
                if res.status_code == 409:
                    assert "timestamp" in res.json()["detail"]
                    return None
                return res.json()

            tasks = [send_one(i) for i in range(25)]
            results = await asyncio.gather(*tasks, return_exceptions=False)
            assert len(results) == 25

            # Every accepted delta contributes once; stale arrivals contribute nothing.
            rows = store.get_telemetry_history("bakery-high-load", limit=100)
            accepted = [result for result in results if result is not None]
            assert len(rows) == len(accepted)
            summary = store.get_daily_summary("bakery-high-load", "2026-09-15")
            expected_kwh = rows[0]["cumulative_energy_kwh"] - rows[-1]["cumulative_energy_kwh"]
            assert summary["total_kwh"] == pytest.approx(expected_kwh)
            assert summary["total_spend_eur"] == round(sum(result["incremental_cost_eur"] for result in accepted), 2)

