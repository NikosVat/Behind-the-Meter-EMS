"""Plot recorded hypothetical battery replay results. Never extrapolate achieved ROI."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / 'reports' / 'real_data'


def summarize_scenario(dispatch):
    """Paired observed/scenario costs only on physically feasible recorded days."""
    dispatch = dispatch.copy()
    dispatch['month'] = pd.to_datetime(dispatch.date).dt.to_period('M').astype(str)
    valid = (~dispatch.solver_failed.fillna(True).astype(bool)
        & ~dispatch.replay_infeasible.fillna(True).astype(bool)
        & np.isfinite(dispatch.replayed_energy_eur) & np.isfinite(dispatch.baseline_energy_eur))
    dispatch['feasible_days'] = valid.astype(int)
    dispatch['excluded_days'] = (~valid).astype(int)
    dispatch.loc[~valid, ['baseline_energy_eur', 'replayed_energy_eur']] = np.nan
    grouped = dispatch.groupby(['building', 'method', 'month'], as_index=False).agg(
        baseline_energy_eur=('baseline_energy_eur', 'sum'), replayed_energy_eur=('replayed_energy_eur', 'sum'),
        feasible_days=('feasible_days', 'sum'), excluded_days=('excluded_days', 'sum'))
    grouped.loc[grouped.feasible_days == 0, ['baseline_energy_eur', 'replayed_energy_eur']] = np.nan
    grouped['energy_difference_eur'] = grouped.baseline_energy_eur - grouped.replayed_energy_eur
    return grouped


def plot_12_month_cost_comparison():
    dispatch = pd.read_csv(OUT_DIR / 'dispatch_daily.csv')
    manifest = json.loads((OUT_DIR / 'manifest.json').read_text(encoding='utf-8'))
    # Hindsight is explicit: no forecast quality or field-savings attribution.
    oracle = dispatch[dispatch.method == 'perfect_foresight']
    summary = summarize_scenario(oracle)
    summary.to_csv(OUT_DIR / 'cost_scenario_monthly.csv', index=False)
    fig, axes = plt.subplots(len(summary.building.unique()), 1, figsize=(12, 10), squeeze=False)
    for ax, (building, group) in zip(axes[:, 0], summary.groupby('building')):
        positions = np.arange(len(group))
        ax.bar(positions-.18, group.baseline_energy_eur, .36, label='Observed-load baseline under assumed prices')
        ax.bar(positions+.18, group.replayed_energy_eur, .36, label='Hypothetical battery with future load known')
        ax.set_xticks(positions, group.month.str[-2:]); ax.set_ylabel('Scenario energy charge (EUR)')
        suffix = '; severe data/load drift' if building == 'Lamb_office_Peggy' else ''
        ax.set_title(f'{building}: {int(group.feasible_days.sum())} feasible recorded days; {int(group.excluded_days.sum())} failed replays excluded{suffix}', fontsize=10)
        ax.legend(fontsize=8)
    fig.suptitle('Hindsight battery scenario on eligible days: no achieved savings or annual ROI claim')
    fig.text(.5, .01, 'BDG2 (Miller et al., 2020), CC BY-SA 4.0; costs exclude equipment, degradation, maintenance and demand charges', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .035, 1, .95))
    fig.savefig(OUT_DIR / 'monthly_cost_reduction_full_year.png', dpi=180); plt.close(fig)
    return summary, manifest


def plot_daily_waterfall_and_pillars(summary=None):
    if summary is None:
        summary, _ = plot_12_month_cost_comparison()
    annual = summary.groupby('building')[['baseline_energy_eur', 'replayed_energy_eur', 'energy_difference_eur', 'feasible_days', 'excluded_days']].sum()
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    annual[['baseline_energy_eur', 'replayed_energy_eur']].plot.bar(ax=axes[0])
    axes[0].set_ylabel('Scenario energy charges on paired feasible days (EUR)')
    axes[0].legend(['Baseline', 'Hypothetical battery; perfect future load'], fontsize=8)
    annual.energy_difference_eur.plot.bar(ax=axes[1], color='#0284c7')
    axes[1].set_ylabel('Modeled gross energy-charge difference (EUR)')
    axes[1].axhline(0, color='grey')
    fig.suptitle('Recorded perfect-foresight scenario; no tariff, penalty or payback claim')
    fig.tight_layout(); fig.savefig(OUT_DIR / 'cost_reduction_waterfall_and_pillars.png', dpi=180); plt.close(fig)


def plot_recorded_day(filenames=None):
    """Inspect the first recorded feasible Wolf hindsight scenario, not a best day."""
    sys.path.insert(0, str(ROOT))
    from scripts.run_real_data_benchmark import RATES, schedule
    dispatch = pd.read_csv(OUT_DIR / 'dispatch_daily.csv')
    eligible = dispatch[(dispatch.building == 'Wolf_retail_Marcella')
        & (dispatch.method == 'perfect_foresight') & ~dispatch.solver_failed
        & ~dispatch.replay_infeasible.fillna(True)].sort_values('date')
    first = eligible.iloc[0]
    frame = pd.read_csv(OUT_DIR / 'heldout_predictions.csv')
    frame = frame[(frame.building == first.building)
        & frame.timestamp_local.str.startswith(first.date)].sort_values('timestamp_local')
    actual = frame.actual_kw.to_numpy()
    net = schedule(actual, first.capacity_kw)
    if net is None or len(actual) != 24:
        raise ValueError('Recorded diagnostic day cannot be replayed')
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(range(24), actual, label='Measured baseline')
    axes[0].plot(range(24), actual+net, label='Hypothetical battery; future load known')
    axes[0].axhline(first.capacity_kw, color='grey', linestyle=':', label='Scenario percentile threshold; contract unknown')
    axes[0].set_ylabel('Average hourly kW'); axes[0].legend(fontsize=8)
    axes[1].bar(range(24), actual * RATES, alpha=.5, label='Baseline energy charge')
    axes[1].plot(range(24), np.maximum(0, actual+net) * RATES, marker='o', label='Scenario energy charge')
    axes[1].set_ylabel('Assumed hourly energy charge (EUR)'); axes[1].set_xlabel('Local hour'); axes[1].legend(fontsize=8)
    fig.suptitle(f'{first.building}, {first.date}: first eligible hindsight day, no achieved savings claim')
    fig.text(.5, .01, 'Hypothetical 20kWh/5kW BESS and assumed prices; no equipment or demand-charge economics; BDG2 CC BY-SA 4.0', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .035, 1, .95))
    for filename in filenames or ['realistic_peak_shaving_real_data.png']:
        fig.savefig(OUT_DIR / filename, dpi=180)
    plt.close(fig)


def main():
    summary, manifest = plot_12_month_cost_comparison()
    plot_daily_waterfall_and_pillars(summary)
    plot_recorded_day(['realistic_peak_shaving_real_data.png', 'realistic_operational_savings.png',
        'savings_analysis.png', 'scientific_energy_savings_24h.png', 'scientific_time_and_financial_breakdown.png'])
    lines = ['# Cost visualization provenance', '',
        'These plots replace illustrative savings and payback amounts with recorded battery replay data from `dispatch_daily.csv`. Legacy filenames (realistic/scientific savings and peak-shaving) now show the first eligible hindsight scenario day; the filenames do not imply achieved savings. The neural-network figure uses the corrected midnight-origin MLP evaluation.',
        'Only perfect-foresight scenarios are displayed; future measured load is known to the optimizer. Baseline and managed costs use the same physically feasible days. Excluded days receive no financial result. Results are not extrapolated to a full year.', '',
        'Dispatch eligibility follows the original recorded benchmark, whose documented BDG2 quality policy excludes nonpositive readings. The newer MLP/profile evaluation retains zero targets. Their eligible-day counts therefore differ; these cost figures do not measure the newer MLP forecast benefit.', '',
        'Assumptions from `manifest.json`:', '', json.dumps(manifest['scenario'], indent=2), '',
        'Energy-charge differences exclude hardware purchase, battery degradation, maintenance, financing and verified demand-charge savings. Contracted capacity is a hypothetical percentile threshold, not a known customer contract. No achieved savings, avoided penalties, power-factor benefits or payback is claimed.', '',
        'Source: BDG2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x; adapted outputs CC BY-SA 4.0.', '',
        '![Paired scenario totals](cost_reduction_waterfall_and_pillars.png)', '![Monthly scenario costs](monthly_cost_reduction_full_year.png)']
    (OUT_DIR / 'COST_SCENARIO.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')

if __name__ == '__main__':
    main()
