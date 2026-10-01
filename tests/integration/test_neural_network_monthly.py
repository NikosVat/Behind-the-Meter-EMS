"""Integration checks of the actual month-by-month day-origin evaluator.

Forecast skill is an experimental result, not a hardcoded acceptance threshold.
"""
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from scripts import test_neural_network_monthly as monthly


@pytest.fixture(scope='module')
def monthly_eval_results(tmp_path_factory):
    if not monthly.DATA_PATH.exists():
        pytest.skip('Optional BDG2 cache unavailable')
    # Optional data integration must not rewrite checked-in benchmark evidence.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(monthly, 'OUT_DIR', tmp_path_factory.mktemp('monthly_benchmark'))
        yield monthly.run_monthly_evaluation()


def test_calendar_months_and_complete_day_accounting(monthly_eval_results):
    result, meta = monthly_eval_results
    assert result.month_num.tolist() == list(range(1, 13))
    assert (result.hours % 24 == 0).all()
    assert result.hours.sum() == meta['test_hours']
    assert meta['test_hours'] // 24 + meta['excluded_test_days'] == 365
    assert meta['contracted_capacity_kw'] is None


def test_monthly_metrics_reconcile_with_recorded_hourly_predictions(monthly_eval_results):
    result, _ = monthly_eval_results
    frame = pd.read_csv(monthly.OUT_DIR / 'monthly_heldout_predictions.csv', index_col='timestamp_local', parse_dates=True)
    assert np.isfinite(frame.to_numpy()).all()
    assert (frame.mlp >= 0).all()
    for row in result.itertuples():
        group = frame[frame.index.month == row.month_num]
        error = (group.mlp - group.actual_kw).abs()
        assert row.actual_kwh == pytest.approx(group.actual_kw.sum())
        assert row.pred_kwh == pytest.approx(group.mlp.sum())
        assert row.mae_kw == pytest.approx(error.mean())
        assert row.wape_pct == pytest.approx(error.sum() / group.actual_kw.sum() * 100)
        assert row.heuristic_exceedance_hours == (group.actual_kw > group.heuristic_upper_kw).sum()
