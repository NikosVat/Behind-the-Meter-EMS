/**
 * @file telemetry_client.cpp
 * @brief Implementation of Resilient Network Telemetry Client.
 */

#include "telemetry_client.h"
#include <time.h>
#include <algorithm>

namespace ems {

TelemetryClient::TelemetryClient()
    : mqtt_port_(1883),
      mode_(TelemetryMode::REST_ONLY),
      wifi_state_(WiFiState::DISCONNECTED),
      backoff_interval_ms_(INITIAL_BACKOFF_MS),
      last_reconnect_attempt_ms_(0),
      reconnect_attempts_(0),
      mqtt_client_(wifi_client_) {
    // Default network settings
#if __has_include("secrets.h")
#include "secrets.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID ""
#endif
#ifndef WIFI_PASSWORD
#define WIFI_PASSWORD ""
#endif
#ifndef EMS_API_BASE_URL
#define EMS_API_BASE_URL "https://ems.local:8000"
#endif
    strncpy(wifi_ssid_, WIFI_SSID, sizeof(wifi_ssid_));
    strncpy(wifi_password_, WIFI_PASSWORD, sizeof(wifi_password_));
    strncpy(rest_base_url_, EMS_API_BASE_URL, sizeof(rest_base_url_));
    strncpy(rest_endpoint_, "/api/v1/telemetry", sizeof(rest_endpoint_));
    strncpy(mqtt_broker_, "ems.local", sizeof(mqtt_broker_));
    strncpy(mqtt_topic_, "ems/telemetry", sizeof(mqtt_topic_));
    strncpy(device_id_, "esp32-ems-001", sizeof(device_id_));
    strncpy(facility_id_, "bakery-central-athens", sizeof(facility_id_));
}

void TelemetryClient::configureWiFi(const char* ssid, const char* password) {
    if (ssid) strncpy(wifi_ssid_, ssid, sizeof(wifi_ssid_) - 1);
    if (password) strncpy(wifi_password_, password, sizeof(wifi_password_) - 1);
}

void TelemetryClient::configureRest(const char* base_url, const char* endpoint) {
    if (base_url) strncpy(rest_base_url_, base_url, sizeof(rest_base_url_) - 1);
    if (endpoint) strncpy(rest_endpoint_, endpoint, sizeof(rest_endpoint_) - 1);
}

void TelemetryClient::configureMqtt(const char* broker_ip, uint16_t broker_port, const char* topic) {
    if (broker_ip) strncpy(mqtt_broker_, broker_ip, sizeof(mqtt_broker_) - 1);
    mqtt_port_ = broker_port;
    if (topic) strncpy(mqtt_topic_, topic, sizeof(mqtt_topic_) - 1);
}

void TelemetryClient::setDeviceIdentity(const char* device_id, const char* facility_id) {
    if (device_id) strncpy(device_id_, device_id, sizeof(device_id_) - 1);
    if (facility_id) strncpy(facility_id_, facility_id, sizeof(facility_id_) - 1);
}

void TelemetryClient::setMode(TelemetryMode mode) {
    mode_ = mode;
}

void TelemetryClient::begin() {
    WiFi.mode(WIFI_STA);
    WiFi.disconnect(true);
    delay(100);

    // Initialize NTP time client for ISO-8601 UTC timestamps
    configTime(0, 0, "pool.ntp.org", "time.google.com");

    if (mode_ == TelemetryMode::MQTT_ONLY || mode_ == TelemetryMode::REST_AND_MQTT) {
        mqtt_client_.setServer(mqtt_broker_, mqtt_port_);
        mqtt_client_.setBufferSize(1024);
    }

    wifi_state_ = WiFiState::DISCONNECTED;
    last_reconnect_attempt_ms_ = 0;
    reconnect_attempts_ = 0;
    backoff_interval_ms_ = INITIAL_BACKOFF_MS;
}

void TelemetryClient::handleWiFiReconnect(uint32_t current_millis) {
    if (WiFi.status() == WL_CONNECTED) {
        if (wifi_state_ != WiFiState::CONNECTED) {
            wifi_state_ = WiFiState::CONNECTED;
            reconnect_attempts_ = 0;
            backoff_interval_ms_ = INITIAL_BACKOFF_MS;
        }
        return;
    }

    wifi_state_ = WiFiState::DISCONNECTED;

    if (current_millis - last_reconnect_attempt_ms_ >= backoff_interval_ms_) {
        last_reconnect_attempt_ms_ = current_millis;
        reconnect_attempts_++;

        WiFi.begin(wifi_ssid_, wifi_password_);

        // Exponential backoff with ceiling: interval = min(MAX, interval * 1.5)
        uint32_t next_backoff = static_cast<uint32_t>(backoff_interval_ms_ * BACKOFF_MULTIPLIER);
        if (next_backoff > MAX_BACKOFF_MS) {
            next_backoff = MAX_BACKOFF_MS;
        }
        backoff_interval_ms_ = next_backoff;
    }
}

void TelemetryClient::handleMqttReconnect(uint32_t current_millis) {
    if (mode_ == TelemetryMode::REST_ONLY) return;
    if (wifi_state_ != WiFiState::CONNECTED) return;

    if (!mqtt_client_.connected()) {
        String client_id_str = String(device_id_) + "-" + String(random(1000, 9999));
        mqtt_client_.connect(client_id_str.c_str());
    } else {
        mqtt_client_.loop();
    }
}

void TelemetryClient::loop(uint32_t current_millis) {
    handleWiFiReconnect(current_millis);

    if (wifi_state_ == WiFiState::CONNECTED) {
        handleMqttReconnect(current_millis);
        // Flush up to 5 queued records per loop iteration
        flushQueue(5);
    }
}

void TelemetryClient::formatIsoTimestamp(uint32_t epoch, char* out_buf, size_t buf_len) {
    if (!out_buf || buf_len < 21) return;

    // If NTP epoch is valid (> year 2020: 1577836800), use real time
    if (epoch > 1577836800UL) {
        time_t raw_time = static_cast<time_t>(epoch);
        struct tm timeinfo;
        gmtime_r(&raw_time, &timeinfo);
        strftime(out_buf, buf_len, "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
    } else {
        // Fallback synthetic ISO timestamp with uptime offset for deterministic offline logging
        uint32_t seconds = millis() / 1000UL;
        uint32_t hh = (seconds / 3600UL) % 24;
        uint32_t mm = (seconds / 60UL) % 60;
        uint32_t ss = seconds % 60;
        snprintf(out_buf, buf_len, "2026-09-14T%02u:%02u:%02uZ", hh, mm, ss);
    }
}

String TelemetryClient::serializePayloadJson(const SystemPowerSnapshot& snapshot, const char* timestamp_str, int8_t rssi_dbm) {
    StaticJsonDocument<1024> doc;

    doc["device_id"] = device_id_;
    doc["facility_id"] = facility_id_;
    doc["timestamp"] = timestamp_str;
    // Mode provenance: "estimated_nominal_voltage_pf" (Mode A) vs "meter_measured" (Mode B)
    doc["power_measurement_method"] = (snapshot.measurement_method && snapshot.measurement_method[0] != '\0')
                                          ? snapshot.measurement_method
                                          : "estimated_nominal_voltage_pf";

    // Phases dictionary
    JsonObject phases = doc.createNestedObject("phases");

    // Per-phase active power rounded to 3 decimal places
    float p_l1 = roundf(snapshot.l1.active_power_kw * 1000.0f) / 1000.0f;
    float p_l2 = roundf(snapshot.l2.active_power_kw * 1000.0f) / 1000.0f;
    float p_l3 = roundf(snapshot.l3.active_power_kw * 1000.0f) / 1000.0f;

    JsonObject l1 = phases.createNestedObject("L1");
    l1["voltage_v"] = roundf(snapshot.l1.voltage_v * 10.0f) / 10.0f;
    l1["current_a"] = roundf(snapshot.l1.current_a * 100.0f) / 100.0f;
    l1["active_power_kw"] = p_l1;
    l1["apparent_power_kva"] = roundf(snapshot.l1.apparent_power_kva * 1000.0f) / 1000.0f;
    l1["power_factor"] = roundf(snapshot.l1.power_factor * 100.0f) / 100.0f;

    JsonObject l2 = phases.createNestedObject("L2");
    l2["voltage_v"] = roundf(snapshot.l2.voltage_v * 10.0f) / 10.0f;
    l2["current_a"] = roundf(snapshot.l2.current_a * 100.0f) / 100.0f;
    l2["active_power_kw"] = p_l2;
    l2["apparent_power_kva"] = roundf(snapshot.l2.apparent_power_kva * 1000.0f) / 1000.0f;
    l2["power_factor"] = roundf(snapshot.l2.power_factor * 100.0f) / 100.0f;

    JsonObject l3 = phases.createNestedObject("L3");
    l3["voltage_v"] = roundf(snapshot.l3.voltage_v * 10.0f) / 10.0f;
    l3["current_a"] = roundf(snapshot.l3.current_a * 100.0f) / 100.0f;
    l3["active_power_kw"] = p_l3;
    l3["apparent_power_kva"] = roundf(snapshot.l3.apparent_power_kva * 1000.0f) / 1000.0f;
    l3["power_factor"] = roundf(snapshot.l3.power_factor * 100.0f) / 100.0f;

    // Exact sum conservation to satisfy backend invariant:
    // |total_active_power_kw - sum(phases[Li])| <= 0.05 kW
    doc["total_active_power_kw"] = p_l1 + p_l2 + p_l3;
    doc["total_apparent_power_kva"] = roundf(snapshot.total_apparent_power_kva * 1000.0f) / 1000.0f;
    doc["system_power_factor"] = roundf(snapshot.system_power_factor * 100.0f) / 100.0f;
    doc["cumulative_energy_kwh"] = roundf(static_cast<float>(snapshot.cumulative_energy_kwh) * 1000.0f) / 1000.0f;
    doc["grid_frequency_hz"] = roundf(snapshot.grid_frequency_hz * 100.0f) / 100.0f;
    doc["wifi_rssi_dbm"] = static_cast<float>(rssi_dbm);

    if (predicted_next_kw >= 0.0f) {
        doc["predicted_next_kw"] = roundf(predicted_next_kw * 1000.0f) / 1000.0f;
        doc["projected_peak_breach"] = projected_peak_breach;
    }

    String output;
    serializeJson(doc, output);
    return output;
}

bool TelemetryClient::sendRestPayload(const String& json_payload) {
    if (wifi_state_ != WiFiState::CONNECTED) return false;

    HTTPClient http;
    String target_url = String(rest_base_url_) + String(rest_endpoint_);
    http.begin(target_url);
    http.addHeader("Content-Type", "application/json");
    http.setTimeout(4000);

    int http_response_code = http.POST(json_payload);
    http.end();

    return (http_response_code >= 200 && http_response_code < 300);
}

bool TelemetryClient::sendMqttPayload(const String& json_payload) {
    if (wifi_state_ != WiFiState::CONNECTED || !mqtt_client_.connected()) {
        return false;
    }
    return mqtt_client_.publish(mqtt_topic_, json_payload.c_str());
}

bool TelemetryClient::dispatchTelemetry(const SystemPowerSnapshot& snapshot, float predicted_next_kw, bool projected_peak_breach) {
    uint32_t now_epoch = (snapshot.timestamp_epoch > 100000)
                             ? snapshot.timestamp_epoch
                             : static_cast<uint32_t>(time(nullptr));
    int8_t rssi = (WiFi.status() == WL_CONNECTED) ? static_cast<int8_t>(WiFi.RSSI()) : -85;

    // If connected and ring buffer is empty, attempt immediate direct transmission
    if (wifi_state_ == WiFiState::CONNECTED && ring_buffer_.isEmpty()) {
        char ts_buf[32];
        formatIsoTimestamp(now_epoch, ts_buf, sizeof(ts_buf));
        String payload = serializePayloadJson(snapshot, ts_buf, rssi, predicted_next_kw, projected_peak_breach);

        bool success = true;
        if (mode_ == TelemetryMode::REST_ONLY || mode_ == TelemetryMode::REST_AND_MQTT) {
            success = sendRestPayload(payload);
        }
        if ((mode_ == TelemetryMode::MQTT_ONLY || mode_ == TelemetryMode::REST_AND_MQTT) && success) {
            sendMqttPayload(payload);
        }

        if (success) {
            return true;
        }
    }

    // Network unavailable or direct send failed: store in ring buffer for store-and-forward
    BufferedTelemetryRecord rec;
    rec.power = snapshot;
    rec.timestamp_epoch = now_epoch;
    rec.rssi_dbm = rssi;
    rec.predicted_next_kw = predicted_next_kw;
    rec.projected_peak_breach = projected_peak_breach;

    ring_buffer_.push(rec);
    return false;
}

void TelemetryClient::flushQueue(uint8_t max_records_per_loop) {
    if (wifi_state_ != WiFiState::CONNECTED || ring_buffer_.isEmpty()) {
        return;
    }

    uint8_t sent_count = 0;
    BufferedTelemetryRecord rec;

    while (sent_count < max_records_per_loop && ring_buffer_.peek(rec)) {
        char ts_buf[32];
        formatIsoTimestamp(rec.timestamp_epoch, ts_buf, sizeof(ts_buf));
        String payload = serializePayloadJson(rec.power, ts_buf, rec.rssi_dbm, rec.predicted_next_kw, rec.projected_peak_breach);

        bool success = true;
        if (mode_ == TelemetryMode::REST_ONLY || mode_ == TelemetryMode::REST_AND_MQTT) {
            success = sendRestPayload(payload);
        }
        if ((mode_ == TelemetryMode::MQTT_ONLY || mode_ == TelemetryMode::REST_AND_MQTT) && success) {
            sendMqttPayload(payload);
        }

        if (success) {
            ring_buffer_.pop(rec);
            sent_count++;
        } else {
            // If transmission failed, stop flushing and keep remaining in buffer
            break;
        }
    }
}

} // namespace ems
