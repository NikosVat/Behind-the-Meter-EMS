# Design Specification: Phase 1 Sub-Project 1 — Tariff, Pricing Units & Operational Savings Core

Date: 2026-10-01  
Topic: Phase 1 Sub-Project 1: Correct tariff timezone handling, explicit price units, and truthful operational savings calculations  
Status: Draft Spec for User Review  

---

## 1. Overview & Business Context

In the comprehensive repository audit (`docs/product/REPOSITORY_REVIEW_2026-10-01.md`), Priority 1 identified that incorrect cost calculations undermine the credibility of every automated recommendation, ROI projection, and competition claim:
1. **Timezone Desynchronization**: Tariff peak windows and wholesale market lookups evaluated UTC timestamps as local hours, shifting peak tariff classification and market Day-Ahead Market (DAM) prices by 2 to 3 hours.
2. **Price Unit Discontinuity**: Wholesale electricity prices in Day-Ahead Market feeds are expressed in €/MWh. A threshold heuristic (`abs(val) > 1.0`) misidentified wholesale prices in the range `[-1.0, 1.0]` €/MWh (frequent during Greek solar surplus midday periods) as €/kWh, causing massive false price spikes and inverting rebate mechanisms into heavy penalty charges.
3. **Overstated Capacity Savings**: Recommendation cards multiplied shifted load power by the solver's internal constraint penalty weight (e.g., 50 €/kW or 18.50 €/kW) rather than true contractual billing deltas, claiming high euro savings (e.g. €127.16) even when facility baseline load continued to exceed contracted capacity at other hours.

This specification details the architecture, interfaces, algorithms, and automated regression tests required to resolve these three core financial issues.

---

## 2. Component Architecture & Interfaces

### 2.1 Timezone Normalization (`tariff_engine/contracts.py` & `backend/market/service.py`)

All Greek regulatory rules (DEDDIE peak/off-peak windows, seasonal schedules) and Hellenic Energy Exchange (HEnEx) Day-Ahead Market clearing schedules operate strictly in Greek civil time (`Europe/Athens`, observing EET UTC+2 / EEST UTC+3).

#### Timezone Normalization Helper
A canonical helper function is established in `tariff_engine/contracts.py`:
```python
from zoneinfo import ZoneInfo
from datetime import datetime

ATHENS_TZ = ZoneInfo("Europe/Athens")

def to_athens_time(dt: datetime) -> datetime:
    """Normalize datetime to Greek civil time (Europe/Athens).
    
    If dt is timezone-aware (e.g., UTC telemetry), converts to Europe/Athens.
    If dt is naive, assumes it represents local Greek civil time and attaches tzinfo.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(ATHENS_TZ)
    return dt.replace(tzinfo=ATHENS_TZ)
```

#### Updated Functions in `tariff_engine/contracts.py`
- `get_greek_season(dt: datetime) -> Season`:
  Normalizes `dt` using `to_athens_time(dt)` before checking `month`. Summer is May 1 to Oct 31; Winter is Nov 1 to Apr 30 in Athens civil time.
- `is_peak_window(dt: datetime, season: str | Season = "auto") -> bool`:
  Normalizes `dt` using `to_athens_time(dt)`. Evaluates weekday on `local_dt.weekday()` and peak hours on `local_dt.hour` (Summer: 14:00–17:00; Winter: 17:00–21:00 Mon–Fri).
- `is_offpeak_window(dt: datetime, season: str | Season = "auto") -> bool`:
  Normalizes `dt` using `to_athens_time(dt)`. Off-peak night window is 23:00–07:00 local time.

#### Market Service Alignment (`backend/market/service.py`)
In `MarketService.get_instantaneous_tea_rate`:
```python
local_dt = to_athens_time(timestamp)
date_str = local_dt.strftime("%Y-%m-%d")
hour = local_dt.hour
```
Ensures Day-Ahead Market queries from UTC telemetry (e.g., `2026-10-01T21:30:00Z` -> `2026-10-02 00:30 EEST`) fetch market delivery prices for the correct Greek market day and hour.

---

### 2.2 Explicit Pricing Units (`tariff_engine/yellow_dynamic.py` & `tariff_engine/green_tariff.py`)

#### Elimination of Heuristics
Remove `_normalize_to_kwh(val)` from both `yellow_dynamic.py` and `green_tariff.py`. The heuristic `if abs(val) > 1.0: return val / 1000.0` is discarded.

#### Explicit Conversion Policy
Define explicit conversion utility:
```python
from typing import Literal

PriceUnit = Literal["EUR_MWH", "EUR_KWH"]

def to_kwh_rate(val: float, unit: PriceUnit = "EUR_MWH") -> float:
    """Converts price to €/kWh with strict unit validation."""
    if unit == "EUR_MWH":
        return val / 1000.0
    elif unit == "EUR_KWH":
        return val
    raise ValueError(f"Unsupported price unit '{unit}'. Must be 'EUR_MWH' or 'EUR_KWH'.")
```

#### Signature Contracts
- In `yellow_dynamic.py`:
  `calculate_yellow_dynamic_supply_rate(tea_eur_mwh: float, loss_factor: float = 0.135, margin_eur_kwh: float = 0.015, p_base: float = 0.050, floor_at_zero: bool = False, unit: PriceUnit = "EUR_MWH") -> float`:
  - `tea_kwh = to_kwh_rate(tea_eur_mwh, unit)`
  - Standard formula: `rate = tea_kwh * (1.0 + loss_factor) + margin_eur_kwh + p_base`
  - Values such as `0.50 €/MWh` correctly convert to `0.00050 €/kWh`, yielding `rate = 0.06557 €/kWh` (instead of 0.6325 €/kWh).
- In `green_tariff.py`:
  - `calculate_green_tariff_fluctuation` and `calculate_green_tariff_supply_rate` accept `tea_eur_mwh`, `ll_eur_mwh`, `lu_eur_mwh`, and `tea_m2_eur_mwh` strictly in €/MWh (defaulting to division by 1000.0) or with explicit `unit: PriceUnit = "EUR_MWH"`.
  - When `tea_eur_mwh = 0.50`, it yields $0.00050 \text{ €/kWh} < L_l = 0.095 \text{ €/kWh}$, producing a valid lower breach rebate ($\text{MD} = -0.10868 \text{ €/kWh}$).
  - Negative wholesale clearing rates (e.g., `-20.0 €/MWh`) compute linearly without inversion.

---

### 2.3 Truthful Operational Savings & Breach Transparency (`optimization_engine/decision_support.py`)

#### Decoupled Valuation Model
1. **Verifiable Energy Savings**:
   Calculated strictly on the tariff differential for shifted kilowatt-hours:
   $$\Delta C_{\text{energy}} = \sum_{t \in \mathcal{T}_{\text{orig}}} P(t) \cdot r(t) - \sum_{t \in \mathcal{T}_{\text{opt}}} P(t) \cdot r(t)$$
2. **Physical Demand Mitigation**:
   Reported in engineering units on the card as `peak_load_avoided_kw = load_kw`.
3. **Contractual Demand Tariff Valuation**:
   - `problem.capacity_penalty_eur_per_kw` (the solver's mathematical constraint weight) is strictly decoupled from financial reporting.
   - An explicit optional field `contracted_demand_rate_eur_per_kw: float = 0.0` is supported on `OptimizationProblem` to represent real customer billing demand charges (€/kW/month).
   - Euro demand savings are credited if and only if the whole facility daily peak drops below the baseline peak and towards/under contracted capacity:
     $$\Delta P = \max(0.0, \min(P_{\text{base}}^{\max}, P_{\text{contract}} + \text{breach}_{\text{base}}) - \max(P_{\text{contract}}, P_{\text{opt}}^{\max}))$$
   - If $\Delta P = 0$ (for example, non-shiftable baseline background loads maintain the peak above capacity), euro demand savings is `€0.00`.
4. **Persistent Breach Alerting**:
   If the optimized schedule still exhibits a capacity breach ($P_{\text{opt}}^{\max} > P_{\text{contract}}$):
   - All recommendation cards maintain `projected_peak_kw` alongside `contracted_capacity_kw`.
   - A dedicated `SYSTEM_WARNING` card is emitted:
     - `priority = PriorityLevel.CRITICAL`
     - Title: `"Contracted Capacity Breach: {breach_kw:.1f} kW Unmitigated"`
     - Description: Clear notice that flexible shifting is insufficient to resolve the peak, prompting operator review or manual shedding.

---

## 3. Data Flow & Sequence

1. **Telemetry / Ingestion**: Telemetry arrives with UTC timestamp.
2. **Market Lookup**: `MarketService.get_instantaneous_tea_rate` converts timestamp to Athens local time, fetching the exact HEnEx DAM rate for that local hour.
3. **Tariff Calculation**: `yellow_dynamic.py` and `green_tariff.py` evaluate rates using explicit unit conversion (`/ 1000.0`), preserving solar-hour rebates and small market rates without distortion.
4. **Optimization & Decision Support**: `DecisionSupportEngine` compares nominal vs. optimal schedules, computing honest energy shifting savings, reporting physical peak reduction in kW, and emitting critical breach alerts when baseline loads exceed limits.

---

## 4. Verification & Regression Plan

### 4.1 Automated Test Suite (`tests/unit/test_phase1_tariff_cost_regressions.py`)
- **Timezone Tests**:
  - Verify `is_peak_window` with UTC `2026-10-01T11:00:00Z` and Athens `2026-10-01T14:00:00+03:00` both evaluate to `True`.
  - Verify winter evening peak `2026-11-05T15:30:00Z` (17:30 EET) evaluates to `True`.
  - Verify season boundary transition at `2026-10-31T22:30:00Z` correctly evaluates to `Season.WINTER`.
- **Pricing Unit Tests**:
  - Verify wholesale TEA `0.50 €/MWh` produces Yellow supply rate `0.06557 €/kWh` (not `0.6325 €/kWh`).
  - Verify continuous transition across `0.99 €/MWh` and `1.01 €/MWh` (difference $< 0.0001$).
  - Verify Green Tariff fluctuation rebate calculation at `0.50 €/MWh` yields negative `MD = -0.10868 €/kWh`.
  - Update `test_market_tariff_concurrency.py`'s `test_low_wholesale_tea_normalization_boundary_anomaly` to verify correct regulatory rebate.
- **Decision Support & Capacity Savings Tests**:
  - Reproduce finding #2 scenario: 40 kW background, 35 kW capacity, 6.8 kW defrost shifted.
  - Assert `estimated_savings_eur` equals €1.36 (energy savings) and does not claim €127.16.
  - Assert `peak_load_avoided_kw` equals 6.8 kW.
  - Assert a critical capacity breach notice is emitted for the remaining 40 kW breach.

### 4.2 Verification Protocol (Red-Green Execution)
1. Run new regression tests against unchanged codebase (verifying red/failure).
2. Apply changes to `tariff_engine/contracts.py`, `tariff_engine/yellow_dynamic.py`, `tariff_engine/green_tariff.py`, `backend/market/service.py`, and `optimization_engine/decision_support.py`.
3. Run new regression tests (verifying green).
4. Run full repository test suite (`pytest`) confirming 0 regressions.
