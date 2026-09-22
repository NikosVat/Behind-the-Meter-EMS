# Real data for load forecasting

Start with public measured data to develop and benchmark the forecasting pipeline. Validate a deployed model on the target facility before relying on its schedules. Public whole-building readings do not identify controllable equipment separately and cannot substantiate savings at a Greek bakery or cold store.

## Recommended starting point: Building Data Genome 2

[Official repository and download folders](https://github.com/buds-lab/building-data-genome-project-2)

BDG2 contains 3,053 energy meters from 1,636 non-residential buildings, with hourly observations for 2016–2017. It includes multiple meter types, building metadata and weather. Select **electricity** meters and relevant building-use categories from the metadata. Begin with a small selection rather than importing the whole collection. Review units and cleaning decisions in the dataset documentation, and retain missing-data flags.

The meter files are under `data/meters/`, metadata under `data/metadata/`, and weather under `data/weather/`. Follow the repository license and attribution requirements. This is the best initial fit for this project's commercial-building forecasting work.

## Alternative benchmark: UCI Electricity Load Diagrams

[Official dataset](https://archive.ics.uci.edu/dataset/321/electricityloaddiagrams20112014)

Measured consumption series for 370 clients, sampled every 15 minutes during 2011–2014. Useful for comparing forecasting methods across many meters. Check the dataset's units, timestamp and daylight-saving conventions before aggregating to hourly values; do not treat missing or pre-activation values as normal operation.

## Acquiring relevant local data

Ask a participating business for an interval export from its electricity supplier, meter portal or building management system, if available. Request timestamps with timezone, interval duration, kWh or average kW, meter identity, and notes on closures or changes in operating hours. Monthly bills can support billing checks but cannot reconstruct an hourly load curve.

If interval exports are unavailable, collect readings with a meter that measures active energy. The current firmware's assumed-voltage/assumed-power-factor readings must remain labeled estimates. As a practical initial target, collect several weeks covering repeated weekdays and weekends; seasonal generalization requires much longer coverage. Record controllable equipment operation so its load can be separated from the baseline that the solver adds device schedules to.

## Evaluation protocol

1. Keep source, units, timezone, building and measurement-quality metadata alongside observations.
2. Separate real measurements, simulator outputs and estimated-power telemetry.
3. Split chronologically into training, validation and untouched future test periods. Use only features available when each forecast would have been issued, including historical weather forecasts rather than future observed weather.
4. Compare same-hour-last-week, regularized regression and gradient-boosted trees before selecting a model. Report error by facility and forecast horizon, including peak hours.
5. Test scheduling outcomes separately: operating constraints, peak breaches and cost under a consistent tariff. Whole-building forecasting accuracy alone is not proof of savings or equipment-level flexibility.

See [scikit-learn's time-series forecasting example](https://scikit-learn.org/stable/auto_examples/applications/plot_time_series_lagged_features.html) for lagged features and evaluation that respects time order.
