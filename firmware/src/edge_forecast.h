/**
 * @file edge_forecast.h
 * @brief Scaled tinyML Dual-Matrix Weekly Seasonal Profiler with P95 Peak Risk and Momentum for ESP32.
 *
 * FEATURES (Phase 2 Scale-Up):
 * 1. Dual-Matrix Profile: 7x24 Mean (mu) and 7x24 Volatility/Std (sigma) in SRAM (1.34 KB).
 * 2. Probabilistic P95 Peak Forecast: P_95 = P_mean + 1.645 * sigma.
 * 3. Multi-Lag Momentum Filter: v_t = P_t - P_{t-1} rate of change boost (phi_v = 0.25).
 * 4. Dual Daily On-Device Continual Adaptation: Updates both mean and standard deviation matrices.
 * 5. NVS Flash Persistence (1.34 KB written once per day).
 */

#pragma once
#ifndef EDGE_FORECAST_H
#define EDGE_FORECAST_H

#include <stdint.h>
#include <stdbool.h>
#include "edge_forecast_model.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifdef __cplusplus
} // extern "C"

class EdgeForecaster {
public:
    EdgeForecaster();

    /**
     * @brief Initialize forecaster, loading dual matrices from NVS if available, or pre-trained baseline.
     */
    void begin();

    /**
     * @brief Generate next-hour mean forecast with residual persistence and momentum velocity boost.
     *
     * @param weekday Current weekday [0 = Monday, 6 = Sunday].
     * @param current_hour Current hour [0..23].
     * @param current_actual_kw Current measured active power in kW.
     * @return Predicted mean active power for next hour in kW.
     */
    float predictNextHour(int weekday, int current_hour, float current_actual_kw) const;

    /**
     * @brief Generate next-hour 95% confidence single-sided peak risk forecast:
     * P_95 = P_mean + 1.645 * sigma(weekday, next_hour).
     */
    float predictNextHourP95(int weekday, int current_hour, float current_actual_kw) const;

    /**
     * @brief Extract full 24-hour day-ahead forecast vector for optimization solver.
     *
     * @param weekday Target weekday [0..6].
     * @param output_mean_24h Destination buffer for mean expected kW (size 24).
     * @param output_std_24h Optional destination buffer for standard deviation (size 24).
     */
    void getDayAheadForecast(int weekday, float output_mean_24h[FORECAST_HOURS_IN_DAY], float output_std_24h[FORECAST_HOURS_IN_DAY] = nullptr) const;

    /**
     * @brief Push real-time power sample to maintain recent multi-lag momentum buffer.
     */
    void updateRecentPowerSample(float sample_kw);

    /**
     * @brief Current power velocity (rate of change dP/dt) in kW.
     */
    float getVelocityKw() const;

    /**
     * @brief Record actual observed average power for a completed hour.
     */
    void recordHourlyPower(int weekday, int hour, float actual_kw);

    /**
     * @brief Perform daily continual adaptation (micro-update) for a completed day.
     * Updates both mean and volatility matrices using EMA and clamps within safety bounds.
     */
    bool performDailyAdaptation(int completed_weekday);

    /**
     * @brief Check whether predicted power (mean or P95) breaches contracted capacity or threshold.
     */
    bool isProjectedBreach(float predicted_kw, float threshold_kw) const;

    /**
     * @brief Get active mean profile cell in kW.
     */
    float getProfileCell(int weekday, int hour) const;

    /**
     * @brief Get active volatility/standard deviation cell in kW.
     */
    float getProfileStdCell(int weekday, int hour) const;

    /**
     * @brief Set active profile cells in kW.
     */
    void setProfileCell(int weekday, int hour, float mean_kw, float std_kw = -1.0f);

    /**
     * @brief Load dual profiles from ESP32 NVS (Preferences).
     */
    bool loadFromNVS();

    /**
     * @brief Save dual profiles to ESP32 NVS (Preferences).
     */
    bool saveToNVS();

    /**
     * @brief Reset dual profiles to pre-trained factory default baseline.
     */
    void resetToDefaultProfile();

private:
    float active_mean_profile_[FORECAST_DAYS_IN_WEEK][FORECAST_HOURS_IN_DAY];
    float active_std_profile_[FORECAST_DAYS_IN_WEEK][FORECAST_HOURS_IN_DAY];
    float daily_recorded_kw_[FORECAST_HOURS_IN_DAY];
    uint32_t hours_recorded_mask_; // 24-bit mask for hours recorded in current day

    // Recent 3-sample multi-lag momentum window [P_t, P_{t-1}, P_{t-2}]
    float recent_samples_[3];
    uint8_t sample_count_;
};

#endif // __cplusplus

#endif // EDGE_FORECAST_H
