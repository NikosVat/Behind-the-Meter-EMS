# Production Deployment & Operations Guide for Greek Commercial SMBs

Current prototype deployment controls (2026-10-01): set a nonempty `API_KEY` with `ENVIRONMENT=production`; use TLS and restricted access. Enter the key in the dashboard, set `EMS_API_KEY` in untracked firmware secrets, and supply `API_KEY` to simulator/E2E clients. A shared key does not establish multi-tenant authorization. Physical installation and hardware accuracy are not certified by software tests.

Install `.[dev,research,firmware]` for the documented complete verification workflow. The firmware extra supplies PlatformIO and a tested Zig C++ fallback for native tests; an installed C++ compiler may be used instead. Research scripts require cached BDG2 data.

Telemetry HTTP 409 is a terminal stale/duplicate rejection: firmware removes that queue entry with a diagnostic, without claiming delivery. Other authentication/network/server failures remain retries. Earlier faulty aggregates require a separately reviewed rebuild.

**System Target:** Greek Commercial Behind-the-Meter Energy Management System (EMS)  
**Target Enterprises:** Commercial Bakeries (Αρτοποιεία), Cold Storage Logistics (Ψυκτικοί Θάλαμοι), Boutique Hotels (Ξενοδοχεία)  
**Contract Schemes:** DEDDIE / ADMIE Commercial Low & Medium Voltage (Γ21, Γ22, Γ23)  
**Regulatory Framework:** Law 5068/2023 (Greek Retail Energy Market Reform & Green Tariff Mechanism)  

---

## 1. System Deployment Architecture

The Behind-the-Meter EMS consists of two primary operational tiers:
1. **Edge Metering Tier (On-Premises):** ESP32-WROOM-32 microcontroller deployed inside the facility's main Low-Voltage electrical distribution board (MCCB panel). It continuously samples 3-phase currents via SCT-013-000 CT clamps, computes real-time True RMS power metrics, and streams encrypted telemetry via Wi-Fi (HTTPS REST or MQTT).
2. **Central Application Tier (Local Gateway or Cloud VPS):** High-performance FastAPI backend backed by an embedded SQLite database in Write-Ahead Logging (WAL) mode. It enforces electrical invariants, computes instantaneous electricity costs against Greek tariff formulas, and dispatches actionable Greek notifications via Telegram before peak-demand penalties accumulate.

```
+-----------------------------------------------------------------------------------+
|                        ON-PREMISES COMMERCIAL FACILITY                            |
|                                                                                   |
|  +-----------------------------+           +-----------------------------------+  |
|  | Main Distribution Board     |           | ESP32 Edge Telemetry Node         |  |
|  | - 3x Phase Tails (L1,L2,L3) |           | - ADC1 Sampling Engine (50 Hz)    |  |
|  | - 3x SCT-013-000 CT Clamps  | ===3.5mm=> | - True RMS, kW, kVA, cos φ        |  |
|  | - 18Ω Precision Burdens     |           | - Reconnection & Ring-Buffer FSM  |  |
|  +-----------------------------+           +-----------------+-----------------+  |
+--------------------------------------------------------------|--------------------+
                                                               | Wi-Fi (WPA2/WPA3)
                                                               v HTTPS REST POST
+-----------------------------------------------------------------------------------+
|                        APPLICATION SERVER (LOCAL OR CLOUD)                        |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  | FastAPI Backend Service (Uvicorn / Systemd)                                 |  |
|  | - Ingestion API: POST /api/v1/telemetry                                     |  |
|  | - Physical Invariant Validator: |P_tot - ΣP_i| <= 0.05 kW                   |  |
|  | - Greek Tariff Engine: Γ21, Γ22, Γ23 (Green / Yellow / Dynamic)             |  |
|  | - Throttling & Hysteresis Dispatcher: 3-Sample Debounce, 30-min Cooldown    |  |
|  +-----------------------+----------------------------------+------------------+  |
|                          |                                  |                     |
|                          v                                  v                     |
|  +-----------------------------------+  +--------------------------------------+  |
|  | SQLite Time-Series Store (WAL)    |  | Telegram Bot Service                 |  |
|  | - Ingestion Logs & Daily Spend    |  | - Greek Alert Notifications          |  |
|  | - Facility Configuration Tables   |  | - Interactive Commands (/status)     |  |
|  +-----------------------------------+  +-------------------+------------------+  |
+-------------------------------------------------------------|---------------------+
                                                              | Telegram Bot API
                                                              v
                                              +-------------------------------+
                                              | Facility Owner / Manager      |
                                              | (Telegram Smartphone Client)  |
                                              +-------------------------------+
```

---

## 2. Hardware Bill of Materials (BOM)

For each 3-phase commercial metering point:

| Item | Component | Specification | Quantity | Purpose |
|---|---|---|---|---|
| **1** | ESP32 Development Board | ESP32-WROOM-32 (30-pin or 38-pin), 2.4GHz Wi-Fi | 1 | Edge sampling and telemetry transmission |
| **2** | Split-Core Current Transformers | YHDC SCT-013-000 ($100\text{ A} / 50\text{ mA}$, 2000:1) | 3 | Non-invasive current sensing on phases L1, L2, L3 |
| **3** | Precision Burden Resistors | $18.0\,\Omega$, 1% metal film, 1/4W | 3 | Current-to-voltage conversion ($K_I = 111.111\text{ A/V}$) |
| **4** | Voltage Divider Resistors | $10.0\text{ k}\Omega$ (or $100\text{ k}\Omega$), 1% metal film | 2 | DC virtual midpoint biasing ($1.65\text{ V}$) |
| **5** | Decoupling Capacitors | $10\,\mu\text{F}$ 16V Low-ESR Electrolytic + $0.1\,\mu\text{F}$ Ceramic | 1 ea | AC ground decoupling on 1.65V bias line |
| **6** | CT Audio Connectors | 3.5mm stereo female panel-mount jacks | 3 | Secure connection for SCT-013 audio plugs |
| **7** | Power Supply | 5V DC / 2A DIN-rail power supply (e.g. Mean Well HDR-15-5) | 1 | Continuous power from distribution board |
| **8** | Industrial Enclosure | DIN-rail mounting enclosure (ABS/Polycarbonate, UL94V-0) | 1 | Protection against dust and accidental contact |

---

## 3. ESP32 Firmware Setup & PlatformIO Flashing

The firmware is located in `firmware/` and built using PlatformIO.

### 3.1 Hardware Pin Verification
Inspect `firmware/src/ct_sampler.h` to confirm pin assignments:
```cpp
constexpr uint8_t PIN_CT_PHASE_L1 = 34; // ADC1_CH6 (Input-only)
constexpr uint8_t PIN_CT_PHASE_L2 = 35; // ADC1_CH7 (Input-only)
constexpr uint8_t PIN_CT_PHASE_L3 = 32; // ADC1_CH4 (Input)
```
*(Notice: Only ADC1 channels are utilized; ADC2 is completely avoided).*

### 3.2 Wi-Fi & Backend Endpoint Configuration
Open `firmware/src/telemetry_client.h` or create a local `firmware/include/credentials.h`:
```cpp
#define WIFI_SSID         "Commercial_Bakery_WiFi"
#define WIFI_PASSWORD     "UltraSecurePassphrase2026"
#define BACKEND_POST_URL  "https://ems.yourdomain.gr/api/v1/telemetry"
#define FACILITY_ID       "bakery-central-athens"
#define DEVICE_ID         "esp32-bakery-001"
#define SAMPLING_INTERVAL 10000 // 10 seconds
```

### 3.3 Compile and Upload
Ensure the ESP32 is connected via USB:
```bash
# Navigate to firmware directory
cd firmware

# Compile PlatformIO project
pio run -e esp32dev

# Upload firmware binary to ESP32
pio run -e esp32dev -t upload

# Open serial monitor to verify boot & Wi-Fi association
pio device monitor -b 115200
```

### 3.4 Verification of Serial Monitor Output
```
[BOOT] Greek Commercial EMS ESP32 Firmware v0.1.0
[ADC] Initializing ADC1 (GPIO 34, 35, 32) at 12-bit resolution, 11dB attenuation...
[ADC] SCT-013-000 calibration factor: 111.111 A/V (18 Ohm burden)
[WIFI] Connecting to Commercial_Bakery_WiFi...
[WIFI] Connected! IP: 192.168.1.145, RSSI: -58 dBm
[TELEMETRY] L1: 230.1V 22.4A 5.15kW | L2: 229.8V 21.8A 5.01kW | L3: 231.2V 22.1A 5.11kW
[TELEMETRY] Total: 15.27 kW, cos φ: 0.98 -> HTTP 200 OK
```

---

## 4. FastAPI Backend Deployment

### 4.1 Prerequisites
- Python 3.11 or 3.12
- SQLite 3.37+ (compiled with WAL support)
- Git & Uvicorn

### 4.2 Installation & Virtual Environment
```bash
# Clone repository and navigate to root
cd /opt/greek-commercial-ems

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

### 4.3 Production Environment Configuration (`.env`)
Create `/opt/greek-commercial-ems/.env`:
```ini
# Application Mode
ENVIRONMENT=production
DEBUG=false
PROJECT_NAME="Greek Commercial Behind-the-Meter EMS"
VERSION=1.0.0

# Database Storage (WAL Mode enabled automatically)
SQLITE_DB_PATH=/var/lib/ems/ems_timeseries.db

# Telegram Alert Bot Configuration
TELEGRAM_BOT_TOKEN=7123456789:AAFlkjw98234-example-token-for-bot
TELEGRAM_DEFAULT_CHAT_ID=999111222

# Alert Throttling & Debounce Parameters
ALERT_COOLDOWN_SECONDS=1800
ALERT_HYSTERESIS_FACTOR=0.90
ALERT_DEBOUNCE_SAMPLES=3

# Greek Electricity Market Settings
TIMEZONE=Europe/Athens
DEFAULT_CONTRACT_TYPE=Γ22
DEFAULT_TARIFF_COLOR=green
```

Ensure storage directory exists with proper permissions:
```bash
sudo mkdir -p /var/lib/ems
sudo chown -R www-data:www-data /var/lib/ems
```

### 4.4 Systemd Service Unit (`ems-backend.service`)
Create `/etc/systemd/system/ems-backend.service`:
```ini
[Unit]
Description=Greek Commercial Behind-the-Meter EMS FastAPI Backend Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/greek-commercial-ems
EnvironmentFile=/opt/greek-commercial-ems/.env
ExecStart=/opt/greek-commercial-ems/.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=5s

# Hardening
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable ems-backend
sudo systemctl start ems-backend
sudo systemctl status ems-backend
```

---

## 5. Telegram Bot Setup & Command Registration

### 5.1 Bot Registration via @BotFather
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot`.
3. Provide a display name (e.g., `Bakery Energy Manager`).
4. Choose a unique username ending in `bot` (e.g., `AthensBakeryEmsBot`).
5. Copy the generated HTTP API Token into your `.env` file under `TELEGRAM_BOT_TOKEN`.

### 5.2 Retrieve Facility Chat ID via @userinfobot
1. Search for `@userinfobot` on Telegram.
2. Send `/start` to retrieve your numerical `Id` (e.g. `999111222`).
3. Set this ID as `TELEGRAM_DEFAULT_CHAT_ID` or assign it to the facility profile in SQLite.

### 5.3 Register Greek Command Menu in @BotFather
Send `/setcommands` to `@BotFather`, select your bot, and paste the official command list:
```text
status - Τρέχουσα ισχύς 3 φάσεων, τάσεις, cos φ και κόστος ανά ώρα
cost_today - Σημερινή κατανάλωση (kWh) και συνολικό κόστος (€)
tariff - Σύμβαση, ωράριο αιχμής DEDDIE και τρέχον τιμολόγιο
settings - Όρια ισχύος, χρόνος cooldown και παράμετροι προστασίας
help - Αναλυτικός οδηγός χρήσης συστήματος
start - Επισκόπηση συστήματος και καλωσόρισμα
```

---

## 6. Commercial Facility Profiles & DEDDIE Schedules

The EMS supports pre-configured profiles modeling Greek commercial enterprises:

| Parameter | Commercial Bakery | Cold Storage Logistics | Boutique Hotel |
|---|---|---|---|
| **Default Facility ID** | `bakery-central-athens` | `cold-storage-piraeus` | `hotel-plaka-boutique` |
| **Contract Scheme** | Γ22 (Dual-Rate LV) | Γ22 (Dual-Rate LV) | Γ23 (Medium Voltage) |
| **Tariff Color** | Green (Ειδικό Τιμολόγιο) | Yellow (Κυμαινόμενο) | Dynamic (Spot DAM) |
| **Contracted Capacity** | $35\text{ kVA}$ ($50.6\text{ A/phase}$) | $50\text{ kVA}$ ($72.4\text{ A/phase}$) | $100\text{ kVA}$ ($144.9\text{ A/phase}$) |
| **Peak Alert Threshold** | **$22.0\text{ kW}$** | **$30.0\text{ kW}$** | **$25.0\text{ kW}$** |
| **90% Release Hysteresis**| **$19.8\text{ kW}$** | **$27.0\text{ kW}$** | **$22.5\text{ kW}$** |
| **Cooldown Period** | 30 minutes ($1800\text{ s}$) | 30 minutes ($1800\text{ s}$) | 30 minutes ($1800\text{ s}$) |
| **Debounce Filter** | 3 consecutive samples | 3 consecutive samples | 3 consecutive samples |
| **Curtailed Equipment** | Deck ovens & proofers | Loading dock doors & defrost | VRV chiller setpoints & laundry |

### DEDDIE Peak Window Schedules (Zone 1)
- **Summer Schedule (May 1 – October 31):**  
  `14:00 – 17:00` (Monday through Friday).  
  *Weekends and official Greek holidays are exempt.*
- **Winter Schedule (November 1 – April 30):**  
  `17:00 – 21:00` (Monday through Friday).  
  *Weekends and official Greek holidays are exempt.*

---

## 7. Offline Pre-Commissioning with Telemetry Simulator

Before deploying physical ESP32 hardware to the customer site, verify the entire communication and alert pipeline using the included CLI simulator:

### 7.1 Dry-Run Verification (Test Signal Generation)
```bash
python -m simulator.cli --profile bakery --speed 3600x --duration-hours 1 --dry-run --max-readings 5
```

### 7.2 Stream Real-Time Telemetry to Local Backend
```bash
python -m simulator.cli --profile bakery --speed 60x --duration-hours 24 --url http://localhost:8000/api/v1/telemetry
```

### 7.3 Trigger High-Load Peak Breach Alert
Force instantaneous breach load ($32.5\text{ kW} > 22.0\text{ kW}$) to verify Telegram alert delivery:
```bash
python -m simulator.cli --profile bakery --trigger-breach --url http://localhost:8000/api/v1/telemetry
```

---

## 8. Automated Verification Runbook

Run the dedicated standalone verification script:
```bash
python scripts/run_e2e_verification.py
```

Expected Output:
```
================================================================================
Greek Commercial Behind-the-Meter EMS: Standalone E2E Verification Runner
Mode: In-Process TestClient
Target Commercial Profile: BAKERY
================================================================================
[1/9] [PASS] | System Health & Facility Seeding               |   16.4 ms | 3 default facilities verified
[2/9] [PASS] | Normal Baseline Ingestion (< Threshold)        |    9.5 ms | P=14.5 kW, Cost=3.62 €/h
[3/9] [PASS] | 3-Sample Debounce Breach Filtering             |   29.0 ms | Filter verified: s1=False, s2=False, s3=TRIGGERED
[4/9] [PASS] | Tariff Cost & Peak Penalty Calculation         |    0.0 ms | Excess=10.5 kW, Penalty=1.68 €
[5/9] [PASS] | Greek Telegram Notification Formatting         |    0.0 ms | Greek header, window, € penalty & advice verified
[6/9] [PASS] | Alert Throttling & Cooldown Suppression        |    9.9 ms | Duplicate breach suppressed during 1800s cooldown
[7/9] [PASS] | 10% Release Hysteresis Recovery                |    9.9 ms | Load dropped <= 19.8 kW -> Recovery alert dispatched
[8/9] [PASS] | Facility Status & Cost Reporting APIs          |   23.6 ms | Total kWh=0.40, Spend=0.10 €
[9/9] [PASS] | Execution Benchmark (< 30.0s)                  |  603.9 ms | Total: 0.60s
================================================================================
ALL VERIFICATION CRITERIA MET (Exit Code 0)
```

---

## 9. Operations Runbook

### 9.1 SQLite WAL Maintenance & Vacuuming
The SQLite database operates in Write-Ahead Logging mode (`PRAGMA journal_mode=WAL;`). To optimize checkpointing and purge historical raw telemetry older than 90 days:
```bash
# Run maintenance script or invoke sqlite3 CLI
sqlite3 /var/lib/ems/ems_timeseries.db "PRAGMA wal_checkpoint(TRUNCATE);"
sqlite3 /var/lib/ems/ems_timeseries.db "DELETE FROM telemetry WHERE timestamp < datetime('now', '-90 days');"
sqlite3 /var/lib/ems/ems_timeseries.db "VACUUM;"
```

### 9.2 Monthly Green Tariff Updates (Law 5068/2023)
On the 1st of every calendar month, Greek suppliers announce the base tariff components for Green contracts:
- Update the base supply charge ($B$), bounds ($L_l, L_u$), and amplification factors ($\alpha, \beta$) in the facility config:
```bash
curl -X POST http://localhost:8000/api/v1/facilities/bakery-central-athens/tariffs \
  -H "Content-Type: application/json" \
  -d '{
    "base_rate_eur_per_kwh": 0.145,
    "tea_reference_eur_per_mwh": 115.40,
    "lu_upper_bound_eur_per_mwh": 120.0,
    "ll_lower_bound_eur_per_mwh": 95.0,
    "alpha_coefficient": 1.25,
    "beta_offset": 0.00
  }'
```

### 9.3 Troubleshooting Common Issues

| Symptom | Cause | Solution |
|---|---|---|
| **Negative Active Power ($P < 0$)** | CT clamp installed backward | Unclamp and reverse CT orientation; verify arrow points toward load. |
| **Readings Read 0.0 kW despite heavy load** | Clamped across multi-core cable | Unclamp; clamp around individual insulated phase conductors (L1 alone, L2 alone, L3 alone). |
| **Wi-Fi Disconnects when sampling starts** | ESP32 pin configured on ADC2 | Move CT wires to ADC1 pins (GPIO 34, 35, 32). Never use ADC2 with Wi-Fi. |
| **Repeated Telegram Breach Alerts** | Debounce filter or cooldown bypassed | Check facility config: verify `debounce_samples >= 3` and `cooldown_seconds >= 1800`. |
| **Telegram Notifications Not Delivered** | Invalid Bot Token or Chat ID | Verify token via `curl https://api.telegram.org/bot<token>/getMe` and chat ID with `@userinfobot`. |
