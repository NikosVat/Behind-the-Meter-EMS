# Design Specification: Phase 1 Sub-Project 2 — Telemetry Quality Checks & Verified Device HTTPS

Date: 2026-10-01  
Topic: Phase 1 Sub-Project 2: Disallow nonfinite measurements at schema boundary, enforce hourly observation quality gates, and require verified device HTTPS  
Status: Approved Design  

---

## 1. Overview & Problem Definition

In the repository audit (`docs/product/REPOSITORY_REVIEW_2026-10-01.md`), Priority 2 identified critical data-quality and transport security gaps:
1. **Finding #5: Nonfinite measurements bypass validation**: `backend/models/telemetry.py` used Pydantic default `allow_inf_nan=True`. Measurements of `NaN`, `Infinity`, or string `"NaN"` were parsed as valid floats, corrupting energy calculations and evading multi-phase conservation balance checks.
2. **Finding #6: A present hour is not a well-observed hour**: `backend/schedule_inputs.py` computed `AVG(total_active_power_kw)` over an hour without verifying sample density or temporal distribution. An isolated single reading or clustered bursts in a 5-minute window masqueraded as representative hourly energy demand.
3. **Finding #4: Firmware HTTPS does not verify the server**: `firmware/src/telemetry_client.cpp` called `http.begin(target_url)` without configuring a trusted Root CA certificate. On Arduino ESP32 core 2.0.17, this silently called `setInsecure()`, transmitting the shared API key without server authentication.

---

## 2. Technical Design & Interfaces

### 2.1 Nonfinite Validation Boundary (`backend/models/telemetry.py`)
- Configure Pydantic v2 `ConfigDict`:
  ```python
  model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
  ```
  applied to:
  - `PhaseReading`
  - `TelemetryPayload`
  - `IngestionResponse`
- Any payload containing `float('nan')`, `float('inf')`, or json strings `"NaN"`, `"Infinity"` will trigger a Pydantic `ValidationError` and return HTTP `422 Unprocessable Entity` at the FastAPI ingestion boundary.

### 2.2 Hourly Observation Quality & Coverage Gating (`backend/schedule_inputs.py`)
- Enhance SQLite aggregation query in `load_facility_forecast`:
  ```sql
  SELECT strftime('%Y-%m-%dT%H:00:00', timestamp) AS hour,
         AVG(total_active_power_kw) AS power,
         COUNT(*) AS sample_count,
         MIN(timestamp) AS min_ts,
         MAX(timestamp) AS max_ts
  FROM telemetry_readings
  WHERE facility_id=? AND timestamp>=? AND timestamp<?
  GROUP BY hour ORDER BY hour
  ```
- Define observation criteria for an hour to be considered valid:
  - `MIN_SAMPLES_PER_HOUR = 6`: Hours with fewer than 6 samples are deemed unobserved.
  - `MIN_SPAN_SECONDS = 1800` (30 minutes): `(max_ts - min_ts).total_seconds() >= 1800`. Readings must span at least 30 minutes of the hour to avoid unrepresentative bursts.
- Under-observed hours are excluded from the `hourly` historical dataset passed to `forecast_day_ahead`. If the forecaster detects insufficient continuous history (e.g., missing critical baseline days), it raises `ForecastUnavailable`.

### 2.3 Verified Device HTTPS (`firmware/src/telemetry_client.cpp` & `telemetry_client.h`)
- In `TelemetryClient`:
  - Add method `void setServerRootCA(const char* root_ca_pem);`
  - Store `const char* root_ca_cert_ = nullptr;`
- In `sendRestPayload`:
  - When `target_url` starts with `"https://"`:
    - Include `<WiFiClientSecure.h>`.
    - If `root_ca_cert_` is set:
      ```cpp
      WiFiClientSecure secure_client;
      secure_client.setCACert(root_ca_cert_);
      HTTPClient http;
      http.begin(secure_client, target_url);
      ```
    - If `root_ca_cert_` is NOT set:
      ```cpp
      #ifndef EMS_ALLOW_INSECURE_TLS
      Serial.println(F("[SECURITY] HTTPS rejected: No server root CA configured."));
      return DeliveryOutcome::TERMINAL_REJECTION;
      #else
      WiFiClientSecure secure_client;
      secure_client.setInsecure();
      HTTPClient http;
      http.begin(secure_client, target_url);
      #endif
      ```
- Verifiable via PlatformIO compilation (`pio run -d firmware`).

---

## 3. Testing & Verification

- Dedicated regression test suite: `tests/unit/test_phase1_telemetry_security_regressions.py`
  - Test rejection of `NaN`, `Inf`, and `"NaN"` strings in `PhaseReading` and `TelemetryPayload`.
  - Test rejection of payloads with `NaN` active power in FastAPI TestClient (`POST /api/v1/telemetry` -> 422).
  - Test `load_facility_forecast` filtering out hours with sample count < 6 or time span < 1800s.
  - Test PlatformIO build succeeds with secure HTTPS client support.
