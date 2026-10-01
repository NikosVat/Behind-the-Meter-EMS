"""Exploratory commercial cohort; fixed MLP issued once per day, no safety claims."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_day_ahead import evaluate, metrics
DATA_DIR = ROOT / '.agents' / 'real_data'
OUT_REPORT = ROOT / 'reports' / 'real_data' / 'FULL_DATASET_MULTI_BUILDING_BENCHMARK.md'
CANDIDATES = ['Wolf_retail_Marcella', 'Panther_retail_Lester', 'Panther_retail_Kristina',
    'Panther_retail_Felix', 'Wolf_retail_Toshia', 'Fox_food_Francesco', 'Fox_food_Scott',
    'Hog_food_Morgan', 'Panther_office_Hannah', 'Panther_lodging_Russell']


def test_multi_meter_availability():
    results = {}
    for name in ['metadata.csv', 'electricity.csv', 'electricity_cleaned.csv', 'weather.csv', 'solar.csv', 'gas.csv', 'chilledwater.csv', 'water.csv']:
        path = DATA_DIR / name
        if not path.exists():
            results[name] = {'status': 'MISSING'}
            continue
        try:
            frame = pd.read_csv(path, nrows=5)
            results[name] = {'status': 'HEADER_READ', 'size_mb': round(path.stat().st_size / 1024**2, 2), 'columns': len(frame.columns)}
        except (ValueError, OSError, pd.errors.ParserError) as error:
            results[name] = {'status': 'ERROR', 'error': str(error)}
    return results


def run_commercial_cohort_benchmark():
    metadata = pd.read_csv(DATA_DIR / 'metadata.csv').set_index('building_id')
    available = [b for b in CANDIDATES if b in pd.read_csv(DATA_DIR / 'electricity.csv', nrows=0).columns]
    raw = pd.read_csv(DATA_DIR / 'electricity.csv', usecols=['timestamp'] + available, index_col='timestamp', parse_dates=True)
    rows = []
    for building in available:
        try:
            frame, meta = evaluate(raw[building], metadata.loc[building, 'timezone'])
        except ValueError as error:
            rows.append({'building': building, 'status': 'SKIPPED', 'reason': str(error)})
            continue
        row = {'building': building, 'status': 'EVALUATED', 'type': metadata.loc[building, 'primaryspaceusage'],
            **meta, 'mean_kw': frame.actual_kw.mean(), 'max_kw': frame.actual_kw.max(),
            **metrics(frame.actual_kw, frame.mlp),
            'heuristic_coverage_pct': (frame.actual_kw <= frame.heuristic_upper_kw).mean()*100,
            'heuristic_exceedance_hours': int((frame.actual_kw > frame.heuristic_upper_kw).sum())}
        for method in ('previous_day', 'previous_week'):
            row[method + '_wape_pct'] = metrics(frame.actual_kw, frame[method])['wape_pct']
        rows.append(row)
        print(building, {key: row[key] for key in ['test_hours', 'r2', 'wape_pct', 'previous_day_wape_pct', 'previous_week_wape_pct']}, flush=True)
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_REPORT.parent / 'cohort_metrics.csv', index=False)
    return rows


def generate_markdown_report(meter_results, cohort_results):
    lines = ['# Exploratory BDG2 commercial cohort benchmark', '',
        'Fixed MLP (48,24), seed 42, trained on eligible 2016 observations and tested on 2017. All 24 hours are forecast at local midnight using only lag 24/48/168 and previous-day summaries. No intraday actuals or future weather are used; early stopping is confined to training data.',
        'Timestamp gaps remain missing on a regular hourly index. Zeros are retained; negative/nonfinite values, incomplete feature/target days and local DST transition/dependent days are excluded. The same eligible hours are used for both baselines and MLP. Calendar source timestamps lack offsets/folds.', '',
        'Ten predefined commercial candidates form a convenience cohort; this is not the full dataset, a representative fleet, a deployment benchmark, or a hardware test.', '',
        '## File presence and header sampling', '',
        'Only the first five rows are parsed. This checks local file presence/readability, not whole-file integrity, units, weather integration, or channel quality.', '',
        '| File | Sample status | MB | Columns |', '|---|---|---:|---:|']
    for name, result in meter_results.items():
        lines.append(f"| {name} | {result['status']} | {result.get('size_mb', '-')} | {result.get('columns', '-')} |")
    lines += ['', '## Held-out 24h forecast results', '',
        '| Building | Hours | Excluded days | R² | MAE kW | MLP WAPE | Previous-day WAPE | Previous-week WAPE | Heuristic coverage | Exceedance hours |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in cohort_results:
        if row['status'] != 'EVALUATED':
            lines.append(f"\nSkipped `{row['building']}`: {row['reason']}\n")
            continue
        lines.append(f"| {row['building']} | {row['test_hours']} | {row['excluded_test_days']} | {row['r2']:.3f} | {row['mae_kw']:.3f} | {row['wape_pct']:.2f}% | {row['previous_day_wape_pct']:.2f}% | {row['previous_week_wape_pct']:.2f}% | {row['heuristic_coverage_pct']:.2f}% | {row['heuristic_exceedance_hours']} |")
    lines += ['', 'Heuristic upper value = MLP + 1.645 × 2016 weekday/hour load standard deviation. This is not calibrated P95 or the firmware model. Exceedance coverage does not establish capacity protection. Contracted capacities, capacity breaches and penalty savings are unknown. No test-set accuracy threshold or commercial readiness is asserted.', '',
        'Exact metrics, model protocol, fit times, baselines, excluded-day counts, optimizer iteration limits and missing capacity values are saved in `cohort_metrics.csv`. Reaching the iteration limit is reported rather than treated as converged. MLP may underperform simple baselines; validation-based model selection and monitoring are needed before deployment.', '',
        'Source: Building Data Genome 2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x. Source and adapted outputs: CC BY-SA 4.0.']
    limited = [r['building'] for r in cohort_results if r.get('reached_iteration_limit')]
    lines += ['', 'Training reached the 300-iteration limit (ConvergenceWarning): ' + (', '.join(limited) if limited else 'none') + '. Results use the recorded fit without test-set retuning.']
    lines += ['', '![Cohort baseline comparison and heuristic exceedances](full_dataset_metrics.png)']
    OUT_REPORT.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main():
    generate_markdown_report(test_multi_meter_availability(), run_commercial_cohort_benchmark())

if __name__ == '__main__':
    main()
