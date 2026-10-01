"""Hourly analogue of edge forecast formulas; no ESP32 or capacity validation."""

from __future__ import annotations

import sys
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_day_ahead import day_ahead_features

CACHE = ROOT / ".agents" / "real_data"
OUT_REPORT = ROOT / "reports" / "real_data" / "TINYML_EVALUATION.md"

BUILDINGS = ["Panther_retail_Lester", "Wolf_retail_Marcella", "Lamb_office_Peggy"]


class SimEdgeForecasterPhase2:
    """Hourly analogue; trained profile and sample cadence differ from firmware."""

    def __init__(
        self,
        initial_mean_profile: np.ndarray,
        initial_std_profile: np.ndarray,
        alpha_rate: float = 0.15,
        alpha_std_rate: float = 0.10,
        persistence_factor: float = 0.60,
        momentum_factor: float = 0.25,
        p95_z_score: float = 1.645,
        min_kw: float = 0.05,
    ):
        self.mean_profile = initial_mean_profile.astype(float).copy()
        self.std_profile = initial_std_profile.astype(float).copy()
        self.initial_mean_profile = initial_mean_profile.astype(float).copy()
        self.initial_std_profile = initial_std_profile.astype(float).copy()

        self.alpha_rate = alpha_rate
        self.alpha_std_rate = alpha_std_rate
        self.persistence_factor = persistence_factor
        self.momentum_factor = momentum_factor
        self.p95_z_score = p95_z_score
        self.min_kw = min_kw

        self.daily_buffer = np.zeros(24, dtype=float)
        self.hours_recorded_mask = 0
        self.recent_samples = [0.0, 0.0, 0.0]
        self.sample_count = 0

    def update_sample(self, val: float) -> None:
        self.recent_samples[2] = self.recent_samples[1]
        self.recent_samples[1] = self.recent_samples[0]
        self.recent_samples[0] = max(self.min_kw, val)
        if self.sample_count < 3:
            self.sample_count += 1

    def get_velocity(self) -> float:
        if self.sample_count >= 2:
            return self.recent_samples[0] - self.recent_samples[1]
        return 0.0

    def predict_next_hour(self, weekday: int, current_hour: int, current_power: float) -> float:
        next_hour = (current_hour + 1) % 24
        next_weekday = weekday if current_hour < 23 else (weekday + 1) % 7

        base_curr = self.mean_profile[weekday, current_hour]
        base_next = self.mean_profile[next_weekday, next_hour]

        residual = current_power - base_curr
        velocity = self.get_velocity()

        pred = base_next + (self.persistence_factor * residual) + (self.momentum_factor * velocity)
        return max(self.min_kw, float(pred))

    def predict_next_hour_p95(self, weekday: int, current_hour: int, current_power: float) -> float:
        p_mean = self.predict_next_hour(weekday, current_hour, current_power)
        next_hour = (current_hour + 1) % 24
        next_weekday = weekday if current_hour < 23 else (weekday + 1) % 7

        sigma = self.std_profile[next_weekday, next_hour]
        return max(self.min_kw, float(p_mean + self.p95_z_score * sigma))

    def get_day_ahead_forecast(self, weekday: int) -> tuple[np.ndarray, np.ndarray]:
        return self.mean_profile[weekday].copy(), self.std_profile[weekday].copy()

    def record_hourly(self, hour: int, actual_kw: float) -> None:
        self.daily_buffer[hour] = max(self.min_kw, actual_kw)
        self.hours_recorded_mask |= (1 << hour)

    def adapt_day(self, weekday: int) -> bool:
        valid_count = bin(self.hours_recorded_mask).count("1")
        if valid_count < 18:
            self.hours_recorded_mask = 0
            return False

        for h in range(24):
            if not (self.hours_recorded_mask & (1 << h)):
                continue
            old_mean = self.mean_profile[weekday, h]
            actual = self.daily_buffer[h]
            init_mean = self.initial_mean_profile[weekday, h]

            # 1. Update Mean profile
            updated_mean = (1.0 - self.alpha_rate) * old_mean + self.alpha_rate * actual
            lower_bound = max(self.min_kw, 0.20 * init_mean)
            upper_bound = max(lower_bound + 1.0, 3.00 * init_mean)
            self.mean_profile[weekday, h] = np.clip(updated_mean, lower_bound, upper_bound)

            # 2. Update Volatility/Std profile
            old_std = self.std_profile[weekday, h]
            deviation = abs(actual - self.mean_profile[weekday, h])
            sample_std = deviation * 1.2533 # Gaussian MAD scale factor
            updated_std = (1.0 - self.alpha_std_rate) * old_std + self.alpha_std_rate * sample_std

            init_std = self.initial_std_profile[weekday, h]
            min_std = max(0.20, init_std * 0.20)
            max_std = max(min_std + 0.5, init_std * 3.50)
            self.std_profile[weekday, h] = np.clip(updated_std, min_std, max_std)

        self.hours_recorded_mask = 0
        return True


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    err = np.asarray(pred) - np.asarray(actual)
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    wape = float(np.sum(np.abs(err)) / np.sum(actual) * 100.0)
    return {"mae_kw": round(mae, 3), "rmse_kw": round(rmse, 3), "wape_pct": round(wape, 2)}


def evaluate_building(building: str, raw_series: pd.Series) -> dict:
    metadata = pd.read_csv(CACHE / "metadata.csv").set_index("building_id")
    x, raw_series, eligible = day_ahead_features(raw_series, metadata.loc[building, "timezone"])
    s_2016_pos = raw_series.loc["2016-01-01":"2016-12-31"].dropna()

    grouped_mean = s_2016_pos.groupby([s_2016_pos.index.dayofweek, s_2016_pos.index.hour]).mean()
    grouped_std = s_2016_pos.groupby([s_2016_pos.index.dayofweek, s_2016_pos.index.hour]).std().fillna(1.0)

    base_mean = np.zeros((7, 24), dtype=float)
    base_std = np.zeros((7, 24), dtype=float)
    for d in range(7):
        for h in range(24):
            base_mean[d, h] = grouped_mean.loc[(d, h)] if (d, h) in grouped_mean.index else s_2016_pos.mean()
            base_std[d, h] = max(0.20, grouped_std.loc[(d, h)] if (d, h) in grouped_std.index else 1.0)

    static_forecaster = SimEdgeForecasterPhase2(base_mean, base_std, alpha_rate=0.0, alpha_std_rate=0.0)
    adaptive_forecaster = SimEdgeForecasterPhase2(base_mean, base_std, alpha_rate=0.15, alpha_std_rate=0.10)

    s_2017 = raw_series.loc["2017-01-01":"2017-12-31"]

    actual_24h_list = []
    static_day_ahead_list = []
    adaptive_day_ahead_list = []

    actual_next_hour_list = []
    adaptive_next_hour_list = []
    adaptive_p95_list = []

    for date, day_df in s_2017.groupby(s_2017.index.normalize()):
        if not eligible.reindex(day_df.index).all() or len(day_df) != 24:
            adaptive_forecaster.sample_count = 0
            adaptive_forecaster.recent_samples = [0.0, 0.0, 0.0]
            continue

        actual_vals = day_df.to_numpy()
        wday = date.dayofweek

        # 1. Day-ahead forecasts
        static_mean, _ = static_forecaster.get_day_ahead_forecast(wday)
        adapt_mean, _ = adaptive_forecaster.get_day_ahead_forecast(wday)

        actual_24h_list.extend(actual_vals)
        static_day_ahead_list.extend(static_mean)
        adaptive_day_ahead_list.extend(adapt_mean)

        # 2. Real-time next-hour forecasts with momentum and P95
        for h in range(23):
            curr_kw = actual_vals[h]
            next_actual = actual_vals[h + 1]

            adaptive_forecaster.update_sample(curr_kw)
            pred_mean = adaptive_forecaster.predict_next_hour(wday, h, curr_kw)
            pred_p95 = adaptive_forecaster.predict_next_hour_p95(wday, h, curr_kw)

            actual_next_hour_list.append(next_actual)
            adaptive_next_hour_list.append(pred_mean)
            adaptive_p95_list.append(pred_p95)

            adaptive_forecaster.record_hourly(h, curr_kw)

        adaptive_forecaster.update_sample(actual_vals[23])
        adaptive_forecaster.record_hourly(23, actual_vals[23])

        # 3. Midnight adaptation
        adaptive_forecaster.adapt_day(wday)

    # Calculate P95 coverage: fraction of actual hours <= P95 forecast
    actual_arr = np.array(actual_next_hour_list)
    p95_arr = np.array(adaptive_p95_list)
    p95_coverage_pct = round(float(np.mean(actual_arr <= p95_arr) * 100.0), 2)

    return {
        "building": building,
        "valid_test_days": len(actual_24h_list) // 24,
        "static_day_ahead": metrics(np.array(actual_24h_list), np.array(static_day_ahead_list)),
        "adaptive_day_ahead": metrics(np.array(actual_24h_list), np.array(adaptive_day_ahead_list)),
        "adaptive_next_hour": metrics(actual_arr, np.array(adaptive_next_hour_list)),
        "heuristic_coverage_pct": p95_coverage_pct,
        "heuristic_exceedance_hours": int(np.sum(actual_arr > p95_arr)),
        "contracted_capacity_kw": None,
        "previous_day": metrics(raw_series.loc[eligible & (raw_series.index.year == 2017)].to_numpy(), x.loc[eligible & (raw_series.index.year == 2017), "lag_24"].to_numpy()),
        "previous_week": metrics(raw_series.loc[eligible & (raw_series.index.year == 2017)].to_numpy(), x.loc[eligible & (raw_series.index.year == 2017), "lag_168"].to_numpy()),
    }


def main():
    csv_path = CACHE / "electricity.csv"
    raw = pd.read_csv(csv_path, usecols=["timestamp"] + BUILDINGS, index_col="timestamp", parse_dates=True)
    results = [evaluate_building(b, raw[b]) for b in BUILDINGS]
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    (OUT_REPORT.parent / "tinyml_metrics.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = ["# Adaptive weekly profile: hourly Python evaluation", "",
        "This is an hourly-resolution analogue of the edge forecast formulas using BDG2 hourly averages. It does not run C++ firmware, validate ESP32 memory/latency, or reproduce five-second sample momentum. Initial mean/std profiles and clamp anchors are estimated from 2016, unlike the deployed firmware's default profile.", "",
        "All 24 day-ahead means are frozen at local midnight. Daily EMA adaptation happens after the evaluated day's 24 samples. The separate next-hour diagnostic observes current hourly load before predicting the following hour and scores only hours 01:00-23:00; it must not be reported as 24h forecast accuracy.",
        "Zero targets are retained. Missing/nonfinite/negative readings, incomplete lag/target days, DST transition/dependent dates are excluded on a regular hourly index. All 24h methods share the lag24/48/168 eligibility mask. Skipped days do not update the profile; sample momentum is reset after gaps. The analogue retains the firmware-style 0.05 kW prediction/sample floor.", "",
        "| Building | Test days | Previous-day WAPE | Previous-week WAPE | Static profile WAPE | Adaptive 24h WAPE | Next-hour WAPE | Next-hour heuristic coverage | Bound exceedance hours |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['building']} | {r['valid_test_days']} | {r['previous_day']['wape_pct']:.2f}% | {r['previous_week']['wape_pct']:.2f}% | {r['static_day_ahead']['wape_pct']:.2f}% | {r['adaptive_day_ahead']['wape_pct']:.2f}% | {r['adaptive_next_hour']['wape_pct']:.2f}% | {r['heuristic_coverage_pct']:.2f}% | {r['heuristic_exceedance_hours']} |")
    lines += ["", "The next-hour heuristic upper value is mean + 1.645 x adaptive profile dispersion. Its empirical coverage does not establish calibrated P95, no-breach reliability, or penalty avoidance. Contracted capacity and capacity-breach outcomes are unknown. Profile quality and distribution shifts require monitoring; the office series contains a severe operating/data regime change.", "",
        "Exact MAE/RMSE/WAPE and exceedance counts are in `tinyml_metrics.json`. Source: Building Data Genome 2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x. Source and adapted outputs: CC BY-SA 4.0."]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
