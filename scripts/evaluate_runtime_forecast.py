"""Replay the shipped runtime selector at each 2017 local-midnight origin.

Requires the research extra and cached BDG2 electricity/metadata. Never writes the
operational database. Past test-day actuals become available at later origins.
"""
from pathlib import Path
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo
from collections import Counter
import json
import sys
import platform

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from optimization_engine.forecasting import ForecastUnavailable, forecast_day_ahead
from benchmark_day_ahead import day_ahead_features, metrics

BUILDINGS = ["Wolf_retail_Marcella", "Panther_retail_Lester", "Hog_food_Morgan"]


def main():
    cache, out = ROOT / ".agents/real_data", ROOT / "reports/real_data"
    raw = pd.read_csv(cache / "electricity.csv", usecols=["timestamp"] + BUILDINGS,
                      index_col="timestamp", parse_dates=True)
    metadata = pd.read_csv(cache / "metadata.csv").set_index("building_id")
    summaries, records = [], []
    for building in BUILDINGS:
        zone = str(metadata.loc[building, "timezone"])
        tz = ZoneInfo(zone)
        x, y, eligible = day_ahead_features(raw[building], zone)
        target_days = sorted(set(y.index[eligible & (y.index >= "2017-01-01")].normalize()))
        selected = Counter()
        actuals, predictions, previous_days, previous_weeks = [], [], [], []
        skipped = 0
        for day in target_days:
            lookback = y.loc[(y.index >= day - pd.Timedelta(days=60)) & (y.index < day)]
            # Source wall-clock timestamps have no fold/offset. Discard transition
            # days rather than inventing an interpretation of their hours.
            hours = {}
            for stamp, value in lookback.items():
                start = stamp.normalize().to_pydatetime().replace(tzinfo=tz)
                stop = start + timedelta(days=1)
                if (stop.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() != 86400:
                    continue
                if np.isfinite(value) and value >= 0:
                    hours[stamp.to_pydatetime().replace(tzinfo=tz)] = float(value)
            try:
                with threadpool_limits(limits=1):
                    result = forecast_day_ahead(hours, day.date(), zone)
            except ForecastUnavailable:
                skipped += 1
                continue
            selected[result.model] += 1
            idx = pd.date_range(day, periods=24, freq="h")
            actual = y.loc[idx].to_numpy()
            actuals.extend(actual)
            predictions.extend(result.load_kw)
            previous_days.extend(x.loc[idx, "lag_24"])
            previous_weeks.extend(x.loc[idx, "lag_168"])
            for stamp, actual_kw, predicted_kw in zip(idx, actual, result.load_kw):
                records.append(dict(building=building, timestamp=stamp.isoformat(),
                                    origin=result.origin, model=result.model,
                                    actual_kw=float(actual_kw), predicted_kw=predicted_kw))
        row = dict(building=building, hours=len(actuals), excluded_days=365 - len(actuals) // 24,
                   forecast_unavailable_days=skipped, selected_days=dict(selected),
                   runtime=metrics(actuals, predictions), previous_day=metrics(actuals, previous_days),
                   previous_week=metrics(actuals, previous_weeks))
        summaries.append(row)
        print(json.dumps(row), flush=True)
    manifest = dict(protocol="60-day rolling history; local-midnight 24h forecast; seven chronological validation days; ridge requires 5% validation MAE improvement over best persistence; zero retained; gap/DST exclusions; no operational DB",
                    python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, results=summaries)
    out.mkdir(parents=True, exist_ok=True)
    (out / "runtime_forecast_metrics.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pd.DataFrame(records).to_csv(out / "runtime_forecast_predictions.csv", index=False)
    lines = ["# Runtime day-ahead forecast replay", "", manifest["protocol"], "",
             "This evaluates the shipped NumPy runtime selector, distinct from the fixed research MLP and firmware predictor. Earlier test-day observations may be used at later origins; future measurements are never available at the current origin.", "",
             "| Building | Held-out hours | Excluded days | Runtime WAPE | Previous day | Previous week | Selected days |",
             "|---|---:|---:|---:|---:|---:|---|"]
    for row in summaries:
        lines.append(f"| {row['building']} | {row['hours']} | {row['excluded_days']} | {row['runtime']['wape_pct']:.2f}% | {row['previous_day']['wape_pct']:.2f}% | {row['previous_week']['wape_pct']:.2f}% | {row['selected_days']} |")
    lines.extend(["", "These are three convenience sites, not a representative fleet. BDG2 hourly electricity channels are used as average-kW equivalents for their one-hour intervals. Source timestamps omit DST folds; ambiguous transition/dependent days are excluded. API forecasts use hourly sample means, whose quality must be verified for a real site. No capacity protection, penalty avoidance, dispatch benefit, or achieved savings is established.", "",
                  "Source: Building Data Genome 2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x. Data/adapted outputs: CC BY-SA 4.0. See the original benchmark manifest for the pinned source revision/checksums.", "",
                  "Reproduce: `python scripts/evaluate_runtime_forecast.py` after caching BDG2 data and installing `.[research]`."])
    (out / "RUNTIME_FORECAST_BENCHMARK.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
