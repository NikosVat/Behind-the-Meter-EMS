"""Offline research benchmark; does not install a forecasting model in the EMS.

Run: python scripts/run_real_data_benchmark.py
Requires pandas, scikit-learn, matplotlib, httpx and the project dependencies.
Raw BDG2 data is cached outside version control. Never writes the operational DB.
"""
from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import sklearn
import scipy
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from optimization_engine.models import BESSLoad, OptimizationProblem
from optimization_engine.solver import ConstrainedLoadSolver

COMMIT = "9b97ccbe90096aff42ed4fd6493bf7ae692d7118"
BASE = f"https://media.githubusercontent.com/media/buds-lab/building-data-genome-project-2/{COMMIT}"
BUILDINGS = ["Wolf_retail_Marcella", "Panther_retail_Lester", "Lamb_office_Peggy"]
CACHE = ROOT / ".agents" / "real_data"
OUT = ROOT / "reports" / "real_data"
FILES = {
    "metadata.csv": ("data/metadata/metadata.csv", "992d0b29f24f96ad4332bc4dbb534b7bdd7dd2689aad093f94e93068ecddca02"),
    "electricity.csv": ("data/meters/raw/electricity.csv", "039d909d8981e2d69eaeb366144e6ab7e84fa5e7e216aee42bddd95384a66418"),
}
# Assumed scenario, not historical Greek or source-building tariffs.
RATES = np.array([.12] * 7 + [.20] * 5 + [.30] * 6 + [.20] * 5 + [.12])
METHODS = ["previous_day", "previous_week", "ridge", "gradient_boosting"]


def download():
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, (remote, expected) in FILES.items():
        target = CACHE / name
        if not target.exists():
            partial = target.with_suffix(".partial")
            with httpx.stream("GET", BASE + "/" + remote, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                with partial.open("wb") as file:
                    for block in response.iter_bytes():
                        file.write(block)
            partial.replace(target)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Source checksum mismatch: {target}")


def features(series, zone):
    # No smoothing, whole-dataset outlier fitting, or target interpolation.
    # Raw electricity zeros are flagged as questionable, following BDG2's cleaning convention.
    y = series.where(np.isfinite(series) & (series > 0))
    x = pd.DataFrame(index=y.index)
    for lag in (24, 48, 168, 336):
        x[f"lag_{lag}"] = y.shift(lag)
    day = y.index.normalize()
    daily = y.groupby(day).agg(["mean", "min", "max", "count"])
    daily.loc[daily["count"] != 24, ["mean", "min", "max"]] = np.nan
    for column in ("mean", "min", "max"):
        x[f"previous_day_{column}"] = daily[column].reindex(day - pd.Timedelta(days=1)).to_numpy()
    for name, values, period in [("hour", y.index.hour, 24), ("weekday", y.index.dayofweek, 7), ("month", y.index.month-1, 12)]:
        x[name + "_sin"] = np.sin(2 * np.pi * values / period)
        x[name + "_cos"] = np.cos(2 * np.pi * values / period)
    # Source timestamps are local, without offset/fold. Do not invent missing DST hours.
    tz = ZoneInfo(zone)
    bad_days = set()
    for date in day.unique():
        start = date.to_pydatetime().replace(tzinfo=tz)
        end = start + timedelta(days=1)
        if (end.astimezone(timezone.utc)-start.astimezone(timezone.utc)).total_seconds() != 86400:
            for offset in (0, 1, 2, 7, 14):
                bad_days.add(date + pd.Timedelta(days=offset))
    eligible = y.notna() & x.notna().all(axis=1) & ~day.isin(bad_days)
    # Common full-day evaluation mask for all methods, no method-specific filtering.
    counts = eligible.groupby(day).sum()
    eligible &= day.isin(counts[counts == 24].index)
    return x, y, eligible


def fit_models(x, y):
    models = {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "gradient_boosting": HistGradientBoostingRegressor(
            max_iter=150, max_leaf_nodes=15, min_samples_leaf=30,
            learning_rate=.05, l2_regularization=1, early_stopping=False, random_state=42),
    }
    for model in models.values():
        model.fit(x, y)
    return models


def predictions(models, x):
    values = {"previous_day": x["lag_24"].to_numpy(), "previous_week": x["lag_168"].to_numpy()}
    values.update({name: np.maximum(0, model.predict(x)) for name, model in models.items()})
    return values


def metrics(actual, predicted):
    error = np.asarray(predicted) - np.asarray(actual)
    return {"mae_kw": float(np.mean(np.abs(error))),
            "rmse_kw": float(np.sqrt(np.mean(error ** 2))),
            "wape_pct": float(np.sum(np.abs(error)) / np.sum(actual) * 100)}


def schedule(load, capacity):
    problem = OptimizationProblem(baseline_load_kw=list(load), tariff_rates_eur_kwh=list(RATES),
        contracted_capacity_kw=capacity, capacity_penalty_eur_per_kw=1.0, bess=BESSLoad())
    result = ConstrainedLoadSolver(problem).solve()
    if not result.is_optimal or not result.operationally_feasible:
        return None
    net = np.array(result.device_schedules["Commercial BESS 20kWh (Net)"])
    # Returned schedules are rounded; use tolerances appropriate to that API.
    assert np.max(np.abs(net)) <= 5.01
    soc = np.asarray(result.bess_soc_history)
    assert soc.min() >= .1499 and soc.max() <= .9501 and soc[-1] >= .3999
    return net


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    download()
    raw = pd.read_csv(CACHE / "electricity.csv", usecols=["timestamp"] + BUILDINGS, index_col="timestamp", parse_dates=True)
    metadata = pd.read_csv(CACHE / "metadata.csv").set_index("building_id")
    assert raw.index.is_unique and raw.index.equals(pd.date_range("2016-01-01", "2017-12-31 23:00", freq="h"))
    forecast_rows, validation_rows, quality, dispatch_rows, hourly_rows = [], [], [], [], []
    for building in BUILDINGS:
        print(f"Evaluating {building}", flush=True)
        series = raw[building]
        x, y, valid = features(series, metadata.loc[building, "timezone"])
        train = valid & (x.index < "2016-10-01")
        validation = valid & (x.index >= "2016-10-01") & (x.index < "2017-01-01")
        test = valid & (x.index >= "2017-01-01")
        assert train.sum() >= 720 and validation.sum() >= 168 and test.sum() >= 168
        with threadpool_limits(limits=1):
            fitted = fit_models(x.loc[train], y.loc[train])
            val_predictions = predictions(fitted, x.loc[validation])
            val_scores = {name: metrics(y.loc[validation], pred) for name, pred in val_predictions.items()}
            selected = min(val_scores, key=lambda name: val_scores[name]["wape_pct"])
            for name, score in val_scores.items():
                validation_rows.append({"building": building, "method": name, **score})
            fitted = fit_models(x.loc[train | validation], y.loc[train | validation])
            test_predictions = predictions(fitted, x.loc[test])
        quality.append({"building": building, "type": metadata.loc[building, "primaryspaceusage"],
            "timezone": metadata.loc[building, "timezone"], "raw_hours": len(series),
            "missing_hours": int(series.isna().sum()), "nonpositive_hours": int((series <= 0).sum()),
            "near_zero_test_hours": int(((series.loc["2017"] > 0) & (series.loc["2017"] < .001)).sum()),
            "mean_2016_kw": float(series.loc["2016"].mean()), "mean_2017_kw": float(series.loc["2017"].mean()),
            "train_hours": int(train.sum()), "validation_hours": int(validation.sum()),
            "test_hours": int(test.sum()), "test_days": int(test.sum()/24),
            "excluded_test_days": 365-int(test.sum()/24), "selected_on_validation": selected})
        for name, pred in test_predictions.items():
            forecast_rows.append({"building": building, "method": name, "selected_on_validation": name == selected,
                "test_hours": int(test.sum()), **metrics(y.loc[test], pred)})
        frame = pd.DataFrame(test_predictions, index=x.index[test])
        frame["actual_kw"] = y.loc[test]
        for timestamp, row in frame.iterrows():
            hourly_rows.append({"building": building, "timestamp_local": str(timestamp), **row.to_dict()})
        # Fixed before the test: scenario capacity is the 2016 observed positive-load 95th percentile.
        capacity = float(y.loc[y.index < "2017-01-01"].quantile(.95))
        for date, day_frame in frame.groupby(frame.index.normalize()):
            assert len(day_frame) == 24
            actual = day_frame["actual_kw"].to_numpy()
            base_energy = float(actual @ RATES)
            base_excess = float(np.maximum(0, actual-capacity).sum())
            for name in METHODS + ["perfect_foresight"]:
                forecast = actual if name == "perfect_foresight" else day_frame[name].to_numpy()
                net = schedule(forecast, capacity)
                record = {"building": building, "date": str(date.date()), "method": name,
                    "capacity_kw": capacity, "baseline_energy_eur": base_energy,
                    "baseline_excess_kwh": base_excess, "solver_failed": net is None}
                if net is not None:
                    actual_grid = actual + net
                    infeasible = bool(np.any(actual_grid < -.02) or np.any(actual_grid > 500.02))
                    record.update({"replay_infeasible": infeasible,
                        "export_attempt_hours": int(np.sum(actual_grid < -.02)),
                        "minimum_grid_kw": float(actual_grid.min()),
                        # No financial claim for physically invalid open-loop schedules.
                        "replayed_energy_eur": None if infeasible else float(np.maximum(0, actual_grid) @ RATES),
                        "replayed_excess_kwh": None if infeasible else float(np.maximum(0, actual_grid-capacity).sum())})
                dispatch_rows.append(record)
        print(f"  held-out days: {int(test.sum()/24)}, validation winner: {selected}", flush=True)

    pd.DataFrame(forecast_rows).to_csv(OUT / "forecast_metrics.csv", index=False)
    pd.DataFrame(validation_rows).to_csv(OUT / "validation_metrics.csv", index=False)
    pd.DataFrame(quality).to_csv(OUT / "data_quality.csv", index=False)
    pd.DataFrame(dispatch_rows).to_csv(OUT / "dispatch_daily.csv", index=False)
    pd.DataFrame(hourly_rows).to_csv(OUT / "heldout_predictions.csv", index=False)
    manifest = {"source": BASE, "source_commit": COMMIT, "source_files": FILES,
        "attribution": "Miller et al., Building Data Genome 2, Scientific Data 7, 368 (2020), https://doi.org/10.1038/s41597-020-00712-x",
        "data_license": "CC BY-SA 4.0; see source repository LICENSE",
        "buildings_selected_before_test": BUILDINGS,
        "training": "2016-01-01 through 2016-09-30", "validation": "2016-10-01 through 2016-12-31",
        "test": "2017-01-01 through 2017-12-31", "final_refit": "all eligible 2016 hours",
        "forecast_origin": "local midnight; predict all 24 next-day hours; lagged test observations available only before origin",
        "scenario": {"rates_eur_kwh": RATES.tolist(), "battery": "hypothetical 20 kWh / 5 kW, 94% efficiency each direction, daily initial/final target SOC 40%",
            "capacity": "2016 positive-load 95th percentile", "overload_objective": "assumed EUR 1 per excess kW per hourly slot; not a statutory tariff",
            "equipment": "battery only; no ovens/HVAC/defrost added to whole-building demand"},
        "project_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__, "sklearn": sklearn.__version__, "scipy": scipy.__version__}}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(pd.DataFrame(forecast_rows).to_string(index=False))
    write_report()


def write_report():
    """Render the recorded experiment outputs, without fitting or selecting again."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    forecast = pd.read_csv(OUT / "forecast_metrics.csv")
    quality = pd.read_csv(OUT / "data_quality.csv")
    dispatch = pd.read_csv(OUT / "dispatch_daily.csv")
    selected = forecast[forecast.selected_on_validation]
    previous = forecast[forecast.method == "previous_day"].set_index("building")
    labels = {"Wolf_retail_Marcella": "Retail A (Wolf)", "Panther_retail_Lester": "Retail B (Panther)", "Lamb_office_Peggy": "Office (Lamb)"}
    lines = ["# Real measured-data experiment", "",
        "This offline research benchmark tests forecast models and the existing EMS solver. It does not deploy ML, operate equipment, or verify savings at a Greek facility.", "",
        "## Data and protocol", "",
        "- Source: [Building Data Genome 2](https://github.com/buds-lab/building-data-genome-project-2), pinned to commit `" + COMMIT + "`. Download SHA-256 values are checked against the Git LFS source objects.",
        "- Three buildings selected by metadata before inspecting held-out results: two retail buildings and one office. This is an exploratory sample, not a representative commercial fleet.",
        "- Raw readings are hourly kWh in local time. For these one-hour intervals, their numerical value is average kW. Source: [meter documentation](https://github.com/buds-lab/building-data-genome-project-2/wiki/Meters-data-features).",
        "- Fit January–September 2016; select a method using October–December 2016 WAPE; refit on eligible 2016 data; test January–December 2017. The held-out year is not used for model choice or tuning.",
        "- Forecast 24 hours at each local midnight using prior-day, two-day, week and two-week lags, previous complete-day summaries and calendar features. No future actual weather or load is used as a forecast input. Models remain fixed throughout 2017; observed past test days become available at subsequent origins.",
        "- Compare previous-day, previous-week, Ridge regression and histogram gradient boosting. Hyperparameters are fixed in the script; there is no test-set search.",
        "- Missing, nonfinite and nonpositive readings are treated as questionable, not interpolated. All methods use the same complete-day mask, including complete lag features. DST transition days and days depending on those lagged observations are excluded. Tiny positive readings remain in the primary results, explicitly flagged below.", "",
        "## Next-day forecast results", "",
        "WAPE = total absolute error / total observed load. Lower is better. MAE is in kW. A validation winner can lose on future data.", "",
        "| Building | Test days | Selected on 2016 validation | Previous-day WAPE | Selected WAPE | Previous-day MAE | Selected MAE |",
        "|---|---:|---|---:|---:|---:|---:|"]
    for _, row in selected.iterrows():
        building = row.building
        base = previous.loc[building]
        count = int(quality.set_index("building").loc[building, "test_days"])
        lines.append(f"| {building} | {count} | {row.method} | {base.wape_pct:.2f}% | {row.wape_pct:.2f}% | {base.mae_kw:.3f} | {row.mae_kw:.3f} |")
    lines += ["", "## Data-quality and distribution changes", "",
        "| Building | Mean kW 2016 | Mean kW 2017 | 2017 positive readings below 0.001 kW | Excluded 2017 days |",
        "|---|---:|---:|---:|---:|"]
    for _, row in quality.iterrows():
        lines.append(f"| {row.building} | {row.mean_2016_kw:.3f} | {row.mean_2017_kw:.3f} | {int(row.near_zero_test_hours)} | {int(row.excluded_test_days)} |")
    lines += ["", "The office series undergoes a large change and contains thousands of tiny positive readings. These could reflect meter/data problems or changed operations; this experiment cannot establish the cause. Large percentage errors on this series are a warning about data quality and model drift. The office has not been silently removed or used to retune the model. Its simulated financial result should not guide investment decisions.", "",
        "## Scheduling replay", "",
        "The battery is hypothetical: 20 kWh, 5 kW charge/discharge, 94% efficiency each direction, SOC bounds 15–95%, initial SOC 40%, terminal SOC at least 40%. Each day is an independent scenario. No controllable ovens/HVAC/defrost are added to aggregate metered load.", "",
        "Assumed hourly prices are EUR 0.12/kWh at 00:00–07:00 and 23:00–24:00, EUR 0.30 at 12:00–18:00, and EUR 0.20 otherwise. The capacity threshold is the building's 2016 positive-load 95th percentile, with an artificial EUR 1 per excess kW per hourly slot in the objective. These are scenario parameters, not historical tariffs or statutory Greek demand charges.", "",
        "A schedule is optimized against a forecast, then its battery commands are replayed against observed load. Export is prohibited by the model. A replay below -0.02 kW or above 500.02 kW is flagged infeasible, allowing 0.02 kW for rounded output. Infeasible plans receive no energy-cost result; they are not counted as successful savings.", "",
        "| Building | Selected method | Solver failures | Infeasible on measured load | Total test days |",
        "|---|---|---:|---:|---:|"]
    for _, row in selected.iterrows():
        group = dispatch[(dispatch.building == row.building) & (dispatch.method == row.method)]
        lines.append(f"| {row.building} | {row.method} | {int(group.solver_failed.sum())} | {int(group.replay_infeasible.sum())} ({100*group.replay_infeasible.mean():.1f}%) | {len(group)} |")
    oracle = dispatch[dispatch.method == "perfect_foresight"]
    lines += ["", f"Across all methods and buildings, {len(dispatch):,} daily optimization problems were evaluated. The perfect-foresight diagnostic uses actual future load and has {int(oracle.solver_failed.sum())} solver failures and {int(oracle.replay_infeasible.sum())} infeasible replays. This establishes performance with known demand, not forecast reliability.", "",
        "**Finding:** numerical solver success under a forecast does not guarantee physical feasibility under realized demand. The forecast-driven plans attempt to discharge more than the building consumes on many days. Autonomous deployment needs a real-time no-export/SOC controller and re-optimization, in addition to model monitoring and fallback forecasts.", "",
        "For context only, the two retail buildings' hypothetical battery energy-cost results with perfect future-load knowledge are:", "",
        "| Building | Baseline energy cost | Energy-cost reduction with hindsight | Reduction |",
        "|---|---:|---:|---:|"]
    for building in BUILDINGS[:2]:
        group = oracle[oracle.building == building]
        base = group.baseline_energy_eur.sum()
        reduction = (group.baseline_energy_eur-group.replayed_energy_eur).sum()
        lines.append(f"| {building} | EUR {base:.2f} | EUR {reduction:.2f} | {100*reduction/base:.2f}% |")
    lines += ["", "These are modeled gross energy-charge changes over eligible test days. They exclude equipment purchase, degradation economics, maintenance and verified demand-charge savings. Perfect foresight optimizes the combined scenario objective, not energy cost alone. Do not interpret these figures as achieved savings or as an ML benefit. Per-method feasible-day subsets differ, so their totals in `dispatch_daily.csv` must not be compared as if they covered the same days.", "",
        "## Conclusions", "",
        "- ML can improve a stable building's forecast, but it did not beat the simplest baseline everywhere.",
        "- Introduce per-building model monitoring and rolling validation, with a simple baseline available as fallback.",
        "- Detect missing values, near-zero plateaus and operating-regime changes before trusting forecast-driven schedules.",
        "- Add closed-loop dispatch guards before autonomous battery control. More training alone will not enforce physical limits under forecast error.", "",
        "![Forecast and schedule replay results](benchmark.png)", "",
        "## Reproduce and inspect", "",
        "Run `python scripts/run_real_data_benchmark.py` from the repository. The experiment additionally needs pandas, scikit-learn and matplotlib; exact versions are in `manifest.json`. Raw files are cached under `.agents/real_data/` and are not put into the operational telemetry database or version control. The initial source download is approximately 174 MB; outputs retain only three buildings.", "",
        "Files: `forecast_metrics.csv`, `validation_metrics.csv`, `data_quality.csv`, `heldout_predictions.csv`, `dispatch_daily.csv`, `manifest.json`. Source and adapted data/plots in this report are attributed to Miller et al., *The Building Data Genome Project 2*, Scientific Data 7, 368 (2020), [DOI](https://doi.org/10.1038/s41597-020-00712-x), under the source repository's [CC BY-SA license](https://github.com/buds-lab/building-data-genome-project-2/blob/master/LICENSE). This report is shared under CC BY-SA 4.0."]
    (OUT / "REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    positions = np.arange(len(BUILDINGS))
    before = [previous.loc[b, "mae_kw"] for b in BUILDINGS]
    after = [selected.set_index("building").loc[b, "mae_kw"] for b in BUILDINGS]
    for offset, values, label, color in [(-.18, before, "Previous-day baseline", "#64748b"), (.18, after, "Model selected on 2016 validation", "#2563eb")]:
        bars = axes[0].bar(positions+offset, values, width=.36, label=label, color=color)
        axes[0].bar_label(bars, fmt="%.2f", padding=3, fontsize=9)
    axes[0].set_ylabel("Mean absolute forecast error (kW); lower is better")
    axes[0].set_title("2017 held-out next-day forecasts")
    axes[0].legend(fontsize=8)
    axes[0].set_ylim(0, max(before+after)*1.35)
    failure = []
    for b in BUILDINGS:
        method = selected.set_index("building").loc[b, "method"]
        group = dispatch[(dispatch.building == b) & (dispatch.method == method)]
        failure.append(100*group.replay_infeasible.mean())
    bars = axes[1].bar(positions, failure, width=.6, color="#dc2626")
    axes[1].bar_label(bars, fmt="%.1f%%", padding=3)
    axes[1].set_ylabel("Days attempting prohibited export (%)")
    axes[1].set_title("Forecast-driven battery replay failures")
    axes[1].set_ylim(0, 110)
    for ax in axes:
        ax.set_xticks(positions, [labels[b] for b in BUILDINGS], fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Real load data; experimental forecasts and hypothetical battery", fontsize=13)
    fig.text(.5, .01, "BDG2 (Miller et al., 2020), CC BY-SA • Office has a severe load/data regime change • No real-world savings claim", ha="center", fontsize=8)
    fig.tight_layout(rect=(0, .045, 1, .96))
    fig.savefig(OUT / "benchmark.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
