# CONTRACT (single source of truth)

Version 0.2 (fork of upstream Behind-the-Meter-EMS). Every phase conforms to this file or
amends it deliberately, with a decision entry in PROJECT_STATE.md. Sections are grep
targets: `## C1` ... `## C9`.

## C1 Conventions

- Time: store and transport UTC (ISO 8601 with offset). Europe/Athens only for tariff
  windows, schedules and display. Naive datetimes are rejected at every boundary.
- Interval: canonical 15 minutes, aligned to :00/:15/:30/:45, identified by its START.
  A local day has 92 (last Sunday of March), 100 (last Sunday of October) or 96 intervals.
  The Greek DAM clears in 15-minute MTUs since 1 Oct 2025. Hourly data is converted to
  four equal quarter-hours only when the source itself is hourly, and marked so.
- Units: existing upstream models keep kW, kVA, kWh as float (measurement values).
  Inside `billing_core`, prices (EUR/MWh, EUR/kWh) and money (EUR) are `Decimal`.
  Conversion float -> Decimal happens once, at the billing_core boundary, via
  `Decimal(str(round(x, 6)))`.
- Price units are always explicit (`EUR_MWH` or `EUR_KWH`); upstream `tariff_engine/units.py`
  semantics are kept until phase 6.
- User-facing text: Greek first, English second.

## C2 Storage (pilot: SQLite WAL, upstream `backend/database/sqlite_store.py`)

Existing upstream tables are kept: facility_configs, telemetry_readings, cost_aggregates,
market_dam_hourly_prices, market_green_tariffs, equipment_assets, schedule_settings,
generated_schedules. Changes, each added by the phase named:

| Change | Phase | Columns / notes |
|---|---|---|
| facility_configs: add tariff fields | 5 | supplier, product_name, tariff_kind (BLUE, GREEN, YELLOW, ORANGE, FLEXIBLE, OTHER), network_category, has_reactive_metering, has_demand_metering, has_dso_smart_meter, valid_from |
| telemetry_readings: add counters | 5 | cumulative_reactive_kvarh (nullable), cumulative_export_kwh (nullable) |
| new: interval_energy_15m | 5 | facility_id, ts_start, e_imp_kwh, e_exp_kwh, eq_kvarh, p_max_kw, coverage (0..1), source ("counter_delta"); unique (facility_id, ts_start) |
| new: market_dam_prices | 8 | bidding_zone, ts_start, resolution_min (15 or 60), price_eur_mwh (TEXT Decimal), source (ENTSOE, HENEX), fetched_at; unique (bidding_zone, ts_start, source) |
| new: cost_intervals | 6 | facility_id, ts_start, component, eur (TEXT Decimal), rule_set_id, rule_version, is_estimate |
| new: recommendation_cards | 7 | id, facility_id, for_date, payload JSON, price_basis, created_at, acknowledged_by, outcome (SUCCESS, PARTIAL, FAILED, null) |
| new: bills | 10 | id, facility_id, period_start, period_end, total_eur (TEXT Decimal), lines JSON, file_ref |

Postgres/TimescaleDB is a later migration (decision D4), not a pilot requirement.

## C3 Telemetry (upstream `backend/models/telemetry.py`, HTTPS POST `/api/v1/telemetry`)

The upstream `TelemetryPayload` stays the wire format. Rules:

- `power_measurement_method` must be `meter_measured` for any facility that receives advice.
  `estimated_nominal_voltage_pf` and `simulated` data may be stored and shown, never billed
  or used for recommendations.
- v0.2 adds optional fields (phase 5): `cumulative_reactive_kvarh: float | None`,
  `cumulative_export_kwh: float | None`, `meter_model: str | None`.
- Interval energy is derived from `cumulative_energy_kwh` deltas, never from averaging
  power. Counter resets are detected and flagged; they never yield negative energy.
- Each device authenticates with its own token (phase 11); until then the upstream API key.
- There are no command endpoints toward devices. Adding one is a contract change.

## C4 Rule files (`rules/gr/*.yaml`)

```yaml
rule_set: gr-dist-network
version: "2026-02-01"
effective_from: 2026-02-01        # local date, inclusive
effective_to: null
source: {title: "...", url: "https://...", retrieved: 2026-10-04}
status: PLACEHOLDER               # PLACEHOLDER | VERIFIED | ILLUSTRATIVE
applies_to: {voltage: [LV], network_category: ["..."]}
components:
  - id: dist_fixed
    kind: per_kva_year            # per_kwh | per_kva_year | per_kw_month | percent_of | flat_month
    value: null                   # Decimal as string; null only if PLACEHOLDER
    unit: EUR_per_kVA_year
  - id: dist_variable
    kind: per_kwh
    value: null
    unit: EUR_per_kWh
    modifiers:
      - {if: has_reactive_metering, divide_by: cos_phi_period}
time_windows: []                  # optional: {name, months, weekdays, from_local, to_local}
```

- Overlapping effective ranges for one rule_set are an error.
- `PLACEHOLDER` on the resolution path raises `RulesIncomplete(missing=[...])`.
- `ILLUSTRATIVE` rule sets live only in `rules/illustrative/`, are loadable only when
  `ENVIRONMENT=development`, and every result computed from them has `is_estimate=True`
  and the label "ILLUSTRATIVE". They exist for demos and tests, never for customers.

## C5 Cost engine (`billing_core/cost.py`, pure)

```python
def compute_costs(site: SiteSnapshot, intervals: list[IntervalEnergy],
                  rules: RuleBook, prices: list[PricePoint] | None) -> CostResult: ...
```

- `CostResult`: per-interval `{ts_start, component, eur: Decimal, rule_set_id, rule_version}`,
  `period_totals`, `is_estimate`.
- No I/O, no clock, deterministic. Supplier price by tariff_kind: BLUE fixed; GREEN/YELLOW
  monthly value from `market_green_tariffs` or user entry; ORANGE supplier hourly/15-min
  price, else DAM + configured adder with `is_estimate=True`.

## C6 Optimizer I/O (upstream `optimization_engine/`, kept)

- Input prices come from `market_dam_prices` at 15-min resolution through an adapter that
  returns the price vector plus `price_basis` (DAM_ONLY, SUPPLIER_PUBLISHED). The adapter
  refuses to return seeded or synthetic prices for advice.
- Output (`decision_support.py` cards) must include `price_basis`, `expected_cost_eur`,
  `baseline_cost_eur`, `savings_eur` computed through billing_core, and must never report
  solver penalty weights as savings (upstream fix 4b0db0d is kept).
- Property tests: no hard-constraint or demand-cap violation; optimized cost never above
  baseline cost under the same prices.

## C7 Alerts (upstream `bot/dispatcher.py`, kept)

Keep upstream debounce, cooldown and hysteresis. Alert kinds after phase 6:
PRICE_SPIKE_TOMORROW, PLAN_READY, DEMAND_PEAK_RISK, PF_LOW (only if has_reactive_metering),
DATA_GAP, BASELOAD_ANOMALY. Peak-window logic comes from rule files, not code constants.

## C8 Notifiers (upstream `bot/telegram_client.py`, `bot/viber_client.py`)

Telegram live for the pilot. Viber client stays implemented but disabled by config until
decision D7 is revisited (cost EUR 100/month per bot plus per-message fees).

## C9 Invariants (tests must enforce each)

- I1 No device writes anywhere (`tests/test_no_actuation.py`).
- I2 No regulatory value or legal citation without a source; PLACEHOLDER blocks computation.
- I3 Interval energy from counter deltas; resets flagged; never negative.
- I4 Interval counts 92/96/100 per local day; both DST days tested.
- I5 Decimal for money and prices inside billing_core and in stored cost/bill columns.
- I6 No advice from estimated, simulated, seeded or synthetic inputs.
- I7 billing_core is pure and deterministic.
- I8 Optimizer never violates hard constraints; savings never exceed baseline minus optimized cost.
- I9 Every plan and card states its price_basis; DAM_ONLY savings are labelled estimates.
- I10 Personal data never appears in logs; auth fails closed.
