# Phase 1 Sub-Project 2: Telemetry Quality Checks & Verified Device HTTPS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disallow nonfinite measurement values (NaN/Inf) at the telemetry schema boundary, enforce sample density and time-coverage eligibility gates on hourly forecasting inputs, and configure verified root CA HTTPS in ESP32 firmware.

**Architecture:**
1. Enable `allow_inf_nan=False` in Pydantic v2 `ConfigDict` across all telemetry schemas, rejecting nonfinite payloads at HTTP boundary with status 422.
2. Enhance `load_facility_forecast` in `backend/schedule_inputs.py` to require at least 6 samples and 30 minutes observation span per hour before accepting hourly averages for forecast history.
3. Configure `WiFiClientSecure` in `firmware/src/telemetry_client.cpp` with `setServerRootCA`, rejecting unauthenticated HTTPS by default.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, C++ / Arduino ESP32 core, PlatformIO.

**Spec:** `docs/superpowers/specs/2026-10-01-phase1-telemetry-quality-https-design.md`

## Global Constraints
- All telemetry numerical fields must strictly reject `NaN`, `+Infinity`, `-Infinity`, and string `"NaN"`.
- Forecast input hours with fewer than 6 samples or less than 1,800 seconds span must be excluded from hourly history.
- Firmware must compile cleanly with `pio run -d firmware`.
- Full test suite must pass with `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`.

---

### Task 1: Nonfinite Validation Boundary in `backend/models/telemetry.py`

**Files:**
- Modify: `backend/models/telemetry.py:16-100`
- Create: `tests/unit/test_phase1_telemetry_security_regressions.py`

- [ ] **Step 1: Write failing regression tests for nonfinite validation**

In `tests/unit/test_phase1_telemetry_security_regressions.py`:
```python
import pytest
from pydantic import ValidationError
from backend.models.telemetry import PhaseReading, TelemetryPayload

class TestNonfiniteTelemetryRegressions:
    def test_phase_reading_rejects_nan_and_inf(self):
        """PhaseReading must reject float('nan') and float('inf')."""
        with pytest.raises(ValidationError):
            PhaseReading(
                voltage_v=230.0,
                current_a=10.0,
                active_power_kw=float("nan"),
                apparent_power_kva=2.3,
                power_factor=0.98,
            )

        with pytest.raises(ValidationError):
            PhaseReading(
                voltage_v=float("inf"),
                current_a=10.0,
                active_power_kw=2.3,
                apparent_power_kva=2.3,
                power_factor=0.98,
            )

    def test_phase_reading_rejects_string_nan(self):
        """PhaseReading must reject string 'NaN'."""
        with pytest.raises(ValidationError):
            PhaseReading.model_validate({
                "voltage_v": 230.0,
                "current_a": 10.0,
                "active_power_kw": "NaN",
                "apparent_power_kva": 2.3,
                "power_factor": 0.98,
            })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_phase1_telemetry_security_regressions.py::TestNonfiniteTelemetryRegressions -v`  
Expected: FAIL.

- [ ] **Step 3: Update `backend/models/telemetry.py`**

Set:
`model_config = ConfigDict(extra="forbid", allow_inf_nan=False)`
on both `PhaseReading` and `TelemetryPayload`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_phase1_telemetry_security_regressions.py::TestNonfiniteTelemetryRegressions -v`  
Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/models/telemetry.py tests/unit/test_phase1_telemetry_security_regressions.py
git commit -m "fix(telemetry): reject nonfinite measurements at schema boundary"
```

---

### Task 2: Hourly Observation Quality & Coverage Gating in `backend/schedule_inputs.py`

**Files:**
- Modify: `backend/schedule_inputs.py:10-32`
- Modify: `tests/unit/test_phase1_telemetry_security_regressions.py`

- [ ] **Step 1: Write failing regression tests for hourly observation gating**

Append to `tests/unit/test_phase1_telemetry_security_regressions.py`:
```python
from datetime import date, datetime, timezone, timedelta
from backend.database.sqlite_store import SQLiteStore
from backend.schedule_inputs import load_facility_forecast
from optimization_engine.forecasting import ForecastUnavailable

class TestObservationQualityRegressions:
    def test_single_sample_hour_rejected(self, tmp_path):
        """An hour with only 1 sample or span < 1800s must not qualify as a well-observed hour."""
        db_path = str(tmp_path / "quality.db")
        store = SQLiteStore(db_path=db_path)
        store.init_db()

        target = date(2026, 10, 10)
        # Store only 1 reading in an hour yesterday
        yesterday_hour = datetime(2026, 10, 9, 14, 0, 0, tzinfo=timezone.utc)
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO telemetry_readings (device_id, facility_id, timestamp, total_active_power_kw, total_apparent_power_kva, system_power_factor, cumulative_energy_kwh, grid_frequency_hz, wifi_rssi_dbm, power_measurement_method) "
                "VALUES ('dev1', 'fac1', ?, 25.0, 25.0, 1.0, 100.0, 50.0, -60, 'meter_measured')",
                (yesterday_hour.isoformat(),)
            )

        # Because the history has only 1 isolated reading, forecast cannot be built
        with pytest.raises(ForecastUnavailable):
            load_facility_forecast(store, "fac1", target, "Europe/Athens")
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/unit/test_phase1_telemetry_security_regressions.py::TestObservationQualityRegressions -v`  
Expected: FAIL.

- [ ] **Step 3: Update `backend/schedule_inputs.py`**

Update query to select `COUNT(*) AS sample_count`, `MIN(timestamp) AS min_ts`, `MAX(timestamp) AS max_ts`, and filter hours:
```python
    hourly = {}
    for row in rows:
        count = row["sample_count"]
        min_ts = datetime.fromisoformat(row["min_ts"])
        max_ts = datetime.fromisoformat(row["max_ts"])
        span_s = (max_ts - min_ts).total_seconds()
        # Require at least 6 samples and 1800s (30m) span
        if count >= 6 and span_s >= 1800:
            h_dt = datetime.fromisoformat(row["hour"]).replace(tzinfo=timezone.utc)
            hourly[h_dt] = row["power"]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_phase1_telemetry_security_regressions.py::TestObservationQualityRegressions -v`  
Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/schedule_inputs.py tests/unit/test_phase1_telemetry_security_regressions.py
git commit -m "fix(forecasting): enforce hourly telemetry density and time span quality gates"
```

---

### Task 3: Verified Device HTTPS Client in Firmware

**Files:**
- Modify: `firmware/src/telemetry_client.h:60-120`
- Modify: `firmware/src/telemetry_client.cpp:215-240`

- [ ] **Step 1: Update `telemetry_client.h`**

Add:
```cpp
    /**
     * @brief Set trusted Root CA certificate for server verification.
     * @param root_ca_pem PEM certificate string
     */
    void setServerRootCA(const char* root_ca_pem);
```
And member `const char* root_ca_cert_ = nullptr;`.

- [ ] **Step 2: Update `telemetry_client.cpp`**

In `sendRestPayload`:
```cpp
    HTTPClient http;
    String target_url = String(rest_base_url_) + String(rest_endpoint_);

    WiFiClientSecure secure_client;
    if (target_url.startsWith("https://")) {
        if (root_ca_cert_ != nullptr && strlen(root_ca_cert_) > 0) {
            secure_client.setCACert(root_ca_cert_);
            http.begin(secure_client, target_url);
        } else {
            #ifndef EMS_ALLOW_INSECURE_TLS
            Serial.println(F("[SECURITY] HTTPS request aborted: no server root CA certificate configured."));
            return DeliveryOutcome::TERMINAL_REJECTION;
            #else
            secure_client.setInsecure();
            http.begin(secure_client, target_url);
            #endif
        }
    } else {
        http.begin(target_url);
    }
```

- [ ] **Step 3: Verify PlatformIO build**

Run: `pio run -d firmware`  
Expected: SUCCESS.

- [ ] **Step 4: Commit Task 3**

```bash
git add firmware/src/telemetry_client.h firmware/src/telemetry_client.cpp
git commit -m "fix(firmware): enforce root CA certificate verification for device HTTPS"
```

---

### Task 4: Complete Suite Verification & GitHub Synchronization

- [ ] **Step 1: Run full test suite**

Run: `pytest -q` with `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1`.  
Verify: 100% pass, 0 failures.

- [ ] **Step 2: Push changes to GitHub remote**

Run: `git push origin main`.
