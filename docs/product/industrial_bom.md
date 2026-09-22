# Industrial Bill of Materials (BoM) & Hardware Architecture Dossier

**Product Name:** Greek Commercial Behind-the-Meter Edge Energy Management System (BTM-EMS)  
**Target Hardware Budget:** Sub-€70 Total Bill of Materials (Prototypes < €70.00 | Scale-Up < €30.00)  
**Industrial Form Factor:** 4-Module (4M) Standard 35mm DIN-Rail Enclosure (EN 50022 / DIN 43880)  
**Operating Temperature:** Industrial Grade -40°C to +85°C  
**Safety & Isolation:** CAT III 300V / Overvoltage Category II 600V, Class II Reinforced Galvanic Isolation  

---

## 1. Executive Summary & Cost Target Adherence

To transform the Behind-the-Meter EMS from a fragile lab prototype into an industrial-grade, commercially scalable edge device eligible for deployment across Greek bakeries, supermarket chains, and small-medium retail businesses, the hardware front-end has been completely re-engineered.

Every component in this Bill of Materials has been selected according to four strict engineering criteria:
1. **Safety & Fire Resistance:** Strict UL94-V0 flammability compliance for installation inside commercial distribution boards (πίνακες).
2. **Faraday Cage Mitigation:** High-gain external 2.4 GHz SMA dipole antenna routing through panel knockouts to eliminate signal loss inside grounded metal electrical enclosures.
3. **Metrological Accuracy & Isolation:** Low-temperature-coefficient ($25\text{ ppm/}^\circ\text{C}$) precision burden resistors and galvanic voltage isolation front-end supporting both Mode A (CT-Only) and Mode B (Synchronized True RMS).
4. **Resilient Offline Timekeeping:** DS3231SN TCXO real-time clock with lithium coin cell backup guaranteeing zero loss of 7x24 weekly seasonal matrix indexing during prolonged grid outages.

### Cost Summary Table by Volume Tier

| Component Category | Prototype (1x) | Pilot Run (100x) | Mass Production (1,000x) |
|---|:---:|:---:|:---:|
| **Compute & Wireless (ESP32-WROOM-32UE + RF)** | €6.80 | €4.60 | €3.20 |
| **Grid Power Supply & In-Line Fuse (Mean Well HDR-15-5)** | €12.50 | €9.20 | €6.80 |
| **DIN-Rail Enclosure & Terminals (UL94-V0 4M)** | €9.40 | €6.10 | €4.20 |
| **Current Sensors (3x YHDC SCT-013-000 Clamps)** | €17.40 | €11.10 | €8.40 |
| **Analog Front-End & Metrology (0.1% Resistors + ZMPT101B)** | €6.20 | €4.10 | €2.80 |
| **Hardware RTC & Timekeeping (DS3231SN + CR1220)** | €3.60 | €2.40 | €1.50 |
| **PCB Fabrication, Passive Filtering & Assembly** | €2.50 | €1.90 | €1.20 |
| **TOTAL HARDWARE BOM** | **€58.40** | **€39.40** | **€28.10** |
| *Budget Compliance (< €70.00)* | **PASSED (-16.6%)** | **PASSED (-43.7%)** | **PASSED (-59.9%)** |

---

## 2. Itemized Industrial Bill of Materials (Exact MPNs & Suppliers)

| Line | Subsystem | Component Description | Manufacturer | Exact Manufacturer Part Number (MPN) | Distributor Ref (Mouser / TME / DigiKey) | Qty | Unit Price (1x) | Total Cost (€) |
|:---:|---|---|---|---|---|:---:|:---:|:---:|
| **1** | **Microcontroller** | ESP32-WROOM-32UE (Dual Core 240MHz, 4MB Flash, IPEX/U.FL connector, -40°C to +85°C) | Espressif Systems | `ESP32-WROOM-32UE-N4` | Mouser: 356-ESP32WROOM32UEN4 | 1 | €4.80 | €4.80 |
| **2** | **RF Antenna** | 2.4 GHz 3dBi Omnidirectional Dipole Antenna with SMA Male connector | Linx Technologies / TE | `ANT-2.4-CW-RAH-SMA` | DigiKey: ANT-2.4-CW-RAH-SMA-ND | 1 | €2.80 | €2.80 |
| **3** | **RF Pigtail** | IPEX (U.FL) to SMA Female Bulkhead Pigtail Cable (150mm, RG178) | Taoglas | `CAB.011` | Mouser: 960-CAB.011 | 1 | €1.20 | €1.20 |
| **4** | **Power Supply** | Ultra-Slim DIN-Rail AC/DC Power Supply, 85–264VAC In, 5VDC 2.4A 12W Out, Class II | Mean Well | `HDR-15-5` | TME: HDR-15-5 | 1 | €10.80 | €10.80 |
| **5** | **Fuse Holder** | 5x20mm DIN-Rail Fuse Terminal Block with Blown-Fuse LED indicator | Phoenix Contact | `UK 5-HESI (3004100)` | Mouser: 651-3004100 | 1 | €1.40 | €1.40 |
| **6** | **Mains Fuse** | 500mA 250VAC Time-Lag (Slow-Blow) Ceramic Cartridge Fuse, High Breaking Capacity | Littelfuse | `0215.500MXP` | DigiKey: F2637-ND | 1 | €0.65 | €0.65 |
| **7** | **Surge Varistor** | Metal Oxide Varistor (MOV) 275VAC, 4.5kA, 14mm disc (transient surge protection) | EPCOS / TDK | `B72214S0271K101` | Mouser: 871-B72214S0271K101 | 1 | €0.45 | €0.45 |
| **8** | **CT Clamps** | Split-Core Current Transformer, 100A RMS / 50mA RMS (2000:1 ratio, 13mm bore, 1m cable) | YHDC | `SCT-013-000` | TME: SCT-013-000 | 3 | €5.80 | €17.40 |
| **9** | **Burden Resistors** | $18.0\,\Omega \pm 0.1\%$, 0.25W Thin Film Precision Resistor, $25\text{ ppm/}^\circ\text{C}$ Low Tempco | Vishay Dale | `PTF5618R000BYEK` | Mouser: 71-PTF5618R000BYEK | 3 | €0.42 | €1.26 |
| **10** | **Voltage Divider** | $100\text{ k}\Omega \pm 0.1\%$, 0.25W Metal Film Resistors for 1.65V virtual midpoint | TE Connectivity | `H8100KBYA` | Mouser: 279-H8100KBYA | 6 | €0.15 | €0.90 |
| **11** | **Filter Capacitors** | $10\,\mu\text{F} / 35\text{V}$ Low ESR Aluminum Electrolytic Capacitors (Virtual Ground Bias) | Panasonic | `EEU-FR1V100` | DigiKey: P14413-ND | 3 | €0.18 | €0.54 |
| **12** | **Bypass Capacitors**| $100\text{ nF} / 50\text{V}$ Ceramic X7R 0805 Capacitors (High-frequency RF shunt) | KEMET | `C0805C104K5RACTU` | Mouser: 80-C0805C104K5R | 6 | €0.06 | €0.36 |
| **13** | **TVS Diodes** | 3.3V Bidirectional TVS Diodes (Transient clamping on analog ADC input lines) | STMicroelectronics | `ESDA6V1BC6` | Mouser: 511-ESDA6V1BC6 | 3 | €0.22 | €0.66 |
| **14** | **Voltage Sensing** | Miniature Active Precision AC Voltage Transformer Front-End (ZMPT101B module, 2kV isolation) | MicroZen / YHDC | `ZMPT101B-MOD` | Distrelec / AliExpress Industrial | 1 | €2.80 | €2.80 |
| **15** | **Hardware RTC** | DS3231SN Extremely Accurate I2C Real-Time Clock with Integrated TCXO & Crystal | Analog Devices / Maxim | `DS3231SN#` | Mouser: 700-DS3231SN | 1 | €2.90 | €2.90 |
| **16** | **Backup Battery** | CR1220 3V 35mAh Lithium Coin Cell Battery (10-year RTC backup endurance) | Renata / Panasonic | `CR1220` | Mouser: 658-CR1220 | 1 | €0.70 | €0.70 |
| **17** | **Enclosure** | 4-Module DIN-Rail Modular Enclosure, Flame-Retardant UL94-V0 Polycarbonate/ABS | Italtronic / CamdenBoss | `11.040.000` (4M Modulbox) | TME: 11.040.000 | 1 | €5.60 | €5.60 |
| **18** | **Terminal Blocks** | 5.08mm Pitch Pluggable Screw Terminal Blocks (3-pole & 4-pole, 300V 12A rated) | Phoenix Contact | `MSTB 2,5/ 4-ST-5,08` | Mouser: 651-1757035 | 2 sets | €1.45 | €2.90 |
| **19** | **Custom 2L PCB** | 2-Layer FR4 PCB, 70mm x 55mm, 1.6mm thickness, 1oz Cu, HASL lead-free finish | JLCPCB / Eurocircuits | `BTM-EMS-REV-B` | Direct PCB Fab | 1 | €0.85 | €0.85 |
| **TOTAL** | | | | | | | | **€58.40** |

---

## 3. Subsystem Engineering Specifications

### 3.1 Mean Well HDR-15-5 Power Supply Integration
- **Grid Input:** 85–264 VAC universal input (handles Greek grid swells up to 264 VAC without stress).
- **Isolation:** Class II isolation (no safety ground required), double insulation rating.
- **Safety Approvals:** UL 62368-1, TUV EN 61558-2-16, EN 61000-6-2 (Industrial Immunity).
- **Efficiency:** 80% typical, ultra-low no-load power consumption < 0.3W.
- **Form Factor:** 1-DIN module width ($17.5\text{ mm}$), leaving 3 DIN modules for the main logic board.

### 3.2 Electrical Ingress & Protection Circuitry
Mains AC power entering the DIN module passes through three cascading safety barriers:
1. **Primary Fuse:** Littelfuse 500mA time-lag ceramic fuse in a DIN-rail modular fuse terminal block. Interrupts dead-shorts immediately with 1500A breaking capacity.
2. **Surge Suppression:** EPCOS 275V Metal Oxide Varistor (MOV) clamps lightning and inductive switching spikes (up to 4.5 kA 8/20 $\mu\text{s}$).
3. **Secondary Low-Voltage Clamping:** Bidirectional TVS diodes across each CT analog input channel restrict voltage spikes to $\le 3.6\text{ V}$, completely shielding the ESP32 internal SAR ADC gates from damage.

### 3.3 Creepage & Clearance Distances (EN 61010-1 Standard)
- **High-Voltage (>50V AC) to Low-Voltage (<5V DC) Isolation Zone:** Guaranteed minimum creepage distance of **$\ge 5.5\text{ mm}$** and clearance of **$\ge 4.5\text{ mm}$** across the PCB isolation barrier.
- **Milled Isolation Slots:** $1.5\text{ mm}$ physical air slots routed under the power transformer and ZMPT101B optocoupler to prevent surface tracking and breakdown under humid Greek coastal bakery environments (up to 95% non-condensing relative humidity).

### 3.4 Faraday Cage Mitigation (External SMA Dipole)
- Commercial electrical cabinets are grounded steel enclosures (e.g. Hager Volta, ABB Mistral) providing up to $35\text{ dB}$ RF shielding at 2.4 GHz, causing complete Wi-Fi dropout for internal antennas.
- The EMS uses an **ESP32-WROOM-32UE** with a factory IPEX/U.FL receptacle.
- A 150mm RG178 coaxial pigtail connects the internal IPEX port to a panel-mount SMA bulkhead jack installed through a standard $6.5\text{ mm}$ enclosure knockout.
- A high-gain $+3\text{ dBi}$ articulated dipole antenna mounts externally, maintaining solid $-55\text{ dBm}$ to $-65\text{ dBm}$ signal strength to the facility's commercial router.

### 3.5 Precision Metrology Front-End & Burden Physics
- **18.0 Ohm Burden Resistor Selection:**
  $$\text{Secondary Peak Current at } 100\text{A RMS} = \frac{100\text{ A}}{2000} \cdot \sqrt{2} = 70.71\text{ mA peak}$$
  $$V_{\text{peak}} = 70.71\text{ mA} \times 18.0\,\Omega = 1.2728\text{ V}$$
  $$V_{\text{peak-to-peak}} = 2.5456\text{ V}$$
- **Headroom Verification:**
  Centered at the $1.65\text{ V}$ virtual ground, the waveform swings between:
  $$V_{\min} = 1.65 - 1.2728 = 0.377\text{ V} \quad (> 0.15\text{ V ADC deadband})$$
  $$V_{\max} = 1.65 + 1.2728 = 2.923\text{ V} \quad (< 3.10\text{ V ADC saturation})$$
- Maximum measurable RMS current without clipping: **$114.3\text{ A RMS}$**, providing ample headroom for industrial commercial bakery refrigeration compressor inrush currents.

---

## 4. Assembly & Quality Control Workflow

```
[Mains Supply 230V] ---> [Littelfuse 500mA Fuse] ---> [Mean Well HDR-15-5] ---> [+5V DC Rail]
                                  |
                                [MOV 275V]
                                  |
[3x SCT-013 Clamps] ---> [3.5mm Terminals] ---> [18Ω 0.1% Burdens] ---> [TVS Diodes] ---> [ESP32 ADC1 (34/35/32)]
                                  |
                        [1.65V Virtual Ground] <--- [Precision Divider + 10uF]
                                  |
[ZMPT101B Isolated] ---> [Linear Voltage Buffer] -----------------------------------------> [ESP32 ADC1 (GPIO 33)]
                                  |
[DS3231SN + CR1220] ---> [I2C Bus (SDA: GPIO 21, SCL: GPIO 22)] -----------------------------> [ESP32 Hardware Wire]
```

1. **SMT Assembly:** Surface-mount components placed on custom 2-layer FR4 PCB. Automated optical inspection (AOI) validates solder joint meniscus.
2. **Hi-Pot Isolation Verification:** $2.5\text{ kV AC}$ dielectric withstand test applied between AC mains terminals and secondary low-voltage ground for 60 seconds (pass criterion: leakage current $< 1.0\text{ mA}$).
3. **ADC Gain & Offset Calibration:** In-line automated 3-point calibration using a precision AC calibrator ($1.0\text{A}$, $10.0\text{A}$, $50.0\text{A}$ at $50.0\text{ Hz}$). Calibration coefficients stored directly in ESP32 NVS Flash memory.
4. **Enclosure Integration:** PCB snapped into Italtronic 4M DIN housing; external antenna secured with lockwasher and O-ring seal; terminals torque-tested to $0.5\text{ N}\cdot\text{m}$.
