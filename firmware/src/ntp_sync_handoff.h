#pragma once
#include <atomic>
#include "rtc_timekeeper.h"

namespace ems {
// SNTP runs on the network task. I2C/NVS writes must run on the main task.
class NtpSyncHandoff {
public:
    void notify(uint32_t epoch) {
        if (epoch >= MIN_VALID_EPOCH) pending_epoch_.store(epoch);
    }
    bool apply(RtcTimekeeper& timekeeper) {
        uint32_t epoch = pending_epoch_.exchange(0);
        if (!epoch) return false;
        timekeeper.syncWithNtp(epoch);
        return true;
    }
private:
    std::atomic<uint32_t> pending_epoch_{0};
};
}
