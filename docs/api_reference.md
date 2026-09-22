# Behind-the-Meter EMS — REST API Reference

The Behind-the-Meter Energy Management System (EMS) exposes a high-performance RESTful API powered by **FastAPI**. It handles real-time 3-phase telemetry ingestion, dynamic tariff calculations, facility status monitoring, and historical spend queries.

Interactive OpenAPI documentation (Swagger UI) is available at:
`http://localhost:8000/docs` or `http://localhost:8000/redoc`

---

## Base URL
```
http://localhost:8000/api/v1
```

---

## Authentication & Headers
- **Content-Type:** `application/json`
- **Accept:** `application/json`
- *(Optional Production Mode)* `X-API-Key: <facility_secret_key>`

---

## 1. Telemetry Ingestion

### `POST /api/v1/telemetry`
Ingests real-time 3-phase electrical readings from ESP32 microcontrollers or the commercial load simulator. Validates electrical invariants ($|P_{tot} - \sum P_i| \le 0.05\text{ kW}$) and triggers immediate peak-hour breach evaluation.

#### Request Body
```json
{
  "device_id": "esp32-ems-001",
  "facility_id": "bakery-central-athens",
  "timestamp": "2026-09-14T15:30:00Z",
  "phases": {
    "L1": {
      "voltage_v": 230.2,
      "current_a": 26.4,
      "active_power_kw": 5.95,
      "apparent_power_kva": 6.08,
      "power_factor": 0.98
    },
    "L2": {
      "voltage_v": 229.8,
      "current_a": 25.8,
      "active_power_kw": 5.82,
      "apparent_power_kva": 5.93,
      "power_factor": 0.98
    },
    "L3": {
      "voltage_v": 231.0,
      "current_a": 27.1,
      "active_power_kw": 6.13,
      "apparent_power_kva": 6.26,
      "power_factor": 0.98
    }
  },
  "total_active_power_kw": 17.90,
  "total_apparent_power_kva": 18.27,
  "system_power_factor": 0.98,
  "cumulative_energy_kwh": 142.50,
  "grid_frequency_hz": 50.01,
  "wifi_rssi_dbm": -62
}
```

#### Field Specifications
| Field | Type | Required | Description |
|---|---|---|---|
| `device_id` | string | Yes | Unique hardware identifier for the ESP32 meter. |
| `facility_id` | string | Yes | Foreign key to registered facility (e.g. `bakery-central-athens`). |
| `timestamp` | ISO-8601 string | Yes | UTC timestamp of sample collection. |
| `phases` | object | Yes | Keyed map for `L1`, `L2`, `L3` phase readings. |
| `phases.<Lx>.voltage_v` | float | Yes | True RMS Phase-to-Neutral voltage ($180.0 - 260.0\text{ V}$). |
| `phases.<Lx>.current_a` | float | Yes | True RMS AC current ($0.0 - 100.0\text{ A}$). |
| `phases.<Lx>.active_power_kw` | float | Yes | Real active power per phase in kW. |
| `phases.<Lx>.apparent_power_kva` | float | Yes | Apparent power per phase ($V_{rms} \times I_{rms} / 1000$). |
| `phases.<Lx>.power_factor` | float | Yes | Displacement power factor ($\cos\varphi \in [-1.0, 1.0]$). |
| `total_active_power_kw` | float | Yes | Arithmetic sum of active power across all 3 phases. |
| `total_apparent_power_kva` | float | Yes | Total apparent power in kVA. |
| `system_power_factor` | float | Yes | Overall weighted system power factor. |
| `cumulative_energy_kwh` | float | Yes | Monotonically increasing energy register. |
| `grid_frequency_hz` | float | No | Mains AC frequency (nominal $50.0\text{ Hz}$). |
| `wifi_rssi_dbm` | integer | No | ESP32 Wi-Fi Received Signal Strength Indicator (in dBm). |

#### Response (`200 OK`)
```json
{
  "status": "success",
  "facility_id": "bakery-central-athens",
  "recorded_at": "2026-09-14T15:30:00Z",
  "cost_metrics": {
    "current_rate_eur_per_kwh": 0.2450,
    "running_cost_eur_per_h": 4.3855,
    "is_peak_window": true,
    "is_excess_breach": false,
    "excess_power_kw": 0.0,
    "projected_excess_penalty_eur": 0.0
  }
}
```

#### Error Response (`422 Unprocessable Entity`)
Occurs if the electrical invariant check fails ($|P_{tot} - \sum P_i| > 0.05\text{ kW}$):
```json
{
  "detail": "Electrical invariant violation: total_active_power_kw (17.90) deviates from sum of phases (18.50) by > 0.05 kW"
}
```

---

## 2. Facilities & Real-Time Monitoring

### `GET /api/v1/facilities`
Lists all registered commercial facilities, their contracted kVA, and alert configurations.

#### Response (`200 OK`)
```json
[
  {
    "facility_id": "bakery-central-athens",
    "name": "Bakery Central Athens",
    "business_type": "bakery",
    "contract_type": "Γ22",
    "tariff_color": "green",
    "contracted_kva": 35.0,
    "peak_threshold_kw": 22.0,
    "telegram_chat_id": 999111222,
    "created_at": "2026-09-01T00:00:00Z"
  },
  {
    "facility_id": "cold-storage-piraeus",
    "name": "Cold Storage Piraeus",
    "business_type": "cold_storage",
    "contract_type": "Γ22",
    "tariff_color": "yellow",
    "contracted_kva": 50.0,
    "peak_threshold_kw": 30.0,
    "telegram_chat_id": 999222333,
    "created_at": "2026-09-01T00:00:00Z"
  }
]
```

---

### `GET /api/v1/facilities/{facility_id}/status`
Returns the instantaneous telemetry, current active kW, running cost in €/h, and active tariff window for a given facility.

#### Query Parameters
- None.

#### Response (`200 OK`)
```json
{
  "facility_id": "bakery-central-athens",
  "name": "Bakery Central Athens",
  "timestamp": "2026-09-14T15:30:00Z",
  "total_active_power_kw": 17.90,
  "total_apparent_power_kva": 18.27,
  "system_power_factor": 0.98,
  "running_cost_eur_per_h": 4.39,
  "is_peak_window": true,
  "peak_threshold_kw": 22.0,
  "breach_state": "NORMAL",
  "last_telemetry_age_seconds": 4
}
```

---

### `GET /api/v1/facilities/{facility_id}/cost-today`
Calculates cumulative electricity expenditure, consumed kilowatt-hours, and projected peak surcharges for the specified date (defaults to today in Europe/Athens).

#### Query Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `date` | `YYYY-MM-DD` | No | Current Date | Date for which to aggregate energy spend. |

#### Response (`200 OK`)
```json
{
  "facility_id": "bakery-central-athens",
  "date": "2026-09-14",
  "cumulative_kwh": 240.50,
  "total_spend_eur": 48.60,
  "peak_hours_spend_eur": 21.40,
  "off_peak_spend_eur": 27.20,
  "average_effective_rate_eur_per_kwh": 0.202,
  "excess_demand_penalty_eur": 0.0,
  "power_factor_penalty_eur": 0.0
}
```

---

### `GET /api/v1/facilities/{facility_id}/tariff`
Returns detailed tariff contract specifications, current pricing components, and DEDDIE time-of-use schedule for the facility.

#### Response (`200 OK`)
```json
{
  "facility_id": "bakery-central-athens",
  "contract_type": "Γ22",
  "tariff_color": "green",
  "contracted_kva": 35.0,
  "active_season": "summer",
  "peak_hours_schedule": "14:00 - 17:00 (Mon-Fri)",
  "base_rate_eur_per_kwh": 0.1450,
  "current_tea_m_minus_1": 118.50,
  "effective_supply_rate_eur_per_kwh": 0.1820,
  "regulated_charges_eur_per_kwh": 0.0630,
  "vat_rate_percent": 6.0
}
```

---

## 3. Energy Market & Dynamic Tariffs

### `GET /api/v1/market/dam-prices`
Query 24-hour Day-Ahead Market (DAM) hourly clearing prices from the Hellenic Energy Exchange (HEnEx) with 4-tier caching (RAM, SQLite, Seed, Algorithmic).

- **Query Parameters:** `date` (YYYY-MM-DD, defaults to current date)
- **Response:** JSON list of 24 hourly prices (€/MWh and €/kWh), summary stats (min, max, average), and cache tier source.

### `GET /api/v1/market/green-tariffs`
Query official RAE green tariff announcements under Law 5068/2023.

- **Query Parameters:** `month` (YYYY-MM, defaults to current month)
- **Response:** JSON list of supplier tariffs with base charge, fluctuation formula parameters ($\alpha, L_u, L_l, \beta$), and final calculated €/kWh rate.

### `GET /api/v1/market/effective-rate`
Calculate effective retail rate combining market feeds and supplier margins.

- **Query Parameters:** `contract_type` (Γ21, Γ22, Γ23), `tariff_color` (green, yellow, dynamic), `supplier_id` (dei, protergia, heron, etc.), `timestamp` (optional ISO 8601).

---

## 4. Multi-Channel Alerting & Viber Bot

### `GET /api/v1/viber/status`
Returns Viber bot operational status and client mode (`mock` or `live`).

### `POST /api/v1/viber/send`
Directly dispatches a plain-text notification to a Viber recipient.

```json
{
  "receiver_id": "viber_user_123",
  "text": "Προσοχή: Υπέρβαση ορίου 24.5 kW",
  "sender_name": "EMS Alert Bot"
}
```

### `POST /api/v1/viber/webhook`
Viber callback webhook endpoint handling setup pings (`event: webhook`), message commands (`/status`, `/cost_today`, `/tariff`, `/settings`, `/help`), and HMAC-SHA256 signature verification (`X-Viber-Content-Signature`).

---

## 5. Web Dashboard & Management

### `GET /dashboard`
Renders the self-contained Greek Commercial EMS Single-Page Web Dashboard served with Tailwind CSS and Chart.js.

### `GET /api/v1/dashboard/metrics/{facility_id}`
Returns aggregated live KPIs, 3-phase voltages and currents, active DEDDIE tariff status, running cost (€/h), today's spend (€), and 24-hour historical timeline points.

### `POST /api/v1/dashboard/config/{facility_id}`
Updates peak threshold (kW) and alert notification preferences (`telegram`, `viber`, or `both`) directly from the UI.

```json
{
  "peak_threshold_kw": 25.0,
  "notification_channel": "both",
  "chat_id": 999111222,
  "viber_receiver_id": "vb_usr_bakery_123"
}
```

---

## 6. Health & Diagnostics

### `GET /health`
Liveness and readiness healthcheck probe.

#### Response (`200 OK`)
```json
{
  "status": "healthy",
  "version": "0.1.0",
  "database": "connected",
  "active_facilities": 3,
  "timestamp": "2026-09-16T10:00:00Z"
}
```

---

## 7. cURL Examples

### Ingest Telemetry Reading
```bash
curl -X POST http://localhost:8000/api/v1/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "esp32-001",
    "facility_id": "bakery-central-athens",
    "timestamp": "2026-09-16T15:00:00Z",
    "phases": {
      "L1": {"voltage_v": 230.0, "current_a": 20.0, "active_power_kw": 4.6, "apparent_power_kva": 4.6, "power_factor": 1.0},
      "L2": {"voltage_v": 230.0, "current_a": 20.0, "active_power_kw": 4.6, "apparent_power_kva": 4.6, "power_factor": 1.0},
      "L3": {"voltage_v": 230.0, "current_a": 20.0, "active_power_kw": 4.6, "apparent_power_kva": 4.6, "power_factor": 1.0}
    },
    "total_active_power_kw": 13.8,
    "total_apparent_power_kva": 13.8,
    "system_power_factor": 1.0,
    "cumulative_energy_kwh": 100.5
  }'
```

### Access Web Dashboard
Open in your browser: `http://localhost:8000/dashboard`

---

## 8. Constrained Optimization & Closed-Loop Verification

### `GET /api/v1/optimization/status`
Returns optimization engine health, solver capabilities, and active recommendation counts.

#### Response `200 OK`
```json
{
  "status": "operational",
  "solver_backend": "scipy_highs_milp",
  "paradigm": "Measure -> Predict -> Optimize -> Act -> Verify",
  "supported_constraints": [
    "flexible_refrigeration_defrost",
    "production_batch_deck_ovens",
    "hvac_thermal_comfort_deadbands",
    "bess_soc_and_power_limits",
    "contracted_capacity_surcharge_avoidance"
  ],
  "active_recommendations_count": 2,
  "verified_interventions_count": 1
}
```

### `POST /api/v1/optimization/solve`
Solves a 24-step hourly MILP schedule using SciPy HiGHS. Missing load and tariff arrays use demo profiles, identified in `input_source`; these are not trained forecasts or live market prices.

`is_optimal` describes mathematical optimality of the relaxed problem. Consumers must also check `operationally_feasible`. A comfort excursion returns `status: "COMFORT_VIOLATION"`, the largest excursion in `comfort_violation_c`, and no recommendations. Mandatory cycles that cannot finish inside the horizon return `FAILED`. Recommendation confidence is `null` until calibrated from outcomes.

#### Request Body
```json
{
  "facility_id": "fac_bakery_01",
  "contracted_capacity_kw": 35.0,
  "include_defrost": true,
  "include_batch_ovens": true,
  "include_hvac": true,
  "include_bess": true
}
```

#### Response `200 OK`
```json
{
  "status": "OPTIMAL",
  "is_optimal": true,
  "horizon_hours": 24,
  "baseline_cost_eur": 184.50,
  "optimized_cost_eur": 156.30,
  "savings_eur": 28.20,
  "savings_pct": 15.28,
  "peak_baseline_kw": 38.2,
  "peak_optimized_kw": 31.4,
  "peak_reduction_kw": 6.8,
  "solve_time_ms": 18.5,
  "recommendations_count": 2,
  "recommendations": [
    {
      "recommendation_id": "rec_defrost_a1b2c3d4",
      "facility_id": "fac_bakery_01",
      "category": "defrost_shift",
      "priority": "high",
      "title": "Shift Defrost: Refrigeration Defrost Rack A",
      "description_el": "Μετατόπιση απόψυξης στις 16:00-17:00 (αντί 14:00-15:00). Αποφυγή αιχμής 6.8 kW. Όφελος: €14.20.",
      "peak_load_avoided_kw": 6.8,
      "estimated_savings_eur": 14.20,
      "confidence_score": null
    }
  ]
}
```

### `POST /api/v1/optimization/verify`
Compares recorded interval energy with a caller-supplied counterfactual. This is an interval estimate, not certified whole-intervention or utility-bill savings.

#### Request Body
```json
{
  "recommendation_id": "rec_defrost_a1b2c3d4",
  "start_reading_id": 101,
  "end_reading_id": 116,
  "counterfactual_baseline_kw": 31.3,
  "tariff_eur_kwh": 0.245
}
```
The IDs must identify chronological, timezone-aware readings from the recommendation's facility and one device. Missing endpoints, reversed times and counter resets are rejected. `actual_measured_kw`, `duration_hours` and `capacity_penalty_rate` are no longer request fields. Actual average power is the cumulative kWh difference divided by elapsed hours. The baseline is assumed constant over this interval; the supplied tariff must be appropriate for it.

For a 15-minute interval with average recorded power 24.5 kW, this example reports 6.8 kW avoided and EUR 0.42 of estimated energy savings: `(31.3 - 24.5) * 0.25 * 0.245`. Increased cost is retained as a negative value. Demand charges and later rebound from shifted loads are excluded.

The response includes `duration_hours`, `start_reading_id`, `end_reading_id`, `evidence_source: "stored_telemetry"`, `baseline_source: "caller_supplied_counterfactual"`, `power_measurement_method`, and `is_certified: false`. Success/partial/failure compares interval load reduction with the recommended kW target; it does not establish causal savings. The legacy `accuracy_pct` field is `null`: interval energy cannot be compared directly with a whole-recommendation estimate that may include demand charges.

### Telemetry measurement provenance
`power_measurement_method` is returned by ingestion, stored with each reading, and exposed in facility status/history and dashboard metrics:

- `estimated_nominal_voltage_pf`: current-only firmware uses configured voltage and power factor; active power and integrated energy are estimates.
- `meter_measured`: the source reports measured power; this label is not independent calibration evidence.
- `simulated`: generated by the simulator.
- `unknown`: legacy readings or sources that provide no provenance.

Existing SQLite databases gain this column at startup; old rows remain `unknown`.
