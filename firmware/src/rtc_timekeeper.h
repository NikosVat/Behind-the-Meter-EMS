/**
 * @file rtc_timekeeper.h
 * @brief Hardware RTC (DS3231) Driver & Resilient Offline Timekeeping for ESP32.
 *
 * RESILIENCE ARCHITECTURE:
 * 1. Hardware DS3231 Real-Time Clock:
 *    - Battery-backed I2C RTC (address 0x68) maintains sub-second accuracy during power outages.
 *    - Temperature-compensated crystal oscillator (TCXO) with +/- 2ppm accuracy (-40C to +85C).
 * 2. NVS Flash Epoch Persistence:
 *    - Periodically saves current Unix epoch into ESP32 Non-Volatile Storage (Preferences).
 *    - If RTC battery fails or hardware is absent, device recovers time from NVS + elapsed millis.
 * 3. 1970 Epoch Immunization:
 *    - Prevents the ESP32 from booting at Unix Epoch 0 (1970-01-01), which corrupts the
 *      on-device tinyML 7x24 seasonal matrix indexing and generates corrupt telemetry.
 *    - Minimum valid epoch floor: 1769904000 (2026-02-01 00:00:00 UTC).
 * 4. Greek Business Week Indexing & Dynamic DST:
 *    - Maps timestamps to Monday = 0 ... Sunday = 6 and Hour = 0 ... 23 for tinyML compatibility.
 *    - Dynamic Greek timezone calculation (EET UTC+2 in winter, EEST UTC+3 in summer) per EU Directive 2000/84/EC.
 */

#pragma once

#include <cstdint>
#include <ctime>

namespace ems {

enum class TimeSyncSource : uint8_t {
    DEFAULT_BUILD_EPOCH = 0,  // Safety baseline (>= Feb 2026)
    NVS_FALLBACK = 1,         // Restored from ESP32 NVS Flash + elapsed millis
    RTC_HARDWARE = 2,         // Read from DS3231 I2C RTC (battery backed)
    NTP_SYNCED = 3            // Synchronized via Wi-Fi NTP server
};

// Safe floor epoch: 2026-02-01 00:00:00 UTC (1769904000)
// Guarantees system NEVER starts at 1970 UNIX epoch (0)
constexpr uint32_t MIN_VALID_EPOCH = 1769904000UL;

// Minimum interval between non-forced NVS flash writes (15 minutes) to avoid wear
constexpr uint32_t NVS_WRITE_MIN_INTERVAL_SEC = 900;

// Sentinel value for automatic Greek timezone (EET UTC+2 in winter, EEST UTC+3 in summer)
constexpr int TZ_AUTO_GREEK = -100;

class RtcTimekeeper {
public:
    RtcTimekeeper();

    /**
     * @brief Initialize DS3231 I2C RTC driver and load fallback timestamp from NVS.
     * @param sda_pin I2C SDA pin (default 21 on ESP32)
     * @param scl_pin I2C SCL pin (default 22 on ESP32)
     * @return true if DS3231 was detected and valid
     */
    bool begin(uint8_t sda_pin = 21, uint8_t scl_pin = 22);

    /**
     * @brief Notify timekeeper of successful NTP time synchronization.
     * Updates ESP32 system clock, writes time to DS3231 RTC, and persists to NVS.
     * @param ntp_epoch Unix timestamp in seconds from NTP server
     */
    void syncWithNtp(uint32_t ntp_epoch);

    /**
     * @brief Get current best estimate of Unix epoch in seconds.
     * Guaranteed to be >= MIN_VALID_EPOCH, monotonically increasing, and immune to 49.7-day millis wrap.
     * @param current_millis Current system millis()
     * @return Unix epoch in seconds
     */
    uint32_t getCurrentEpoch(uint32_t current_millis);

    /**
     * @brief Extract hour and weekday indices for tinyML 7x24 weekly matrix indexing.
     * @param current_millis Current system millis()
     * @param[out] out_hour Local Greek commercial hour [0..23]
     * @param[out] out_weekday Greek commercial weekday [0..6] (0 = Monday, ..., 6 = Sunday)
     * @param tz_offset_hours Timezone offset relative to UTC (default TZ_AUTO_GREEK for automatic EET/EEST DST)
     * @return Current epoch timestamp
     */
    uint32_t getTimeInfo(uint32_t current_millis, int& out_hour, int& out_weekday, int tz_offset_hours = TZ_AUTO_GREEK);

    /**
     * @brief Persist current timestamp to ESP32 NVS Flash.
     * Rate-limited to prevent flash memory wear unless force is true.
     * @param epoch Unix timestamp in seconds
     * @param force Force write immediately, bypassing rate limit
     * @return true if written to NVS
     */
    bool persistTimestampToNvs(uint32_t epoch, bool force = false);

    /**
     * @brief Get current active time synchronization source.
     */
    TimeSyncSource getSyncSource() const { return sync_source_; }

    /**
     * @brief Reliable wall time requires a running hardware RTC or an NTP sync.
     * NVS/build floors cannot account for time spent powered off.
     */
    bool isReliable() const { return sync_source_ == TimeSyncSource::RTC_HARDWARE || sync_source_ == TimeSyncSource::NTP_SYNCED; }

    /**
     * @brief Check if physical DS3231 hardware was detected on I2C bus.
     */
    bool isDs3231Available() const { return ds3231_available_; }

    /**
     * @brief Dynamic Greek timezone offset calculation per EU Directive 2000/84/EC.
     * Returns +3 for EEST (Daylight Saving Time, last Sunday March 01:00 UTC to last Sunday October 01:00 UTC)
     * and +2 for EET (Standard Time).
     * @param epoch Unix timestamp in seconds
     * @return Timezone offset in hours (+2 or +3)
     */
    static int getGreekTimezoneOffsetHours(uint32_t epoch);

    /**
     * @brief Algorithmic conversion from Unix epoch to Greek business calendar components.
     * @param epoch Unix timestamp in seconds
     * @param[out] out_hour Hour of day [0..23]
     * @param[out] out_weekday Day of week [0..6] where 0 = Monday, ..., 6 = Sunday
     * @param tz_offset_hours Timezone offset in hours (default TZ_AUTO_GREEK for auto EET/EEST)
     */
    static void epochToGreekCalendar(uint32_t epoch, int& out_hour, int& out_weekday, int tz_offset_hours = TZ_AUTO_GREEK);

    /**
     * @brief Static helper to convert calendar components to Unix epoch.
     */
    static uint32_t calendarToEpoch(int year, int month, int day, int hour, int minute, int second);

    // Static BCD conversion helpers for DS3231 registers
    static inline uint8_t bcdToDec(uint8_t val) { return ((val / 16) * 10) + (val % 16); }
    static inline uint8_t decToBcd(uint8_t val) { return ((val / 10) * 16) + (val % 10); }

private:
    TimeSyncSource sync_source_;
    uint32_t current_epoch_base_;
    uint32_t base_millis_;
    uint32_t last_nvs_write_epoch_;
    bool ds3231_available_;

    bool readDs3231(uint32_t& out_epoch);
    bool writeDs3231(uint32_t epoch);
    uint32_t readNvsTimestamp();
    void writeNvsTimestamp(uint32_t epoch);
    void applySystemTime(uint32_t epoch);
};

} // namespace ems
