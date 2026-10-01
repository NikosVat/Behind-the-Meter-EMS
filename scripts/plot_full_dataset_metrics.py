"""Plot recorded cohort forecast errors without tuning or unsupported guarantees."""
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports' / 'real_data'


def main():
    frame = pd.read_csv(OUT / 'cohort_metrics.csv')
    frame = frame[frame.status == 'EVALUATED']
    positions = np.arange(len(frame))
    fig, axes = plt.subplots(2, 1, figsize=(14, 9), sharex=True)
    for shift, column, label in [(-.25, 'previous_day_wape_pct', 'Previous day'), (0, 'wape_pct', 'MLP'), (.25, 'previous_week_wape_pct', 'Previous week')]:
        axes[0].bar(positions+shift, frame[column], .25, label=label)
    axes[0].set_ylabel('Held-out 2017 WAPE (%)'); axes[0].legend()
    axes[1].bar(positions, frame.heuristic_exceedance_hours, color='#dc2626')
    axes[1].set_ylabel('Hours above uncalibrated MLP + historical-std bound')
    axes[1].set_xticks(positions, frame.building, rotation=35, ha='right', fontsize=9)
    wins = int((frame.wape_pct < frame.previous_day_wape_pct).sum())
    fig.suptitle(f'Exploratory cohort: midnight 24h forecasts; MLP beats previous day on {wins}/{len(frame)} buildings')
    fig.text(.5, .01, 'Complete-day common masks; contracts unknown; no capacity protection guarantee; BDG2 (Miller et al. 2020), CC BY-SA 4.0', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .035, 1, .95)); fig.savefig(OUT / 'full_dataset_metrics.png', dpi=180); plt.close(fig)

if __name__ == '__main__':
    main()
