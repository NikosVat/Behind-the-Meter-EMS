/**
 * @file power_calc.h
 * @brief Dual-Mode 3-Phase Electrical Metrology & Trapezoidal Energy Engine.
 *
 * SCIENTIFIC METROLOGY MODES:
 * ----------------------------
 * 1. MODE A: CT-Only Estimation (Standard Non-Invasive Hardware)
 *    - Voltage is unmeasured and set to grid nominal (e.g. 230.0V phase-to-neutral in Greece).
 *    - Current is measured via True RMS: I_RMS = sqrt((1/N) * sum(i[k]^2)).
 *    - Apparent Power is physically computed: S = V_nominal * I_RMS / 1000.0 [kVA].
 *    - Active Power is estimated using a configured baseline power factor: P_est = S * pf_nominal.
 *    - Reactive Power is derived: Q_est = sqrt(max(0, S^2 - P_est^2)) [kVAR].
 *    - Provenance metadata is set to "estimated_nominal_voltage_pf", with is_estimated = true.
 *    - Does NOT claim measured cos phi or measured real power.
 *
 * 2. MODE B: True RMS with Synchronized Voltage Sampling (Calibrated Metrology)
 *    - Instantaneous sampling v(t) and i(t) sampled synchronously over integer 50Hz cycles (N samples).
 *    - True Active Power: P = (1/1000) * (1/N) * sum_{k=0}^{N-1} (v[k] * i[k]) [kW].
 *    - RMS Voltage: V_RMS = sqrt((1/N) * sum_{k=0}^{N-1} (v[k]^2)) [V].
 *    - RMS Current: I_RMS = sqrt((1/N) * sum_{k=0}^{N-1} (i[k]^2)) [A].
 *    - Apparent Power: S = (V_RMS * I_RMS) / 1000.0 [kVA].
 *    - Reactive Power: Q = sqrt(max(0.0, S^2 - P^2)) [kVAR] (Budeanu / IEEE 1459).
 *    - True Power Factor: cos phi = P / S, bounded strictly in [-1.0, 1.0].
 *    - Provenance metadata is set to "meter_measured", with is_estimated = false.
 *
 * 3. Trapezoidal Cumulative Energy Integration:
 *    Delta_E [kWh] = ((P_prev + P_curr) / 2.0) * (delta_t_sec / 3600.0)
 *    E_cumulative [kWh] += Delta_E
 *
 * 4. BACKEND INVARIANT:
 *    | total_active_power_kw - (P_L1 + P_L2 + P_L3) | <= 0.05 kW
 *    Enforced identically by construction: total_active_power_kw is the direct sum.
 */

#pragma once

#include <cstdint>
#include <cstddef>
#include "ct_sampler.h"

namespace ems {

// Metrology operational mode
enum class MetrologyMode : uint8_t {
    MODE_A_CT_ONLY = 0,         // Apparent power accurately measured, active power estimated via baseline PF
    MODE_B_TRUE_RMS = 1         // Instantaneous synchronized V & I sampling: True active, apparent, reactive power & cos phi
};

// Per-phase power results matching backend PhaseReading schema
struct PhasePower {
    float voltage_v;           // Phase RMS voltage [V] (nominal 230.0V in Mode A, measured RMS in Mode B)
    float current_a;           // Phase RMS current [A] (True RMS)
    float active_power_kw;     // Real active power [kW] (estimated in Mode A, true mean(v*i) in Mode B)
    float apparent_power_kva;  // Apparent power [kVA] = V_RMS * I_RMS / 1000.0
    float reactive_power_kvar; // Reactive power [kVAR] = sqrt(max(0, S^2 - P^2))
    float power_factor;        // Phase displacement / true power factor cos phi in [-1.0, 1.0]
    bool is_estimated;         // True if active power / PF is estimated (Mode A)
};

// System 3-phase aggregated telemetry snapshot
struct SystemPowerSnapshot {
    PhasePower l1;
    PhasePower l2;
    PhasePower l3;
    float total_active_power_kw;
    float total_apparent_power_kva;
    float total_reactive_power_kvar;
    float system_power_factor;
    double cumulative_energy_kwh;
    float grid_frequency_hz;
    uint32_t timestamp_epoch;       // Unix timestamp in seconds
    MetrologyMode metrology_mode;    // Active calculation mode
    const char* measurement_method;  // "estimated_nominal_voltage_pf" or "meter_measured"
};

// Instantaneous waveform buffer for Mode B True RMS sampling
struct PhaseWaveform {
    const float* voltage_samples;  // Instantaneous voltage samples in Volts
    const float* current_samples;  // Instantaneous current samples in Amperes
    size_t sample_count;           // Number of samples (e.g. 500 for 10 cycles @ 2.5kHz)
    float voltage_calibration;     // Gain multiplier for voltage
    float current_calibration;     // Gain multiplier for current
};

class PowerCalculator {
public:
    PowerCalculator();

    /**
     * @brief Set operational metrology mode (Mode A CT-Only or Mode B True RMS).
     */
    void setMetrologyMode(MetrologyMode mode);

    /**
     * @brief Get currently configured metrology mode.
     */
    MetrologyMode getMetrologyMode() const { return metrology_mode_; }

    /**
     * @brief Set nominal phase-to-neutral AC voltage (nominal 230.0V in Greece).
     */
    void setNominalVoltage(float v_l1, float v_l2, float v_l3);

    /**
     * @brief Set expected baseline phase displacement power factor (cos phi) for Mode A.
     * @param pf_l1 Power factor for L1 (default 0.95 for commercial load)
     * @param pf_l2 Power factor for L2 (default 0.95)
     * @param pf_l3 Power factor for L3 (default 0.95)
     */
    void setPhasePowerFactors(float pf_l1, float pf_l2, float pf_l3);

    /**
     * @brief Set grid frequency (nominal 50.0 Hz in Greek DEDDIE network).
     */
    void setGridFrequency(float freq_hz);

    /**
     * @brief Set initial cumulative energy (e.g. restored from NVS).
     */
    void setCumulativeEnergy(double initial_kwh);

    /**
     * @brief Mode A / CT-Only update: computes apparent power and estimated active power.
     * @param measurements 3-phase RMS current measurements from CTSampler
     * @param current_millis Current system millis() for delta_t calculation
     * @return SystemPowerSnapshot with measurement_method = "estimated_nominal_voltage_pf"
     */
    SystemPowerSnapshot update(const ThreePhaseMeasurement& measurements, uint32_t current_millis);

    // Fail closed when the selected mode requires voltage/meter inputs unavailable to CTSampler.
    // On failure, preserves the configured mode, output and cumulative energy.
    bool tryUpdateCtOnly(const ThreePhaseMeasurement& measurements, uint32_t current_millis,
                         SystemPowerSnapshot& output);

    /**
     * @brief Mode B update: computes True Active Power, True RMS V and I, S, Q, and true cos phi
     * from synchronously sampled instantaneous voltage and current waveforms.
     * @param l1 Waveform samples for Phase 1
     * @param l2 Waveform samples for Phase 2
     * @param l3 Waveform samples for Phase 3
     * @param current_millis Current system millis() for delta_t calculation
     * @return SystemPowerSnapshot with measurement_method = "meter_measured"
     */
    SystemPowerSnapshot updateSynchronized(const PhaseWaveform& l1,
                                          const PhaseWaveform& l2,
                                          const PhaseWaveform& l3,
                                          uint32_t current_millis);

    /**
     * @brief Update using externally calibrated meter readings (e.g. Modbus or smart meter).
     * @param l1 Calibrated Phase 1 reading
     * @param l2 Calibrated Phase 2 reading
     * @param l3 Calibrated Phase 3 reading
     * @param current_millis Current system millis()
     * @return SystemPowerSnapshot
     */
    SystemPowerSnapshot updateCalibrated(const PhasePower& l1,
                                         const PhasePower& l2,
                                         const PhasePower& l3,
                                         uint32_t current_millis);

    /**
     * @brief Pure calculation function for single-phase RMS from sample array.
     * @param ac_samples Array of zero-mean AC voltage or current samples
     * @param count Number of samples
     * @param calibration_factor Multiplier to convert sample units
     * @return True RMS value
     */
    static float calculateTrueRms(const float* ac_samples, size_t count, float calibration_factor = 1.0f);

    /**
     * @brief Pure calculation function for Mode A single-phase power estimation.
     * @param nominal_voltage_v Grid nominal RMS voltage [V]
     * @param current_rms_a Measured True RMS current [A]
     * @param assumed_power_factor Assumed displacement power factor (cos phi)
     * @return PhasePower with is_estimated = true
     */
    static PhasePower calculateEstimatedPower(float nominal_voltage_v,
                                             float current_rms_a,
                                             float assumed_power_factor = 0.95f);

    /**
     * @brief Pure calculation function for Mode B instantaneous True Active Power, S, Q, and cos phi.
     * @param v_samples Instantaneous voltage samples in Volts
     * @param i_samples Instantaneous current samples in Amperes
     * @param count Number of sample pairs
     * @param v_calibration Calibration gain multiplier for voltage
     * @param i_calibration Calibration gain multiplier for current
     * @return PhasePower with is_estimated = false
     */
    static PhasePower calculateInstantaneousPower(const float* v_samples,
                                                 const float* i_samples,
                                                 size_t count,
                                                 float v_calibration = 1.0f,
                                                 float i_calibration = 1.0f);

    /**
     * @brief Pure numerical trapezoidal integration step.
     * @param p_prev_kw Active power at previous timestep [kW]
     * @param p_curr_kw Active power at current timestep [kW]
     * @param dt_seconds Elapsed time in seconds
     * @return Incremental energy in kWh
     */
    static double trapezoidalIntegrationStep(float p_prev_kw, float p_curr_kw, double dt_seconds);

    // Get current total cumulative energy
    double getCumulativeEnergyKWh() const { return cumulative_energy_kwh_; }

private:
    MetrologyMode metrology_mode_;
    float nominal_voltage_[3];
    float power_factors_[3];
    float grid_frequency_hz_;
    double cumulative_energy_kwh_;
    float last_total_power_kw_;
    uint32_t last_update_millis_;
    bool has_prior_sample_;

    void integrateCumulativeEnergy(float current_total_power_kw, uint32_t current_millis);
};

} // namespace ems
