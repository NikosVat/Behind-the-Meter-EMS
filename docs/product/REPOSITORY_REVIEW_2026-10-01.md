# Repository review and feasibility assessment

Review date: 2026-10-01. Reviewed application revision: `08e3f0ffcf604174d67fcf7b9fd5a5a119c048a5`.

This is a code, documentation and recorded-evidence review, not a physical
installation inspection, electrical certification, utility-bill audit, or market
validation study. No application behavior was changed in this review.

## Overall opinion

The project is a credible competition prototype with a plausible path to a
small, operator-reviewed pilot. It is not yet a production metering product, an
autonomous equipment controller, or a demonstrated savings business. Its best
commercial opportunity is equipment-aware advice integrated with reliable meter
data and actual contracts. Cheap component cost alone is not a durable advantage.

There are important remaining correctness problems despite a passing test suite.
Fix financial interpretation, market time/units, transport authentication and
telemetry quality before adding model complexity or presenting financial results
to a customer. A competition can demonstrate the system honestly while these
limits are disclosed; operational adoption needs stricter gates.

| Use | Assessment | Conditions |
|---|---|---|
| Competition demonstration | Credible technical entry; outcome depends on competition criteria | Demonstrate a coherent workflow and baseline comparisons; correct misleading claims. |
| Single-site shadow pilot | Feasible after correctness and installation review | One trusted site, reference meter, actual rates, operator-approved assets, no actuation. |
| Paid advisory pilot | Plausible, not validated | Demonstrate net benefit, reliable data and manageable support effort. |
| Multi-customer hosted service | Not ready | Scoped credentials, tenant authorization, durable audit and operating controls. |
| Custom commercial meter / autonomous control | Not ready | Meter acquisition, physical validation, product conformity and equipment-specific safety work. |

## Verification performed

- Fresh full test run: **691 passed**, one Starlette/httpx deprecation warning,
  27.51 seconds on this Windows host. `OPENBLAS_NUM_THREADS=1` and
  `OMP_NUM_THREADS=1` were set for this run. The previous unrestricted run had a
  Windows access violation; its underlying native-runtime issue remains unresolved.
- ESP32 PlatformIO build: **SUCCESS**, RAM 59,484 / 327,680 bytes, flash
  978,113 / 1,310,720 bytes. This was the configured build, not a hardware bench test.
- Small read-only Python reproductions confirmed timezone-dependent tariff
  classification, price-unit discontinuity, and acceptance of nonfinite active power.
- A separate code review reproduced incorrect capacity-saving recommendation
  amounts. Firmware TLS behavior was checked against the installed Arduino core
  and Espressif's upstream source.
- Existing public-data benchmark reports were assessed, not all rerun. No
  facility telemetry was collected and no installed saving was measured.

## What is strong

The repository has real implementation across ingestion, persistence, forecasts,
scheduling, dashboard, notifications and firmware. It has considerably more
substance than a dashboard-only demonstration.

SQLite WAL and an immediate write transaction protect counter-delta aggregation.
Duplicate and stale readings are rejected per device. API-key enforcement fails
fast in production without a key, and Viber callbacks use HMAC. Measurement
provenance is exposed. Schedule Studio records warnings and distinguishes demo
inputs. Mode B does not silently pretend a voltage driver is installed.

The shipped forecasting selector uses only pre-origin observations, chronological
validation and previous-day/week baselines. Public-data reports disclose exclusions,
model limitations and unfavorable results. These are good foundations for a pilot.

## Remaining findings, in priority order

### 1. Market timezone is not consistently applied — P1

`tariff_engine/contracts.py:155` reads `dt.hour` directly. The same instant,
`2026-10-01T11:00:00+00:00` and `2026-10-01T14:00:00+03:00`, produces different
peak classifications. The independent reproduction gave effective G22 green rates
of EUR 0.2094 and EUR 0.2494/kWh. `backend/market/service.py:246` similarly chooses
DAM date/hour from the incoming clock, and monthly lookup uses the raw month.
UTC firmware timestamps therefore select the wrong market-local interval near
hour/day/month boundaries. Normalize at an explicit market boundary, define naive
timestamp behavior, and retain DST interval identity. This bug exists regardless
of whether the assumed tariff schedule matches a real contract.

### 2. Recommendation cards overstate capacity savings — P1

`optimization_engine/decision_support.py:119` credits capacity avoidance when the
baseline hour exceeds capacity, without establishing an actual reduction in the
optimized contractual demand. A 40 kW flat background, 35 kW capacity and shifted
6.8 kW defrost left both baseline and optimized peaks at 46.8 kW. The solver
reported EUR 1.36 savings and an ongoing breach; the card claimed EUR 127.16.
The batch recommendation has a similar pattern. Compare the complete before/after
plans under the actual billing rule and avoid double-crediting overlapping actions.
Penalty weights in an optimizer are not measured invoice savings.

### 3. Price units are guessed from magnitude — P1/P2

`tariff_engine/yellow_dynamic.py:16` and `green_tariff.py:22` assume values with
absolute magnitude at most one are EUR/kWh. The market feed supplies EUR/MWh.
At 0.5 EUR/MWh the yellow supply function returns EUR 0.6325/kWh instead of
EUR 0.06557/kWh under its stated formula. At 1.01 EUR/MWh it abruptly returns
EUR 0.06615/kWh. Low and slightly negative market intervals are affected. Require
explicit units and test zero, negative prices and values around the boundary.

### 4. Firmware HTTPS does not verify the server — P1 for deployment

`firmware/src/telemetry_client.cpp:223` calls `http.begin(target_url)` without a
trusted CA or configured secure client. In the installed Arduino 2.0.17 core this
falls back to TLS with a null CA and calls `setInsecure()`. Encryption alone does
not authenticate the server; the shared API key could be exposed to an impersonator.
Use a validating secure client and test rejection of an untrusted certificate.
Optional MQTT also uses a plain `WiFiClient`, no broker credentials and QoS-0
publish; REST-only is the current configured mode. A port number of 8883 alone
does not enable MQTT TLS.

### 5. Nonfinite measurements bypass validation — P2

`backend/models/telemetry.py:76` does not disable NaN/infinity. String `"NaN"`
for total and phase active power successfully constructs a payload; comparisons
against NaN do not reject the conservation violation. Reject all nonfinite
measurement values at the schema boundary and exercise the actual ingestion API.

### 6. A present hour is not a well-observed hour — P2

`backend/schedule_inputs.py:22` averages received power readings without minimum
count, time coverage, maximum-gap or time-weighting checks. One sample per hour
can satisfy the forecaster's 24-hour completeness check. Irregular packet delivery
changes the effective weights. Add quality metadata and eligibility gates, and
consider validated energy-counter deltas for interval means. Hourly forecasts
also cannot establish sub-hourly peak protection by themselves.

### 7. Tariff outputs remain contract assumptions — pilot blocker

`tariff_engine/cost_calculator.py:111` uses fixed green base/formula parameters;
supplier announcements are reduced to wholesale TEA rather than fully applied
retail contracts. It applies hardcoded +25% peak and -30% night modifiers for
G22/G23. The current projected penalty is an excess-load/rate-difference scenario,
not a reconciled demand-billing calculation. Replace assumptions with effective,
dated customer contract inputs and independently reconcile representative bills.
Counter deltas spanning price/day boundaries are valued at the endpoint rate;
long gaps need an explicit allocation/uncertainty policy. Financial reporting
labels help, but do not make the underlying rate accurate.

### 8. Intervention audit is not durable — P2

`backend/routes/optimization.py:38` stores recommendations and verification audit
in process memory. A restart loses them, another worker may not find them, and a
new solve replaces previous recommendation IDs. Saved Schedule Studio plans are
persisted separately, so this finding concerns the legacy recommendation/verification
workflow. Persist owned, versioned interventions and results before a paying pilot.

### 9. Hardware documentation contains consequential inaccuracies

The CT-only implementation assumes 230 V and PF 0.95. It measures current RMS,
not true active power or power factor. `calibration.cpp` exists, but its custom
correction functions are not called by the deployed CT sampling path; factory
ADC calibration through `analogReadMilliVolts` is distinct. Claimed corrected
active-power accuracy is therefore not evidence about this hardware.

`docs/product/industrial_bom.md:53` describes ESDA6V1BC6 as a 3.3 V device and
line 77 promises clamping below 3.6 V. ST specifies breakdown of 6.1–8 V, so that
protection claim is false. README line 107 calls an ADC waveform reaching 2.923 V
safe within a linear region up to 3.10 V; Espressif specifies worse accuracy above
approximately 2.45 V at this attenuation. Reassess the analog range/protection
against manufacturer specifications. No manufactured PCB/test evidence in the
review establishes the claimed CAT rating, clearance or industrial qualification.

### 10. Outage resilience and field observability are limited

The queue is 64 readings in RAM: about **5 minutes 20 seconds** at the configured
5-second cadence, then the oldest record is overwritten. Reboot loses queued
records; cumulative energy starts from zero in the current startup path. Backend
reset handling avoids obvious double counting but cannot reconstruct lost detail.
Network calls also block sampling despite the maintenance-loop comment calling
them non-blocking. Assess persistent delivery, acquisition cadence, gap visibility,
clock correction, Wi-Fi installation and recovery on actual hardware.

The edge upper envelope is heuristic but serial output calls it `P95` and
`NO (SAFE)`. Remove probabilistic/safety implications until independently calibrated.

### 11. Multi-customer operating controls are absent

One global API key grants all facilities' data and mutations, including to a
device given that key. This is a disclosed single-trusted-site limitation, not
tenant isolation. Add scoped device/user credentials, authorization, rotation,
backups with tested restore, monitoring, retention and audit before shared hosting.
Five-second cadence generates 17,280 readings/device/day; storage policy matters.
SQLite is a sensible inexpensive pilot choice; the present evidence does not
justify paying for a larger distributed platform yet.

### 12. Documentation and tests need consolidation

The README introduction is candid, while later sections still guarantee adapter
accuracy and cite 511 tests in 5.5 seconds. Industrial BOM/compliance documents
state performance as achieved while the product dossier correctly calls it planned.
The industrial itemized totals add to EUR 58.97, not the stated EUR 58.40; neither
is a verified current supplier quote or installed cost.

Some tiered E2E tests exercise a separate reference harness rather than the shipped
application. Reference oracles are useful, but those counts do not mean every
production path is covered. There is no tracked GitHub Actions workflow, and
dependency lower bounds/unpinned firmware platform reduce reproducibility.
Add production-path regressions for the failures above and a reproducible CI
environment; consolidate claims rather than adding more superficial test counts.

## ML assessment

The ten-building research MLP beats previous-day persistence at 8/10 convenience
sites, with WAPE 7.62–28.50%. This supports further experimentation, not a universal
accuracy claim. The shipped runtime model's three-site WAPE is 29.71%, 19.49% and
8.62%, versus previous-day 32.56%, 19.83% and 8.93%. Runtime gains are modest at
two sites. These reports evaluate different models/protocols; they must not be
presented as one deployed model's performance.

An older selected-model experiment also reports 211.86% WAPE on a changed office
series. Its honest negative result shows why drift and fallback behavior matter.
The battery cost plots use assumed tariffs and perfect foresight, not the achieved
economic value of the runtime forecast or installed equipment schedules.

Priority ML work is coverage/freshness, bias and drift monitoring, site-specific
baseline comparisons, calibrated forecast errors and operational evaluation.
Weather, opening hours and production variables can be tried when available at
forecast origin. Retain the simpler model if a more complex one fails forward
validation or does not improve decisions. No GPU purchase or new embedded neural
network hardware is justified by the present evidence.

## Low-cost commercial path

Start with one customer segment and one existing facility with a few genuinely
flexible loads. A flat-price contract with fixed energy/runtime and no relevant
demand benefit may offer little financial value from shifting alone. Validate
the customer's billing incentive and operational flexibility before installation.

Reuse an installed meter or accessible interval export where possible. Commercial
meters already expose power/energy through interfaces such as Modbus; the first
integration can support the scheduling product without custom mains-voltage
hardware development. Keep the ESP32 demonstration for the competition, and
evaluate total installed cost rather than only the component list.

The repository's EUR 44.28 hobby BOM is a planning estimate. Installation,
reference calibration, assembly, shipping/taxes, reliable power/enclosure,
support and conformity work are separate. The custom product's conformity route
and cost need actual assessment; a component certificate does not establish
finished-product conformity. The roadmap's cost/time estimates are not quotes.

Collect sufficient trustworthy history, operate in shadow mode, then let staff
follow selected feasible schedules. Reject capacity slack and unsupported safety
assumptions. Include installation, recurring costs, staff disruption and support
in net benefit. Do not buy a battery to justify the existing hindsight plots.

Before treating this as a startup, establish: trustworthy meter data; correct
contract rates; advice operators can follow; measured benefit against an adjusted
counterfactual; and willingness to pay above delivery/support cost. Durable
advantage can come from integrations, equipment constraints, operator workflow
and consented facility data. Monitoring dashboards and algorithms alone are weak
commercial differentiation, particularly with MIT-licensed code.

## Recommended order

1. Correct timezone, explicit units, recommendation valuation, finite validation
   and firmware server authentication; add direct regressions.
2. Align README/BOM/compliance and UI wording with demonstrated capabilities;
   make verification reproducible in CI.
3. Integrate one dependable meter/contract, enforce data quality and persist the
   intervention record. Continue with low-cost local/single-site infrastructure.
4. Run the bounded pilot and measure prediction, accepted actions, demand/cost
   outcomes and support effort. Expand only if the measured economics work.

## Primary external references checked

- [Espressif Arduino 2.0.17 HTTPClient source](https://github.com/espressif/arduino-esp32/blob/2.0.17/libraries/HTTPClient/src/HTTPClient.cpp): null-CA legacy HTTPS behavior.
- [Espressif ESP32-WROOM-32 datasheet](https://documentation.espressif.com/esp32-wroom-32_datasheet_en.html): ADC range and accuracy qualifications.
- [ST ESDA6V1BC6 datasheet](https://www.st.com/resource/en/datasheet/esda6v1bc6.pdf): actual protection-device specifications.
- [Shelly Pro 3EM manufacturer documentation](https://www.shelly.com/products/shelly-pro-3em-x1): an example of existing Modbus-capable metering, not a purchasing recommendation or price quote.
- [European Commission conformity assessment guidance](https://europa.eu/youreurope/business/product-rules-compliance/general-product-compliance/conformity-assessment/index_en.htm): manufacturer responsibility and assessment routes; actual applicability needs product-specific review.
- [EVO IPMVP](https://evo-world.org/en/products-services-mainmenu-en/protocols/ipmvp): verified savings require a measurement/verification framework; this review does not certify the proposed pilot.
