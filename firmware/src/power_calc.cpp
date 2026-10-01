/**
 * @file power_calc.cpp
 * @brief Implementation of Dual-Mode 3-Phase Electrical Metrology & Trapezoidal Energy Engine.
 */

#include "power_calc.h"
#include <cmath>
#include <algorithm>

namespace ems {

PowerCalculator::PowerCalculator()
    : metrology_mode_(MetrologyMode::MODE_A_CT_ONLY),
      grid_frequency_hz_(50.0f),
      cumulative_energy_kwh_(0.0),
      last_total_power_kw_(0.0f),
      last_update_millis_(0),
      has_prior_sample_(false) {
    // Standard Greek Commercial 230/400V 50Hz supply
    setNominalVoltage(230.0f, 230.0f, 230.0f);
    // Baseline Greek commercial power factor (0.95 inductive default)
    setPhasePowerFactors(0.95f, 0.95f, 0.95f);
}

void PowerCalculator::setMetrologyMode(MetrologyMode mode) {
    metrology_mode_ = mode;
}

void PowerCalculator::setNominalVoltage(float v_l1, float v_l2, float v_l3) {
    nominal_voltage_[0] = (v_l1 > 0.0f) ? v_l1 : 230.0f;
    nominal_voltage_[1] = (v_l2 > 0.0f) ? v_l2 : 230.0f;
    nominal_voltage_[2] = (v_l3 > 0.0f) ? v_l3 : 230.0f;
}

void PowerCalculator::setPhasePowerFactors(float pf_l1, float pf_l2, float pf_l3) {
    power_factors_[0] = std::max(-1.0f, std::min(1.0f, pf_l1));
    power_factors_[1] = std::max(-1.0f, std::min(1.0f, pf_l2));
    power_factors_[2] = std::max(-1.0f, std::min(1.0f, pf_l3));
}

void PowerCalculator::setGridFrequency(float freq_hz) {
    if (freq_hz > 0.0f) {
        grid_frequency_hz_ = freq_hz;
    }
}

void PowerCalculator::setCumulativeEnergy(double initial_kwh) {
    if (initial_kwh >= 0.0) {
        cumulative_energy_kwh_ = initial_kwh;
    }
}

float PowerCalculator::calculateTrueRms(const float* ac_samples, size_t count, float calibration_factor) {
    if (!ac_samples || count == 0) {
        return 0.0f;
    }

    double sum_sq = 0.0;
    for (size_t i = 0; i < count; ++i) {
        sum_sq += static_cast<double>(ac_samples[i]) * static_cast<double>(ac_samples[i]);
    }

    double mean_sq = sum_sq / static_cast<double>(count);
    return static_cast<float>(std::sqrt(mean_sq)) * calibration_factor;
}

PhasePower PowerCalculator::calculateEstimatedPower(float nominal_voltage_v,
                                                   float current_rms_a,
                                                   float assumed_power_factor) {
    PhasePower p;
    p.voltage_v = std::max(0.0f, nominal_voltage_v);
    p.current_a = std::max(0.0f, current_rms_a);
    p.apparent_power_kva = (p.voltage_v * p.current_a) / 1000.0f;
    p.power_factor = std::max(-1.0f, std::min(1.0f, assumed_power_factor));
    p.active_power_kw = p.apparent_power_kva * p.power_factor;

    // Q = sqrt(max(0, S^2 - P^2))
    float s2 = p.apparent_power_kva * p.apparent_power_kva;
    float p2 = p.active_power_kw * p.active_power_kw;
    p.reactive_power_kvar = std::sqrt(std::max(0.0f, s2 - p2));
    p.is_estimated = true;

    return p;
}

PhasePower PowerCalculator::calculateInstantaneousPower(const float* v_samples,
                                                       const float* i_samples,
                                                       size_t count,
                                                       float v_calibration,
                                                       float i_calibration) {
    PhasePower p;
    if (!v_samples || !i_samples || count == 0) {
        p.voltage_v = 0.0f;
        p.current_a = 0.0f;
        p.active_power_kw = 0.0f;
        p.apparent_power_kva = 0.0f;
        p.reactive_power_kvar = 0.0f;
        p.power_factor = 1.0f;
        p.is_estimated = false;
        return p;
    }

    double sum_v_sq = 0.0;
    double sum_i_sq = 0.0;
    double sum_instant_p = 0.0;

    for (size_t k = 0; k < count; ++k) {
        double v = static_cast<double>(v_samples[k]) * static_cast<double>(v_calibration);
        double i = static_cast<double>(i_samples[k]) * static_cast<double>(i_calibration);

        if (!std::isfinite(v) || !std::isfinite(i)) {
            continue;
        }

        sum_v_sq += v * v;
        sum_i_sq += i * i;
        sum_instant_p += (v * i);
    }

    double mean_v_sq = sum_v_sq / static_cast<double>(count);
    double mean_i_sq = sum_i_sq / static_cast<double>(count);
    double mean_p_w = sum_instant_p / static_cast<double>(count); // Mean instantaneous active power [Watts]

    p.voltage_v = static_cast<float>(std::sqrt(mean_v_sq));
    p.current_a = static_cast<float>(std::sqrt(mean_i_sq));
    p.apparent_power_kva = (p.voltage_v * p.current_a) / 1000.0f;
    p.active_power_kw = static_cast<float>(mean_p_w / 1000.0);

    // Safeguard against floating point rounding: |P| <= S
    if (p.active_power_kw > p.apparent_power_kva) {
        p.active_power_kw = p.apparent_power_kva;
    } else if (p.active_power_kw < -p.apparent_power_kva) {
        p.active_power_kw = -p.apparent_power_kva;
    }

    // Reactive Power: Q = sqrt(max(0, S^2 - P^2))
    float s2 = p.apparent_power_kva * p.apparent_power_kva;
    float p2 = p.active_power_kw * p.active_power_kw;
    p.reactive_power_kvar = std::sqrt(std::max(0.0f, s2 - p2));

    // True Power Factor: cos phi = P / S
    if (p.apparent_power_kva > 0.001f) {
        float raw_pf = p.active_power_kw / p.apparent_power_kva;
        p.power_factor = std::max(-1.0f, std::min(1.0f, raw_pf));
    } else {
        p.power_factor = 1.0f;
    }

    p.is_estimated = false;
    return p;
}

double PowerCalculator::trapezoidalIntegrationStep(float p_prev_kw, float p_curr_kw, double dt_seconds) {
    if (dt_seconds <= 0.0) {
        return 0.0;
    }
    double avg_power_kw = (static_cast<double>(p_prev_kw) + static_cast<double>(p_curr_kw)) / 2.0;
    return avg_power_kw * (dt_seconds / 3600.0);
}

void PowerCalculator::integrateCumulativeEnergy(float current_total_power_kw, uint32_t current_millis) {
    if (has_prior_sample_) {
        uint32_t dt_ms = current_millis - last_update_millis_;
        double dt_sec = static_cast<double>(dt_ms) / 1000.0;

        // Discard absurdly large dt (e.g. reboot or sleep > 1 hour)
        if (dt_sec > 0.0 && dt_sec < 3600.0) {
            double delta_kwh = trapezoidalIntegrationStep(last_total_power_kw_, current_total_power_kw, dt_sec);
            cumulative_energy_kwh_ += delta_kwh;
        }
    } else {
        has_prior_sample_ = true;
    }

    last_total_power_kw_ = current_total_power_kw;
    last_update_millis_ = current_millis;
}

bool PowerCalculator::tryUpdateCtOnly(const ThreePhaseMeasurement& measurements, uint32_t current_millis,
                                     SystemPowerSnapshot& output) {
    if (metrology_mode_ != MetrologyMode::MODE_A_CT_ONLY) return false;
    output = update(measurements, current_millis);
    return true;
}

SystemPowerSnapshot PowerCalculator::update(const ThreePhaseMeasurement& measurements, uint32_t current_millis) {
    metrology_mode_ = MetrologyMode::MODE_A_CT_ONLY;
    SystemPowerSnapshot snap;
    snap.grid_frequency_hz = grid_frequency_hz_;
    snap.timestamp_epoch = 0;
    snap.metrology_mode = MetrologyMode::MODE_A_CT_ONLY;
    snap.measurement_method = "estimated_nominal_voltage_pf";

    // Mode A: CT-only estimation with nominal voltages and baseline power factors
    snap.l1 = calculateEstimatedPower(nominal_voltage_[0], measurements.l1.current_rms_a, power_factors_[0]);
    snap.l2 = calculateEstimatedPower(nominal_voltage_[1], measurements.l2.current_rms_a, power_factors_[1]);
    snap.l3 = calculateEstimatedPower(nominal_voltage_[2], measurements.l3.current_rms_a, power_factors_[2]);

    // Direct sum ensures strict backend conservation invariant
    snap.total_active_power_kw = snap.l1.active_power_kw + snap.l2.active_power_kw + snap.l3.active_power_kw;
    snap.total_apparent_power_kva = snap.l1.apparent_power_kva + snap.l2.apparent_power_kva + snap.l3.apparent_power_kva;
    snap.total_reactive_power_kvar = snap.l1.reactive_power_kvar + snap.l2.reactive_power_kvar + snap.l3.reactive_power_kvar;

    if (snap.total_apparent_power_kva > 0.001f) {
        float raw_sys_pf = snap.total_active_power_kw / snap.total_apparent_power_kva;
        snap.system_power_factor = std::max(-1.0f, std::min(1.0f, raw_sys_pf));
    } else {
        snap.system_power_factor = 1.0f;
    }

    integrateCumulativeEnergy(snap.total_active_power_kw, current_millis);
    snap.cumulative_energy_kwh = cumulative_energy_kwh_;

    return snap;
}

SystemPowerSnapshot PowerCalculator::updateSynchronized(const PhaseWaveform& l1,
                                                      const PhaseWaveform& l2,
                                                      const PhaseWaveform& l3,
                                                      uint32_t current_millis) {
    metrology_mode_ = MetrologyMode::MODE_B_TRUE_RMS;
    SystemPowerSnapshot snap;
    snap.grid_frequency_hz = grid_frequency_hz_;
    snap.timestamp_epoch = 0;
    snap.metrology_mode = MetrologyMode::MODE_B_TRUE_RMS;
    snap.measurement_method = "meter_measured";

    // Mode B: True instantaneous v(t)*i(t) calculation for each phase
    snap.l1 = calculateInstantaneousPower(l1.voltage_samples, l1.current_samples, l1.sample_count,
                                         l1.voltage_calibration, l1.current_calibration);
    snap.l2 = calculateInstantaneousPower(l2.voltage_samples, l2.current_samples, l2.sample_count,
                                         l2.voltage_calibration, l2.current_calibration);
    snap.l3 = calculateInstantaneousPower(l3.voltage_samples, l3.current_samples, l3.sample_count,
                                         l3.voltage_calibration, l3.current_calibration);

    snap.total_active_power_kw = snap.l1.active_power_kw + snap.l2.active_power_kw + snap.l3.active_power_kw;
    snap.total_apparent_power_kva = snap.l1.apparent_power_kva + snap.l2.apparent_power_kva + snap.l3.apparent_power_kva;
    snap.total_reactive_power_kvar = snap.l1.reactive_power_kvar + snap.l2.reactive_power_kvar + snap.l3.reactive_power_kvar;

    if (snap.total_apparent_power_kva > 0.001f) {
        float raw_sys_pf = snap.total_active_power_kw / snap.total_apparent_power_kva;
        snap.system_power_factor = std::max(-1.0f, std::min(1.0f, raw_sys_pf));
    } else {
        snap.system_power_factor = 1.0f;
    }

    integrateCumulativeEnergy(snap.total_active_power_kw, current_millis);
    snap.cumulative_energy_kwh = cumulative_energy_kwh_;

    return snap;
}

SystemPowerSnapshot PowerCalculator::updateCalibrated(const PhasePower& l1,
                                                     const PhasePower& l2,
                                                     const PhasePower& l3,
                                                     uint32_t current_millis) {
    metrology_mode_ = MetrologyMode::MODE_B_TRUE_RMS;
    SystemPowerSnapshot snap;
    snap.grid_frequency_hz = grid_frequency_hz_;
    snap.timestamp_epoch = 0;
    snap.metrology_mode = MetrologyMode::MODE_B_TRUE_RMS;
    snap.measurement_method = "meter_measured";

    snap.l1 = l1;
    snap.l2 = l2;
    snap.l3 = l3;

    snap.total_active_power_kw = snap.l1.active_power_kw + snap.l2.active_power_kw + snap.l3.active_power_kw;
    snap.total_apparent_power_kva = snap.l1.apparent_power_kva + snap.l2.apparent_power_kva + snap.l3.apparent_power_kva;
    snap.total_reactive_power_kvar = snap.l1.reactive_power_kvar + snap.l2.reactive_power_kvar + snap.l3.reactive_power_kvar;

    if (snap.total_apparent_power_kva > 0.001f) {
        float raw_sys_pf = snap.total_active_power_kw / snap.total_apparent_power_kva;
        snap.system_power_factor = std::max(-1.0f, std::min(1.0f, raw_sys_pf));
    } else {
        snap.system_power_factor = 1.0f;
    }

    integrateCumulativeEnergy(snap.total_active_power_kw, current_millis);
    snap.cumulative_energy_kwh = cumulative_energy_kwh_;

    return snap;
}

} // namespace ems
