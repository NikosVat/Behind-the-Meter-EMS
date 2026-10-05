# Greek Commercial Behind-the-Meter EMS: Test Infrastructure Specification

**Document Version:** 1.0.0  
**Target Root:** `.`  
**Date:** 2026-09-14  

---

## 1. Overview & Testing Philosophy

This document defines the testing infrastructure, execution harnesses, and test tier hierarchy for the Greek Behind-the-Meter Energy Management System (EMS). 

The testing architecture adheres to strict **opaque-box, requirement-driven verification**:
1. **Opaque-Box Independence**: Tests interact with the EMS solely via public REST APIs, contract functions, and bot client interfaces specified in the architecture documentation.
2. **Deterministic & Offline**: All external dependencies (Telegram Bot API, ESP32 Wi-Fi hardware, HEnEx Day-Ahead Market wholesale feeds) are decoupled via high-fidelity in-memory adapters (`MockTelegramClient`, SQLite `:memory:` / temp WAL database, parametric telemetry generators).
3. **Execution Speed Guarantee**: The entire test suite completes execution in under 30 seconds (standard runs execute in < 4 seconds), enabling instant feedback loops in development and CI/CD pipelines.
4. **Authoritative Output Derivation**: Expected test values are derived explicitly from:
   - Greek Law 5068/2023 & Ministerial Decision (ΥΠΕΝ) Green Tariff Fluctuation Mechanism ($MD$).
   - Official DEDDIE / ADMIE regulated tariff schedules, capacity thresholds, and power factor penalty multipliers ($F_{PF} = 0.85 / \cos\varphi$).
   - Three-phase electrical mathematical invariants ($P = \sqrt{3} V I \cos\varphi$, $|P_{total} - \sum P_i| \le 0.05\text{ kW}$).
   - Contractual requirements for Greek Telegram alerts, debouncing, and hysteresis state transitions.

---

## 2. Directory Layout

The testing tree is structured into modular layers, separating unit, integration, and full-scale opaque-box E2E test tiers:

```
e:\project1\tests\
├── __init__.py
├── conftest.py                             # Root fixtures: MockTelegramClient, test DB, payload factories
├── unit/                                   # Module-level unit tests (Tariff, Pydantic, Bot, Math)
│   ├── test_tariff_engine.py
│   ├── test_telemetry_models.py
│   ├── test_alert_dispatcher.py
│   ├── test_simulator_profiles.py
│   └── test_firmware_math.py
├── integration/                            # Inter-module integration tests
│   ├── test_ingestion_api.py               # REST API & SQLite persistence
│   └── test_bot_commands.py                # Bot command handlers & Greek responses
└── e2e/                                    # Full-system end-to-end test suite
    ├── test_full_pipeline_e2e.py           # Standard pipeline smoke test (< 3.5s)
    └── test_tiers/                         # Systematic 4-Tier Opaque-Box E2E Test Suite
        ├── __init__.py
        ├── harness.py                      # Contractual adapter, oracle models & test helpers
        ├── test_tier1_feature_coverage.py  # Tier 1: Feature Coverage (>=5 test cases per feature)
        ├── test_tier2_boundary_corner.py   # Tier 2: Boundary & Corner Cases (>=5 test cases per boundary)
        ├── test_tier3_cross_feature.py     # Tier 3: Pairwise & Cross-Feature Combinations
        └── test_tier4_real_world_scenarios.py # Tier 4: Real-World Commercial Workloads (Bakery, Cold, Hotel)
```

---

## 3. Systematic 4-Tier Test Suite Specification

### Tier 1: Feature Coverage (`test_tier1_feature_coverage.py`)
Provides exhaustive, fine-grained coverage of all core EMS features with at least 5 distinct test cases per feature:
- **Feature 1: Telemetry Calculations**: True RMS current, active power ($P$), apparent power ($S$), power factor ($\cos\varphi$), and trapezoidal cumulative energy ($E_{kWh}$).
- **Feature 2: Γ21 Commercial Tariff**: Single-rate Low Voltage commercial contracts ($\le 25\text{ kVA}$), uniform 24h rates, fixed fees.
- **Feature 3: Γ22 Commercial Tariff**: Dual-rate Low Voltage commercial contracts ($> 25\text{ kVA}$), summer/winter peak vs off-peak schedules.
- **Feature 4: Γ23 Commercial Tariff**: Medium Voltage commercial contracts ($> 250\text{ kVA}$), tri-rate time-of-use, capacity demand charges.
- **Feature 5: Green Tariff Formula (Law 5068/2023)**: Fluctuation Mechanism ($MD$) calculating wholesale Day-Ahead Market $TEA$ against bounds $[L_l, L_u]$ with amplification factor $\alpha$ and offset $\beta$.
- **Feature 6: Yellow & Dynamic DAM Spot Pricing**: Day-Ahead Market spot rates with grid loss factor and supplier margins.
- **Feature 7: Regulated Charges**: DEDDIE distribution, ADMIE transmission, ETMEAR, YKO, EFK excise tax, DETE 5‰ levy, and 6% VAT.
- **Feature 8: Capacity Excess & Power Factor Penalties**: Contracted kVA breach penalties and $\cos\varphi < 0.85$ distribution charge scaling.
- **Feature 9: Running Costs & Daily Spend**: Instantaneous running cost (€/h), daily accumulated spend, and rate breakdown.
- **Feature 10: Peak Surcharge Projection**: Financial projection of excess penalty (€) for remaining active peak window duration.
- **Feature 11: Telemetry Ingestion API**: FastAPI endpoint `POST /api/v1/telemetry`, Pydantic v2 validation, invariant checks ($|P_{total} - \sum P_i| \le 0.05$).
- **Feature 12: Proactive Alert Generation within 30s**: Real-time breach detection and alert triggering during peak tariff windows.
- **Feature 13: Greek Notification Templates**: Localized Greek HTML formatting with load kW, threshold kW, active window, estimated € penalty, and curtailment advice.
- **Feature 14: Alert Throttling, Cooldown & Hysteresis**: 3-sample debounce, 30-min cooldown per category, and 10% release hysteresis ($P \le 0.90 \times P_{thresh}$).
- **Feature 15: Greek Conversational Commands**: `/status`, `/cost_today`, `/tariff`, `/settings`.
- **Feature 16: Commercial Simulation Profiles**: Parametric curves for Commercial Bakery, Cold Storage Logistics, and Boutique Hotel.

### Tier 2: Boundary & Corner Cases (`test_tier2_boundary_corner.py`)
Exercises strict physical, mathematical, and temporal boundaries with at least 5 test cases per boundary:
- **Boundary 1: Zero & Extreme Power Factor**: $\cos\varphi = 0.0$, negative/capacitive power factor handling, boundary $\cos\varphi = 0.8499$ vs $0.8500$, unity power factor $\cos\varphi = 1.0$.
- **Boundary 2: Max Capacity Breach**: Exact contracted kVA boundary ($S = S_{contracted}$), slight excess ($+0.1\text{ kVA}$), massive 200% overload, zero load ($0\text{ kW}$).
- **Boundary 3: Boundary Minute of Peak Tariff Windows**: 13:59:59 (normal) vs 14:00:00 (summer peak), 16:59:59 (peak) vs 17:00:00 (normal), winter peak transitions, weekend/holiday off-peak exemptions.
- **Boundary 4: Rapid-Fire Telemetry Bursts**: High-frequency sub-second bursts, microsecond jitter, duplicate timestamps, out-of-order packets.
- **Boundary 5: Borderline Threshold Hysteresis**: Exact threshold ($P = P_{thresh}$), hysteresis boundary ($P = 0.9001 \times P_{thresh}$ vs $P = 0.8999 \times P_{thresh}$), single-sample noise spikes rejected by debounce.

### Tier 3: Cross-Feature Combinations (`test_tier3_cross_feature.py`)
Evaluates multi-variable interactions across different subsystems:
- Pairwise Combination A: Green tariff fluctuation mechanism operating during an active peak window with low $\cos\varphi < 0.85$ penalty.
- Pairwise Combination B: Yellow / Dynamic hourly spot tariff during a commercial bakery morning baking spike.
- Pairwise Combination C: Γ22 dual-rate tariff with cold storage cyclical refrigeration compressor pull-downs and 30-minute alert cooldown suppression.
- Pairwise Combination D: Regulated charges under extreme wholesale market volatility ($TEA > 250\text{ €/MWh}$) and contracted capacity breach.
- Pairwise Combination E: Interactive Greek bot query `/cost_today` immediately following rapid-fire telemetry bursts during an active peak breach.

### Tier 4: Real-World Commercial Application Scenarios (`test_tier4_real_world_scenarios.py`)
End-to-end full-day lifecycle simulations verifying realistic business operations:
- **Scenario 1: Artisanal Commercial Bakery Morning Baking Schedule**: 03:00 oven pre-heating (35 kW), 05:30 mixer operation, 07:00 retail opening, 14:30 afternoon prep surge breaching peak threshold, alert generation, operator curtailment, and recovery.
- **Scenario 2: Cold Storage Logistics Door Disturbance & Compressor Pull-Down**: Continuous baseline refrigeration (18 kW), 14:15 delivery dock door left open, thermal surge driving compressors to 32 kW during peak tariff hours, proactive alert dispatch, door closure, and hysteresis clearance.
- **Scenario 3: Boutique Hotel Summer HVAC & Evening Dining Peak**: Morning laundry & kitchen load (22 kW), 15:00 guest arrival and VRV chiller ramp during peak heat/rate window (32 kW), alert trigger, 20:30 restaurant dinner service.
- **Scenario 4: Multi-Facility Concurrent Telemetry & Isolated Bot Reporting**: Simultaneous ingestion from multiple distinct commercial facilities (Bakery, Cold Storage, Hotel) verifying tenant isolation, independent cooldown timers, and accurate `/status` responses.

---

## 4. Test Fixtures & Utilities

### 4.1 MockTelegramClient
An asynchronous, thread-safe test double implementing `ITelegramClient`:
```python
class MockTelegramClient:
    def __init__(self):
        self.sent_messages: list[dict] = []

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
        self.sent_messages.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "timestamp": datetime.utcnow()})
        return True

    def get_last_message(self) -> dict | None: ...
    def clear(self) -> None: ...
    def count(self) -> int: ...
```

### 4.2 Telemetry Payload Factory
Generates physically consistent, schema-validated three-phase telemetry payloads with adjustable phase currents, voltages, and power factors, automatically maintaining the physical invariant:
$$|P_{total} - (P_1 + P_2 + P_3)| \le 0.05\text{ kW}$$

### 4.3 Database Isolation
Every test module or test session utilizes an isolated in-memory or ephemeral SQLite database initialized with Write-Ahead Logging (WAL) mode pragmas, guaranteeing zero residual state across tests.

---

## 5. Execution Instructions

### Run the Complete 4-Tier Test Suite
```bash
pytest tests/e2e/test_tiers/ -v
```

### Run by Specific Tier
```bash
# Tier 1: Feature Coverage
pytest tests/e2e/test_tiers/test_tier1_feature_coverage.py -v

# Tier 2: Boundary & Corner Cases
pytest tests/e2e/test_tiers/test_tier2_boundary_corner.py -v

# Tier 3: Cross-Feature Combinations
pytest tests/e2e/test_tiers/test_tier3_cross_feature.py -v

# Tier 4: Real-World Commercial Application Scenarios
pytest tests/e2e/test_tiers/test_tier4_real_world_scenarios.py -v
```

### Run with Execution Timing & Benchmark Check (< 30s)
```bash
pytest tests/e2e/test_tiers/ --durations=10
```

---

## 6. Verification Criteria & Performance Budgets

| Metric | Requirement | Target |
|---|---|---|
| Total E2E Test Runtime | $\le 30.0\text{ s}$ | $< 5.0\text{ s}$ |
| Tier 1 Feature Test Count | $\ge 5\text{ cases / feature}$ (16 features) | $\ge 80\text{ test cases}$ |
| Tier 2 Boundary Test Count | $\ge 5\text{ cases / boundary}$ (5 boundaries) | $\ge 25\text{ test cases}$ |
| Tier 3 Combinations Test Count | Pairwise cross-features | $\ge 10\text{ test cases}$ |
| Tier 4 Commercial Scenarios | End-to-end full workloads | $\ge 4\text{ complete scenarios}$ |
| Total Test Suite Pass Rate | 100% pass | 100% pass |
| Flakiness Rate | 0% (zero flaky tests) | 0% |
