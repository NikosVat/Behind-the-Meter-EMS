#pragma once

namespace ems {
enum class DeliveryOutcome { DELIVERED, TERMINAL_REJECTION, RETRY };

inline DeliveryOutcome classifyRestDelivery(int status_code) {
    if (status_code >= 200 && status_code < 300) return DeliveryOutcome::DELIVERED;
    // Telemetry endpoint uses 409 exclusively for permanently out-of-order timestamps.
    if (status_code == 409) return DeliveryOutcome::TERMINAL_REJECTION;
    return DeliveryOutcome::RETRY;
}

template <typename Queue, typename Record>
bool retireBufferedRecord(Queue& queue, Record& record, DeliveryOutcome outcome) {
    if (outcome == DeliveryOutcome::RETRY) return false;
    return queue.pop(record);
}
}
