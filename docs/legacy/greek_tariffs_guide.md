# In-Depth Guide: Greek Commercial Electricity Tariffs

**Authoritative Standards:** Greek Law 5068/2023 | RAEWW Decisions | DEDDIE & ADMIE Grid Codes  
**Applicable Customers:** Greek Commercial SMBs (Bakeries, Cold Storage Logistics, Boutique Hotels, Workshops)

---

## 1. Commercial Connection Categories in Greece

Commercial electricity connections in Greece are structured by voltage tier and contracted apparent power (kVA):

### 1.1 Tariff Contract Types
| Tariff Code | Connection Voltage | Contracted Capacity | Rate Structure | Target Customer Profile |
|---|---|---|---|---|
| **Γ21** | Low Voltage (LV, 230/400V) | $\le 25\text{ kVA}$ (up to $3\times 35\text{ A}$) | Uniform 24-hour rate | Small retail stores, artisanal workshops, small cafes |
| **Γ22** | Low Voltage (LV, 230/400V) | $> 25\text{ kVA}$ (up to $3\times 100\text{ A}$ / $70\text{ kVA}$) | Dual-rate (Day Peak & Off-Peak) | Commercial bakeries, supermarkets, boutique hotels, cold storage |
| **Γ23** | Medium Voltage (MV, 15/20kV) | $> 250\text{ kVA}$ | Tri-rate (Peak, Day, Night) | Large industrial packaging plants, olive presses, big resort complexes |

---

## 2. Greek "Color-Coded" Retail Tariff System

Established by the Ministry of Environment and Energy (ΥΠΕΝ) under **Law 5068/2023**, retail electricity tariffs are categorized into color schemes:

### 2.1 The Green Tariff (Special Universal Tariff)
Mandatory for all suppliers to offer. Features a uniform structure across all providers with rates announced on the **1st of each calendar month**.

#### Mathematical Formula of the Fluctuation Mechanism ($MD$):
The Fluctuation Mechanism ($MD$) adjusts the monthly retail rate based on the preceding month's average wholesale Day-Ahead Market clearing price ($TEA_{m-1}$):

$$MD = \begin{cases} 
\alpha \times (TEA_{m-1} - L_u), & \text{if } TEA_{m-1} > L_u \\
0, & \text{if } L_l \le TEA_{m-1} \le L_u \\
\alpha \times (TEA_{m-1} - L_l), & \text{if } TEA_{m-1} < L_l 
\end{cases}$$

Where:
- $TEA_{m-1}$: Wholesale Settlement Price (Τιμή Εκκαθάρισης Αγοράς) of month $m-1$ in €/MWh or €/kWh.
- $L_u$: Upper limit threshold (e.g., $95\text{ \euro/MWh} = 0.095\text{ \euro/kWh}$).
- $L_l$: Lower limit threshold (e.g., $80\text{ \euro/MWh} = 0.080\text{ \euro/kWh}$).
- $\alpha$: Coefficient reflecting network losses and wholesale risk factor (typically $1.15 - 1.25$).
- $\beta$: Supplier fixed or operational component.
- $B$: Base retail supply charge (€/kWh).

#### Final Retail Energy Rate:
$$R_{\text{supply}} = \max\Big(0.00,\; B + MD \times (1 - L_{\text{loss}}) + \beta\Big)$$
*(where $L_{\text{loss}}$ represents the grid loss factor, typically $13.5\%$)*

### 2.2 The Yellow Tariff (Indexed Dynamic)
- The tariff is not fixed in advance; it is indexed directly to wholesale prices during the consumption month.
- Consists of a fixed supplier markup plus the actual monthly or daily wholesale price.
- Can be more cost-effective during high solar production windows, but exposes SMBs to extreme wholesale price spikes during summer heatwaves.

### 2.3 The Dynamic / Orange Tariff (Smart Meter Spot DAM)
- Requires a certified bidirectional telemetry smart meter (DEDDIE telemetry integration).
- Prices change **hourly** matching the 24 hourly auction results of the Hellenic Energy Exchange (HEnEx).
- Enables demand-response automation (e.g., running cold room blast freezers during midday negative price windows).

---

## 3. DEDDIE Time-of-Use Schedules (Γ22 Dual-Rate)

Commercial Γ22 contracts benefit from lower energy rates during official off-peak periods defined by DEDDIE:

```
SUMMER SCHEDULE (May 1 – October 31):
+-----------------------+---------------------+-----------------------+
|  Normal / Day Rate    |    HIGH PEAK RATE   |    Reduced Night Rate |
|  07:00 - 14:00        |    14:00 - 17:00    |    23:00 - 07:00      |
|  (Standard Rate)      |  (Surcharge Window) |   (Discounted Rate)   |
+-----------------------+---------------------+-----------------------+

WINTER SCHEDULE (November 1 – April 30):
+-----------------------+---------------------+-----------------------+
|  Normal / Day Rate    |    EVENING PEAK     |   Off-Peak Windows    |
|  08:00 - 15:00        |    17:00 - 21:00    |   02:00 - 08:00       |
|                       |                     |   15:00 - 17:00       |
+-----------------------+---------------------+-----------------------+
*Note: Saturdays, Sundays, and official national holidays are billed uniformly at off-peak rates.*
```

---

## 4. Regulated Charges & Surcharges (Non-Contestable)

Every Greek electricity bill includes non-negotiable regulated network charges approved by RAEWW:

### 4.1 Transmission System (ADMIE)
- **Power Capacity Fee:** $€/\text{kVA}/\text{year}$ on contracted capacity.
- **Energy Transport Fee:** $€0.0084/\text{kWh}$ transported.

### 4.2 Distribution Network (DEDDIE)
- **Network Capacity Fee:** Fixed charge per kVA of contracted supply.
- **Distribution Usage Fee:** Variable charge per consumed kWh (varies between peak and off-peak).

### 4.3 Public Policy Surcharges
- **ETMEAR (Special Duty for Greenhouse Gases):** Surcharge supporting renewable energy generators (typically $€0.0170/\text{kWh}$ for commercial LV).
- **YKO (Public Utility Services):** Funding subsidized electricity for Greek island grids and vulnerable households.
- **EFK (Special Consumption Tax):** Greek state excise tax on electricity.
- **DETE (Special Duty 5‰):** Mandatory Greek state levy.
- **Value Added Tax (VAT):** **6.0%** on electricity supply and regulated charges.

---

## 5. Penalties & Surcharge Triggers

### 5.1 Contracted Capacity Excess Penalty
If instantaneous apparent load ($S_{\text{tot}}$) exceeds the facility's contracted connection capacity ($S_{\text{contract}}$):
$$\Delta S = S_{\text{tot}} - S_{\text{contract}}$$
A demand penalty multiplier ($3\times - 5\times$ base capacity rate) is applied by DEDDIE to the peak demand register.

### 5.2 Power Factor Penalty ($\cos\varphi < 0.85$)
Low power factor due to uncompensated inductive loads (compressors, refrigeration units, ventilation blowers) pollutes the electrical grid.
For installations with $\cos\varphi < 0.85$, DEDDIE multiplies distribution network charges by:

$$K_{\text{penalty}} = \frac{0.85}{\cos\varphi}$$

*Example: If an uncompensated bakery refrigeration plant operates at $\cos\varphi = 0.68$, its DEDDIE distribution charges are inflated by:*
$$K_{\text{penalty}} = \frac{0.85}{0.68} = 1.25 \implies +25\% \text{ penalty on distribution fees!}$$

---

## 6. How the Behind-the-Meter EMS Saves Money

1. **Immediate Peak Load Shedding:** Alerts the business owner within 30 seconds of an initial breach during 14:00–17:00 summer peak windows.
2. **Defrost Cycle Shifting:** Recommends postponing industrial freezer defrost cycles from 15:00 to 23:30 (saving €35–€60 daily).
3. **Power Factor Monitoring:** Displays instantaneous $\cos\varphi$ to identify failing capacitor banks before DEDDIE penalties appear on the monthly bill.
