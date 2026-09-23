"""Integration tests for the complete Building Data Genome 2 (BDG2) dataset.

Validates multi-meter real data files, schema integrity, and ML feature pipeline
across the downloaded dataset (700+ MB multi-meter records).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import pytest

DATA_DIR = Path(__file__).resolve().parents[2] / ".agents" / "real_data"


@pytest.fixture(scope="module")
def bdg2_data_dir() -> Path:
    if not DATA_DIR.exists():
        pytest.skip("BDG2 real data directory (.agents/real_data) is not present.")
    return DATA_DIR


def test_all_dataset_files_present_and_non_empty(bdg2_data_dir: Path):
    """Verifies that all raw, cleaned, and environmental dataset files exist."""
    required_files = [
        "metadata.csv",
        "electricity.csv",
        "electricity_cleaned.csv",
        "weather.csv",
        "solar.csv",
        "solar_cleaned.csv",
        "gas.csv",
        "gas_cleaned.csv",
        "chilledwater.csv",
        "chilledwater_cleaned.csv",
        "water.csv",
        "water_cleaned.csv",
        "hotwater.csv",
        "hotwater_cleaned.csv",
        "steam.csv",
        "steam_cleaned.csv",
        "irrigation.csv",
        "irrigation_cleaned.csv",
    ]

    for fname in required_files:
        fpath = bdg2_data_dir / fname
        assert fpath.exists(), f"Missing dataset file: {fname}"
        file_size = fpath.stat().st_size
        assert file_size > 10_000, f"File {fname} is unexpectedly small: {file_size} bytes"


def test_electricity_metadata_and_dimensions(bdg2_data_dir: Path):
    """Verifies electricity matrix has 1,579 buildings and proper timestamp column."""
    elec_path = bdg2_data_dir / "electricity.csv"
    sample_df = pd.read_csv(elec_path, nrows=10)
    assert "timestamp" in sample_df.columns
    # 1 timestamp column + 1,578 building columns = 1,579 columns
    assert len(sample_df.columns) == 1579

    meta_path = bdg2_data_dir / "metadata.csv"
    meta_df = pd.read_csv(meta_path)
    assert "building_id" in meta_df.columns
    assert "primaryspaceusage" in meta_df.columns
    assert len(meta_df) >= 1500


def test_weather_dataset_integrity(bdg2_data_dir: Path):
    """Verifies weather dataset contains valid temperatures across sites."""
    weather_path = bdg2_data_dir / "weather.csv"
    w_df = pd.read_csv(weather_path, nrows=100)
    assert "timestamp" in w_df.columns
    assert "airTemperature" in w_df.columns
    assert "site_id" in w_df.columns
    temps = w_df["airTemperature"].dropna()
    assert len(temps) > 0
    # Temperatures should fall within realistic terrestrial range (-50C to +60C)
    assert (temps >= -50.0).all() and (temps <= 60.0).all()


def test_real_commercial_building_ml_feature_pipeline(bdg2_data_dir: Path):
    """Verifies real commercial building time-series transforms into ML features cleanly."""
    elec_path = bdg2_data_dir / "electricity.csv"
    building_id = "Wolf_retail_Marcella"

    df = pd.read_csv(
        elec_path,
        usecols=["timestamp", building_id],
        index_col="timestamp",
        parse_dates=True,
        nrows=2000,
    )
    s = df[building_id].dropna()
    assert len(s) > 1500
    assert (s >= 0.0).all()

    # Feature engineering
    x = pd.DataFrame(index=s.index)
    for lag in [1, 2, 24]:
        x[f"lag_{lag}"] = s.shift(lag)
    x["hour_sin"] = np.sin(2 * np.pi * s.index.hour / 24.0)
    x["hour_cos"] = np.cos(2 * np.pi * s.index.hour / 24.0)
    x["roll_24h"] = s.shift(1).rolling(24).mean()

    valid_mask = x.notna().all(axis=1) & (s > 0.0)
    x_clean = x[valid_mask]
    y_clean = s[valid_mask]

    assert len(x_clean) > 1000
    assert not np.isnan(x_clean.values).any()
    assert not np.isinf(x_clean.values).any()
    assert (y_clean > 0.0).all()


def test_real_commercial_building_p95_envelope(bdg2_data_dir: Path):
    """Verifies empirical tinyML weekly seasonal P95 envelope on real commercial load."""
    elec_path = bdg2_data_dir / "electricity.csv"
    building_id = "Panther_retail_Lester"

    df = pd.read_csv(
        elec_path,
        usecols=["timestamp", building_id],
        index_col="timestamp",
        parse_dates=True,
    )
    s = df[building_id].dropna()
    s = s[s > 0.0]

    # Partition into historical baseline and evaluation test slice
    train = s.loc["2016-01-01":"2016-06-30"]
    test = s.loc["2016-07-01":"2016-07-31"]

    assert len(train) > 2000
    assert len(test) > 400

    # Compute (weekday, hour) mean and standard deviation
    weekly_mean = train.groupby([train.index.dayofweek, train.index.hour]).mean()
    weekly_std = train.groupby([train.index.dayofweek, train.index.hour]).std().fillna(0.5)

    # Compute P95 upper bound: mean + 1.645 * std
    p95_bounds = []
    for dt in test.index:
        key = (dt.dayofweek, dt.hour)
        m = weekly_mean.get(key, train.mean())
        sd = weekly_std.get(key, 1.0)
        p95_bounds.append(m + 1.645 * sd)

    p95_arr = np.array(p95_bounds)
    coverage = float(np.mean(test.values <= p95_arr) * 100.0)

    # Real data P95 bound must protect against at least 90% of actual consumption
    assert coverage >= 90.0, f"P95 envelope coverage was too low: {coverage:.2f}%"
