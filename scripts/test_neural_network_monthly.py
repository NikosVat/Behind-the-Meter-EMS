"""Held-out monthly MLP day-ahead benchmark; no hardware or savings validation."""
from __future__ import annotations
import calendar
import json
from pathlib import Path
import sys
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_day_ahead import day_ahead_features, evaluate, metrics
DATA_PATH = ROOT / '.agents' / 'real_data' / 'electricity.csv'
OUT_DIR = ROOT / 'reports' / 'real_data'


def run_monthly_evaluation(building_id='Wolf_retail_Marcella'):
    raw = pd.read_csv(DATA_PATH, usecols=['timestamp', building_id], index_col='timestamp', parse_dates=True)
    metadata = pd.read_csv(DATA_PATH.parent / 'metadata.csv').set_index('building_id')
    frame, meta = evaluate(raw[building_id], metadata.loc[building_id, 'timezone'])
    meta['building_id'] = building_id
    rows = []
    for number, group in frame.groupby(frame.index.month):
        row = {'month_num': number, 'month_name': calendar.month_abbr[number], 'hours': len(group),
            'actual_kwh': group.actual_kw.sum(), 'pred_kwh': group.mlp.sum(),
            'actual_peak_kw': group.actual_kw.max(), 'pred_peak_kw': group.mlp.max(),
            'heuristic_upper_peak_kw': group.heuristic_upper_kw.max(),
            'heuristic_coverage_pct': (group.actual_kw <= group.heuristic_upper_kw).mean()*100,
            'heuristic_exceedance_hours': int((group.actual_kw > group.heuristic_upper_kw).sum())}
        row.update(metrics(group.actual_kw, group.mlp))
        for method in ('previous_day', 'previous_week'):
            row[method + '_wape_pct'] = metrics(group.actual_kw, group[method])['wape_pct']
        rows.append(row)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_DIR / 'monthly_heldout_predictions.csv', index_label='timestamp_local')
    pd.DataFrame(rows).to_csv(OUT_DIR / 'monthly_metrics.csv', index=False)
    (OUT_DIR / 'monthly_manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    return pd.DataFrame(rows), meta


def plot_monthly_accuracy(res_df, building_id):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(res_df.month_name, res_df.r2, marker='o')
    axes[0].axhline(0, color='grey', linewidth=.7)
    axes[0].set_ylabel('MLP R² (negative values allowed)')
    for column, label in [('wape_pct', 'MLP'), ('previous_day_wape_pct', 'Previous day'), ('previous_week_wape_pct', 'Previous week')]:
        axes[1].plot(res_df.month_name, res_df[column], marker='o', label=label)
    axes[1].set_ylabel('WAPE (%)'); axes[1].legend()
    fig.suptitle(f'{building_id}: 2017 held-out 24h forecasts issued at local midnight')
    fig.tight_layout(); fig.savefig(OUT_DIR / 'monthly_neural_network_accuracy_profile.png', dpi=180); plt.close(fig)


def plot_monthly_peaks_and_safety(res_df, building_id):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for column, label in [('actual_peak_kw', 'Observed peak'), ('pred_peak_kw', 'MLP peak'), ('heuristic_upper_peak_kw', 'Maximum heuristic upper value')]:
        axes[0].plot(res_df.month_name, res_df[column], marker='o', label=label)
    axes[0].set_ylabel('kW'); axes[0].legend()
    axes[1].bar(res_df.month_name, res_df.heuristic_exceedance_hours)
    axes[1].set_ylabel('Hours above heuristic bound')
    fig.suptitle(f'{building_id}: heuristic bounds are uncalibrated; contracted capacity unknown')
    fig.tight_layout(); fig.savefig(OUT_DIR / 'monthly_peak_and_safety_envelope.png', dpi=180); plt.close(fig)


def write_monthly_report(res_df, meta):
    lines = ['# Monthly MLP day-ahead evaluation', '', f"Building: `{meta['building_id']}`; local timezone: `{meta['timezone']}`.",
        f"Fit fixed MLP (48,24), seed 42, on {meta['train_hours']} eligible 2016 hours; test {meta['test_hours']} hours in 2017 ({meta['excluded_test_days']} excluded days). Training plus prediction: {meta['train_sec']} seconds.", '',
        'Forecast origin is local midnight. All 24 forecasts use lag 24/48/168 and previous complete-day statistics available before midnight. No intraday actuals or future weather enter the inputs. Early stopping uses only 2016 data; no 2017 tuning.',
        'Hourly kWh readings equal average kW for one-hour intervals. Zero readings are retained; missing/nonfinite/negative readings stay missing. Reindex before shifting, exclude incomplete feature/target days and local DST transition days and dependent lags. Energy totals cover eligible hours only.', '',
        'The heuristic upper value is MLP + 1.645 × 2016 weekday/hour load standard deviation. It is not a calibrated P95, not the firmware forecaster, and not a capacity threshold. Hourly exceedances are recorded separately from maxima. Contracted capacity and breach/penalty outcomes are unknown.', '',
        '| Month | Hours | MLP R² | MLP MAE kW | MLP WAPE | Previous-day WAPE | Previous-week WAPE | Heuristic coverage | Exceedance hours |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in res_df.itertuples():
        lines.append(f'| {r.month_name} | {r.hours} | {r.r2:.3f} | {r.mae_kw:.3f} | {r.wape_pct:.2f}% | {r.previous_day_wape_pct:.2f}% | {r.previous_week_wape_pct:.2f}% | {r.heuristic_coverage_pct:.2f}% | {r.heuristic_exceedance_hours} |')
    lines += ['', 'No accuracy, capacity protection, or savings guarantee is inferred. Compare baselines on the same eligible hours. See CSVs for exact metrics and observed/forecast energy and peaks.', '',
        'Source: Building Data Genome 2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x. Source data and adapted outputs: CC BY-SA 4.0.', '',
        '![Monthly forecast errors](monthly_neural_network_accuracy_profile.png)', '![Heuristic exceedances](monthly_peak_and_safety_envelope.png)']
    (OUT_DIR / 'MONTHLY_NEURAL_NETWORK_BENCHMARK.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main():
    result, meta = run_monthly_evaluation()
    plot_monthly_accuracy(result, meta['building_id'])
    plot_monthly_peaks_and_safety(result, meta['building_id'])
    write_monthly_report(result, meta)
    print(result.to_string(index=False))

if __name__ == '__main__':
    main()
