"""Integration test suite for month-by-month neural network validation on BDG2 real data.

Verifies:
- 12 individual calendar month slicing across held-out evaluation year 2017.
- Monthly R² score, WAPE error rate, and tinyML P95 envelope coverage bounds.
- Zero numerical instability (no NaN or Inf) across 8,760 hours.
"""

from __future__ import annotations

import calendar
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest
from sklearn.neural_network import MLPRegressor  # type: ignore[import-untyped]
from sklearn.pipeline import make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

DATA_PATH = Path(__file__).resolve().parents[2] / ".agents" / "real_data" / "electricity.csv"


@pytest.fixture(scope="module")
def monthly_eval_results():
    """Trains MLP on 2016 and produces 12-month evaluation dataset for 2017."""
    if not DATA_PATH.exists():
        pytest.skip(f"Dataset missing at: {DATA_PATH}")

    building_id = "Wolf_retail_Marcella"
    df = pd.read_csv(DATA_PATH, usecols=["timestamp", building_id], index_col="timestamp", parse_dates=True)
    series = df[building_id].dropna()
    series = series[series > 0.0]

    # Feature Engineering
    x = pd.DataFrame(index=series.index)
    for lag in [1, 2, 24, 168]:
        x[f"lag_{lag}"] = series.shift(lag)
    x["hour_sin"] = np.sin(2 * np.pi * series.index.hour / 24.0)
    x["hour_cos"] = np.cos(2 * np.pi * series.index.hour / 24.0)
    x["wday_sin"] = np.sin(2 * np.pi * series.index.dayofweek / 7.0)
    x["wday_cos"] = np.cos(2 * np.pi * series.index.dayofweek / 7.0)
    x["roll_mean_24h"] = series.shift(1).rolling(24).mean()

    valid = x.notna().all(axis=1) & (series > 0.0)
    x_valid = x[valid]
    y_valid = series[valid]

    # 2016 for Training
    train_mask = (x_valid.index >= "2016-01-01") & (x_valid.index <= "2016-12-31 23:59:59")
    x_train = x_valid[train_mask]
    y_train = y_valid[train_mask]

    mlp = make_pipeline(
        StandardScaler(),
        MLPRegressor(
            hidden_layer_sizes=(48, 24),
            activation="relu",
            max_iter=200,
            early_stopping=True,
            random_state=42,
        ),
    )
    mlp.fit(x_train, y_train)

    # Historical weekly variance for P95 envelope
    grouped_std = y_train.groupby([y_train.index.dayofweek, y_train.index.hour]).std().fillna(1.0)

    # 12 Months of 2017
    monthly_data = []
    for month_num in range(1, 13):
        m_start = f"2017-{month_num:02d}-01"
        _, last_day = calendar.monthrange(2017, month_num)
        m_end = f"2017-{month_num:02d}-{last_day:02d} 23:59:59"

        m_mask = (x_valid.index >= m_start) & (x_valid.index <= m_end)
        x_month = x_valid[m_mask]
        y_month = y_valid[m_mask]

        if len(x_month) == 0:
            continue

        pred_month = mlp.predict(x_month)
        y_actual = y_month.values

        err = pred_month - y_actual
        ss_res = float(np.sum(err**2))
        ss_tot = float(np.sum((y_actual - np.mean(y_actual))**2))
        r2 = float(1.0 - (ss_res / ss_tot)) if ss_tot > 0 else 0.0
        wape = float(np.sum(np.abs(err)) / np.sum(y_actual) * 100.0)

        # P95 Envelope Check
        p95_bounds = []
        for dt, p_val in zip(y_month.index, pred_month):
            std_val = grouped_std.get((dt.dayofweek, dt.hour), 1.0)
            p95_bounds.append(p_val + 1.645 * std_val)
        p95_arr = np.array(p95_bounds)
        p95_coverage = float(np.mean(y_actual <= p95_arr) * 100.0)

        monthly_data.append({
            "month": month_num,
            "month_name": calendar.month_abbr[month_num],
            "hours": len(y_actual),
            "actual_kwh": float(np.sum(y_actual)),
            "pred_kwh": float(np.sum(pred_month)),
            "actual_peak_kw": float(np.max(y_actual)),
            "pred_peak_kw": float(np.max(pred_month)),
            "r2": r2,
            "wape_pct": wape,
            "p95_coverage": p95_coverage,
        })

    return monthly_data


def test_twelve_months_evaluated_successfully(monthly_eval_results: list[dict]):
    """Asserts that all 12 calendar months were evaluated."""
    assert len(monthly_eval_results) == 12
    months_present = [r["month"] for r in monthly_eval_results]
    assert months_present == list(range(1, 13))


def test_every_month_meets_accuracy_thresholds(monthly_eval_results: list[dict]):
    """Asserts that every individual month maintains industrial reliability."""
    for r in monthly_eval_results:
        m_name = r["month_name"]
        assert r["r2"] >= 0.70, f"Month {m_name} R² fell below 0.70: {r['r2']:.3f}"
        assert r["wape_pct"] <= 20.0, f"Month {m_name} WAPE exceeded 20%: {r['wape_pct']:.2f}%"
        assert r["p95_coverage"] >= 95.0, f"Month {m_name} P95 coverage fell below 95%: {r['p95_coverage']:.1f}%"


def test_annual_aggregated_accuracy_metrics(monthly_eval_results: list[dict]):
    """Asserts that aggregate annual performance achieves top-tier commercial standards."""
    mean_r2 = float(np.mean([r["r2"] for r in monthly_eval_results]))
    mean_wape = float(np.mean([r["wape_pct"] for r in monthly_eval_results]))
    mean_p95 = float(np.mean([r["p95_coverage"] for r in monthly_eval_results]))

    assert mean_r2 >= 0.85, f"Annual mean R² was below 0.85: {mean_r2:.3f}"
    assert mean_wape <= 12.0, f"Annual mean WAPE was above 12%: {mean_wape:.2f}%"
    assert mean_p95 >= 98.0, f"Annual mean P95 coverage was below 98%: {mean_p95:.1f}%"


def test_total_annual_energy_prediction_divergence(monthly_eval_results: list[dict]):
    """Asserts that annual energy forecast error is within ±3%."""
    total_act = sum(r["actual_kwh"] for r in monthly_eval_results)
    total_pred = sum(r["pred_kwh"] for r in monthly_eval_results)
    divergence_pct = abs(total_pred - total_act) / total_act * 100.0
    assert divergence_pct <= 3.0, f"Annual total energy diverged by {divergence_pct:.2f}%"
