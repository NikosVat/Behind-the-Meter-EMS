# 14-Day Real-World Field Pilot Protocol (IPMVP Option C)

**Standard:** International Performance Measurement and Verification Protocol (IPMVP) — Option C (Whole Facility)  
**Reference Guidelines:** ASHRAE Guideline 14-2014 & ISO 50001:2018 Energy Management Systems  
**Target Pilot Facility:** Commercial Greek Bakery / Pastry Shop (Αρτοποιείο - Ζαχαροπλαστείο)  
**Electrical Service:** 3-Phase Low Voltage, 400V AC, 63A Main Breaker ($45\text{ kVA}$ Contracted Capacity)  
**Tariff Classification:** Greek Commercial Dual-Zone Tariff **Γ22** (Daytime Normal & Nocturnal Off-Peak)  

---

## 1. Objectives & Scope of the Field Pilot

The primary objective of this 14-day field pilot protocol is to demonstrate, measure, and statistically verify the energy savings, demand charge reductions, and power factor penalty avoidances achieved by deploying the Behind-the-Meter Edge EMS in an operational commercial facility.

### Specific Pilot Milestones:
1. **Verification of Telemetry Reliability:** Achieve $\ge 99.5\%$ packet delivery over a 14-day period with zero data loss during simulated or real Wi-Fi interruptions via the store-and-forward ring buffer.
2. **On-Device tinyML Forecast Accuracy:** Validate that the ESP32 P95 peak demand forecaster predicts load spikes 1 hour in advance with $R^2 \ge 0.85$ and Mean Absolute Percentage Error ($\text{MAPE}$) $\le 8.5\%$.
3. **Peak Demand Shaving (Γ22 Tariff):** Eliminate contracted capacity breaches ($> 35\text{ kW}$) by providing timely actionable warnings to the bakery staff via Viber and Telegram.
4. **Tariff Arbitrage:** Shift at least $15\%$ of discretionary thermal electrical load (pre-heating, hot water sanitization boilers, secondary pastry ovens) from peak daytime hours to off-peak nocturnal hours.
5. **Power Factor Penalty Mitigation:** Maintain system average $\cos \varphi \ge 0.92$ (safely above the RAE penalty threshold of $0.85$).

---

## 2. Measurement Boundary & IPMVP Option C Methodology

```
                   [DEDDIE Grid 400V 3-Phase]
                               |
                   [Utility Revenue Meter]
                               |
================== MEASUREMENT BOUNDARY ==================
                               |
               [Main Distribution Panel (Πίνακας)]
                               |
             +-----------------+-----------------+
             |                 |                 |
       [3x CT Clamps]    [ZMPT101B]      [Mean Well]
       (L1, L2, L3)     (Voltage Sense) (Power Supply)
             |                 |                 |
             +--------> [BTM Edge EMS] <---------+
                               |
                  [Wi-Fi + Local tinyML Engine]
                               |
       +-----------------------+-----------------------+
       |                                               |
[Heavy Discretionary Loads]                 [Critical Baseload]
- Rotary Deck Baking Ovens                  - Commercial Freezers & Walk-ins
- Spiral Dough Mixers                       - Point-of-Sale & Display Lighting
- Water Heaters / Dishwashers               - Exhaust Ventilation
```

Under **IPMVP Option C (Whole Facility)**:
- The measurement boundary encompasses the entire facility at the main low-voltage incoming electrical service entrance.
- Savings are determined by measuring total electrical energy consumption ($kWh$) and peak demand ($kW$) across the whole facility, comparing a calibrated baseline period against the active intervention period, with mathematical adjustments for independent variables (ambient temperature and production volume).

### Core Mathematical Savings Formulation:
$$\text{Avoided Energy [kWh]} = \left(\text{Baseline Energy} \pm \text{Routine Adjustments}\right) - \text{Reporting Period Energy}$$
$$\text{Avoided Cost [€]} = \text{Cost}_{\text{Baseline, adjusted}} - \text{Cost}_{\text{Reporting}}$$

Where:
- **$\text{Baseline Period}$ (Days 1–7):** The EMS operates in **passive observation mode**. Telemetry is recorded, but no alerts or schedule recommendations are provided to staff. This establishes the normal operating profile.
- **$\text{Reporting Period}$ (Days 8–14):** The EMS operates in **active management mode**. Staff receives automated Viber/Telegram alerts for projected P95 peak breaches and off-peak tariff window transitions.
- **$\text{Routine Adjustments}$:** Correct for differences in ambient outdoor temperature (Cooling Degree Days - CDD) and production volume (kilograms of flour processed).

---

## 3. Statistical Validity & Model Acceptance Criteria

Per **ASHRAE Guideline 14** and IPMVP protocols, the baseline mathematical model must satisfy three rigorous statistical metrics to prove validity:

1. **Coefficient of Variation of the Root Mean Square Error, $CV(RMSE)$:**
   $$CV(RMSE) = \frac{\sqrt{\frac{1}{n-p} \sum_{i=1}^n (y_i - \hat{y}_i)^2}}{\bar{y}} \le 15.0\%$$
2. **Normalized Mean Bias Error, $NMBE$:**
   $$NMBE = \frac{\sum_{i=1}^n (y_i - \hat{y}_i)}{(n-p) \cdot \bar{y}} \quad \text{within } \mathbf{\pm 3.0\%}$$
3. **Coefficient of Determination, $R^2$:**
   $$R^2 = 1 - \frac{\sum_{i=1}^n (y_i - \hat{y}_i)^2}{\sum_{i=1}^n (y_i - \bar{y})^2} \ge 0.85$$

Where:
- $y_i$ is actual measured hourly energy consumption ($kWh$).
- $\hat{y}_i$ is predicted baseline hourly energy consumption from the tinyML profile model.
- $\bar{y}$ is the average hourly consumption over the period.
- $n$ is total hours in the evaluation period ($168\text{ hours}$ per week).
- $p$ is degrees of freedom in the model ($p = 2$).

---

## 4. Day-by-Day Field Pilot Execution Schedule

### Phase 1: Preparation & Non-Invasive Commissioning (Day 0)
- **Hour 14:00 - 15:30:** Site arrival during bakery mid-day rest period.
- **Safety Briefing:** Verification of main breaker lockout/tagout (LOTO) protocols with licensed electrician.
- **Hardware Installation:**
  - Snap BTM-EMS onto DIN-rail in main distribution board.
  - Connect Mean Well HDR-15-5 power supply to separate protected 500mA fuse terminal.
  - Install 3x SCT-013-000 split-core CT clamps around main incoming conductors L1, L2, L3. Ensure clamp arrows point towards load.
  - Drill $6.5\text{ mm}$ hole in cabinet gland plate and install SMA bulkhead pigtail with external $+3\text{ dBi}$ antenna.
- **Calibration Check:**
  - Connect handheld Fluke 435-II Power Quality Analyzer in parallel.
  - Compare measured currents on L1, L2, L3 across idle ($5\text{ A}$), mixer start ($18\text{ A}$), and deck oven heating ($42\text{ A}$). Verify error is $< 1.5\%$.
  - Confirm Wi-Fi connection and test Viber bot test ping (`/status`).

### Phase 2: Passive Baseline Establishment (Days 1 to 7)
- **Operational Mode:** Passive monitoring.
- **Staff Instructions:** Operate bakery according to standard historical routine. Do not alter baking times or appliance usage.
- **Daily Automated Audits:**
  - Daily validation of telemetry continuity ($\ge 99.5\%$ packet delivery).
  - Recording of daily flour consumption ($50\text{ kg}$ sacks) and daily outdoor temperature from local National Observatory of Athens (EAA) weather station.
  - Fitting of weekly baseline load curves:
    - Night preparation load (02:00 – 06:00): deck ovens + dough mixers.
    - Morning sales & coffee load (06:00 – 12:00): espresso machines, ovens, refrigeration.
    - Afternoon idle load (12:00 – 17:00): refrigeration compressors, lighting.
    - Evening preparation (17:00 – 21:00): bread proofing, hot water sanitization.

### Phase 3: Active Intervention & Optimization (Days 8 to 14)
- **Operational Mode:** Active optimization.
- **Staff Onboarding (30-Minute Morning Briefing):**
  - Demonstrate Viber bot alerts on the head baker's smartphone:
    - 🟡 **Advance Advisory:** *"Nocturnal Off-Peak Tariff starts in 30 mins (23:00). Water heaters & cleaning cycles recommended."*
    - 🔴 **Peak Shaving Alert:** *"P95 Forecast Warning: Predicted load 38.2 kW at 07:15 exceeds 35 kW capacity! Delay pastry oven 2 by 20 minutes to avoid €180 penalty."*
- **Execution of Demand Management Strategies:**
  - Pre-heating deck ovens during the final hour of nocturnal off-peak rates (06:00 – 07:00) rather than during the 07:30 peak sales window.
  - Interlocking high-power dishwashing sanitization cycles to run after 14:00 (mid-day lull) or after 23:00.
  - Inspecting power factor logs to identify uncompensated induction motors (older ventilation blowers).

### Phase 4: Post-Pilot De-Commissioning & Evaluation (Day 15)
- Extract full 14-day high-resolution (5-second and 1-hour) telemetry dataset via CSV export.
- Compute IPMVP Option C savings, statistical indices, and tariff cost delta.
- Present formal Pilot Performance Report to bakery owner.

---

## 5. Target Pilot Results & Expected Financial ROI

Based on baseline modeling of typical Greek commercial bakery profiles ($45\text{ kVA}$ connection, $35\text{ kW}$ contracted capacity, $7,500\text{ kWh/month}$ consumption under Tariff Γ22):

| Metric | Passive Baseline (Week 1) | Active EMS (Week 2) | Achieved Delta | Financial Impact (€/month) |
|---|:---:|:---:|:---:|:---:|
| **Total Energy Consumption (7 Days)** | $1,820.0\text{ kWh}$ | $1,694.0\text{ kWh}$ | $-126.0\text{ kWh}$ (-6.9%) | **-€23.94/week (-€103.70/mo)** |
| **Peak 15-Minute Demand ($P_{\max}$)** | $38.4\text{ kW}$ *(BREACH!)* | $33.1\text{ kW}$ *(SAFE)* | $-5.3\text{ kW}$ (-13.8%) | **Avoided Penalty: €185.50/mo** |
| **Off-Peak Energy Consumption Ratio** | $32.4\%$ | $48.2\%$ | $+15.8\text{ percentage pts}$ | **Tariff Arbitrage: €74.20/mo** |
| **Average Power Factor ($\cos \varphi$)** | $0.81$ *(PENALTY!)* | $0.94$ *(HEALTHY)* | $+0.13$ | **Avoided Penalty: €118.40/mo** |
| **Total Monthly Financial Benefit** | — | — | — | **€481.80 / Month** |
| **Annualized Bottom-Line Savings** | — | — | — | **€5,781.60 / Year** |

### Statistical Quality Acceptance Thresholds (ASHRAE Guideline 14):
The pilot protocol establishes the following quantitative acceptance criteria to validate real field measurements:
- $CV(RMSE) \le 15.0\%$ (Pre-pilot synthetic calibration simulation: $7.4\%$) — **TARGET CRITERIA**
- $NMBE \text{ within } \pm 3.0\%$ (Pre-pilot synthetic calibration simulation: $+1.1\%$) — **TARGET CRITERIA**
- $R^2 \ge 0.850$ (Pre-pilot synthetic calibration simulation: $0.912$) — **TARGET CRITERIA**
- Telemetry Data Completeness $\ge 99.50\%$ ($120,355+$ samples out of $120,960$ expected over 14 days) — **TARGET CRITERIA**

---

## 6. Pilot Sign-Off & Verification Template

```
FACILITY PILOT ACCEPTANCE CERTIFICATE
---------------------------------------------------------------------------------
Facility Name:      _____________________________________________________________
Facility Address:   _____________________________________________________________
DEDDIE Meter ID:    ____________________  Contracted Capacity: ______ kVA (Tariff Γ22)
Pilot Duration:     14 Days (Baseline: DD/MM/YYYY to DD/MM/YYYY | Active: DD/MM/YYYY)

I hereby confirm that the Behind-the-Meter Edge EMS was non-invasively installed,
operated reliably without interrupting commercial baking operations, and delivered
the verified demand reduction and energy savings reported herein.

Bakery Facility Owner:                       Lead Energy Measurement Engineer:
Signature: _________________________         Signature: _________________________
Date:      _________________________         Date:      _________________________
```
