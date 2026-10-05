# Phase 1 Sub-Project 1: Tariff, Pricing Units & Operational Savings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct Greek market timezone handling in tariff contracts, enforce explicit Day-Ahead Market wholesale price units without magnitude heuristics, and decouple decision-support recommendation savings from solver penalty weights to eliminate false financial ROI claims.

**Architecture:** 
1. Establish `to_athens_time` in `tariff_engine/contracts.py` to ensure regulatory windows and HEnEx Day-Ahead Market queries align with physical civil delivery intervals in Greece (`Europe/Athens`).
2. Replace heuristic threshold sniffing (`abs(val) > 1.0`) in `yellow_dynamic.py` and `green_tariff.py` with strict unit-validated conversion (`to_kwh_rate`), accurately preserving solar-surplus and negative wholesale pricing.
3. Decouple energy savings from optimizer constraint penalty multipliers in `optimization_engine/decision_support.py`, reporting physical demand mitigation in kW, real energy delta in EUR, and explicit `SYSTEM_WARNING` notices when background loads exceed capacity.

**Tech Stack:** Python 3.11+, pytest, zoneinfo, Pydantic, SciPy MILP.

**Spec:** `docs/superpowers/specs/2026-10-01-phase1-tariff-cost-core-design.md`

## Global Constraints
- Target timezone for Greek electricity regulation and HEnEx DAM is strictly `ZoneInfo("Europe/Athens")`.
- Naive datetime inputs are treated as already Greek civil time (`replace(tzinfo=ATHENS_TZ)`). Timezone-aware datetimes (such as UTC telemetry) are converted with `.astimezone(ATHENS_TZ)`.
- Wholesale electricity prices named `_eur_mwh` are strictly treated as €/MWh (`/ 1000.0` for kWh rates).
- Solver tuning weight `capacity_penalty_eur_per_kw` MUST NOT be multiplied into recommendation card `estimated_savings_eur`.
- All changes must pass existing and newly added regression tests with 0 failures under `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`.

---

### Task 1: Greek Market Timezone Normalization in `tariff_engine/contracts.py` & `backend/market/service.py`

**Files:**
- Modify: `tariff_engine/contracts.py:112-197`
- Modify: `backend/market/service.py:244-270`
- Create: `tests/unit/test_phase1_tariff_cost_regressions.py`

**Interfaces:**
- Consumes: `ZoneInfo("Europe/Athens")` from standard library `zoneinfo`.
- Produces: `to_athens_time(dt: datetime) -> datetime` in `tariff_engine.contracts`.

- [ ] **Step 1: Write failing regression tests for timezone handling**

Add to `tests/unit/test_phase1_tariff_cost_regressions.py`:
```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest
from tariff_engine.contracts import get_greek_season, is_offpeak_window, is_peak_window, Season, to_athens_time

ATHENS_TZ = ZoneInfo("Europe/Athens")

class TestTimezoneRegressions:
    def test_utc_and_athens_instant_parity_summer_peak(self):
        """2026-10-01 11:00:00 UTC is 14:00:00 EEST (Summer weekday peak). Both must return True."""
        dt_utc = datetime(2026, 10, 1, 11, 0, 0, tzinfo=timezone.utc)
        dt_athens = datetime(2026, 10, 1, 14, 0, 0, tzinfo=ATHENS_TZ)
        assert is_peak_window(dt_utc) is True
        assert is_peak_window(dt_athens) is True

    def test_utc_timestamp_outside_peak_in_athens(self):
        """2026-10-01 14:00:00 UTC is 17:00:00 EEST (Summer peak ends at 17:00). Must return False."""
        dt_utc = datetime(2026, 10, 1, 14, 0, 0, tzinfo=timezone.utc)
        assert is_peak_window(dt_utc) is False

    def test_winter_season_and_peak_hour(self):
        """2026-11-05 15:30:00 UTC is 17:30:00 EET (Winter weekday peak 17:00-21:00)."""
        dt_utc = datetime(2026, 11, 5, 15, 30, 0, tzinfo=timezone.utc)
        assert get_greek_season(dt_utc) == Season.WINTER
        assert is_peak_window(dt_utc) is True

    def test_month_transition_at_midnight_utc(self):
        """2026-10-31 22:30:00 UTC is 2026-11-01 00:30:00 EET. Must classify as WINTER, not SUMMER."""
        dt_utc = datetime(2026, 10, 31, 22, 30, 0, tzinfo=timezone.utc)
        assert get_greek_season(dt_utc) == Season.WINTER

    def test_offpeak_night_window_with_utc(self):
        """2026-10-01 21:30:00 UTC is 2026-10-02 00:30:00 EEST (Night off-peak 23:00-07:00)."""
        dt_utc = datetime(2026, 10, 1, 21, 30, 0, tzinfo=timezone.utc)
        assert is_offpeak_window(dt_utc) is True
```

- [ ] **Step 2: Run test to verify it fails on existing codebase**

Run: `pytest tests/unit/test_phase1_tariff_cost_regressions.py::TestTimezoneRegressions -v`  
Expected: FAIL (assertion errors on `is_peak_window(dt_utc)`, `get_greek_season(dt_utc)`, and `to_athens_time` ImportError).

- [ ] **Step 3: Implement `to_athens_time` and normalize timezone handling**

In `tariff_engine/contracts.py`:
```python
from zoneinfo import ZoneInfo

ATHENS_TZ = ZoneInfo("Europe/Athens")

def to_athens_time(dt: datetime) -> datetime:
    """Normalize datetime to Greek civil time (Europe/Athens)."""
    if dt.tzinfo is not None:
        return dt.astimezone(ATHENS_TZ)
    return dt.replace(tzinfo=ATHENS_TZ)
```
Update `get_greek_season`, `is_peak_window`, and `is_offpeak_window` in `tariff_engine/contracts.py` to call `local_dt = to_athens_time(dt)` and inspect `local_dt.month`, `local_dt.weekday()`, and `local_dt.hour`.

In `backend/market/service.py:246`:
```python
        if color in ("yellow", "dynamic"):
            from tariff_engine.contracts import to_athens_time
            local_dt = to_athens_time(timestamp)
            date_str = local_dt.strftime("%Y-%m-%d")
            hour = local_dt.hour
```
And update monthly rate resolution in `backend/market/service.py` to use `to_athens_time(timestamp).strftime("%Y-%m")`.

- [ ] **Step 4: Run timezone tests to verify they pass**

Run: `pytest tests/unit/test_phase1_tariff_cost_regressions.py::TestTimezoneRegressions -v`  
Expected: PASS with 5 passed.

- [ ] **Step 5: Commit Task 1 changes**

```bash
git add tariff_engine/contracts.py backend/market/service.py tests/unit/test_phase1_tariff_cost_regressions.py
git commit -m "fix(tariffs): normalize Greek market timezone to Europe/Athens across contracts and market service"
```

---

### Task 2: Explicit Wholesale & Retail Pricing Units in `tariff_engine/yellow_dynamic.py` and `tariff_engine/green_tariff.py`

**Files:**
- Modify: `tariff_engine/yellow_dynamic.py:16-85`
- Modify: `tariff_engine/green_tariff.py:22-125`
- Modify: `tests/integration/test_market_tariff_concurrency.py:549-573`
- Modify: `tests/unit/test_phase1_tariff_cost_regressions.py`

**Interfaces:**
- Consumes: `PriceUnit = Literal["EUR_MWH", "EUR_KWH"]`.
- Produces: `to_kwh_rate(val: float, unit: PriceUnit = "EUR_MWH") -> float`.

- [ ] **Step 1: Write failing regression tests for pricing units**

Add to `tests/unit/test_phase1_tariff_cost_regressions.py`:
```python
from tariff_engine.yellow_dynamic import calculate_yellow_dynamic_supply_rate, to_kwh_rate
from tariff_engine.green_tariff import calculate_green_tariff_fluctuation, calculate_green_tariff_supply_rate

class TestPricingUnitsRegressions:
    def test_low_positive_tea_yellow_dynamic_rate(self):
        """TEA = 0.50 €/MWh must convert to 0.00050 €/kWh, yielding 0.06557 €/kWh, NOT 0.6325 €/kWh."""
        rate = calculate_yellow_dynamic_supply_rate(
            tea_eur_mwh=0.50,
            loss_factor=0.135,
            margin_eur_kwh=0.015,
            p_base=0.050,
        )
        assert rate == 0.06557

    def test_continuity_across_one_euro_boundary(self):
        """Ensure no 1000x jump between 0.99 €/MWh and 1.01 €/MWh."""
        rate_0_99 = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.99)
        rate_1_01 = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=1.01)
        assert abs(rate_1_01 - rate_0_99) < 0.0001

    def test_green_tariff_fluctuation_solar_surplus_rebate(self):
        """TEA = 0.50 €/MWh is far below Ll = 95.0 €/MWh, so MD must be a negative rebate."""
        md = calculate_green_tariff_fluctuation(
            tea_eur_mwh=0.50,
            ll_eur_mwh=95.0,
            lu_eur_mwh=115.0,
            alpha=1.15,
        )
        # Expected: 1.15 * (0.00050 - 0.095) = -0.108675 €/kWh
        assert pytest.approx(md, rel=1e-4) == -0.108675

    def test_zero_and_negative_wholesale_prices(self):
        """Zero and negative wholesale prices calculate linearly without exception."""
        rate_zero = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=0.0, floor_at_zero=False)
        assert rate_zero == 0.06500

        rate_neg = calculate_yellow_dynamic_supply_rate(tea_eur_mwh=-20.0, floor_at_zero=False)
        # -0.020 * 1.135 + 0.015 + 0.050 = -0.0227 + 0.065 = 0.04230
        assert rate_neg == 0.04230
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_phase1_tariff_cost_regressions.py::TestPricingUnitsRegressions -v`  
Expected: FAIL (`rate == 0.06557` fails because old code yields `0.6325`).

- [ ] **Step 3: Implement explicit unit conversions and remove heuristic guessing**

In `tariff_engine/yellow_dynamic.py` and `tariff_engine/green_tariff.py`:
1. Define:
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
2. Replace `_normalize_to_kwh(val)` with `to_kwh_rate(val, unit)` where `unit` defaults to `"EUR_MWH"`.
3. In `tests/integration/test_market_tariff_concurrency.py:549-573`, update `test_low_wholesale_tea_normalization_boundary_anomaly`: replace the assertion expecting positive MD with `assert calc_md_0_5 < 0.0` confirming the rebate.

- [ ] **Step 4: Run pricing unit tests to verify they pass**

Run: `pytest tests/unit/test_phase1_tariff_cost_regressions.py::TestPricingUnitsRegressions tests/integration/test_market_tariff_concurrency.py -k test_low_wholesale_tea -v`  
Expected: PASS with all tests green.

- [ ] **Step 5: Commit Task 2 changes**

```bash
git add tariff_engine/yellow_dynamic.py tariff_engine/green_tariff.py tests/integration/test_market_tariff_concurrency.py tests/unit/test_phase1_tariff_cost_regressions.py
git commit -m "fix(tariffs): replace price unit heuristic with strict to_kwh_rate conversion"
```

---

### Task 3: Decoupled Capacity Savings & Truthful Breach Alerting in `optimization_engine/decision_support.py`

**Files:**
- Modify: `optimization_engine/models.py:129-142`
- Modify: `optimization_engine/decision_support.py:80-250`
- Modify: `tests/unit/test_phase1_tariff_cost_regressions.py`

**Interfaces:**
- Consumes: `OptimizationProblem.contracted_demand_rate_eur_per_kw` (optional, default `0.0`).
- Produces: `ActionRecommendation` with honest `estimated_savings_eur`, `peak_load_avoided_kw`, and `SYSTEM_WARNING` when breaches persist.

- [ ] **Step 1: Write failing regression tests for decision support capacity savings**

Add to `tests/unit/test_phase1_tariff_cost_regressions.py`:
```python
from optimization_engine.decision_support import DecisionSupportEngine
from optimization_engine.models import (
    DefrostLoad,
    OptimizationProblem,
    PriorityLevel,
    RecommendationCategory,
    ScheduleResult,
)

class TestDecisionSupportSavingsRegressions:
    def test_reproduce_finding_2_capacity_savings_not_overstated(self):
        """Scenario: 40 kW flat background, 35 kW capacity, 6.8 kW defrost shifted from hr 14 to hr 11.
        Baseline peak = 46.8 kW. Optimized peak = 46.8 kW (breach persists at other hours).
        Old code claimed EUR 127.16 savings by multiplying 6.8 kW * capacity_penalty_eur_per_kw.
        New code must claim ONLY the true energy shift savings (EUR 1.36) and avoid claiming false demand savings.
        """
        engine = DecisionSupportEngine(facility_id="fac_test_01")
        tariffs = [0.15] * 24
        tariffs[14] = 0.35  # expensive nominal defrost window
        tariffs[11] = 0.15  # cheap optimal defrost window
        # rate diff = 0.20 EUR/kWh * 6.8 kW * 1h = 1.36 EUR energy savings

        problem = OptimizationProblem(
            baseline_load_kw=[40.0] * 24,
            tariff_rates_eur_kwh=tariffs,
            contracted_capacity_kw=35.0,
            capacity_penalty_eur_per_kw=18.50,
            defrost_loads=[DefrostLoad(name="Freezer Rack", nominal_start_hour=14, duration_hours=1, power_kw=6.8)],
        )

        dev_sched = [0.0] * 24
        dev_sched[11] = 6.8  # shifted to 11
        schedule = ScheduleResult(
            status="optimal",
            is_optimal=True,
            horizon_hours=24,
            baseline_total_load_kw=[40.0 + (6.8 if t == 14 else 0.0) for t in range(24)],
            optimized_total_load_kw=[40.0 + (6.8 if t == 11 else 0.0) for t in range(24)],
            baseline_cost_eur=146.36,
            optimized_cost_eur=145.00,
            savings_eur=1.36,
            savings_pct=0.93,
            peak_baseline_kw=46.8,
            peak_optimized_kw=46.8,
            peak_reduction_kw=0.0,
            capacity_breached_baseline=True,
            capacity_breached_optimized=True,
            device_schedules={"Freezer Rack": dev_sched},
            operationally_feasible=True,
        )

        recs = engine.generate_recommendations(problem, schedule)
        defrost_recs = [r for r in recs if r.category == RecommendationCategory.DEFROST_SHIFT]
        assert len(defrost_recs) == 1
        rec = defrost_recs[0]

        # Must report exact energy delta, NOT inflated penalty surcharge
        assert rec.estimated_savings_eur == 1.36
        assert rec.peak_load_avoided_kw == 6.8

        # Must generate critical warning card for unmitigated breach
        warning_recs = [r for r in recs if r.priority == PriorityLevel.CRITICAL and "Capacity Breach" in r.title]
        assert len(warning_recs) >= 1
        assert "46.8" in warning_recs[0].description_en or "11.8" in warning_recs[0].description_en
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_phase1_tariff_cost_regressions.py::TestDecisionSupportSavingsRegressions -v`  
Expected: FAIL (`assert rec.estimated_savings_eur == 1.36` fails with `127.16 != 1.36`).

- [ ] **Step 3: Update `decision_support.py` and `models.py`**

1. In `optimization_engine/models.py`:
   Add `contracted_demand_rate_eur_per_kw: float = 0.0` to `OptimizationProblem`.
2. In `optimization_engine/decision_support.py`:
   - In `_evaluate_defrost_shift`:
     Compute:
     ```python
     rate_diff = max(0.0, nominal_rate - opt_rate)
     energy_savings = rate_diff * defrost.power_kw * defrost.duration_hours
     
     # Whole-facility demand savings: only credited if whole schedule peak dropped below baseline peak
     demand_savings = 0.0
     if problem.contracted_demand_rate_eur_per_kw > 0.0:
         net_peak_drop = max(0.0, schedule.peak_baseline_kw - max(problem.contracted_capacity_kw, schedule.peak_optimized_kw))
         if net_peak_drop > 0.0 and nominal_start == schedule.baseline_total_load_kw.index(schedule.peak_baseline_kw):
             demand_savings = min(defrost.power_kw, net_peak_drop) * problem.contracted_demand_rate_eur_per_kw

     total_savings = round(energy_savings + demand_savings, 2)
     ```
   - In `_evaluate_batch_shift`: apply identical decoupled logic.
   - In `generate_recommendations`:
     If `schedule.capacity_breached_optimized` is `True`:
     Append an unmitigated capacity breach alert card:
     ```python
     breach_kw = max(0.0, schedule.peak_optimized_kw - problem.contracted_capacity_kw)
     peak_hour = schedule.optimized_total_load_kw.index(schedule.peak_optimized_kw)
     recommendations.append(ActionRecommendation(
         recommendation_id=f"rec_breach_{uuid.uuid4().hex[:8]}",
         facility_id=self.facility_id,
         category=RecommendationCategory.PEAK_SHAVING,
         priority=PriorityLevel.CRITICAL,
         title=f"Capacity Breach Alert: {breach_kw:.1f} kW Unmitigated",
         description_el=f"Η ζήτηση προβλέπεται στα {schedule.peak_optimized_kw:.1f} kW την ώρα {peak_hour:02d}:00 (όριο: {problem.contracted_capacity_kw:.1f} kW). Οι ευέλικτες μετατοπίσεις δεν επαρκούν.",
         description_en=f"Projected demand reaches {schedule.peak_optimized_kw:.1f} kW at hour {peak_hour:02d}:00 exceeding contracted {problem.contracted_capacity_kw:.1f} kW. Flexible load shifting alone cannot resolve this breach; manual shedding or contract review required.",
         asset_name="Whole Facility Demand",
         original_window=f"{peak_hour:02d}:00-{(peak_hour+1):02d}:00",
         recommended_window=f"{peak_hour:02d}:00-{(peak_hour+1):02d}:00",
         peak_load_avoided_kw=0.0,
         estimated_savings_eur=0.0,
         contracted_capacity_kw=problem.contracted_capacity_kw,
         projected_peak_kw=schedule.peak_optimized_kw,
     ))
     ```

- [ ] **Step 4: Run decision support regression tests to verify they pass**

Run: `pytest tests/unit/test_phase1_tariff_cost_regressions.py::TestDecisionSupportSavingsRegressions -v`  
Expected: PASS.

- [ ] **Step 5: Commit Task 3 changes**

```bash
git add optimization_engine/models.py optimization_engine/decision_support.py tests/unit/test_phase1_tariff_cost_regressions.py
git commit -m "fix(optimization): decouple recommendation savings from solver penalty weights and add breach alerting"
```

---

### Task 4: Complete Suite Verification & GitHub Synchronization

**Files:**
- Test: Full repository test suite (`pytest`)

- [ ] **Step 1: Run complete test suite**

Run: `pytest -v` (with `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`)  
Verify: ALL tests pass, 0 failures.

- [ ] **Step 2: Check git status and diff**

Run: `git status; git diff`  
Verify: No untracked junk, no unintended edits.

- [ ] **Step 3: Push changes to remote as authorized in `AGENTS.md`**

Run: `git push origin main`  
Report: Commit hash and remote push status.
