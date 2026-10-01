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
