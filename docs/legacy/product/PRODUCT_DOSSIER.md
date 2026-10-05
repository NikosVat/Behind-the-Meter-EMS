# Competition and pilot dossier

Updated 2026-10-01. This replaces earlier unsupported savings, payback, competitor pricing, and hardware guarantees in the pitch materials.

## Product and customer hypothesis

Commercial operators with flexible equipment need schedules that respect production deadlines and electrical headroom. This prototype combines telemetry, load forecasts, effective tariff inputs, and equipment scheduling into advice staff can inspect. Start with one site and a few loads whose operating windows are agreed with its operator.

The proposition is an equipment-aware decision layer that can complement existing meters. Hardware accuracy, forecast quality, achieved savings, installation cost, and willingness to pay each need validation. MIT-licensed code and public-data models are not proprietary intellectual property.

## What is implemented

- FastAPI/SQLite telemetry, protected APIs, and dashboard with page-local API-key entry.
- Schedule Studio at 5/15/30/60-minute resolution, full-slot windows, contiguous or interruptible runs, priorities, and capacity warnings.
- Runtime ridge forecasting ([full-year replay](../../reports/real_data/RUNTIME_FORECAST_BENCHMARK.md)) compared with persistence on seven chronological validation days. ML selection needs 21 complete eligible feature days plus earlier day/week history.
- ESP32 current sensing with explicit power-estimate provenance, RTC/NTP synchronization, hourly averaging, and an adaptive edge predictor.
- Native C++ behavior tests and an actual PlatformIO ESP32 build.

Mode B has mathematical functions but no installed meter/voltage acquisition driver; CT-only dispatch fails closed when Mode B is selected. Recommendations advise staff; the prototype does not switch mains equipment autonomously.

## Evidence to present

| Claim | Artifact | Limit |
|---|---|---|
| A whole day can be forecast without intraday actuals | [Monthly benchmark](../../reports/real_data/MONTHLY_NEURAL_NETWORK_BENCHMARK.md) | Wolf MLP WAPE about 27.7%; errors remain material. |
| ML can outperform persistence at some sites | [Ten-building cohort](../../reports/real_data/FULL_DATASET_MULTI_BUILDING_BENCHMARK.md) | MLP beats previous-day persistence at 8/10 convenience sites; not universally better. |
| Equipment windows/priorities have regression checks | [Scheduler tests](../../tests/unit/test_schedule_review_regressions.py) | Capacity has penalty slack; breaches must block operational adoption. |
| Clock/adaptation behavior is tested | [Native firmware tests](../../tests/unit/test_firmware_runtime.py) | Host/compiler tests do not establish physical accuracy or installed reliability. |
| Scenarios can be replayed on public data | [Cost scenario](../../reports/real_data/COST_SCENARIO.md) | Assumed tariffs/battery and perfect foresight; not achieved savings. |

Research upper envelopes are uncalibrated heuristics. Their coverage does not establish capacity protection, avoided penalties, or achieved savings. There is no verified bakery saving or payback claim.

## Three-minute demonstration

1. Start a local backend with a disposable database and open `/dashboard`. Identify simulated, public-replay, or facility inputs.
2. Configure two assets, deadlines, and critical priority. Show the timeline, assumptions, capacity warnings, and an infeasible window.
3. Show `/api/v1/facilities/{id}/load-forecast?schedule_date=YYYY-MM-DD` with adequate history, or the reproducible public-data reports. Explain origin, model selection, and baselines.
4. Finish with the proposed pilot and what must be measured before quoting ROI.

`auto` mode labels fallback data/prices as demo. `data_mode="telemetry"` rejects missing history and requires effective marginal tariffs. Whole-facility predictions reserve existing demand: adding assets can double-count equipment. Supply an isolated background profile for operational scheduling. The 24-hour calendar horizon rejects DST days and runs requiring tomorrow’s hours; overnight tasks may use only pre-midnight slots.

## Reproduction

```powershell
python -m pip install -e ".[dev,research,firmware]"
python -m pytest -q
python -m platformio run -d firmware
python scripts/test_neural_network_monthly.py
python scripts/test_full_dataset_ml_benchmark.py
python scripts/evaluate_tinyml_edge.py
python scripts/evaluate_runtime_forecast.py
python scripts/plot_comprehensive_cost_reduction.py
```

Research scripts need cached BDG2 data; see [data sources](../forecasting_data_sources.md). Set `API_KEY` on backend/simulator and `EMS_API_KEY` in local firmware secrets. Production startup requires `ENVIRONMENT=production` and a nonempty key. A shared API key is an initial single-site control, not tenant isolation or role-based authorization.

## Path to a paying pilot

Use the [pilot plan](field_pilot_protocol_ipmvp.md) to establish measurement quality, prediction accuracy, operator acceptance, and net benefit. Agree on measured success criteria before intervention.

Before multi-customer service: add per-device/per-tenant credentials, authorization, audit trails, key rotation, backups, monitoring, calibrated uncertainty, drift handling, and verified meter integration. Reconcile tariffs and demand billing with actual contracts. The [hardware/compliance roadmap](regulatory_compliance_roadmap.md) is proposed work; component budgets and target prices are estimates, not quotes or certificates.
