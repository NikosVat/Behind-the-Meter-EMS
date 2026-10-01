# Runtime day-ahead forecast replay

60-day rolling history; local-midnight 24h forecast; seven chronological validation days; ridge requires 5% validation MAE improvement over best persistence; zero retained; gap/DST exclusions; no operational DB

This evaluates the shipped NumPy runtime selector, distinct from the fixed research MLP and firmware predictor. Earlier test-day observations may be used at later origins; future measurements are never available at the current origin.

| Building | Held-out hours | Excluded days | Runtime WAPE | Previous day | Previous week | Selected days |
|---|---:|---:|---:|---:|---:|---|
| Wolf_retail_Marcella | 8568 | 8 | 29.71% | 32.56% | 34.81% | {'previous_week': 95, 'ridge': 173, 'previous_day': 89} |
| Panther_retail_Lester | 8328 | 18 | 19.49% | 19.83% | 26.80% | {'previous_day': 154, 'ridge': 161, 'previous_week': 32} |
| Hog_food_Morgan | 8568 | 8 | 8.62% | 8.93% | 13.70% | {'previous_day': 194, 'ridge': 35, 'previous_week': 128} |

These are three convenience sites, not a representative fleet. BDG2 hourly electricity channels are used as average-kW equivalents for their one-hour intervals. Source timestamps omit DST folds; ambiguous transition/dependent days are excluded. API forecasts use hourly sample means, whose quality must be verified for a real site. No capacity protection, penalty avoidance, dispatch benefit, or achieved savings is established.

Source: Building Data Genome 2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x. Data/adapted outputs: CC BY-SA 4.0. See the original benchmark manifest for the pinned source revision/checksums.

Reproduce: `python scripts/evaluate_runtime_forecast.py` after caching BDG2 data and installing `.[research]`.
