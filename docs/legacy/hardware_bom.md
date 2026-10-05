# Hardware Bill of Materials (BOM) & Assembly Guide

**Target Budget:** Sub-€50 Total Assembly Cost  
**Target Architecture:** Non-Invasive 3-Phase Behind-the-Meter EMS  
**Microcontroller:** ESP32-WROOM-32 (Dual-Core, Wi-Fi 802.11 b/g/n, Bluetooth LE)  
**Sensors:** 3x Split-Core Current Transformers (SCT-013-000)

---

## 1. Bill of Materials (BOM)

| Item | Component Description | Quantity | Part Number / Reference | Unit Price (€) | Total (€) | Recommended Source |
|---|---|:---:|---|:---:|:---:|---|
| 1 | **ESP32 Development Board** (ESP-WROOM-32 with external antenna IPEX connector or onboard PCB antenna) | 1 | ESP32-DevKitC V4 / ESP32-WROOM-32U | €5.50 | €5.50 | DigiKey, Mouser, AliExpress |
| 2 | **Split-Core Current Transformers** (100A:50mA, 2000:1 ratio, 13mm aperture) | 3 | YHDC SCT-013-000 | €6.20 | €18.60 | YHDC Official, Amazon, eBay |
| 3 | **Precision Burden Resistors** ($18\,\Omega \pm 1\%$, 0.25W Metal Film, $50\text{ ppm/}^\circ\text{C}$) | 3 | Vishay Dale MRS25 / TE Connectivity | €0.15 | €0.45 | TME, Farnell, Mouser |
| 4 | **DC Voltage Divider Resistors** ($100\text{ k}\Omega \pm 1\%$, 0.25W Metal Film) | 6 | Yageo MFR-25 / Multicomp Pro | €0.05 | €0.30 | TME, DigiKey |
| 5 | **Bias Decoupling Electrolytic Capacitors** ($10\,\mu\text{F} / 25\text{V}$ Low ESR) | 3 | Panasonic FC / Rubycon YXF | €0.18 | €0.54 | Farnell, TME |
| 6 | **High-Frequency Bypass Capacitors** ($100\,\text{nF} / 50\text{V}$ Ceramic X7R) | 3 | KEMET C315C104K5R5TA | €0.08 | €0.24 | Mouser, TME |
| 7 | **3.5mm Stereo Audio Jacks** (PCB Mount or Panel Mount for CT clamp plugs) | 3 | CUI Devices SJ1-3513-SMT | €0.65 | €1.95 | Mouser, DigiKey |
| 8 | **AC-to-DC Regulated Power Supply** (Input: 85–264V AC, Output: 5V DC 1A, isolated, DIN rail or PCB module) | 1 | Mean Well IRM-05-5 / MDR-10-5 | €7.20 | €7.20 | TME, Distrelec |
| 9 | **DIN-Rail Enclosure** (Flame-retardant UL94V-0 ABS housing, 2-module or 4-module width) | 1 | Italtronic 4M DIN Modular Box | €6.80 | €6.80 | TME, Reichelt |
| 10 | **Prototyping PCB / Perfboard** (Single-sided or custom 2-layer PCB) | 1 | Standard FR4 Double Sided 5x7cm | €1.20 | €1.20 | Local hobby store, JLCPCB |
| 11 | **Terminal Blocks & Wire** (Screw terminal 5.08mm pitch, 22 AWG hookup wire) | 1 set | Phoenix Contact / Degson | €1.50 | €1.50 | Local distributor |
| **TOTAL** | | | | | **€44.28** | *(Fully within €50 budget!)* |

---

## 2. Sensor Selection: Why SCT-013-000?

### 2.1 The Critical Current Output Advantage
The **SCT-013-000** produces a secondary current output ($50\text{ mA}$ at $100\text{ A}$ primary). This provides essential advantages over fixed-voltage models (like SCT-013-030):
1. **Customizable Voltage Swing:** The user selects the burden resistor to perfectly match the ESP32's ADC range ($18\,\Omega$ for 3.3V ADC yields maximum dynamic range without clipping).
2. **Noise Immunity:** Current-mode transmission across the 1-meter CT cable prevents noise pickup from adjacent 400V 50Hz busbars.
3. **Internal Transient Clamping:** The SCT-013-000 has built-in antiparallel transient diodes to protect against open-circuit secondary high-voltage hazards.

---

## 3. Circuit Schematics (Single Phase Front-End)

Duplicate this circuit across **Phase L1, L2, and L3**:

```
                  +3.3V DC (ESP32 VCC)
                     |
                   [R1] 100 kΩ (1%)
                     |
                     +-----------------------+ Virtual Midpoint (1.65V)
                     |                       |
                   [R2] 100 kΩ (1%)        [C1] 10 μF (Low ESR)
                     |                       |
                    GND                     GND
                     |
                     +-----------------------+ (Virtual Ground)
                     |
                 +---+---+
                 |       |
              [R_burden] | (CT Clamp 3.5mm Audio Jack)
                 18 Ω    |
                 (1%)    |
                 +---+---+
                     |
                     +-----------------------> To ESP32 ADC1 (GPIO 34 / 35 / 32)
                     |
                   [C2] 100 nF (Bypass to GND)
                     |
                    GND
```

### Component Roles:
- **`R1` and `R2` (100 kΩ):** Form a precision voltage divider stepping down 3.3V to a stable 1.65V virtual ground.
- **`C1` (10 μF):** Provides low AC impedance to ground, stabilizing the 1.65V bias against transient fluctuations.
- **`R_burden` (18 Ω):** Converts the secondary current into an AC voltage ($V_{peak} = 1.273\text{ V}$).
- **`C2` (100 nF):** Shunts high-frequency RF noise from switching inverters and motor drives before the ADC.

---

## 4. Physical Installation in Greek Commercial Distribution Boxes

Greek commercial electrical distribution boards (πίνακες) are typically metal wall enclosures containing Hager, ABB, or Schneider DIN-rail breakers.

### 4.1 Faraday Cage Mitigation
- Metal distribution boxes block 2.4 GHz Wi-Fi signals.
- **Solution:** Use an **ESP32-WROOM-32U** module with an external IPEX/U.FL antenna. Mount an adhesive 2.4 GHz dipole patch antenna on the exterior plastic or side surface of the distribution cabinet.

### 4.2 CT Clamp Orientation
- Each SCT-013 clamp features an arrow showing current flow direction ($K \to L$ or $P1 \to P2$).
- **Rule:** The arrow must point **from the grid supply towards the load**.
- If a phase displays negative active power in telemetry, simply open the split-core clamp and reverse its physical orientation around the phase cable.

### 4.3 Clean Cable Management
- Keep low-voltage CT signal cables separated from 400V three-phase busbars by at least 50mm.
- Clamp **only around the individual live phase conductors (L1, L2, L3)**. Never clamp around a 3-phase bundled cable (L1+L2+L3+N cancel each other out resulting in 0A reading).
