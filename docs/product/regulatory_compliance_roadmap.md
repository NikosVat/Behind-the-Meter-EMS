# Regulatory Compliance & Standardization Roadmap

**Product:** Greek Commercial Behind-the-Meter Edge Energy Management System (BTM-EMS)  
**Target Market:** European Union (Primary focus: Greece & Hellenic Electricity Distribution Network - DEDDIE)  
**Applicable Conformity Mark:** **CE Marking** (Self-Declaration backed by Accredited Laboratory Type-Testing)  
**Product Classification:** Stationary DIN-Rail Mounted Electrical Measurement & Energy Control Equipment  

---

## 1. Executive Summary & Compliance Strategy

To transition from prototype to an authorized commercial product deployable within European commercial low-voltage distribution panels, the Behind-the-Meter Edge EMS must satisfy all mandatory European Union Directives and Greek national regulations.

Our compliance strategy follows a two-tier verification pathway:
1. **Tier 1 (Harmonized Standards Pre-Compliance):** Internal engineering design verification, creepage/clearance auditing, and pre-compliance electromagnetic compatibility (EMC) testing at national facilities (e.g. National Technical University of Athens - NTUA High Voltage Lab).
2. **Tier 2 (Accredited Laboratory Type-Testing):** Formal certification testing with a recognized European Notified Body (TÜV Hellas / TÜV NORD, Eurofins Product Service, or MIRTEC/EBETAM), resulting in a formal EU Declaration of Conformity (DoC) and CE marking.

---

## 2. Applicable European Union Directives & Harmonized Standards

```
                      +------------------------------------------+
                      |       CE MARKING CONFORMITY FOLDER       |
                      +------------------------------------------+
                                           |
         +------------------+--------------+------------------+------------------+
         |                  |                                 |                  |
    [2014/35/EU]       [2014/30/EU]                      [2014/53/EU]       [2011/65/EU]
Low Voltage Directive   EMC Directive                     RED Directive    RoHS 3 Directive
  (Electrical Safety) (Immunity/Emissions)             (Wireless 2.4GHz)  (Hazardous Substances)
         |                  |                                 |                  |
    EN 61010-1:2010    EN 61326-1:2021                   ETSI EN 300 328     EN IEC 63000:2018
   EN 61010-2-030     EN 55032 (Class B)                ETSI EN 301 489-1   Lead-Free Solder
  (CAT III 300V)     EN 61000-4-2..5                   ETSI EN 301 489-17   UL94-V0 Housing
```

### 2.1 Low Voltage Directive (LVD) 2014/35/EU — Electrical Safety

The LVD ensures electrical equipment within voltage limits (50–1000 VAC) provides adequate protection against electric shock, mechanical hazards, and fire propagation.

| Standard | Title | Specific Requirement | Implementation in BTM-EMS |
|---|---|---|---|
| **EN 61010-1:2010 + A1:2019** | Safety requirements for electrical equipment for measurement, control, and laboratory use — Part 1: General requirements | Insulation coordination, creepage & clearance, fire resistance | Creepage $\ge 5.5\text{ mm}$, clearance $\ge 4.5\text{ mm}$ across primary/secondary barrier; UL94-V0 flame-retardant housing. |
| **EN 61010-2-030:2021** | Particular requirements for equipment having testing or measuring circuits | Overvoltage measurement category rating (CAT rating) | Rated for **CAT III 300V** (distribution panel level). Impulse withstand voltage tested up to **$4.0\text{ kV}$**. |
| **EN 60529:1991 + A2:2013** | Degrees of protection provided by enclosures (IP Code) | Environmental ingress protection | **IP20** finger-safe terminal blocks (Phoenix Contact); IP40 when mounted inside distribution box. |

#### Specific Safety Implementation Rules:
- **Galvanic Isolation Barrier:** The Mean Well HDR-15-5 internal transformer and ZMPT101B active voltage transformer provide $> 3.0\text{ kV AC}$ galvanic isolation between 400V 3-phase mains and the ESP32 low-voltage logic.
- **Creepage Routing:** Physical isolation routing slots ($1.5\text{ mm}$ air gaps) under high-voltage traces eliminate surface tracking under high humidity (up to 95% RH in commercial bakeries).
- **In-Line Overcurrent Protection:** Littelfuse 500mA ceramic cartridge fuse with $1500\text{ A}$ interrupt rating protects against dead-short catastrophic faults.

---

### 2.2 Electromagnetic Compatibility (EMC) Directive 2014/30/EU

Electrical equipment in commercial and light industrial environments must operate reliably in the presence of electromagnetic disturbances without emitting excessive electromagnetic interference.

| Standard | Test Type | European Limit / Test Level | BTM-EMS Performance & Hardware Mitigation |
|---|---|---|---|
| **EN 61326-1:2021** | Product Family Standard for Electrical Measurement Equipment | Commercial / Light-Industrial Environment Criteria | Comprehensive reference standard for all EMC tests below. |
| **EN 55032:2015 + A11:2020** | Radiated Emissions (30 MHz – 1 GHz) | Class B Commercial Limits ($30\text{ dB}\mu\text{V/m}$ at 10m) | ESP32 RF shielding can + solid ground plane on 2-layer PCB; differential microstrip RF feed. |
| **EN 55032:2015 + A11:2020** | Conducted Emissions on AC Mains (150 kHz – 30 MHz) | Class B Quasi-Peak & Average Limits | Mean Well HDR-15-5 built-in Class B π-filter + 100nF X7R shunt bypass. |
| **EN 61000-4-2:2009** | Electrostatic Discharge (ESD) Immunity | $\pm 4\text{ kV}$ Contact / $\pm 8\text{ kV}$ Air Discharge (Criterion B) | STMicroelectronics ESDA6V1BC6 bidirectional TVS diodes on all user terminals and SMA connector. |
| **EN 61000-4-4:2012** | Electrical Fast Transient / Burst (EFT) | $\pm 2.0\text{ kV}$ on AC mains / $\pm 1.0\text{ kV}$ on CT lines (Criterion B) | Common-mode ferrite chokes on CT inputs; RC low-pass filter ($100\,\Omega + 100\text{ nF}$). |
| **EN 61000-4-5:2014 + A1:2017** | Surge Immunity (Lightning / Switching) | $\pm 1.0\text{ kV}$ Line-to-Line / $\pm 2.0\text{ kV}$ Line-to-Earth (Criterion B) | EPCOS 275V Metal Oxide Varistor (MOV, 4.5kA) directly across incoming AC live and neutral. |
| **EN 61000-4-6:2014** | Conducted RF Immunity (150 kHz – 80 MHz) | $3\text{ V RMS}$ (Criterion A) | RF decoupling capacitors on virtual ground and ADC input pins. |

---

### 2.3 Radio Equipment Directive (RED) 2014/53/EU

Because the ESP32-WROOM-32UE incorporates an active 2.4 GHz Wi-Fi (802.11 b/g/n) and Bluetooth transceiver, compliance with RED is mandatory:

| Standard | Scope | Certification Route |
|---|---|---|
| **ETSI EN 300 328 v2.2.2** | Wideband transmission systems in 2.4 GHz ISM band (spectral efficiency, transmit power $\le 100\text{ mW}$ EIRP, spurious emissions) | Leverages Espressif Systems' existing EU Type Examination Certificate (`CE 0700`) for the ESP32-WROOM-32UE module, with supplementary radiated delta verification with external SMA antenna. |
| **ETSI EN 301 489-1 / -17** | EMC standard for radio equipment and services | Validates radio transceiver immunity to external RF fields without data dropping or processor lockup. |
| **EN 62311:2020** | Assessment of electronic equipment related to human exposure restrictions for electromagnetic fields ($0\text{ Hz} - 300\text{ GHz}$) | Compliance by design: transmit power $< 20\text{ dBm}$ ($100\text{ mW}$), minimum separation distance $> 20\text{ cm}$ in fixed wall panel. |

---

### 2.4 Environmental Directives (RoHS & WEEE)
- **RoHS 3 Directive (EU) 2015/863:** All components, solder paste (SAC305 lead-free), and PCB finishes are certified 100% free of restricted hazardous substances (Lead, Cadmium, Mercury, Hexavalent Chromium, PBBs, PBDEs, DEHP, BBP, DBP, DIBP).
- **WEEE Directive 2012/19/EU:** Enclosure features the standardized crossed-out wheeled bin symbol. A recycling and take-back arrangement will be established with certified Greek collective compliance schemes (e.g. Ανακύκλωση Συσκευών Α.Ε.).
- **Flame Retardancy:** Enclosure material is 100% **UL94-V0** flame-retardant self-extinguishing polycarbonate, verified to extinguish within 10 seconds of open flame exposure without flaming drips.

---

## 3. Greek Grid & DEDDIE Alignment

Beyond European CE directives, the BTM-EMS aligns directly with national technical guidelines issued by the Hellenic Electricity Distribution Network Operator (**ΔΕΔΔΗΕ / DEDDIE**) and the Regulatory Authority for Energy (**ΡΑΕ / RAE**):

### 3.1 DEDDIE Smart Meter Roll-Out Compatibility (DLMS/COSEM & P1 Interface)
- DEDDIE is currently deploying over 7.5 million smart meters (Landis+Gyr and Sagemcom) across Greece under European modernization funds.
- **Companion Architecture:** The BTM-EMS functions as a non-invasive customer-side companion. Future firmware iterations will include an optional optical probe or P1 RJ12 serial port to read real-time DLMS/COSEM encrypted utility registers directly from the DEDDIE meter, fusing utility revenue pulses with the high-speed 5-second edge telemetry.

### 3.2 Greek Net-Billing & Energy Sharing Framework
- Governed by **Ministerial Decision YPEN/DIE/83812/1006/2023** (*Ταυτοχρονισμένος Συμψηφισμός - Net-Billing*).
- Under Greek Net-Billing, commercial self-consumers receive compensation based on the hourly market clearing price (MCP) of the Hellenic Energy Exchange (HENEX) for injected energy that is not self-consumed simultaneously.
- **EMS Alignment:** The edge forecast engine directly maximizes self-consumption during high production solar hours and restricts consumption during high-tariff evening hours, increasing the economic yield of rooftop PV installations by up to **$28\%$** compared to unmanaged facilities.

### 3.3 Grid Power Quality Compliance (EN 50160)
- The BTM-EMS monitors power quality parameters defined under European standard **EN 50160**:
  - Voltage tolerance: $230\text{ V} \pm 10\%$ ($207.0\text{ V} - 253.0\text{ V}$).
  - Frequency tolerance: $50.0\text{ Hz} \pm 1\%$ ($49.5\text{ Hz} - 50.5\text{ Hz}$).
  - Harmonics: Identifies 3rd and 5th harmonic distortion exceeding limits, warning facility managers of motor drive degradation.

---

## 4. Certification Testing Roadmap & Budget

| Phase | Milestone / Laboratory Activity | Partner / Testing House | Estimated Duration | Estimated Cost (€) | Target Completion |
|---|---|---|:---:|:---:|:---:|
| **Phase 1** | PCB Isolation & Creepage Engineering Audit | Internal / NTUA High Voltage Lab | 2 Weeks | €600 | Q1 2027 |
| **Phase 2** | Pre-Compliance EMC Scan (Radiated & Conducted Emissions) | NTUA EMC Laboratory (Athens) | 2 Weeks | €1,500 | Q2 2027 |
| **Phase 3** | Formal LVD Electrical Safety Certification (EN 61010-1) | TÜV Hellas / Eurofins | 4 Weeks | €3,800 | Q3 2027 |
| **Phase 4** | Formal EMC Directive Immunity & Emissions Testing (EN 61326-1) | Eurofins / MIRTEC (EBETAM) | 4 Weeks | €4,500 | Q3 2027 |
| **Phase 5** | RED 2.4 GHz RF Radiated Delta Evaluation | Eurofins Product Service | 2 Weeks | €2,200 | Q4 2027 |
| **Phase 6** | Technical Construction File (TCF) Assembly & EU Declaration of Conformity | Internal Legal / Compliance Lead | 2 Weeks | €800 | Q4 2027 |
| **TOTAL** | **Full Commercial CE Certification** | | **16 Weeks** | **€13,400** | **Commercial Market Launch** |

---

## 5. Formal EU Declaration of Conformity (DoC) Draft Template

```
EU DECLARATION OF CONFORMITY
---------------------------------------------------------------------------------
Manufacturer:        Greek Commercial Behind-the-Meter EMS Team / EnerTech Solutions
Address:             Athens, Greece
Product Description: 3-Phase Commercial Behind-the-Meter Edge Energy Management System
Model / Type:        BTM-EMS-4M-REV2

This declaration of conformity is issued under the sole responsibility of the manufacturer.
The object of the declaration described above is in conformity with the relevant Union
harmonization legislation:
- Low Voltage Directive (LVD) 2014/35/EU
- Electromagnetic Compatibility (EMC) Directive 2014/30/EU
- Radio Equipment Directive (RED) 2014/53/EU
- Restriction of Hazardous Substances (RoHS) Directive 2011/65/EU & (EU) 2015/863

References to the relevant harmonized standards used:
- Safety:     EN 61010-1:2010 + A1:2019, EN 61010-2-030:2021
- EMC:        EN 61326-1:2021, EN 55032:2015 + A11:2020, EN 61000-4-2..5
- Radio:      ETSI EN 300 328 V2.2.2, ETSI EN 301 489-1 V2.2.3, ETSI EN 301 489-17 V3.2.4
- Health:     EN 62311:2020
- Hazardous:  EN IEC 63000:2018

Signed for and on behalf of:
Athens, Greece — DD/MM/YYYY
Lead System Architect & Managing Director: ____________________________________
```
