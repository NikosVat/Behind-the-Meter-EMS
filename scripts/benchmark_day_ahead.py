"""Shared offline MLP evaluation: all 24 hourly forecasts are issued at midnight."""
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
import time
import platform

import numpy as np
import pandas as pd
import sklearn
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits


def regular_hourly(series):
    if not isinstance(series.index, pd.DatetimeIndex) or not series.index.is_unique:
        raise ValueError('Unique datetime meter timestamps are required')
    series = series.sort_index()
    if not series.index.equals(series.index.floor('h')):
        raise ValueError('Hourly timestamps are required')
    series = series.reindex(pd.date_range(series.index.min(), series.index.max(), freq='h'))
    return series.where(np.isfinite(series) & (series >= 0))


def day_ahead_features(series, zone='UTC'):
    y = regular_hourly(series)
    x = pd.DataFrame(index=y.index)
    for lag in (24, 48, 168):
        x[f'lag_{lag}'] = y.shift(lag)
    day = y.index.normalize()
    daily = y.groupby(day).agg(['mean', 'std', 'count'])
    daily.loc[daily['count'] != 24, ['mean', 'std']] = np.nan
    for column in ('mean', 'std'):
        x[f'previous_day_{column}'] = daily[column].reindex(day - pd.Timedelta(days=1)).to_numpy()
    for name, values, period in [('hour', y.index.hour, 24), ('weekday', y.index.dayofweek, 7)]:
        x[name + '_sin'] = np.sin(2 * np.pi * values / period)
        x[name + '_cos'] = np.cos(2 * np.pi * values / period)
    # Local timestamps omit offset/fold: exclude transition dates and dependent lags.
    tz = ZoneInfo(zone)
    bad_days = set()
    for date in day.unique():
        start = date.to_pydatetime().replace(tzinfo=tz)
        end = start + timedelta(days=1)
        if (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() != 86400:
            bad_days.update(date + pd.Timedelta(days=offset) for offset in (0, 1, 2, 7))
    eligible = y.notna() & x.notna().all(axis=1) & ~day.isin(bad_days)
    counts = eligible.groupby(day).sum()
    eligible &= day.isin(counts[counts == 24].index)
    return x, y, eligible


def metrics(actual, prediction):
    actual, prediction = np.asarray(actual), np.asarray(prediction)
    error = prediction - actual
    total = float(actual.sum())
    variance = float(((actual - actual.mean()) ** 2).sum())
    return {'mae_kw': float(np.abs(error).mean()), 'rmse_kw': float(np.sqrt((error**2).mean())),
            'r2': 1 - float((error**2).sum()) / variance if variance else np.nan,
            'wape_pct': float(np.abs(error).sum()) / total * 100 if total else np.nan}


def evaluate(series, zone):
    x, y, valid = day_ahead_features(series, zone)
    train = valid & (x.index >= '2016-01-01') & (x.index < '2017-01-01')
    test = valid & (x.index >= '2017-01-01') & (x.index < '2018-01-01')
    if train.sum() < 1000 or test.sum() < 24:
        raise ValueError('Insufficient complete training/test days')
    model = make_pipeline(StandardScaler(), MLPRegressor(hidden_layer_sizes=(48, 24),
        max_iter=300, early_stopping=True, random_state=42))
    start = time.perf_counter()
    with threadpool_limits(limits=1):
        model.fit(x.loc[train], y.loc[train])
        prediction = np.maximum(0, model.predict(x.loc[test]))
    elapsed = time.perf_counter() - start
    frame = pd.DataFrame({'actual_kw': y.loc[test], 'mlp': prediction,
        'previous_day': x.loc[test, 'lag_24'], 'previous_week': x.loc[test, 'lag_168']})
    std = y.loc[train].groupby([y.loc[train].index.dayofweek, y.loc[train].index.hour]).std()
    frame['heuristic_upper_kw'] = prediction + 1.645 * np.array([
        std.get((dt.dayofweek, dt.hour), 0) for dt in frame.index])
    return frame, {'train_hours': int(train.sum()), 'test_hours': len(frame),
        'excluded_test_days': 365 - len(frame) // 24, 'train_sec': round(elapsed, 2),
        'forecast_origin': 'local midnight; all 24 hours; observed inputs strictly before origin',
        'timezone': zone, 'contracted_capacity_kw': None,
        'optimizer_iterations': model[-1].n_iter_,
        'reached_iteration_limit': model[-1].n_iter_ >= model[-1].max_iter,
        'versions': {'python': platform.python_version(), 'numpy': np.__version__,
                     'pandas': pd.__version__, 'scikit_learn': sklearn.__version__}}
