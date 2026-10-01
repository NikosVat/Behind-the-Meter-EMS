#pragma once
#include <cstdint>
#include <cmath>

namespace ems {
struct CompletedPowerHour {
    int weekday;
    int hour;
    float mean_kw;
    bool day_completed;
};

// Samples belong to their acquisition hour; a boundary sample starts the new bucket.
class HourlyPowerAccumulator {
public:
    void reset() { count_ = 0; sum_kw_ = 0; }
    bool addSample(uint32_t epoch, int hour, int weekday, int tz_hours,
                   float power_kw, CompletedPowerHour& completed) {
        if (!std::isfinite(power_kw) || hour < 0 || hour > 23 || weekday < 0 || weekday > 6) return false;
        const uint32_t bucket = epoch / 3600UL; // Distinguishes repeated local hour at DST end.
        const uint32_t day = (epoch + tz_hours * 3600UL) / 86400UL;
        bool ready = false;
        if (count_ && bucket != bucket_) {
            completed = {weekday_, hour_, static_cast<float>(sum_kw_ / count_), day != day_};
            reset();
            ready = true;
        }
        bucket_ = bucket; day_ = day; hour_ = hour; weekday_ = weekday;
        sum_kw_ += power_kw; ++count_;
        return ready;
    }
private:
    uint32_t bucket_ = 0, day_ = 0, count_ = 0;
    int hour_ = 0, weekday_ = 0;
    double sum_kw_ = 0;
};
}
