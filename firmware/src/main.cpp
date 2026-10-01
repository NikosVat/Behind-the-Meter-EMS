/**
 * @file main.cpp
 * @brief Greek Commercial Behind-the-Meter EMS - ESP32 3-Phase Firmware.
 *
 * METROLOGY & SENSORS:
 * - Dual-Mode Metrology Engine (Mode A: CT-Only baseline estimation, Mode B: True RMS synchronized)
 * - 3x YHDC SCT-013-000 (100A/50mA, 2000:1) Split-Core CT Clamps
 * - 18 Ohm 1% precision burden resistors (linear range up to 114 A RMS)
 * - 1.65V DC midpoint bias virtual ground
 * - Dedicated ADC1 pins: L1 -> GPIO 34, L2 -> GPIO 35, L3 -> GPIO 32
 *
 * TIMEKEEPING RESILIENCE:
 * - DS3231 I2C RTC + NVS Flash timestamp persistence
 * - Zero-Epoch (1970) immunization guaranteeing valid tinyML 7x24 weekly matrix indexing
 *
 * TELEMETRY:
 * - HTTP REST client posting JSON payloads to /api/v1/telemetry
 * - In-memory store-and-forward ring buffer resilient against network outages
 * - Non-blocking exponential backoff Wi-Fi reconnection manager
 */

#include <Arduino.h>
#include <time.h>
#include <esp_sntp.h>
#include "ct_sampler.h"
#include "power_calc.h"
#include "telemetry_client.h"
#include "edge_forecast.h"
#include "rtc_timekeeper.h"
#include "ntp_sync_handoff.h"
#include "hourly_power_accumulator.h"

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef WIFI_SSID
#define WIFI_SSID "EMS_Commercial_WLAN"
#endif

#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD ""
#endif

#ifndef EMS_API_BASE_URL
#define EMS_API_BASE_URL "https://ems.local:8000"
#endif

#ifndef EMS_MQTT_BROKER
#define EMS_MQTT_BROKER "ems.local"
#endif

// Configuration Constants
static constexpr uint32_t SERIAL_BAUD_RATE = 115200;
static constexpr uint32_t TELEMETRY_INTERVAL_MS = 5000; // 5-second cadence
static constexpr uint16_t SAMPLING_CYCLES = 10;          // 10 cycles @ 50Hz = 200ms per phase
static constexpr uint16_t SAMPLES_PER_CYCLE = 50;        // 50 samples / cycle = 2.5 kHz
static constexpr float CONTRACTED_CAPACITY_KW = 35.0f;  // Commercial connection limit (e.g. Gamma-22)

// Subsystem instances
static ems::CTSampler ct_sampler;
static ems::PowerCalculator power_calc;
static ems::TelemetryClient telemetry_client;
static EdgeForecaster edge_forecaster;
static ems::RtcTimekeeper rtc_timekeeper;

static uint32_t last_telemetry_time = 0;
static ems::NtpSyncHandoff ntp_sync;
static ems::HourlyPowerAccumulator hourly_power;

static void onNtpSync(struct timeval* synced_time) {
    if (synced_time) ntp_sync.notify(static_cast<uint32_t>(synced_time->tv_sec));
}

void setup() {
    Serial.begin(SERIAL_BAUD_RATE);
    delay(1000);

    Serial.println();
    Serial.println(F("================================================================="));
    Serial.println(F(" Greek Behind-the-Meter EMS - ESP32 3-Phase Telemetry Firmware   "));
    Serial.println(F(" Version: 2.0.0 (Dual-Mode Metrology & Resilient RTC / NVS)     "));
    Serial.println(F("================================================================="));

    // 1. Initialize Hardware RTC & Offline Timekeeping
    Serial.println(F("[SETUP] Initializing DS3231 Hardware RTC & NVS Flash Persistence..."));
    bool rtc_found = rtc_timekeeper.begin(21, 22);
    Serial.printf("[SETUP] Timekeeper online. Hardware DS3231: %s | Source: %u\r\n",
                  rtc_found ? "DETECTED" : "OFFLINE (Using NVS/Build Floor)",
                  static_cast<unsigned int>(rtc_timekeeper.getSyncSource()));

    // 2. Initialize Analog Current Transformer Sampler
    Serial.println(F("[SETUP] Initializing SCT-013 CT Sampler on ADC1..."));
    ct_sampler.configurePhase(ems::PhaseId::L1, ems::CTSensorModel::SCT_013_000, 18.0f, 0.05f);
    ct_sampler.configurePhase(ems::PhaseId::L2, ems::CTSensorModel::SCT_013_000, 18.0f, 0.05f);
    ct_sampler.configurePhase(ems::PhaseId::L3, ems::CTSensorModel::SCT_013_000, 18.0f, 0.05f);
    ct_sampler.begin();
    Serial.println(F("[SETUP] CT Sampler initialized (L1: GPIO 34, L2: GPIO 35, L3: GPIO 32)."));

    // 3. Initialize Electrical Power Calculator (Dual-Mode Engine)
    Serial.println(F("[SETUP] Initializing Dual-Mode 3-Phase Metrology Engine..."));
    power_calc.setMetrologyMode(ems::MetrologyMode::MODE_A_CT_ONLY);
    power_calc.setNominalVoltage(230.0f, 230.0f, 230.0f); // Nominal Greek Phase-to-Neutral
    power_calc.setPhasePowerFactors(0.95f, 0.95f, 0.95f);  // Commercial baseline PF
    power_calc.setGridFrequency(50.0f);
    Serial.println(F("[SETUP] Dual-Mode Metrology ready (Mode A: CT-Only, explicit provenance)."));

    // 4. Initialize Telemetry Client
    Serial.println(F("[SETUP] Initializing Telemetry Client (Wi-Fi + REST/MQTT)..."));
    telemetry_client.configureWiFi(WIFI_SSID, WIFI_PASSWORD);
    telemetry_client.configureRest(EMS_API_BASE_URL, "/api/v1/telemetry");
    telemetry_client.configureMqtt(EMS_MQTT_BROKER, 8883, "ems/telemetry");
    telemetry_client.setDeviceIdentity("esp32-ems-001", "bakery-central-athens");
    telemetry_client.setMode(ems::TelemetryMode::REST_ONLY);
    sntp_set_time_sync_notification_cb(onNtpSync);
    telemetry_client.begin();
    Serial.println(F("[SETUP] Telemetry Client initialized."));

    // 5. Initialize On-Device tinyML Edge Forecaster
    Serial.println(F("[SETUP] Initializing tinyML Edge Forecaster (Weekly Profile + Adaptive Filter)..."));
    edge_forecaster.begin();
    Serial.println(F("[SETUP] tinyML Edge Forecaster active. Starting sampling loop..."));
    Serial.println(F("-----------------------------------------------------------------"));
}

void loop() {
    uint32_t now = millis();

    // Run non-blocking telemetry network loop (Wi-Fi reconnect + queue flush)
    telemetry_client.loop(now);
    const uint32_t epoch_before_sync = rtc_timekeeper.getCurrentEpoch(millis());
    const bool trusted_before_sync = rtc_timekeeper.isReliable();
    if (ntp_sync.apply(rtc_timekeeper)) {
        const int64_t correction = static_cast<int64_t>(rtc_timekeeper.getCurrentEpoch(millis())) - epoch_before_sync;
        if (!trusted_before_sync || correction > 5 || correction < -5) {
            // A clock correction must not join observations from unrelated calendar days.
            hourly_power.reset();
            edge_forecaster.resetObservationHistory();
        }
    }
    // Refresh after network work / NTP rebasing; an older millis value looks like wraparound.
    now = millis();

    // Periodic 3-phase sampling and dispatch
    if (now - last_telemetry_time >= TELEMETRY_INTERVAL_MS) {
        last_telemetry_time = now;

        // 1. Sample all 3 phases synchronously over exact integer cycles
        ems::ThreePhaseMeasurement samples = ct_sampler.sampleAllPhases(SAMPLING_CYCLES, SAMPLES_PER_CYCLE);

        // 2. Compute Power and Trapezoidal Cumulative Energy based on configured metrology mode
        ems::SystemPowerSnapshot snapshot;
        if (!power_calc.tryUpdateCtOnly(samples, now, snapshot)) {
            // No synchronized voltage/meter acquisition driver is installed in this hardware path.
            Serial.println(F("[METROLOGY] Mode B unavailable: integrate calibrated meter acquisition before dispatch."));
            return;
        }

        // 3. Resilient Timekeeping: obtain valid Greek calendar components for tinyML indexing
        int current_hour = 12;
        int current_weekday = 0; // Monday default
        // Automatic Greek timezone calculation (EET UTC+2 in winter, EEST UTC+3 in summer)
        uint32_t current_epoch = rtc_timekeeper.getTimeInfo(now, current_hour, current_weekday);
        snapshot.timestamp_epoch = current_epoch;
        if (!rtc_timekeeper.isReliable()) {
            Serial.println(F("[TIME] Waiting for valid DS3231 or NTP before dated telemetry and profile adaptation."));
            return;
        }

        // Feed real-time power sample to edge forecaster's momentum filter
        edge_forecaster.updateRecentPowerSample(snapshot.total_active_power_kw);

        float predicted_next_kw = edge_forecaster.predictNextHour(current_weekday, current_hour, snapshot.total_active_power_kw);
        float predicted_p95_kw = edge_forecaster.predictNextHourP95(current_weekday, current_hour, snapshot.total_active_power_kw);
        bool projected_breach = edge_forecaster.isProjectedBreach(predicted_p95_kw, CONTRACTED_CAPACITY_KW);

        // Hourly accumulation for daily profile adaptation
        ems::CompletedPowerHour completed;
        if (hourly_power.addSample(current_epoch, current_hour, current_weekday,
                                  ems::RtcTimekeeper::getGreekTimezoneOffsetHours(current_epoch),
                                  snapshot.total_active_power_kw, completed)) {
            edge_forecaster.recordHourlyPower(completed.weekday, completed.hour, completed.mean_kw);
            if (completed.day_completed) {
                edge_forecaster.performDailyAdaptation(completed.weekday);
            }
        }

        // Print operational metrics and edge forecast to Serial console
        int effective_tz = ems::RtcTimekeeper::getGreekTimezoneOffsetHours(current_epoch);
        Serial.printf("[METRICS] Mode: %s | Prov: %s\r\n",
                      snapshot.metrology_mode == ems::MetrologyMode::MODE_B_TRUE_RMS ? "Mode B (True RMS)" : "Mode A (CT-Only)",
                      snapshot.measurement_method);
        Serial.printf("[PHASES]  L1: %.2fA (%.2fkW) | L2: %.2fA (%.2fkW) | L3: %.2fA (%.2fkW)\r\n",
                      snapshot.l1.current_a, snapshot.l1.active_power_kw,
                      snapshot.l2.current_a, snapshot.l2.active_power_kw,
                      snapshot.l3.current_a, snapshot.l3.active_power_kw);
        Serial.printf("[TOTALS]  P: %.3f kW | S: %.3f kVA | Q: %.3f kVAR | PF: %.2f | E: %.4f kWh\r\n",
                      snapshot.total_active_power_kw,
                      snapshot.total_apparent_power_kva,
                      snapshot.total_reactive_power_kvar,
                      snapshot.system_power_factor,
                      snapshot.cumulative_energy_kwh);
        Serial.printf("[TIME]    Epoch: %lu | Greek Wday: %d (Mon=0) | Hour: %02d:00 (UTC+%d %s)\r\n",
                      static_cast<unsigned long>(current_epoch), current_weekday, current_hour,
                      effective_tz, effective_tz == 3 ? "EEST" : "EET");
        Serial.printf("[FORECAST] Pred Mean: %.2f kW | P95 Peak: %.2f kW | Breach Risk: %s (Cap: %.1f kW)\r\n",
                      predicted_next_kw, predicted_p95_kw, projected_breach ? "YES (RISK!)" : "NO (SAFE)", CONTRACTED_CAPACITY_KW);
        Serial.printf("[STATUS]  Wi-Fi: %s | Queue Backlog: %u | Overflows: %u\r\n",
                      telemetry_client.isWiFiConnected() ? "CONNECTED" : "DISCONNECTED",
                      static_cast<unsigned int>(telemetry_client.getBufferedCount()),
                      static_cast<unsigned int>(telemetry_client.getBufferOverflows()));
        Serial.println(F("-----------------------------------------------------------------"));

        // 4. Dispatch telemetry snapshot with edge forecast
        telemetry_client.dispatchTelemetry(snapshot, predicted_next_kw, projected_breach);
    }
}
