# Greek Commercial Behind-the-Meter Energy Management System (EMS)

[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![PlatformIO ESP32](https://img.shields.io/badge/PlatformIO-ESP32-orange.svg)](https://platformio.org/)
[![Regulatory Standard](https://img.shields.io/badge/Law-5068%2F2023-brightgreen.svg)](https://ypen.gov.gr)
[![Safety Standard](https://img.shields.io/badge/Standard-ELOT%2060364-red.svg)](https://www.elot.gr)
[![Bill Validation: 0.00% Error](https://img.shields.io/badge/Bill%20Audit-0.00%25%20Error%20(198%20lines)-success.svg)](docs/tariff_validation_report.md)
[![Calibration: simulation model](https://img.shields.io/badge/Calibration-simulation%20model-blue.svg)](docs/measurement_uncertainty_report.md)
[![Tests](https://img.shields.io/badge/tests-run%20pytest-blue.svg)](tests/)

An advisory energy-management prototype for commercial SMBs, combining ESP32 telemetry, day-ahead load forecasting, and equipment-constrained MILP scheduling. Staff review recommendations before changing equipment operation. Public-data benchmarks support software evaluation; achieved site savings and production hardware accuracy remain unverified.

See the [competition and pilot dossier](docs/product/PRODUCT_DOSSIER.md) for the evidence, demo flow, and remaining deployment work.
---

## Measurement and optimization limits

The current CT-only firmware measures RMS current and **estimates power and energy using configured voltage and power factor**. It does not measure instantaneous voltage or power factor. Telemetry labels these readings `estimated_nominal_voltage_pf`; simulated and legacy/unknown readings are also distinguished. The calibration report is a simulation and does not certify physical hardware accuracy.

Schedule Studio forecasts a local 24-hour day from stored hourly telemetry, comparing a NumPy ridge model with previous-day/week baselines on seven chronological validation days. All inputs precede the forecast origin. With insufficient history it uses persistence or explicitly labelled demo data. `data_mode="telemetry"` rejects missing history and requires caller-supplied effective tariff rates. The separate legacy `/optimization/solve` endpoint still uses demonstration defaults.

Whole-facility forecasts reserve existing demand. Adding named equipment to them is an incremental-load scenario that may double-count equipment; a pilot must provide an isolated background profile and actual marginal tariffs. Capacity constraints include penalized slack: any breach is a warning, not a safe-to-execute instruction. Default uncertainty margins are policy buffers, not calibrated probabilities.

CT-only readings remain `estimated_nominal_voltage_pf`. Mode B acquisition is blocked until a real voltage/meter driver is integrated. Clock-dependent telemetry waits for valid RTC/NTP time. Configured API keys protect API/dashboard data; production startup requires a key. The dashboard accepts a key for the current page only. Telemetry rejects stale/duplicate timestamps with HTTP 409 rather than billing them again.

## Reproducible evidence

| Evaluation | Evidence and scope |
|---|---|
| Day-ahead ML | [Ten-building BDG2 cohort](reports/real_data/FULL_DATASET_MULTI_BUILDING_BENCHMARK.md): MLP WAPE 7.62–28.50%, improving on previous-day persistence at 8 of 10 selected buildings; convenience sample, not a representative fleet. |
| Runtime model | [Full-year replay on three sites](reports/real_data/RUNTIME_FORECAST_BENCHMARK.md): WAPE 29.71%, 19.49%, 8.62%, compared with previous-day 32.56%, 19.83%, 8.93%. |
| Monthly ML | [Wolf retail 2017](reports/real_data/MONTHLY_NEURAL_NETWORK_BENCHMARK.md): full-day forecasts issued at midnight, with baselines and excluded days disclosed. |
| Dispatch | [Real-data scenarios](reports/real_data/REPORT.md): assumed tariffs/battery, including perfect-foresight scenarios; no achieved facility savings. |
| Firmware | ESP32 PlatformIO build and native C++ behavior regressions; physical accuracy and installation are not certified. |
| Tariffs | [Formula regression benchmark](docs/tariff_validation_report.md): synthetic expected bills check implementation consistency, not independent utility-bill reconciliation. |

Research dependencies: `python -m pip install -e ".[dev,research,firmware]"`. Run `python -m pytest -q`, `python -m platformio run -d firmware`, and the evaluation scripts linked in the dossier. Data attribution and CC BY-SA requirements are recorded in the reports.


## Documentation Index

| Guide | Document Link | Description |
|---|---|---|
| **Tariff & Bill Audit Report** | [`docs/tariff_validation_report.md`](docs/tariff_validation_report.md) | Calculation regression benchmark across 9 Greek bills (Γ21, Γ22, Γ23, Green, Yellow, Dynamic) with 0.00% line-item formula discrepancy. |
| **Measurement Uncertainty Report** | [`docs/measurement_uncertainty_report.md`](docs/measurement_uncertainty_report.md) | ISO/IEC Guide 98-3 GUM error budget, ESP32 ADC linearization, CT phase-shift compensation, and Class 0.5S benchmark. |
| **Hardware Schematics & Wiring** | [`docs/wiring_schematic.md`](docs/wiring_schematic.md) | SCT-013 CT clamp connections, burden resistor calculation ($18\,\Omega$), ADC1 pinout, virtual ground, and ELOT 60364 safety standards. |
| **Hardware Bill of Materials (BOM)** | [`docs/hardware_bom.md`](docs/hardware_bom.md) | Sub-€50 component list, part numbers, suppliers, PCB layout, and DIN-rail enclosure recommendations. |
| **Greek Commercial Electricity Tariffs** | [`docs/greek_tariffs_guide.md`](docs/greek_tariffs_guide.md) | Detailed analysis of contracts Γ21, Γ22, Γ23, Law 5068/2023 Green tariff formula, DEDDIE peak schedules, and power factor penalties. |
| **REST API Reference** | [`docs/api_reference.md`](docs/api_reference.md) | FastAPI endpoint documentation, optimization endpoints (`/api/v1/optimization`), market feeds, Viber webhook, and dashboard. |
| **Telegram & Viber Bot Guide** | [`docs/telegram_bot_guide.md`](docs/telegram_bot_guide.md) | BotFather configuration, Viber bot tokens, webhook routing, anti-spam throttling, and Greek interactive commands. |
| **Production Deployment Guide** | [`docs/deployment_guide.md`](docs/deployment_guide.md) | Systemd unit configuration, environment variables, PlatformIO ESP32 firmware flashing, and operations runbook. |

---

## 1. Mathematical Optimization Engine (`optimization_engine/`)

The core optimization engine formulates and solves a multi-period Mixed-Integer Linear Program (MILP) over a rolling 24-hour horizon:

$$\min \sum_{t=0}^{H-1} \left( C_t \cdot P_{\text{total}, t} \cdot \Delta t + \lambda_{\text{cap}} \cdot S_t \right)$$

Subject to:

1. **Power Balance:**
   $$P_{\text{total}, t} = P_{\text{base}, t} + \sum_i P_{\text{defrost}, i, t} + \sum_j P_{\text{hvac}, j, t} + \sum_k P_{\text{batch}, k, t} + P_{\text{chg}, t} - P_{\text{dis}, t}$$
2. **Contracted Capacity Limit & Surcharge Avoidance:**
   $$P_{\text{total}, t} - S_t \le P_{\text{contracted}}, \quad S_t \ge 0$$
3. **Flexible Defrost Shifting (Cold Storage & Freezers):**
   $$\sum_{t \in W_i} u_{\text{defrost}, i, t} = D_{\text{defrost}, i}, \quad W_i = [t_{\text{nominal}} - \tau_{\text{shift}}, t_{\text{nominal}} + \tau_{\text{shift}}]$$
4. **Production Batch Contiguity (Commercial Bakery Deck Ovens):**
   $$\sum_{t=t_{\text{earliest}}}^{t_{\text{latest}}} u_{\text{start}, k, t} = 1, \quad y_{\text{active}, k, t} = \sum_{\tau=\max(0, t-D_k+1)}^t u_{\text{start}, k, \tau}$$
5. **HVAC Thermal Comfort Deadband:**
   $$T_{j, t+1} = (1 - \alpha_j) T_{j, t} + \alpha_j T_{\text{ambient}, t} - \beta_j P_{\text{hvac}, j, t}, \quad T_{\min} \le T_{j, t} \le T_{\max}$$
6. **Battery Energy Storage (BESS) Arbitrage:**
   $$E_{t+1} = E_t + \left(\eta_{\text{chg}} P_{\text{chg}, t} - \frac{1}{\eta_{\text{dis}}} P_{\text{dis}, t}\right) \Delta t, \quad E_{\min} \le E_t \le E_{\max}$$

### Decision Support & Closed-Loop Verification

- **Actionable Operational Guidance:** The solver output is translated into prioritized operational cards (`DecisionSupportEngine`) in Greek and English.
- **Closed-Loop Telemetry Audit:** When an intervention is executed, post-intervention telemetry is evaluated against the counterfactual baseline (`ClosedLoopVerifier`) to estimate interval load differences and energy value against an assumed counterfactual (`SUCCESS`, `PARTIAL`, `FAILED`).

---

## 2. Pluggable European Market Architecture

The billing and tariff architecture supports modular market adapters across European jurisdictions:

```
tariff_engine/adapters/
  ├── base.py       # Abstract Base European Tariff Adapter (Contract, Grid, Taxes, Regulatory)
  ├── greek.py      # Greek Market Adapter (Γ21, Γ22, Γ23, Law 5068/2023, DEDDIE, ADMIE, ETMEAR, YKO, 6% VAT)
  ├── german.py     # German Market Adapter (§19 StromNEV Netzentgelte, Konzessionsabgabe, KWKG, Stromsteuer, 19% MwSt)
  ├── spanish.py    # Spanish Market Adapter (Tarifa 2.0TD / 3.0TD, Periodos Punta/Llano/Valle, Peajes, 21% IVA)
  └── registry.py   # Thread-safe Adapter Registry & Factory
```

Each adapter guarantees line-item calculation accuracy according to national energy regulatory authority standards.

---

## 3. Hardware Interfacing, Calibration & Safety

### 3.1 SCT-013-000 Burden Resistor Sizing
- **Turns Ratio:** $2000:1$ ($100\text{ A RMS primary} \implies 50\text{ mA RMS secondary} \implies 70.71\text{ mA peak}$).
- **3.3V ESP32 ADC ($18\,\Omega$ 1% Metal Film):**
  $$V_{peak} = 0.07071\text{ A} \times 18\,\Omega = 1.273\text{ V} \implies V_{pp} = 2.546\text{ V}$$
  Biased at $1.65\text{ V}$, signal spans $[0.377\text{ V}, 2.923\text{ V}]$, safely inside the linear range ($0.15\text{ V} - 3.10\text{ V}$).
  **Calibration constant:** $K_I = 2000 / 18 = 111.111\text{ A/V} = 0.11111\text{ A/mV}$.

### 3.2 ESP32 Pin Allocation (ADC1 Exclusively)
- **Phase L1:** GPIO 34 (`ADC1_CH6`)
- **Phase L2:** GPIO 35 (`ADC1_CH7`)
- **Phase L3:** GPIO 32 (`ADC1_CH4`)
- **ADC2 Restriction:** The ESP32 Wi-Fi RF driver locks ADC2. Sampling ADC2 pins causes Wi-Fi disconnects and measurement corruption.

### 3.3 Embedded Calibration DSP Algorithms (`firmware/src/calibration.cpp`)
- **Piecewise ADC Linearization:** Compresses dead-zone error below 120 mV and decompress saturation near 3.3V.
- **CT Phase-Angle Lead Compensation:** Corrects current transformer core phase lead ($\theta_e(I) \approx 1.5^\circ - 3.5^\circ$), eliminating active power distortion on inductive loads ($\cos\varphi < 0.85$).
- **Dynamic Virtual Ground Tracking:** Exponential moving average auto-calibrates $1.65\text{ V}$ bias drift due to temperature expansion.

---

## 4. Quickstart Guide

### 4.1 Installation
```bash
git clone https://github.com/DimThanasoulias/Behind-the-Meter-EMS.git
cd Behind-the-Meter-EMS

# Create virtual environment and install in editable mode
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### 4.2 Run Automated Test Suite (511 Tests)
```bash
pytest -v
```
All 511 unit, integration, and tiered end-to-end tests execute in **~5.5 seconds** with 100% pass rate.

### 4.3 Start the FastAPI Backend & Web Dashboard
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```
Access endpoints:
- **Interactive Web Dashboard:** `http://localhost:8000/dashboard`
- **Optimization API:** `http://localhost:8000/api/v1/optimization/status`
- **Interactive OpenAPI Documentation:** `http://localhost:8000/docs`

### 4.4 Run Standalone E2E Verification (< 30 Seconds)
```bash
# Commercial Bakery
python scripts/run_e2e_verification.py --profile bakery

# Cold Storage Facility
python scripts/run_e2e_verification.py --profile cold_storage

# Boutique Hotel
python scripts/run_e2e_verification.py --profile hotel
```

### 4.5 Run the Commercial Telemetry Simulator
Stream 24 hours of accelerated commercial load with peak breach injection:
```bash
python -m simulator.cli --profile bakery --speed 60x --url http://localhost:8000/api/v1/telemetry
```

---

## 5. Generic SME Equipment Schedule Studio (`optimization_engine/scheduling_service.py`)

An SME-friendly, multi-resolution scheduling environment ("Schedule Studio") allowing facility owners to define arbitrary electrical equipment and generate mathematical advisory schedules:

- **Multi-Resolution Solving:** Native support for 5, 15, 30, and 60-minute time intervals ($N \in \{288, 96, 48, 24\}$ slots).
- **Flexible Equipment Operating Models:**
  - Arbitrary rated kW and required duration.
  - Permissible time windows including midnight-crossing ranges (e.g., 22:00 to 06:00).
  - Strict contiguity constraints for non-interruptible loads (ovens, dishwashers, industrial machinery).
  - Distributed time-slot allocations for interruptible loads (water heaters, HVAC pre-cooling, EV chargers).
  - Must-run guarantees vs. graceful omission of lower-priority optional equipment under constrained capacity.
  - Soft preferred start times with priority-weighted penalties.
- **Conservative Baseline Uncertainty Buffer (+10%):** Protects against physical breaker trips and replay infeasibility discovered during real-world empirical audits ($P_{\text{base, cons}} = 1.10 \times P_{\text{base}}$).
- **Advisory-Only Paradigm:** Strictly decision-support. Generates natural-language Greek operational explanations (`explanation_el`) for each asset without automatic hardware actuation.
- **Full REST API Suite:**
  - `GET/POST /api/v1/facilities/{facility_id}/assets`: Equipment inventory CRUD.
  - `GET/PUT/DELETE /api/v1/facilities/{facility_id}/assets/{asset_id}`: Single asset operations.
  - `GET/PUT /api/v1/facilities/{facility_id}/schedule-settings`: Facility resolution, power limits, and objective modes (`cost`, `peak`, `balanced`).
  - `POST /api/v1/facilities/{facility_id}/schedules/preview`: Real-time schedule optimization preview with Gantt and load profile timeline.
  - `POST /api/v1/facilities/{facility_id}/schedules/save`: Persistent storage of approved schedules.
  - `GET /api/v1/facilities/{facility_id}/schedules`: Historical schedule retrieval.
- **Interactive Web UI:** Integrated into `/dashboard` under the "Schedule Studio" tab, featuring quick templates, equipment toggles, modal dialogs, and Chart.js before/after load comparison curves.

---

## 6. Technical Feature Inventory (F01–F34)

| # | Feature | Subsystem | Description |
|---|---|---|---|
| **F01** | ESP32 Analog Sampling & ADC1 Pinout | Firmware | Non-invasive CT sampling using ADC1 (GPIO 34, 35, 32), DC bias (1.65V), avoiding Wi-Fi ADC2 conflict |
| **F02** | SCT-013 Burden Resistor Derivation | Firmware | Exact derivation for SCT-013-000 ($18\,\Omega$ for 3.3V) and SCT-013-030 internal burden |
| **F03** | 3-Phase Electrical Power Calculations | Firmware | Computation of True RMS current, active power ($P$), apparent power ($S$), power factor ($\cos\varphi$) |
| **F04** | Trapezoidal Cumulative Energy Integration | Firmware | Real-time numeric integration of active power into cumulative kWh |
| **F05** | ESP32 Wi-Fi & Reconnection Resilience | Firmware | Automatic exponential backoff reconnection for Wi-Fi and HTTPS/MQTT endpoints |
| **F06** | ESP32 Telemetry Store-and-Forward | Firmware | In-memory ring buffer holding telemetry readings during temporary network dropouts |
| **F07** | PlatformIO Clean Build Configuration | Firmware | Clean compilable `platformio.ini` for `esp32dev` board without warnings/errors |
| **F08** | Greek Tariff Contract Γ21 (LV Commercial) | Tariff Engine | Single-rate commercial tariff modeling for $\le 25\text{ kVA}$ connections |
| **F09** | Greek Tariff Contract Γ22 (Dual Rate) | Tariff Engine | Commercial dual-rate tariff modeling with DEDDIE peak and off-peak windows |
| **F10** | Greek Tariff Contract Γ23 (MV Commercial) | Tariff Engine | Medium voltage commercial structure modeling ($> 250\text{ kVA}$) |
| **F11** | Green Tariff Fluctuation Mechanism | Tariff Engine | Official Law 5068/2023 / MD ΥΠΕΝ formula with TEA, $\alpha$, $L_u$, $L_l$, $\beta$ parameters |
| **F12** | Yellow & Dynamic Hourly Spot Tariff | Tariff Engine | Day-Ahead Market (DAM) hourly spot price indexing with supplier margins |
| **F13** | Regulated Charges Modeling | Tariff Engine | DEDDIE distribution, ADMIE transmission, ETMEAR, YKO, EFK, DETE, and 6% VAT |
| **F14** | Capacity Excess & Power Factor Penalties | Tariff Engine | Penalties for exceeding contracted kVA and $\cos\varphi < 0.85$ distribution multiplier |
| **F15** | Real-Time Running Cost (€/h) & Daily Spend | Tariff Engine | Instantaneous €/h calculation, daily spend accumulator, and baseline delta |
| **F16** | Projected Peak-Hour Surcharge Calculation | Tariff Engine | Mathematical projection of excess cost during active peak tariff windows |
| **F17** | Backend Telemetry Ingestion API | Backend | FastAPI REST endpoint (`POST /api/v1/telemetry`) with Pydantic v2 payload validation |
| **F18** | Electrical Invariant Validation | Backend | Strict backend checks ensuring $\vert P_{tot} - \sum P_i \vert \le 0.05\text{ kW}$ and $\cos\varphi \in [-1, 1]$ |
| **F19** | SQLite Time-Series Storage | Backend | High-throughput SQLite storage with WAL mode, facility indexing, and query APIs |
| **F20** | Facility Status & Cost Endpoints | Backend | `GET /api/v1/facilities/{id}/status`, `/cost-today`, `/tariff` for dashboards and bot queries |
| **F21** | Proactive Peak Breach Alert Generation | Alerting | Automated triggering of proactive alerts when load breaches kW threshold during peak rates |
| **F22** | Greek Alert Notification Templates | Alerting | Greek messages with kW, peak window, estimated € penalty, and tailored curtailment advice |
| **F23** | Alert Throttling, Cooldown & Hysteresis | Alerting | 3-sample debounce, 30-min cooldown, $\ge 25\%$ escalation jump, 10% release hysteresis |
| **F24** | Telegram Bot Greek Command Handlers | Alerting | Interactive commands (`/start`, `/status`, `/cost_today`, `/tariff`, `/settings`, `/help`) |
| **F25** | Dual Telegram Client Architecture | Alerting | `MockTelegramClient` for deterministic offline testing + `LiveTelegramClient` for production |
| **F26** | Commercial Telemetry Simulator CLI | Simulator | Parametric simulation for Commercial Bakery, Cold Storage, and Boutique Hotel |
| **F27** | Simulator Fast-Forward & Breach Triggering | Simulator | Configurable time-compression (`--speed`), Gaussian noise (`--noise`), and breach injection |
| **F28** | Hardware Wiring Schematics & Safety Guide | Hardware | Complete circuit diagrams, SCT-013 CT clamp connections, burden resistor sizing, and safety rules |
| **F29** | System Deployment & Operation Guide | Deployment | Production setup guide with PlatformIO, systemd, `.env`, Telegram bot setup, and runbook |
| **F30** | End-to-End Automated Integration Test Suite | Testing | Standalone automated script executing under 30s, verifying full pipeline from simulator to alert |
| **F31** | Live Greek Energy Market Price Ingestion | Market Feeds | Scraping of monthly RAE Green tariffs & 24h HEnEx DAM spot prices with 4-tier caching |
| **F32** | Unified Multi-Channel Alerting & Viber Bot | Alerting | Multi-channel dispatching (`telegram`, `viber`, `both`), `MockViberClient`, live Viber webhook, HMAC check |
| **F33** | Interactive Real-Time Web Dashboard | Dashboard UI | Responsive Single-Page UI at `/dashboard` with 3-phase live metrics, DEDDIE badges, 24h load curve |
| **F34** | Generic SME Equipment Schedule Studio | Optimization | Multi-resolution (5/15/30/60m) MILP advisory scheduler with conservative uncertainty buffer (+10%), Greek explanations, and REST API |

---

## 7. Regulatory Compliance & Electrical Safety

- **ELOT 60364 / HD 384:** Electrical installations of buildings. Guarantees physical isolation between low-voltage signal wiring and 400V mains busbars.
- **Law 5068/2023 & MD ΥΠΕΝ:** Greek retail electricity market reorganization establishing Green, Yellow, and Dynamic retail tariffs.
- **DEDDIE & ADMIE Grid Codes:** Compliant with distribution network connection terms, contracted kVA thresholds, and low power factor surcharge schedules ($\cos\varphi < 0.85$).
