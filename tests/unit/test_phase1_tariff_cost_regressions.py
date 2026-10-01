from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from tariff_engine.contracts import get_greek_season, is_offpeak_window, is_peak_window, Season, to_athens_time
from backend.market.service import MarketPriceService
from backend.database.sqlite_store import SQLiteStore
from backend.market.models import DamHourlyPrice

ATHENS_TZ = ZoneInfo("Europe/Athens")

class TestTimezoneRegressions:
    def test_utc_and_athens_instant_parity_summer_peak(self):
        """2026-10-01 11:00:00 UTC is 14:00:00 EEST (Summer weekday peak). Both must return True."""
        dt_utc = datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc)
        dt_athens = datetime(2026, 10, 1, 14, 0, 0, tzinfo=ATHENS_TZ)
        assert is_peak_window(dt_utc) is True
        assert is_peak_window(dt_athens) is True

    def test_utc_timestamp_outside_peak_in_athens(self):
        """2026-10-01 14:00:00 UTC is 17:00:00 EEST (Summer peak ends at 17:00). Must return False."""
        dt_utc = datetime(2026, 10, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert is_peak_window(dt_utc) is False

    def test_winter_season_and_peak_hour(self):
        """2026-11-05 15:30:00 UTC is 17:30:00 EET (Winter weekday peak 17:00-21:00)."""
        dt_utc = datetime(2026, 11, 5, 15, 30, 0, tzinfo=timezone.utc)
        assert get_greek_season(dt_utc) == Season.WINTER
        assert is_peak_window(dt_utc) is True

    def test_month_transition_at_midnight_utc(self):
        """2026-10-31 22:30:00 UTC is 2026-11-01 00:30:00 EET. Must classify as WINTER, not SUMMER."""
        dt_utc = datetime(2026, 10, 31, 22, 30, 0, tzinfo=timezone.utc)
        assert get_greek_season(dt_utc) == Season.WINTER

    def test_offpeak_night_window_with_utc(self):
        """2026-10-01 21:30:00 UTC is 2026-10-02 00:30:00 EEST (Night off-peak 23:00-07:00)."""
        dt_utc = datetime(2026, 10, 1, 21, 30, 0, tzinfo=timezone.utc)
        assert is_offpeak_window(dt_utc) is True

    def test_to_athens_time_naive_and_aware(self):
        """Naive datetime is assigned Europe/Athens; aware is converted."""
        dt_naive = datetime(2026, 10, 1, 14, 0, 0)
        dt_norm = to_athens_time(dt_naive)
        assert dt_norm.tzinfo == ATHENS_TZ
        assert dt_norm.hour == 14

        dt_utc = datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc)
        dt_norm_utc = to_athens_time(dt_utc)
        assert dt_norm_utc.tzinfo == ATHENS_TZ
        assert dt_norm_utc.hour == 14

    def test_market_service_timezone_normalization(self, tmp_path):
        """MarketPriceService.get_effective_tea normalizes UTC timestamps to Europe/Athens."""
        db_path = str(tmp_path / "test_mkt_tz.db")
        store = SQLiteStore(db_path)
        store.init_db()
        service = MarketPriceService(store=store)

        # Preload cache for Athens date 2026-10-02 hour 1
        with service._lock:
            service._dam_cache["2026-10-02"] = [
                DamHourlyPrice(date="2026-10-02", hour=1, price_eur_mwh=155.0, price_eur_kwh=0.155, source="test")
            ]

        # 2026-10-01 22:30:00 UTC is 2026-10-02 01:30:00 EEST (Athens)
        dt_utc = datetime(2026, 10, 1, 22, 30, 0, tzinfo=timezone.utc)
        price = service.get_effective_tea(dt_utc, tariff_color="yellow")
        assert price == 155.0


from tariff_engine.yellow_dynamic import calculate_yellow_dynamic_supply_rate, to_kwh_rate
from tariff_engine.green_tariff import calculate_green_tariff_fluctuation, calculate_green_tariff_supply_rate


class TestPricingUnitsRegressions:
    def test_low_positive_tea_yellow_dynamic_rate(self):
        """TEA = 0.50 €/MWh must convert to 0.00050 €/kWh, yielding 0.06557 €/kWh, NOT 0.6325 €/kWh."""
        rate = calculate_yellow_dynamic_supply_rate(
            tea_eur_mwh=0.50,
            loss_factor=0.135,
            margin_eur_kwh=0.015,
            p_base=0.050,
        )
        assert rate == 0.06557

    def test_continuity_across_one_euro_boundary(self):
        """Ensure no 1000x jump between 0.99 €/MWh and 1.01 €/MWh."""
        rate_0_99 = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.99)
        rate_1_01 = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=1.01)
        assert abs(rate_1_01 - rate_0_99) < 0.0001

    def test_green_tariff_fluctuation_solar_surplus_rebate(self):
        """TEA = 0.50 €/MWh is far below Ll = 95.0 €/MWh, so MD must be a negative rebate."""
        md = calculate_green_tariff_fluctuation(
            tea_eur_mwh=0.50,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        # Expected: 1.15 * (0.00050 - 0.095) = -0.108675 €/kWh
        assert pytest.approx(md, rel=1e-4) == -0.108675

    def test_zero_and_negative_wholesale_prices(self):
        """Zero and negative wholesale prices calculate linearly without exception."""
        rate_zero = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.0, floor_at_zero=False)
        assert rate_zero == 0.06500

        rate_neg = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=-20.0, floor_at_zero=False)
        # -0.020 * 1.135 + 0.015 + 0.050 = -0.0227 + 0.065 = 0.04230
        assert rate_neg == 0.04230

    def test_explicit_price_unit_conversion(self):
        """Verify to_kwh_rate with EUR_MWH and EUR_KWH."""
        assert to_kwh_rate(100.0, "EUR_MWH") == 0.100
        assert to_kwh_rate(0.150, "EUR_KWH") == 0.150
        with pytest.raises(ValueError, match="Unsupported price unit"):
            to_kwh_rate(100.0, "INVALID")

