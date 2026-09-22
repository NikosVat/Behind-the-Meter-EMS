/**
 * @file edge_forecast.cpp
 * @brief Scaled tinyML Dual-Matrix Weekly Seasonal Profiler with P95 Peak Risk and Momentum.
 */

#include "edge_forecast.h"
#include <string.h>
#include <math.h>

#if defined(ARDUINO) || defined(ESP32)
#include <Preferences.h>
#endif

EdgeForecaster::EdgeForecaster() : hours_recorded_mask_(0), sample_count_(0) {
    resetToDefaultProfile();
    for (int h = 0; h < FORECAST_HOURS_IN_DAY; ++h) {
        daily_recorded_kw_[h] = 0.0f;
    }
    for (int s = 0; s < 3; ++s) {
        recent_samples_[s] = 0.0f;
    }
}

void EdgeForecaster::resetToDefaultProfile() {
    for (int d = 0; d < FORECAST_DAYS_IN_WEEK; ++d) {
        for (int h = 0; h < FORECAST_HOURS_IN_DAY; ++h) {
            active_mean_profile_[d][h] = DEFAULT_WEEKLY_PROFILE[d][h];
            active_std_profile_[d][h] = DEFAULT_STD_PROFILE[d][h];
        }
    }
}

void EdgeForecaster::begin() {
    if (!loadFromNVS()) {
        resetToDefaultProfile();
    }
    hours_recorded_mask_ = 0;
    sample_count_ = 0;
}

void EdgeForecaster::updateRecentPowerSample(float sample_kw) {
    recent_samples_[2] = recent_samples_[1];
    recent_samples_[1] = recent_samples_[0];
    recent_samples_[0] = (sample_kw > 0.0f) ? sample_kw : FORECAST_MIN_POWER_KW;
    if (sample_count_ < 3) {
        sample_count_++;
    }
}

float EdgeForecaster::getVelocityKw() const {
    if (sample_count_ >= 2) {
        return recent_samples_[0] - recent_samples_[1];
    }
    return 0.0f;
}

float EdgeForecaster::predictNextHour(int weekday, int current_hour, float current_actual_kw) const {
    if (weekday < 0 || weekday >= FORECAST_DAYS_IN_WEEK ||
        current_hour < 0 || current_hour >= FORECAST_HOURS_IN_DAY) {
        return FORECAST_MIN_POWER_KW;
    }

    int next_hour = (current_hour + 1) % FORECAST_HOURS_IN_DAY;
    int next_weekday = (current_hour < 23) ? weekday : ((weekday + 1) % FORECAST_DAYS_IN_WEEK);

    float base_curr = active_mean_profile_[weekday][current_hour];
    float base_next = active_mean_profile_[next_weekday][next_hour];

    // Compute residual deviation
    float residual = current_actual_kw - base_curr;

    // Rate-of-change momentum velocity term
    float velocity = getVelocityKw();

    // Combined prediction: Base + Damped Residual + Momentum Boost
    float pred = base_next + (FORECAST_PERSISTENCE_FACTOR * residual) + (FORECAST_MOMENTUM_FACTOR * velocity);

    return (pred > FORECAST_MIN_POWER_KW) ? pred : FORECAST_MIN_POWER_KW;
}

float EdgeForecaster::predictNextHourP95(int weekday, int current_hour, float current_actual_kw) const {
    float p_mean = predictNextHour(weekday, current_hour, current_actual_kw);
    int next_hour = (current_hour + 1) % FORECAST_HOURS_IN_DAY;
    int next_weekday = (current_hour < 23) ? weekday : ((weekday + 1) % FORECAST_DAYS_IN_WEEK);

    float sigma = active_std_profile_[next_weekday][next_hour];
    float p95 = p_mean + (FORECAST_P95_Z_SCORE * sigma);

    return (p95 > FORECAST_MIN_POWER_KW) ? p95 : FORECAST_MIN_POWER_KW;
}

void EdgeForecaster::getDayAheadForecast(int weekday, float output_mean_24h[FORECAST_HOURS_IN_DAY], float output_std_24h[FORECAST_HOURS_IN_DAY]) const {
    if (weekday < 0 || weekday >= FORECAST_DAYS_IN_WEEK || !output_mean_24h) {
        return;
    }
    for (int h = 0; h < FORECAST_HOURS_IN_DAY; ++h) {
        output_mean_24h[h] = active_mean_profile_[weekday][h];
        if (output_std_24h) {
            output_std_24h[h] = active_std_profile_[weekday][h];
        }
    }
}

void EdgeForecaster::recordHourlyPower(int weekday, int hour, float actual_kw) {
    (void)weekday;
    if (hour < 0 || hour >= FORECAST_HOURS_IN_DAY) {
        return;
    }
    daily_recorded_kw_[hour] = (actual_kw > 0.0f) ? actual_kw : FORECAST_MIN_POWER_KW;
    hours_recorded_mask_ |= (1UL << hour);
    updateRecentPowerSample(actual_kw);
}

bool EdgeForecaster::performDailyAdaptation(int completed_weekday) {
    if (completed_weekday < 0 || completed_weekday >= FORECAST_DAYS_IN_WEEK) {
        return false;
    }

    int valid_hours = 0;
    for (int h = 0; h < FORECAST_HOURS_IN_DAY; ++h) {
        if (hours_recorded_mask_ & (1UL << h)) {
            valid_hours++;
        }
    }
    if (valid_hours < 18) {
        hours_recorded_mask_ = 0;
        return false;
    }

    // Dual Exponential Moving Average (EMA) update for Mean and Volatility
    for (int h = 0; h < FORECAST_HOURS_IN_DAY; ++h) {
        if (!(hours_recorded_mask_ & (1UL << h))) {
            continue;
        }

        float old_mean = active_mean_profile_[completed_weekday][h];
        float actual = daily_recorded_kw_[h];
        float init_mean = DEFAULT_WEEKLY_PROFILE[completed_weekday][h];

        // 1. Update Mean profile
        float new_mean = ((1.0f - FORECAST_ALPHA_ADAPTATION_RATE) * old_mean) +
                         (FORECAST_ALPHA_ADAPTATION_RATE * actual);

        float lower_bound = init_mean * FORECAST_MIN_CLAMP_FACTOR;
        if (lower_bound < FORECAST_MIN_POWER_KW) lower_bound = FORECAST_MIN_POWER_KW;
        float upper_bound = init_mean * FORECAST_MAX_CLAMP_FACTOR;
        if (upper_bound < lower_bound + 1.0f) upper_bound = lower_bound + 1.0f;

        if (new_mean < lower_bound) new_mean = lower_bound;
        if (new_mean > upper_bound) new_mean = upper_bound;
        active_mean_profile_[completed_weekday][h] = new_mean;

        // 2. Update Volatility/Standard Deviation profile using Absolute Deviation scaled by sqrt(pi/2) ~ 1.2533
        float old_std = active_std_profile_[completed_weekday][h];
        float deviation = fabsf(actual - new_mean);
        float sample_std_estimate = deviation * 1.2533f;

        float new_std = ((1.0f - FORECAST_ALPHA_STD_RATE) * old_std) +
                        (FORECAST_ALPHA_STD_RATE * sample_std_estimate);

        float init_std = DEFAULT_STD_PROFILE[completed_weekday][h];
        float min_std = init_std * 0.20f;
        if (min_std < 0.20f) min_std = 0.20f;
        float max_std = init_std * 3.50f;
        if (max_std < min_std + 0.5f) max_std = min_std + 0.5f;

        if (new_std < min_std) new_std = min_std;
        if (new_std > max_std) new_std = max_std;
        active_std_profile_[completed_weekday][h] = new_std;
    }

    hours_recorded_mask_ = 0;
    return saveToNVS();
}

bool EdgeForecaster::isProjectedBreach(float predicted_kw, float threshold_kw) const {
    return (predicted_kw > threshold_kw);
}

float EdgeForecaster::getProfileCell(int weekday, int hour) const {
    if (weekday >= 0 && weekday < FORECAST_DAYS_IN_WEEK &&
        hour >= 0 && hour < FORECAST_HOURS_IN_DAY) {
        return active_mean_profile_[weekday][hour];
    }
    return FORECAST_MIN_POWER_KW;
}

float EdgeForecaster::getProfileStdCell(int weekday, int hour) const {
    if (weekday >= 0 && weekday < FORECAST_DAYS_IN_WEEK &&
        hour >= 0 && hour < FORECAST_HOURS_IN_DAY) {
        return active_std_profile_[weekday][hour];
    }
    return 0.50f;
}

void EdgeForecaster::setProfileCell(int weekday, int hour, float mean_kw, float std_kw) {
    if (weekday >= 0 && weekday < FORECAST_DAYS_IN_WEEK &&
        hour >= 0 && hour < FORECAST_HOURS_IN_DAY) {
        active_mean_profile_[weekday][hour] = (mean_kw > FORECAST_MIN_POWER_KW) ? mean_kw : FORECAST_MIN_POWER_KW;
        if (std_kw > 0.0f) {
            active_std_profile_[weekday][hour] = std_kw;
        }
    }
}

bool EdgeForecaster::loadFromNVS() {
#if defined(ARDUINO) || defined(ESP32)
    Preferences prefs;
    if (!prefs.begin("edge_fc", true)) {
        return false;
    }
    size_t sz_mean = sizeof(active_mean_profile_);
    size_t sz_std = sizeof(active_std_profile_);

    size_t r1 = prefs.getBytes("mean_prof", active_mean_profile_, sz_mean);
    size_t r2 = prefs.getBytes("std_prof", active_std_profile_, sz_std);
    prefs.end();

    return (r1 == sz_mean && r2 == sz_std);
#endif
    return false;
}

bool EdgeForecaster::saveToNVS() {
#if defined(ARDUINO) || defined(ESP32)
    Preferences prefs;
    if (!prefs.begin("edge_fc", false)) {
        return false;
    }
    size_t sz_mean = sizeof(active_mean_profile_);
    size_t sz_std = sizeof(active_std_profile_);

    size_t w1 = prefs.putBytes("mean_prof", active_mean_profile_, sz_mean);
    size_t w2 = prefs.putBytes("std_prof", active_std_profile_, sz_std);
    prefs.end();

    return (w1 == sz_mean && w2 == sz_std);
#else
    return true; // Desktop simulation stub
#endif
}
