"""Compatibility entry point for recorded battery scenario visualizations.

Legacy synthetic savings, penalties and ROI have been replaced by paired-day
replay figures. Scenario assumptions and limitations are in COST_SCENARIO.md.
"""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.plot_comprehensive_cost_reduction import main

if __name__ == '__main__':
    main()
