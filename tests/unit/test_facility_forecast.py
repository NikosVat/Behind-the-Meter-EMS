from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from optimization_engine.forecasting import ForecastUnavailable, forecast_day_ahead


def history(days=70):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {start + timedelta(hours=h): 5 + h // 24 * .1 + 2 * np.sin(h % 24 / 24 * 2 * np.pi)
            for h in range(days * 24)}


def test_day_origin_does_not_use_future_measurements():
    data = history()
    target = date(2026, 3, 1)
    a = forecast_day_ahead(data, target, "UTC")
    changed = {t: (v if t.date() < target else 9999.) for t, v in data.items()}
    b = forecast_day_ahead(changed, target, "UTC")
    assert a == b
    assert len(a.load_kw) == 24
    assert a.model == "ridge"
    assert a.validation_mae_kw["ridge"] < a.validation_mae_kw["previous_day"]


def test_seasonal_fallback_retains_valid_zero_consumption():
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    data = {start + timedelta(hours=h): 0. for h in range(24)}
    result = forecast_day_ahead(data, date(2026, 3, 2), "UTC")
    assert result.load_kw == [0.] * 24
    assert result.model == "previous_day"
    assert result.warnings


def test_incomplete_and_stale_history_is_not_filled_with_future_data():
    with pytest.raises(ForecastUnavailable):
        forecast_day_ahead({datetime(2026, 3, 1, tzinfo=timezone.utc): 5.}, date(2026, 3, 2), "UTC")


def test_dst_days_are_explicitly_rejected_by_24h_scheduler():
    with pytest.raises(ForecastUnavailable, match="DST"):
        forecast_day_ahead(history(), date(2026, 3, 29), "Europe/Athens")
