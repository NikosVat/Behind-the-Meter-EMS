/**
 * @file rtc_timekeeper.cpp
 * @brief Implementation of DS3231 RTC Driver and Resilient Offline Timekeeping.
 */

#include "rtc_timekeeper.h"
#include <Arduino.h>
#include <Wire.h>
#include <Preferences.h>
#include <sys/time.h>

namespace ems {

static constexpr uint8_t DS3231_I2C_ADDR = 0x68;
static constexpr uint8_t REG_SECONDS = 0x00;
static constexpr uint8_t REG_STATUS = 0x0F;
static const char* NVS_NAMESPACE = "ems_time";
static const char* NVS_KEY_EPOCH = "last_epoch";

RtcTimekeeper::RtcTimekeeper()
    : sync_source_(TimeSyncSource::DEFAULT_BUILD_EPOCH),
      current_epoch_base_(MIN_VALID_EPOCH),
      base_millis_(0),
      last_nvs_write_epoch_(0),
      ds3231_available_(false) {}

bool RtcTimekeeper::begin(uint8_t sda_pin, uint8_t scl_pin) {
    base_millis_ = millis();

    // 1. Initialize I2C Bus for DS3231
    Wire.begin(sda_pin, scl_pin);

    // 2. Probe DS3231 Hardware
    uint32_t rtc_epoch = 0;
    bool rtc_ok = readDs3231(rtc_epoch);

    if (rtc_ok && rtc_epoch >= MIN_VALID_EPOCH) {
        ds3231_available_ = true;
        current_epoch_base_ = rtc_epoch;
        sync_source_ = TimeSyncSource::RTC_HARDWARE;
        applySystemTime(rtc_epoch);
        return true;
    }

    // 3. Fallback to NVS Flash
    uint32_t nvs_epoch = readNvsTimestamp();
    if (nvs_epoch >= MIN_VALID_EPOCH) {
        current_epoch_base_ = nvs_epoch;
        sync_source_ = TimeSyncSource::NVS_FALLBACK;
        applySystemTime(nvs_epoch);
        return false;
    }

    // 4. Fallback to compile-time safety floor
    current_epoch_base_ = MIN_VALID_EPOCH;
    sync_source_ = TimeSyncSource::DEFAULT_BUILD_EPOCH;
    applySystemTime(MIN_VALID_EPOCH);
    return false;
}

void RtcTimekeeper::syncWithNtp(uint32_t ntp_epoch) {
    if (ntp_epoch < MIN_VALID_EPOCH) {
        return;
    }

    current_epoch_base_ = ntp_epoch;
    base_millis_ = millis();
    sync_source_ = TimeSyncSource::NTP_SYNCED;

    applySystemTime(ntp_epoch);

    // Sync hardware RTC
    writeDs3231(ntp_epoch);

    // Persist to NVS flash immediately on authoritative NTP sync
    persistTimestampToNvs(ntp_epoch, true);
}

uint32_t RtcTimekeeper::getCurrentEpoch(uint32_t current_millis) {
    uint32_t elapsed_ms = current_millis - base_millis_;
    uint32_t elapsed_sec = elapsed_ms / 1000UL;
    if (elapsed_sec > 0) {
        current_epoch_base_ += elapsed_sec;
        base_millis_ += elapsed_sec * 1000UL;
    }
    return current_epoch_base_;
}

uint32_t RtcTimekeeper::getTimeInfo(uint32_t current_millis, int& out_hour, int& out_weekday, int tz_offset_hours) {
    uint32_t epoch = getCurrentEpoch(current_millis);
    epochToGreekCalendar(epoch, out_hour, out_weekday, tz_offset_hours);

    // Periodically update NVS flash in background to protect against sudden power cut
    persistTimestampToNvs(epoch, false);

    return epoch;
}

bool RtcTimekeeper::persistTimestampToNvs(uint32_t epoch, bool force) {
    if (epoch < MIN_VALID_EPOCH) {
        return false;
    }

    // Rate-limiting check to preserve flash write endurance (unless forced)
    if (!force && last_nvs_write_epoch_ > 0 &&
        (epoch - last_nvs_write_epoch_ < NVS_WRITE_MIN_INTERVAL_SEC)) {
        return false;
    }

    writeNvsTimestamp(epoch);
    last_nvs_write_epoch_ = epoch;
    return true;
}

void RtcTimekeeper::applySystemTime(uint32_t epoch) {
    struct timeval tv;
    tv.tv_sec = static_cast<time_t>(epoch);
    tv.tv_usec = 0;
    settimeofday(&tv, nullptr);
}

bool RtcTimekeeper::readDs3231(uint32_t& out_epoch) {
    // Check Oscillator Stop Flag (OSF) first in register 0x0F
    Wire.beginTransmission(DS3231_I2C_ADDR);
    Wire.write(REG_STATUS);
    if (Wire.endTransmission() != 0) {
        return false; // I2C communication failed
    }

    if (Wire.requestFrom(static_cast<int>(DS3231_I2C_ADDR), 1) != 1) {
        return false;
    }

    uint8_t status_reg = Wire.read();
    if (status_reg & 0x80) {
        // OSF is set: oscillator stopped, battery lost, time is invalid
        return false;
    }

    // Read time registers 0x00 .. 0x06
    Wire.beginTransmission(DS3231_I2C_ADDR);
    Wire.write(REG_SECONDS);
    if (Wire.endTransmission() != 0) {
        return false;
    }

    if (Wire.requestFrom(static_cast<int>(DS3231_I2C_ADDR), 7) != 7) {
        return false;
    }

    uint8_t sec = bcdToDec(Wire.read() & 0x7F);
    uint8_t min = bcdToDec(Wire.read() & 0x7F);
    uint8_t hour = bcdToDec(Wire.read() & 0x3F);
    Wire.read(); // skip day-of-week register (0x03)
    uint8_t date = bcdToDec(Wire.read() & 0x3F);
    uint8_t month_raw = Wire.read();
    uint8_t month = bcdToDec(month_raw & 0x1F);
    uint8_t year_two_digit = bcdToDec(Wire.read());

    int full_year = 2000 + year_two_digit;

    out_epoch = calendarToEpoch(full_year, month, date, hour, min, sec);
    return (out_epoch >= MIN_VALID_EPOCH);
}

bool RtcTimekeeper::writeDs3231(uint32_t epoch) {
    time_t raw_time = static_cast<time_t>(epoch);
    struct tm ti;
    if (!gmtime_r(&raw_time, &ti)) {
        return false;
    }

    Wire.beginTransmission(DS3231_I2C_ADDR);
    Wire.write(REG_SECONDS);
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_sec)));
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_min)));
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_hour)));
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_wday + 1))); // DS3231 dow is 1..7
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_mday)));
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_mon + 1)));  // tm_mon is 0..11
    Wire.write(decToBcd(static_cast<uint8_t>(ti.tm_year % 100)));
    if (Wire.endTransmission() != 0) {
        return false;
    }

    // Clear OSF bit in Status register (0x0F)
    Wire.beginTransmission(DS3231_I2C_ADDR);
    Wire.write(REG_STATUS);
    Wire.write(0x00);
    Wire.endTransmission();

    ds3231_available_ = true;
    return true;
}

uint32_t RtcTimekeeper::readNvsTimestamp() {
    Preferences prefs;
    if (!prefs.begin(NVS_NAMESPACE, true)) { // Read-only mode
        return 0;
    }
    uint32_t epoch = prefs.getUInt(NVS_KEY_EPOCH, 0);
    prefs.end();
    return epoch;
}

void RtcTimekeeper::writeNvsTimestamp(uint32_t epoch) {
    Preferences prefs;
    if (prefs.begin(NVS_NAMESPACE, false)) { // Read-write mode
        prefs.putUInt(NVS_KEY_EPOCH, epoch);
        prefs.end();
    }
}

int RtcTimekeeper::getGreekTimezoneOffsetHours(uint32_t epoch) {
    time_t raw_time = static_cast<time_t>(epoch);
    struct tm ti;
    if (!gmtime_r(&raw_time, &ti)) {
        return 2; // Fallback to EET (UTC+2)
    }

    int month = ti.tm_mon + 1; // 1..12
    int day = ti.tm_mday;       // 1..31
    int hour = ti.tm_hour;      // 0..23 (UTC)
    int wday = ti.tm_wday;      // 0=Sun, 1=Mon, ..., 6=Sat

    // Winter months (Nov, Dec, Jan, Feb) are always EET (UTC+2)
    if (month < 3 || month > 10) {
        return 2;
    }
    // Summer months (Apr, May, Jun, Jul, Aug, Sep) are always EEST (UTC+3)
    if (month > 3 && month < 10) {
        return 3;
    }

    // March and October: transition occurs on the last Sunday at 01:00 UTC
    // Compute day of week for the 31st of the current month
    int days_to_31 = 31 - day;
    int wday_31 = (wday + days_to_31) % 7;
    int last_sunday = 31 - wday_31;

    if (month == 3) {
        // March: DST starts on last Sunday at 01:00 UTC (EET -> EEST, +2 -> +3)
        if (day > last_sunday || (day == last_sunday && hour >= 1)) {
            return 3;
        }
        return 2;
    } else {
        // October: DST ends on last Sunday at 01:00 UTC (EEST -> EET, +3 -> +2)
        if (day < last_sunday || (day == last_sunday && hour < 1)) {
            return 3;
        }
        return 2;
    }
}

void RtcTimekeeper::epochToGreekCalendar(uint32_t epoch, int& out_hour, int& out_weekday, int tz_offset_hours) {
    int effective_tz = (tz_offset_hours == TZ_AUTO_GREEK) ? getGreekTimezoneOffsetHours(epoch) : tz_offset_hours;
    int64_t local_epoch = static_cast<int64_t>(epoch) + (static_cast<int64_t>(effective_tz) * 3600);
    int64_t days_since_epoch = local_epoch / 86400LL;
    int seconds_in_day = static_cast<int>(local_epoch % 86400LL);

    if (seconds_in_day < 0) {
        seconds_in_day += 86400;
        days_since_epoch -= 1;
    }

    out_hour = seconds_in_day / 3600;

    // Unix epoch 0 (1970-01-01) was Thursday.
    // Greek business week mapping: Monday = 0 ... Sunday = 6.
    // Thursday is day 3. Therefore: (days + 3) % 7.
    int dow = static_cast<int>((days_since_epoch + 3) % 7);
    if (dow < 0) {
        dow += 7;
    }
    out_weekday = dow;
}

uint32_t RtcTimekeeper::calendarToEpoch(int year, int month, int day, int hour, int minute, int second) {
    // Days before each month in a standard non-leap year
    static const int days_before_month[] = {
        0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334
    };

    if (year < 1970 || month < 1 || month > 12 || day < 1 || day > 31) {
        return MIN_VALID_EPOCH;
    }

    int y = year - 1970;
    // Number of leap days between 1970 and year
    int leap_days = (year - 1969) / 4 - (year - 1901) / 100 + (year - 1601) / 400;
    bool is_leap = ((year % 4 == 0 && year % 100 != 0) || (year % 400 == 0));

    int day_of_year = days_before_month[month - 1] + (day - 1);
    if (is_leap && month > 2) {
        day_of_year += 1;
    }

    uint32_t total_days = (y * 365) + leap_days + day_of_year;
    return (total_days * 86400UL) + (hour * 3600UL) + (minute * 60UL) + second;
}

} // namespace ems
