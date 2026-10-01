# Adaptive weekly profile: hourly Python evaluation

This is an hourly-resolution analogue of the edge forecast formulas using BDG2 hourly averages. It does not run C++ firmware, validate ESP32 memory/latency, or reproduce five-second sample momentum. Initial mean/std profiles and clamp anchors are estimated from 2016, unlike the deployed firmware's default profile.

All 24 day-ahead means are frozen at local midnight. Daily EMA adaptation happens after the evaluated day's 24 samples. The separate next-hour diagnostic observes current hourly load before predicting the following hour and scores only hours 01:00-23:00; it must not be reported as 24h forecast accuracy.
Zero targets are retained. Missing/nonfinite/negative readings, incomplete lag/target days, DST transition/dependent dates are excluded on a regular hourly index. All 24h methods share the lag24/48/168 eligibility mask. Skipped days do not update the profile; sample momentum is reset after gaps. The analogue retains the firmware-style 0.05 kW prediction/sample floor.

| Building | Test days | Previous-day WAPE | Previous-week WAPE | Static profile WAPE | Adaptive 24h WAPE | Next-hour WAPE | Next-hour heuristic coverage | Bound exceedance hours |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Panther_retail_Lester | 347 | 19.83% | 26.80% | 60.96% | 31.45% | 16.21% | 97.93% | 165 |
| Wolf_retail_Marcella | 357 | 32.56% | 34.81% | 25.70% | 25.59% | 13.72% | 98.68% | 108 |
| Lamb_office_Peggy | 357 | 27.18% | 62.48% | 1109.23% | 304.74% | 129.29% | 99.77% | 19 |

The next-hour heuristic upper value is mean + 1.645 x adaptive profile dispersion. Its empirical coverage does not establish calibrated P95, no-breach reliability, or penalty avoidance. Contracted capacity and capacity-breach outcomes are unknown. Profile quality and distribution shifts require monitoring; the office series contains a severe operating/data regime change.

Exact MAE/RMSE/WAPE and exceedance counts are in `tinyml_metrics.json`. Source: Building Data Genome 2, Miller et al. (2020), https://doi.org/10.1038/s41597-020-00712-x. Source and adapted outputs: CC BY-SA 4.0.
