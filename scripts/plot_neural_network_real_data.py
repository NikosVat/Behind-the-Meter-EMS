"""Plot the recorded, honest 24h MLP forecasts and hindsight battery scenario."""
from pathlib import Path
import sys
import matplotlib.pyplot as plt
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_neural_network_monthly import run_monthly_evaluation, OUT_DIR
from scripts.plot_comprehensive_cost_reduction import plot_recorded_day


def main():
    # One authoritative evaluation path keeps gap policy and horizon identical.
    run_monthly_evaluation()
    frame = pd.read_csv(OUT_DIR / 'monthly_heldout_predictions.csv', index_col='timestamp_local', parse_dates=True)
    frame = frame.loc['2017-01-01':'2017-01-07']
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True)
    for column, label in [('actual_kw', 'Observed'), ('mlp', 'MLP issued at midnight')]:
        axes[0].plot(frame.index, frame[column], label=label)
    for column, label in [('previous_day', 'Previous-day baseline'), ('previous_week', 'Previous-week baseline')]:
        axes[1].plot(frame.index, frame[column], label=label)
    axes[1].plot(frame.index, frame.actual_kw, color='black', alpha=.5, label='Observed')
    for ax in axes:
        ax.set_ylabel('Hourly average power (kW)'); ax.legend(fontsize=8)
    fig.suptitle('Wolf retail: fixed first week of 2017; all 24 forecasts frozen at local midnight')
    fig.tight_layout(); fig.savefig(OUT_DIR / 'neural_network_real_data_forecast.png', dpi=180); plt.close(fig)
    plot_recorded_day(['realistic_peak_shaving_real_data.png'])

if __name__ == '__main__':
    main()
