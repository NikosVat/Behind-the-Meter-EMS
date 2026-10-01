/**
 * @file telemetry_client.h
 * @brief Resilient Network Telemetry Client with Wi-Fi Reconnection & Dual REST/MQTT Dispatch.
 *
 * FEATURES:
 * 1. Wi-Fi Manager with Exponential Backoff Auto-Reconnect:
 *    - Backoff formula: interval = min(max_backoff_ms, initial_backoff_ms * (factor ^ retries)) + jitter.
 *    - Fully non-blocking state machine in loop().
 * 2. REST HTTP POST Telemetry Client:
 *    - Transmits JSON payloads matching backend TelemetryPayload Pydantic schema to /api/v1/telemetry.
 *    - Invariant conservation: total_active_power_kw == sum(phases[Li].active_power_kw).
 * 3. MQTT Telemetry Client:
 *    - PubSubClient integration for lightweight MQTT pub/sub on "ems/telemetry".
 * 4. Store-and-Forward Ring Buffer Integration:
 *    - Buffers readings during network disconnects and flushes queue upon reconnection.
 */

#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include "power_calc.h"
#include "ring_buffer.h"
#include "telemetry_delivery.h"

namespace ems {

// Telemetry dispatch protocol mode
enum class TelemetryMode : uint8_t {
    REST_ONLY = 0,
    MQTT_ONLY = 1,
    REST_AND_MQTT = 2
};

// Wi-Fi Connection state machine
enum class WiFiState : uint8_t {
    DISCONNECTED = 0,
    CONNECTING = 1,
    CONNECTED = 2
};

// Compact snapshot stored in ring buffer
struct BufferedTelemetryRecord {
    SystemPowerSnapshot power;
    uint32_t timestamp_epoch;
    int8_t rssi_dbm;
    float predicted_next_kw;
    bool projected_peak_breach;
};

// Capacity of the in-memory store-and-forward buffer
constexpr size_t TELEMETRY_BUFFER_CAPACITY = 64;

class TelemetryClient {
public:
    TelemetryClient();

    /**
     * @brief Configure Wi-Fi network credentials and backoff parameters.
     */
    void configureWiFi(const char* ssid, const char* password);

    /**
     * @brief Configure REST ingestion server details.
     * @param base_url Base URL e.g. "http://192.168.1.100:8000"
     * @param endpoint Ingestion endpoint e.g. "/api/v1/telemetry"
     */
    void configureRest(const char* base_url, const char* endpoint = "/api/v1/telemetry");

    /**
     * @brief Configure MQTT broker and topic parameters.
     */
    void configureMqtt(const char* broker_ip, uint16_t broker_port, const char* topic = "ems/telemetry");

    /**
     * @brief Set device identity metadata.
     */
    void setDeviceIdentity(const char* device_id, const char* facility_id);

    /**
     * @brief Set operational protocol dispatch mode.
     */
    void setMode(TelemetryMode mode);

    /**
     * @brief Initialize Wi-Fi and network drivers.
     */
    void begin();

    /**
     * @brief Main non-blocking maintenance loop. Handles Wi-Fi/MQTT reconnections and flushes queue.
     * @param current_millis Current millis()
     */
    void loop(uint32_t current_millis);

    /**
     * @brief Enqueue a new telemetry snapshot for transmission.
     * Transmits immediately if connected; otherwise stores in ring buffer.
     * @param snapshot System power snapshot from PowerCalculator
     * @param predicted_next_kw Optional on-device edge forecast for next hour
     * @param projected_peak_breach Optional flag for predicted capacity breach
     * @return true only when transmitted; false when buffered for retry or permanently rejected.
     */
    bool dispatchTelemetry(const SystemPowerSnapshot& snapshot, float predicted_next_kw = -1.0f, bool projected_peak_breach = false);

    /**
     * @brief Format ISO-8601 UTC timestamp string from epoch.
     * @param epoch Unix timestamp in seconds
     * @param[out] out_buf Destination character buffer (at least 25 bytes)
     * @param buf_len Length of destination buffer
     */
    static void formatIsoTimestamp(uint32_t epoch, char* out_buf, size_t buf_len);

    /**
     * @brief Serialize a telemetry snapshot into JSON string matching backend schema.
     */
    String serializePayloadJson(const SystemPowerSnapshot& snapshot, const char* timestamp_str, int8_t rssi_dbm, float predicted_next_kw = -1.0f, bool projected_peak_breach = false);

    /**
     * @brief Check if Wi-Fi is currently connected.
     */
    bool isWiFiConnected() const { return wifi_state_ == WiFiState::CONNECTED; }

    /**
     * @brief Current buffered backlog size.
     */
    size_t getBufferedCount() const { return ring_buffer_.size(); }

    /**
     * @brief Total dropped packets due to ring buffer overflow.
     */
    uint32_t getBufferOverflows() const { return ring_buffer_.overflowCount(); }

private:
    char wifi_ssid_[64];
    char wifi_password_[64];
    char rest_base_url_[128];
    char rest_endpoint_[64];
    char mqtt_broker_[64];
    uint16_t mqtt_port_;
    char mqtt_topic_[64];
    char device_id_[64];
    char facility_id_[64];

    TelemetryMode mode_;
    WiFiState wifi_state_;

    // Exponential backoff parameters
    uint32_t backoff_interval_ms_;
    uint32_t last_reconnect_attempt_ms_;
    uint8_t reconnect_attempts_;
    static constexpr uint32_t INITIAL_BACKOFF_MS = 2000;
    static constexpr uint32_t MAX_BACKOFF_MS = 60000;
    static constexpr float BACKOFF_MULTIPLIER = 1.5f;

    WiFiClient wifi_client_;
    PubSubClient mqtt_client_;
    RingBuffer<BufferedTelemetryRecord, TELEMETRY_BUFFER_CAPACITY> ring_buffer_;

    void handleWiFiReconnect(uint32_t current_millis);
    void handleMqttReconnect(uint32_t current_millis);
    DeliveryOutcome sendRestPayload(const String& json_payload);
    bool sendMqttPayload(const String& json_payload);
    void flushQueue(uint8_t max_records_per_loop);
};

} // namespace ems
