# Hardware Wiring Schematic & Sensor Interfacing Guide

**Project:** Behind-the-Meter Energy Management System (EMS) for Greek Commercial SMBs  
**Authoritative Standard:** ELOT 60364 / HD 384 Electrical Installations  
**Microcontroller Target:** ESP32-WROOM-32 (12-bit SAR ADC, 3.3V Logic)  
**Sensors:** Split-Core Current Transformers (YHDC SCT-013 Series)  

---

## 1. Executive Hardware Overview

The Behind-the-Meter EMS monitors 3-phase Low Voltage (230V/400V 50Hz) commercial electrical supplies (e.g., Bakeries, Cold Storage Logistics, Boutique Hotels) non-invasively using split-core current transformers (CT clamps). The secondary current signals are converted into proportional AC voltages using low-tolerance burden resistors, elevated to a stable 1.65V DC midpoint virtual ground, and sampled by the ESP32's internal 12-bit Analog-to-Digital Converter.

```
       +-------------------------------------------------------------------------+
       |                           PHYSICAL POWER MAINS                          |
       |  Phase L1 (230V~ 50Hz) --------[ SCT-013 Clamp ]---------> Consumer     |
       |  Phase L2 (230V~ 50Hz) --------[ SCT-013 Clamp ]---------> Consumer     |
       |  Phase L3 (230V~ 50Hz) --------[ SCT-013 Clamp ]---------> Consumer     |
       |  Neutral  (0V)         ----------------------------------> Consumer     |
       +-------------------------------------------------------------------------+
                                         |      |      |
                                         | (3.5mm Stereo Cables)
                                         v      v      v
       +-------------------------------------------------------------------------+
       |                      ANALOG FRONT-END CONDITIONING                      |
       |  • Precision Burden Resistors: 18Ω (3.3V ADC) / 22Ω (5V ADC)           |
       |  • DC Bias Virtual Midpoint: 1.65V (2x 10kΩ/100kΩ divider + 10μF cap)   |
       |  • TVS Surge Suppression Diode Clamping (Internal to SCT-013)          |
       +-------------------------------------------------------------------------+
                                         |      |      |
                                         v      v      v
       +-------------------------------------------------------------------------+
       |                         ESP32 ADC1 INPUT PINS                           |
       |  Phase L1 -------------------> GPIO 34 (ADC1_CH6)                       |
       |  Phase L2 -------------------> GPIO 35 (ADC1_CH7)                       |
       |  Phase L3 -------------------> GPIO 32 (ADC1_CH4)                       |
       |  ADC2 PINS (GPIO 0,2,4,12-15,25-27) --> RESTRICTED (Used by Wi-Fi RF)   |
       +-------------------------------------------------------------------------+
```

---

## 2. CT Sensor Comparison: SCT-013-000 vs SCT-013-030

| Specification | SCT-013-000 (Recommended) | SCT-013-030 |
|---|---|---|
| **Primary Current Rating** | 0 to 100 A RMS | 0 to 30 A RMS |
| **Turns Ratio ($N_p : N_s$)** | $1 : 2000$ | Integrated internal burden |
| **Output Type** | Current Output ($50\text{ mA RMS}$ at $100\text{ A}$) | Voltage Output ($1.0\text{ V RMS}$ at $30\text{ A}$) |
| **External Burden Resistor** | **MANDATORY** ($18\,\Omega$ for 3.3V ADC) | **FORBIDDEN** (Pre-installed internal resistor) |
| **Internal Burden Resistance** | None ($0\,\Omega$) | Built-in internal resistor ($\approx 66.7\,\Omega$) |
| **Full-Scale Voltage Output** | Tailored by burden ($V_{peak} \le 1.273\text{ V}$) | $1.0\text{ V RMS} \implies 1.414\text{ V peak}$ |
| **Suitability for Greek SMBs** | Ideal for $\le 70\text{ kVA}$ connections (Γ21, Γ22) | Limited to small single-phase loads ($\le 6.9\text{ kVA}$) |

> **CRITICAL DISTINCTION:**  
> Never connect an external burden resistor to an **SCT-013-030**. Doing so places two burden resistors in parallel, dividing the effective resistance and corrupting current readings by up to 80%.

---

## 3. Burden Resistor Sizing & Physics Derivation

Current transformers act as constant-current sources whose secondary current $I_s$ is strictly proportional to the primary conductor current $I_p$ divided by the turns ratio $N = 2000$:

$$I_s = \frac{I_p}{N} = \frac{I_p}{2000}$$

For rated primary RMS current $I_{p,RMS} = 100\text{ A}$:

$$I_{s,RMS} = \frac{100\text{ A}}{2000} = 0.050\text{ A} = 50\text{ mA}$$

Peak secondary current:

$$I_{s,peak} = I_{s,RMS} \times \sqrt{2} = 0.050\text{ A} \times 1.41421 \approx 0.07071\text{ A} = 70.71\text{ mA}$$

### 3.1 3.3V ESP32 ADC Sizing (Recommended: $18\,\Omega$)

1. **ADC Voltage Limits:**  
   The ESP32 ADC operates from $0.0\text{ V}$ to $3.3\text{ V}$. Biasing the input to the virtual midpoint $V_{mid} = 1.65\text{ V}$ allows symmetrical bipolar AC voltage swings. To stay within the ADC's linear range ($0.15\text{ V} \le V_{in} \le 3.10\text{ V}$), the maximum allowable peak swing is:
   
   $$V_{peak,max} \le 1.45\text{ V}$$

2. **Theoretical Maximum Burden Resistance:**
   
   $$R_{burden,max} = \frac{V_{peak,max}}{I_{s,peak}} = \frac{1.45\text{ V}}{0.07071\text{ A}} \approx 20.50\,\Omega$$

3. **Standard Resistor Selection ($18\,\Omega$ 1% Metal Film):**
   - Peak secondary voltage at $100\text{ A RMS}$:
     $$V_{peak} = I_{s,peak} \times R_{burden} = 0.07071\text{ A} \times 18\,\Omega = 1.2728\text{ V}$$
   - Peak-to-peak voltage swing:
     $$V_{pp} = 2 \times V_{peak} = 2.5456\text{ V}$$
   - Input voltage range at ESP32 pin:
     $$V_{in} \in [1.65\text{ V} - 1.2728\text{ V},\; 1.65\text{ V} + 1.2728\text{ V}] = [0.377\text{ V},\; 2.923\text{ V}]$$
     *This fits cleanly inside the $[0.15\text{ V}, 3.10\text{ V}]$ linear conversion window.*
   - **Overcurrent Headroom:** Clips only when $I_{p,RMS} \ge 113.9\text{ A}$ ($+13.9\%$ safety margin).
   - **Current Calibration Factor ($K_I$):**
     $$K_I = \frac{N}{R_{burden}} = \frac{2000}{18\,\Omega} \approx 111.111\text{ A/V} = 0.11111\text{ A/mV}$$

### 3.2 5V External ADC Sizing (Alternative: $22\,\Omega$)

When interfacing to a 5.0V ADC (e.g., ADS1115 or Arduino Uno powered at 5V, virtual midpoint at 2.50V):
- Standard selection: $22\,\Omega$ 1% metal film resistor.
- Peak voltage swing:
  $$V_{peak} = 0.07071\text{ A} \times 22\,\Omega \approx 1.5556\text{ V} \implies V_{pp} = 3.1112\text{ V}$$
- **Current Calibration Factor ($K_I$):**
  $$K_I = \frac{N}{R_{burden}} = \frac{2000}{22\,\Omega} \approx 90.909\text{ A/V} = 0.09091\text{ A/mV}$$

### 3.3 Burden Resistor Power Dissipation

At rated continuous $100\text{ A RMS}$ primary load:

$$P_{burden} = (I_{s,RMS})^2 \times R_{burden} = (0.050\text{ A})^2 \times 22\,\Omega = 0.055\text{ W} = 55\text{ mW}$$

Standard $0.25\text{ W}$ (1/4W) metal film resistors operate at only **$22\%$ of rated thermal capacity**, guaranteeing long-term drift-free accuracy and negligible temperature coefficient deviation.

---

## 4. DC Midpoint Virtual Ground Biasing Circuit

Because current transformers generate an alternating current (AC) signal that swings symmetrically between positive and negative potentials, connecting a CT secondary directly to an ESP32 ADC pin would expose the microcontroller to negative voltages ($-1.27\text{ V}$), destroying the silicon substrate protection diodes.

To safely sample the full AC waveform:
1. Two matched $10\text{ k}\Omega$ (or $100\text{ k}\Omega$) 1% metal film resistors form a precision voltage divider between 3.3V and GND, establishing a virtual ground at $V_{mid} = 1.65\text{ V}$.
2. A $10\,\mu\text{F}$ low-ESR electrolytic or tantalum capacitor is placed between the 1.65V midpoint and GND to provide an ultra-low impedance AC return path for 50Hz currents.
3. The CT secondary negative lead is connected to this 1.65V virtual ground, while the positive lead connects to the burden resistor and the ESP32 ADC input pin.

---

## 5. ESP32 ADC1 Pin Allocation & Wi-Fi ADC2 Conflict

The ESP32 SoC incorporates two independent 12-bit Successive Approximation Register (SAR) ADCs:

| ADC Controller | Available GPIO Pins | Wi-Fi Compatibility | Assignment |
|---|---|---|---|
| **ADC1** | GPIO 32, 33, 34, 35, 36, 39 | **100% Compatible** (No conflict) | **MANDATORY for CT Clamps** |
| **ADC2** | GPIO 0, 2, 4, 12, 13, 14, 15, 25, 26, 27 | **INCOMPATIBLE with Wi-Fi** | **STRICTLY PROHIBITED** |

> **CRITICAL HARDWARE WARNING:**  
> The ESP32's Wi-Fi and Bluetooth physical layer (RF transceiver) uses **ADC2** internally for continuous power management, AGC calibration, and RF RSSI measurements.  
> Attempting to call `analogRead()` on any ADC2 pin while Wi-Fi is initialized will return arbitrary erroneous values (`0` or `4095`), cause Wi-Fi packet drops, or crash the FreeRTOS TCP/IP stack.

### Approved Phase-to-Pin Mappings (ADC1 Only):
- **Phase L1 (CT 1):** GPIO 34 (`ADC1_CH6`) — Input-only pin, no internal pull-up/down resistors.
- **Phase L2 (CT 2):** GPIO 35 (`ADC1_CH7`) — Input-only pin, no internal pull-up/down resistors.
- **Phase L3 (CT 3):** GPIO 32 (`ADC1_CH4`) — High-impedance ADC1 analog input.
- *(Optional Reference):* GPIO 33 (`ADC1_CH5`) — Analog voltage midpoint sampling if hardware calibration is desired.

---

## 6. Complete ASCII Circuit Schematic

```
3.3V DC Rail
  +-------------------------------------------------------------------------------+
  |                                                                               |
  |   +------------------+                                                        |
  |   |                  |                                                        |
 [R1] 10kΩ 1%           [R3] 10kΩ 1%                                             |
  |   (Divider 1)        |   (Optional Divider 2)                                 |
  |                      |                                                        |
  +--------*-------------+---------*------------------------*                     |
  |        |                       |                        |                     |
 [C1]     [C2]                    [R_b1]                   [R_b2]                 |
 10μF     0.1μF                   18Ω 1%                   18Ω 1%                 |
 Low-ESR  Ceramic                 Burden L1                Burden L2              |
  |        |                       |                        |                     |
 GND      GND                      |                        |                     |
  |        |                       |                        |                     |
  |        |    1.65V Virtual Midpoint Reference Bus        |                     |
  |        +=======================*========================*==========+          |
  |                                |                        |          |          |
  |                                |                        |         [R_b3]      |
  |                                |                        |         18Ω 1%      |
  |                                |                        |         Burden L3   |
  |                                |                        |          |          |
  |            3.5mm Jack L1       |    3.5mm Jack L2       |          |          |
  |           +-------------+      |   +-------------+      |          |          |
  |           | SLEEVE (S)  |<-----+   | SLEEVE (S)  |<-----+          |          |
  |           |             |          |             |                 |          |
  |           | TIP (T)     |----+     | TIP (T)     |----+            |          |
  |           +-------------+    |     +-------------+    |            |          |
  |                  ^           |            ^           |            |          |
  |                  |           |            |           |            |          |
  |            [SCT-013 L1]      |      [SCT-013 L2]      |            |          |
  |            Phase 1 Main      |      Phase 2 Main      |            |          |
  |                              |                        |            |          |
  |                              |   3.5mm Jack L3        |            |          |
  |                              |  +-------------+       |            |          |
  |                              |  | SLEEVE (S)  |<-------------------+          |
  |                              |  |             |       |                       |
  |                              |  | TIP (T)     |--------------------+          |
  |                              |  +-------------+       |            |          |
  |                              |         ^              |            |          |
  |                              |         |              |            |          |
  |                              |   [SCT-013 L3]         |            |          |
  |                              |   Phase 3 Main         |            |          |
  |                              |                        |            |          |
  |                              v                        v            v          |
  |                         +-----------------------------------------------+     |
  |                         |               ESP32-WROOM-32                  |     |
  |                         |                                               |     |
  |                         |   GPIO 34 (ADC1_CH6) <--- Phase L1 Signal     |     |
  |                         |   GPIO 35 (ADC1_CH7) <--- Phase L2 Signal     |     |
  |                         |   GPIO 32 (ADC1_CH4) <--- Phase L3 Signal     |     |
  |                         |                                               |     |
  |                         |   3V3 ------------------> 3.3V Power Rail     |     |
  |                         |   GND ------------------> System Ground       |     |
  |                         +-----------------------------------------------+     |
  +-------------------------------------------------------------------------------+
```

---

## 7. Open CT Hazard Safety & ELOT 60364 Compliance

### 7.1 Physics of the Open-Circuit CT Hazard

A Current Transformer's primary winding is connected in series with the load conductor, meaning the primary magnetomotive force ($\text{MMF} = I_p \times N_p$) is established by the facility's electrical demand, entirely independent of the secondary circuit. Under normal operation with a burden resistor attached, the secondary current produces an opposing MMF that cancels approximately $99.5\%$ of the primary core flux.

If the secondary circuit is opened ($Z_{burden} \to \infty$) while the primary conductor is energized:
1. The opposing secondary flux drops to zero instantly.
2. The entire primary current becomes magnetizing current, driving the laminated silicon steel core into deep saturation.
3. At the zero-crossings of the AC current wave, the rate of change of magnetic flux ($d\Phi / dt$) reaches extreme values.
4. According to Faraday's Law ($V_s = -N_s \frac{d\Phi}{dt}$), this induces **destructive voltage spikes ($> 1000\text{ V}$ to $3000\text{ V}$)** across the open secondary terminals.

These voltage pulses present severe hazards:
- **Lethal Electric Shock:** Touching open CT leads can deliver a fatal high-voltage shock.
- **Arc Flash & Dielectric Breakdown:** Flashover can ignite electrical switchgear.
- **Thermal Core Destruction:** Eddy current losses overheat and destroy the CT core.

### 7.2 SCT-013 Internal TVS Diode Protection

Genuine YHDC SCT-013-000 current transformers include a built-in bidirectional Transient Voltage Suppressor (TVS) clamp diode mounted inside the 3.5mm audio jack molded housing. 
- The TVS diode clamps secondary voltages to approximately $\pm 8.2\text{ V}$ to $\pm 9.1\text{ V}$ if disconnected.
- **Operational Precaution:** The internal TVS is rated for transient absorption, NOT continuous power dissipation. If an open-circuit CT is left clamped around a $100\text{ A}$ line for extended periods, the TVS diode will thermally fail, short-circuit, or rupture.

### 7.3 Mandatory Installation & Maintenance Safety Rules

1. **Install Clamps with Main Breaker Disengaged:** Always de-energize the commercial panel distribution board (MCCB) before positioning CT clamps around busbars or main tails.
2. **Never Open an Active Secondary Loop:** Always connect the 3.5mm audio jack firmly into the EMS terminal breakout before closing the clamp around an energized conductor.
3. **Clamp Individual Phase Conductors Only:**  
   - Clamp **Phase L1** conductor alone.
   - Clamp **Phase L2** conductor alone.
   - Clamp **Phase L3** conductor alone.  
   *Never clamp a 3-phase multicore cable or Phase + Neutral together; the opposing magnetic fields will cancel out, reading 0.00 A.*
4. **Physical Orientation Arrow:** Observe the arrow stamped on the SCT-013 housing indicating current flow direction ($\text{Grid } \to \text{ Consumer}$). Reversing the clamp inverts the active power sign ($P < 0$).
5. **ELOT 60364 / HD 384 Compliance:** All secondary signal wiring inside electrical panels must be double-insulated ($600\text{V}$ rated) and physically segregated from high-voltage bare busbars by approved insulating barriers.
