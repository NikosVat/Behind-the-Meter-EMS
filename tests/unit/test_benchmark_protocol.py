import numpy as np
import pandas as pd
from scripts import test_neural_network_monthly as monthly


def build(series):
    fn = getattr(monthly, 'day_ahead_features', None)
    assert callable(fn), 'benchmark must expose timestamp-aware day-origin features'
    return fn(series, 'UTC')


def test_missing_timestamp_does_not_compress_lags_and_zero_is_retained():
    idx = pd.date_range('2016-01-01', periods=240, freq='h')
    values = pd.Series(np.arange(240, dtype=float), index=idx)
    values.iloc[24] = 0
    values = values.drop(idx[25])
    x, y, valid = build(values)
    assert y.loc[idx[24]] == 0
    assert np.isnan(y.loc[idx[25]])
    assert x.loc[idx[48], 'lag_24'] == 0
    assert np.isnan(x.loc[idx[49], 'lag_24'])
    assert not valid.loc[idx[48]:idx[71]].any()


def test_forecast_inputs_are_frozen_at_midnight_for_all_24_hours():
    idx = pd.date_range('2016-01-01', periods=336, freq='h')
    values = pd.Series(np.arange(336, dtype=float), index=idx)
    x, _, _ = build(values)
    changed = values.copy()
    changed.loc['2016-01-12'] = 999999
    x_changed, _, _ = build(changed)
    pd.testing.assert_frame_equal(x.loc['2016-01-12'], x_changed.loc['2016-01-12'])
    assert x.loc['2016-01-12', 'previous_day_mean'].nunique() == 1
from scripts import plot_comprehensive_cost_reduction as costs


def test_cost_summary_pairs_only_feasible_days_and_never_annualizes():
    fn = getattr(costs, 'summarize_scenario', None)
    assert callable(fn), 'cost visualizer must derive paired-day results from dispatch replay'
    frame = pd.DataFrame([
        {'building': 'A', 'date': '2017-01-01', 'method': 'ridge', 'baseline_energy_eur': 10, 'replayed_energy_eur': 8, 'solver_failed': False, 'replay_infeasible': False},
        {'building': 'A', 'date': '2017-01-02', 'method': 'ridge', 'baseline_energy_eur': 100, 'replayed_energy_eur': None, 'solver_failed': False, 'replay_infeasible': True},
        {'building': 'A', 'date': '2017-02-01', 'method': 'ridge', 'baseline_energy_eur': 12, 'replayed_energy_eur': 13, 'solver_failed': False, 'replay_infeasible': False},
    ])
    summary = fn(frame)
    assert summary.baseline_energy_eur.sum() == 22
    assert summary.replayed_energy_eur.sum() == 21
    assert summary.energy_difference_eur.sum() == 1
    assert summary.feasible_days.sum() == 2
    assert summary.excluded_days.sum() == 1
from scripts.evaluate_tinyml_edge import SimEdgeForecasterPhase2


def test_hourly_adaptation_does_not_overwrite_sample_momentum():
    model = SimEdgeForecasterPhase2(np.ones((7, 24)), np.ones((7, 24)))
    model.update_sample(2)
    model.update_sample(5)
    model.record_hourly(0, 1)
    assert model.get_velocity() == 3
    assert model.daily_buffer[0] == 1
