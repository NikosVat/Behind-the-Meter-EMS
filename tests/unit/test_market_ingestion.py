"""Unit tests for Greek Energy Market & Tariff Ingestion Engine (Milestone E1 / R1).

Covers:
- RAE monthly Green Tariff HTML/JSON parser and supplier normalization
- Green tariff MD fluctuation mechanism cross-validation
- HEnEx Day-Ahead Market 24-hour hourly clearing price parsing (CSV and JSON)
- Daylight Saving Time (DST) transitions (23h March, 25h October)
- Price sanity boundary checks (-500.0 to 3000.0 €/MWh)
- 4-Tier caching & offline fallback progression (L1 -> L2 -> L3 -> L4)
- Direct tariff engine market feed adapter resolution
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.database.sqlite_store import SQLiteStore
from backend.market.clients import MockMarketClient
from backend.market.fetcher_henex import (
    is_dst_transition_day,
    parse_henex_csv,
    parse_henex_json,
    validate_dam_prices,
)
from backend.market.models import DamHourlyPrice, GreenTariffAnnouncement
from backend.market.scraper_rae import (
    normalize_supplier,
    parse_float_gr,
    parse_rae_html,
    parse_rae_json,
    verify_green_tariff_formula,
)
from backend.market.service import MarketPriceService
from tariff_engine.market_feed import resolve_effective_tea

# --- RAE Scraper & Formula Validation Unit Tests ---

class TestRaeScraperAndParser:
    """Test RAE monthly Green Tariff scraping and parser routines."""

    def test_normalize_supplier_names(self):
        assert normalize_supplier("ΔΕΗ")[0] == "dei"
        assert normalize_supplier("PPC Renewables")[0] == "dei"
        assert normalize_supplier("ΠΡΟΤΕΡΓΙΑ")[0] == "protergia"
        assert normalize_supplier("METLEN Energy")[0] == "protergia"
        assert normalize_supplier("ΕΛΠΕΔΙΣΟΝ Α.Ε.")[0] == "elpedison"
        assert normalize_supplier("ΗΡΩΝ ΕΝΕΡΓΕΙΑΚΗ")[0] == "heron"
        assert normalize_supplier("ZeniΘ")[0] == "zenith"
        assert normalize_supplier("Custom Solar Ltd")[0] == "custom_solar_ltd"

    def test_parse_float_greek_formats(self):
        assert parse_float_gr("0,155") == 0.155
        assert parse_float_gr("0.155") == 0.155
        assert parse_float_gr(" 120,50 €/MWh ") == 120.50
        assert parse_float_gr("12,5%") == 12.5
        assert parse_float_gr(None, default=5.0) == 5.0
        assert parse_float_gr("invalid", default=0.0) == 0.0

    def test_parse_rae_html_table(self):
        html_sample = """
        <html>
        <body>
            <table class="tariffs-table">
                <thead>
                    <tr>
                        <th>Προμηθευτής</th>
                        <th>Τύπος</th>
                        <th>Βασική Χρέωση (€/kWh)</th>
                        <th>Έκπτωση (€/kWh)</th>
                        <th>Τελική Χρέωση (€/kWh)</th>
                        <th>α</th>
                        <th>Lu</th>
                        <th>Ll</th>
                        <th>β</th>
                        <th>Πάγιο (€)</th>
                        <th>TEA (M-1)</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>ΔΕΗ</td>
                        <td>Γ22</td>
                        <td>0,165</td>
                        <td>0,020</td>
                        <td>0,15075</td>
                        <td>1,15</td>
                        <td>115,0</td>
                        <td>95,0</td>
                        <td>0,0</td>
                        <td>5,00</td>
                        <td>120,0</td>
                    </tr>
                    <tr>
                        <td>Protergia</td>
                        <td>Γ21</td>
                        <td>0,150</td>
                        <td>0,015</td>
                        <td>0,14700</td>
                        <td>1,20</td>
                        <td>110,0</td>
                        <td>90,0</td>
                        <td>0,0</td>
                        <td>4,50</td>
                        <td>120,0</td>
                    </tr>
                </tbody>
            </table>
        </body>
        </html>
        """
        tariffs = parse_rae_html(html_sample, "2026-09")
        assert len(tariffs) == 2

        dei_g22 = next(t for t in tariffs if t.supplier_id == "dei")
        assert dei_g22.contract_type == "G22"
        assert dei_g22.p_base == 0.165
        assert dei_g22.e_disc == 0.020
        assert dei_g22.published_final_rate_eur_per_kwh == 0.15075
        assert dei_g22.alpha == 1.15
        assert dei_g22.fixed_monthly_fee_eur == 5.00

        protergia_g21 = next(t for t in tariffs if t.supplier_id == "protergia")
        assert protergia_g21.contract_type == "G21"
        assert protergia_g21.p_base == 0.150
        assert protergia_g21.fixed_monthly_fee_eur == 4.50

    def test_parse_rae_json_feed(self):
        json_data = {
            "tariffs": [
                {
                    "supplier_name": "ΔΕΗ",
                    "contract_type": "G21",
                    "p_base": 0.155,
                    "e_disc": 0.020,
                    "published_final_rate_eur_per_kwh": 0.14075,
                    "alpha": 1.15,
                    "lu_eur_mwh": 115.0,
                    "ll_eur_mwh": 95.0,
                    "beta": 0.0,
                    "fixed_monthly_fee_eur": 5.0,
                    "tea_m1_eur_mwh": 120.0,
                }
            ]
        }
        tariffs = parse_rae_json(json_data, "2026-09")
        assert len(tariffs) == 1
        assert tariffs[0].supplier_id == "dei"
        assert tariffs[0].contract_type == "G21"
        assert tariffs[0].published_final_rate_eur_per_kwh == 0.14075

    def test_verify_green_tariff_formula_passes(self):
        # Green tariff MD: 120 > 115 => MD = 1.15 * (0.120 - 0.115) = 0.00575 €/kWh
        # Final = 0.155 - 0.020 + 0.00575 = 0.14075 €/kWh
        announcement = GreenTariffAnnouncement(
            month="2026-09",
            supplier_id="dei",
            supplier_name="ΔΕΗ",
            contract_type="G21",
            p_base=0.155,
            e_disc=0.020,
            alpha=1.15,
            lu_eur_mwh=115.0,
            ll_eur_mwh=95.0,
            beta=0.0,
            fixed_monthly_fee_eur=5.0,
            published_final_rate_eur_per_kwh=0.14075,
            tea_m1_eur_mwh=120.0,
        )
        is_valid, calc_p, disc = verify_green_tariff_formula(announcement)
        assert is_valid is True
        assert disc < 0.0001
        assert calc_p == 0.14075

    def test_verify_green_tariff_formula_detects_tampered_rate(self):
        bad_announcement = GreenTariffAnnouncement(
            month="2026-09",
            supplier_id="dei",
            supplier_name="ΔΕΗ",
            contract_type="G21",
            p_base=0.155,
            e_disc=0.020,
            alpha=1.15,
            lu_eur_mwh=115.0,
            ll_eur_mwh=95.0,
            beta=0.0,
            published_final_rate_eur_per_kwh=0.25000,  # Intentional discrepancy (+0.11 €/kWh)
            tea_m1_eur_mwh=120.0,
        )
        is_valid, _calc_p, disc = verify_green_tariff_formula(bad_announcement)
        assert is_valid is False
        assert disc > 0.10


# --- HEnEx DAM Fetcher & DST Unit Tests ---

class TestHenexFetcherAndValidation:
    """Test HEnEx Day-Ahead Market parser, DST transitions, and boundary checks."""

    def test_parse_henex_csv(self):
        csv_data = "Hour,MCP_EUR_MWh\n" + "\n".join(f"{h},{80.0 + h * 2.5}" for h in range(24))
        prices = parse_henex_csv(csv_data, "2026-09-14")
        assert len(prices) == 24
        assert prices[0].hour == 0
        assert prices[0].price_eur_mwh == 80.0
        assert prices[0].price_eur_kwh == 0.080
        assert prices[23].hour == 23
        assert prices[23].price_eur_mwh == 80.0 + 23 * 2.5

    def test_parse_henex_json(self):
        json_payload = {
            "prices": [{"hour": h, "price_eur_mwh": 100.0 + h} for h in range(24)]
        }
        prices = parse_henex_json(json_payload, "2026-09-14")
        assert len(prices) == 24
        assert prices[10].hour == 10
        assert prices[10].price_eur_mwh == 110.0

    def test_dst_transition_day_identification(self):
        # 2026-03-29 is the last Sunday of March 2026 (Spring DST: 23 hours)
        is_dst_spring, hours_spring = is_dst_transition_day("2026-03-29")
        assert is_dst_spring is True
        assert hours_spring == 23

        # 2026-10-25 is the last Sunday of October 2026 (Autumn DST: 25 hours)
        is_dst_autumn, hours_autumn = is_dst_transition_day("2026-10-25")
        assert is_dst_autumn is True
        assert hours_autumn == 25

        # Normal weekday or non-transition Sunday
        is_dst_norm, hours_norm = is_dst_transition_day("2026-09-15")
        assert is_dst_norm is False
        assert hours_norm == 24

    def test_validate_dam_prices_normal_24h(self):
        prices = [
            DamHourlyPrice(date="2026-09-15", hour=h, price_eur_mwh=110.0, price_eur_kwh=0.11)
            for h in range(24)
        ]
        assert validate_dam_prices(prices, "2026-09-15") is True

    def test_validate_dam_prices_spring_dst_23h(self):
        prices_23 = [
            DamHourlyPrice(date="2026-03-29", hour=h, price_eur_mwh=110.0, price_eur_kwh=0.11)
            for h in range(23)
        ]
        assert validate_dam_prices(prices_23, "2026-03-29") is True

        # Rejecting 24 hours on 23h DST date
        prices_24 = [
            DamHourlyPrice(date="2026-03-29", hour=h, price_eur_mwh=110.0, price_eur_kwh=0.11)
            for h in range(24)
        ]
        assert validate_dam_prices(prices_24, "2026-03-29") is False

    def test_validate_dam_prices_autumn_dst_25h(self):
        prices_25 = [
            DamHourlyPrice(date="2026-10-25", hour=h, price_eur_mwh=110.0, price_eur_kwh=0.11)
            for h in range(25)
        ]
        assert validate_dam_prices(prices_25, "2026-10-25") is True

    def test_validate_dam_prices_out_of_bounds_rejected(self):
        # Less than EU market floor (-500 €/MWh)
        bad_prices_low = [
            DamHourlyPrice(date="2026-09-15", hour=h, price_eur_mwh=100.0, price_eur_kwh=0.1)
            for h in range(24)
        ]
        bad_prices_low[5] = DamHourlyPrice(
            date="2026-09-15", hour=5, price_eur_mwh=-550.0, price_eur_kwh=-0.55
        )
        assert validate_dam_prices(bad_prices_low, "2026-09-15") is False

        # Greater than EU market cap (+3000 €/MWh)
        bad_prices_high = [
            DamHourlyPrice(date="2026-09-15", hour=h, price_eur_mwh=100.0, price_eur_kwh=0.1)
            for h in range(24)
        ]
        bad_prices_high[18] = DamHourlyPrice(
            date="2026-09-15", hour=18, price_eur_mwh=3500.0, price_eur_kwh=3.5
        )
        assert validate_dam_prices(bad_prices_high, "2026-09-15") is False


# --- 4-Tier Caching & Fallback Progression Tests ---

class TestFourTierCachingAndFallbackProgression:
    """Test L1 Memory -> L2 SQLite -> L3 JSON Seed -> L4 Algorithmic progression."""

    @pytest.mark.anyio
    async def test_tier1_in_memory_cache_hit(self, tmp_path):
        db_path = str(tmp_path / "test_tier1.db")
        store = SQLiteStore(db_path)
        store.init_db()

        mock_client = MockMarketClient()
        service = MarketPriceService(store=store, client=mock_client)

        # Initial fetch populates L1 and L2
        summary1 = await service.get_dam_curve("2026-09-15")
        assert len(summary1.prices) == 24
        assert len(mock_client.dam_calls) == 1

        # Second fetch hits L1 in-memory cache without calling remote client
        summary2 = await service.get_dam_curve("2026-09-15")
        assert len(summary2.prices) == 24
        assert len(mock_client.dam_calls) == 1  # No extra network call

    @pytest.mark.anyio
    async def test_tier2_sqlite_persistence_after_l1_clear(self, tmp_path):
        db_path = str(tmp_path / "test_tier2.db")
        store = SQLiteStore(db_path)
        store.init_db()

        mock_client = MockMarketClient()
        service = MarketPriceService(store=store, client=mock_client)

        # Populate SQLite
        await service.get_dam_curve("2026-09-15")
        assert len(mock_client.dam_calls) == 1

        # Clear L1 memory cache and simulate network failure
        service.clear_l1_cache()
        mock_client.should_fail_dam = True

        # Must successfully recover 24h curve from L2 SQLite
        summary_l2 = await service.get_dam_curve("2026-09-15")
        assert len(summary_l2.prices) == 24
        assert service.dam_source_mode == "cached"
        # Network call was never attempted because L2 satisfied query
        assert len(mock_client.dam_calls) == 1

    @pytest.mark.anyio
    async def test_tier3_bundled_seed_fallback_when_remote_fails(self, tmp_path):
        db_path = str(tmp_path / "test_tier3.db")
        store = SQLiteStore(db_path)
        store.init_db()

        # Remote network fails completely on fresh date
        mock_client = MockMarketClient(should_fail_dam=True, should_fail_green=True)
        service = MarketPriceService(store=store, client=mock_client)

        # Query uncached date: falls back to L3 bundled seed
        summary_l3 = await service.get_dam_curve("2026-09-15")
        assert len(summary_l3.prices) == 24
        assert summary_l3.avg_price_eur_mwh > 50.0
        assert service.dam_source_mode == "fallback_seed"

        # Check Green Tariffs also fall back to L3
        green_l3 = await service.get_green_tariffs("2026-09")
        assert len(green_l3) >= 4
        assert any(t.supplier_id == "dei" for t in green_l3)
        assert service.green_source_mode == "fallback_seed"

    @pytest.mark.anyio
    async def test_tier4_algorithmic_default_when_seed_missing(self, tmp_path):
        db_path = str(tmp_path / "test_tier4.db")
        store = SQLiteStore(db_path)
        store.init_db()

        # Point to non-existent seed file path
        non_existent_seed = tmp_path / "empty_seed.json"
        mock_client = MockMarketClient(should_fail_dam=True, should_fail_green=True)
        service = MarketPriceService(store=store, client=mock_client, seed_path=non_existent_seed)

        # Uncached date with no seed file available -> triggers L4 synthetic curve
        summary_l4 = await service.get_dam_curve("2026-09-15")
        assert len(summary_l4.prices) == 24
        # Synthetic curve average is normalized to exactly 120.0 €/MWh
        assert abs(summary_l4.avg_price_eur_mwh - 120.0) < 0.1
        assert service.dam_source_mode == "fallback_synthetic"

    def test_get_effective_tea_synchronous_lookups(self, tmp_path):
        db_path = str(tmp_path / "test_eff_tea.db")
        store = SQLiteStore(db_path)
        store.init_db()

        service = MarketPriceService(store=store, client=MockMarketClient())
        dt = datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc)

        # Green tariff resolves TEA = 120.0
        tea_green = service.get_effective_tea(dt, tariff_color="green", contract_type="G22", supplier_id="dei")
        assert tea_green == 120.0

        # Yellow / Dynamic tariff resolves hourly MCP
        tea_yellow = service.get_effective_tea(dt, tariff_color="yellow", contract_type="G22")
        assert tea_yellow > 0.0


# --- Tariff Engine Adapter Tests ---

class TestTariffEngineMarketFeedAdapter:
    """Test resolve_effective_tea pure functional adapter in tariff_engine/market_feed.py."""

    def test_adapter_with_healthy_market_service(self, tmp_path):
        db_path = str(tmp_path / "test_adapter.db")
        store = SQLiteStore(db_path)
        store.init_db()
        service = MarketPriceService(store=store, client=MockMarketClient())

        dt = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        rate = resolve_effective_tea(dt, "green", "G22", "dei", market_service=service)
        assert rate == 120.0

    def test_adapter_without_service_returns_120_default(self):
        dt = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        rate = resolve_effective_tea(dt, "green", "G22", "dei", market_service=None)
        assert rate == 120.0

    def test_adapter_with_broken_service_falls_back_to_120(self):
        class BrokenService:
            def get_effective_tea(self, *args, **kwargs):
                raise RuntimeError("Service crash")

        dt = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
        rate = resolve_effective_tea(dt, "green", "G22", "dei", market_service=BrokenService())
        assert rate == 120.0
