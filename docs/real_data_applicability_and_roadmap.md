# Έρευνα εφαρμοσιμότητας σε πραγματικά και universal δεδομένα

**Ημερομηνία έρευνας:** 22 Σεπτεμβρίου 2026  
**Αφορά:** Greek Commercial Behind-the-Meter EMS

## Συμπέρασμα

**Είναι εφαρμόσιμο ως pilot και ως συμμετοχή σε διαγωνισμό: Ναι.**  
**Είναι έτοιμο ως αυτόνομο εμπορικό προϊόν: Όχι ακόμη.**

Το repository διαθέτει λειτουργικό ingestion API, tariff engine, optimizer, simulator και εκτεταμένα automated tests. Έχει επίσης δοκιμαστεί offline σε πραγματικές μετρήσεις του Building Data Genome 2. Αυτό αποδεικνύει ότι ο κώδικας μπορεί να επεξεργαστεί πραγματικές χρονοσειρές, όχι όμως ότι λειτουργεί αξιόπιστα και με οικονομικό όφελος σε ελληνική εμπορική εγκατάσταση.

Τα σημερινά αποτελέσματα δείχνουν ακριβώς γιατί απαιτείται field pilot:

- Χρησιμοποιήθηκαν μόνο τρία κτίρια και κανένα δεν είναι ελληνικός φούρνος, ψυκτική εγκατάσταση ή ξενοδοχείο.
- Το WAPE του επιλεγμένου μοντέλου ήταν **23,66%**, **21,88%** και **211,86%** αντίστοιχα. Στο δεύτερο κτίριο το απλό previous-day baseline ήταν καλύτερο από το επιλεγμένο Ridge μοντέλο.
- Τα forecast-driven battery schedules ήταν μη εφαρμόσιμα πάνω στις πραγματικές μετρήσεις στο **26,2%**, **33,0%** και **96,6%** των ημερών.
- Οι τιμές ρεύματος, η μπαταρία και το capacity penalty του benchmark είναι υποθετικά. Άρα τα savings είναι modeled scenarios και όχι επαληθευμένη εξοικονόμηση.

**Go/No-Go απόφαση:** να προχωρήσει άμεσα ένα read-only pilot με πραγματικό μετρητή και πραγματικές τιμές αγοράς. Δεν πρέπει ακόμη να ενεργοποιηθεί αυτόματος χειρισμός φορτίων. Πρώτα απαιτούνται data-quality gates, rolling re-optimization και τεκμηριωμένη επιβεβαίωση των recommendations από τον χρήστη.

---

## 1. Έλεγχος λειτουργίας σε real data και πληρότητας αριθμών

### Τι θεωρείται «real data»

Πρέπει να διαχωρίζονται τέσσερις κατηγορίες:

1. **Facility telemetry:** πραγματικό kW, kWh, τάση, ρεύμα, power factor και συχνότητα από τον χώρο.
2. **Utility metering:** πιστοποιημένες ή μη πιστοποιημένες καμπύλες ΔΕΔΔΗΕ. Είναι κατάλληλες για reconciliation, όχι απαραίτητα για sub-minute control.
3. **Market/tariff data:** Day-Ahead prices, δημοσιευμένα τιμολόγια και ρυθμιζόμενες χρεώσεις. Δεν είναι μετρήσεις κατανάλωσης.
4. **Context data:** καιρός, ωράριο, παραγωγή, πληρότητα ή παραγγελίες. Χρειάζονται για σωστό baseline και πρόβλεψη.

Η τρέχουσα CT-only λειτουργία εκτιμά ενεργό ισχύ από ονομαστική τάση και προκαθορισμένο power factor. Είναι αποδεκτή για prototype alerts, αλλά όχι ως reference measurement για billing, power-factor penalties ή πιστοποίηση savings.

### Minimum canonical record

Κάθε connector πρέπει να μετασχηματίζει την πηγή στο ίδιο schema:

```json
{
  "source_id": "shelly-pro-3em-001",
  "facility_id": "bakery-athens-01",
  "meter_id": "main-incomer",
  "timestamp_utc": "2026-09-22T10:15:00Z",
  "interval_seconds": 60,
  "active_power_kw": 18.42,
  "active_energy_import_kwh": 12403.88,
  "reactive_power_kvar": 4.11,
  "power_factor": 0.976,
  "voltage_v": {"l1": 231.2, "l2": 229.8, "l3": 232.0},
  "current_a": {"l1": 27.1, "l2": 24.8, "l3": 26.3},
  "quality": "measured",
  "is_estimated": false,
  "source_timestamp": "2026-09-22T13:15:00+03:00",
  "received_at_utc": "2026-09-22T10:15:02Z"
}
```

Υποχρεωτικά metadata: μονάδα, timezone, interval, import/export sign convention, cumulative-counter reset flag, source/provenance, calibration status και quality flag. Δεν πρέπει να μετατρέπεται αυθαίρετα το `kWh` σε `kW` αν το interval δεν είναι γνωστό.

### Data-quality gates πριν από optimization

| Έλεγχος | Pilot acceptance criterion | Ενέργεια αποτυχίας |
|---|---:|---|
| Πληρότητα telemetry | ≥ 99% ανά ημέρα | Καμία αυτόματη πρόταση· fallback schedule |
| Καθυστέρηση edge telemetry | p95 < 10 s για alerts | Alarm και degradation σε advisory-only |
| Διπλότυπα | 0 μετά το idempotent ingestion | Deduplication με source + timestamp + sequence |
| Timestamp/DST | Όλα timezone-aware, UTC storage | Απόρριψη ambiguous timestamps |
| Σύγκριση ενέργειας με reference meter | ≤ 2% ημερήσια απόκλιση για pilot | Recalibration/investigation |
| Counter reset ή αρνητικό delta | 0 μη εξηγημένα περιστατικά | Quarantine interval |
| Phase sum | συμφωνία total με phases εντός tolerance | Flag wiring/CT orientation |
| Forecast | rolling WAPE/MAE ανά facility και horizon | Χρήση simple baseline αν χειροτερεύει |
| Schedule replay | 100% τήρηση hard constraints | Μη δημοσίευση schedule |

Το forecast δεν πρέπει να γίνεται αποδεκτό με ένα καθολικό όριο. Για κάθε facility συγκρίνεται rolling με `previous_day` και `previous_week`. Το ML χρησιμοποιείται μόνο όταν κερδίζει σταθερά σε out-of-sample παράθυρο.

### Προτεινόμενο real-world test

- **Εβδομάδες 1–2:** passive collection και σύγκριση με reference meter/ΔΕΔΔΗΕ.
- **Εβδομάδες 3–4:** shadow forecasting και scheduling χωρίς recommendations προς προσωπικό.
- **Εβδομάδες 5–6:** advisory mode, όπου ο χρήστης αποδέχεται ή απορρίπτει κάθε πρόταση.
- **Μετά:** μόνο εφόσον τηρούνται τα quality gates, δοκιμή περιορισμένου closed-loop control σε μη κρίσιμο φορτίο με manual override.

Για οικονομική επαλήθευση χρειάζεται μεγαλύτερο baseline από 7 ημέρες όταν υπάρχουν εβδομαδιαίες/εποχικές μεταβολές. Καταγράφονται ανεξάρτητες μεταβλητές όπως θερμοκρασία, ώρες λειτουργίας και παραγωγή. Τα αποτελέσματα αναφέρονται με confidence interval και όχι ως εγγυημένο μηνιαίο saving.

---

## 2. API routes και πηγές για πραγματικά ή near-real-time δεδομένα

### Συνιστώμενες πηγές

| Πηγή | Πρόσβαση / routes | Τι δίνει | Καταλληλότητα και περιορισμοί |
|---|---|---|---|
| **ΔΕΔΔΗΕ Metering Data API** | Base: `https://apps.deddie.gr/mdp/rest`; `POST /getCurves`, `POST /getCurvesv2`, `POST /getMdmIndications`, `POST /retrieveSuppliesList` | Καμπύλες ενεργού/αέργου, παραγωγής/έγχυσης, ενδείξεις και λίστα παροχών | Πολύ σημαντικό για reconciliation. Απαιτεί account, δικαίωμα στο ΑΦΜ/παροχή και access token. Η διαθεσιμότητα/φρεσκάδα εξαρτάται από την τηλεμέτρηση· δεν πρέπει να θεωρηθεί sub-minute stream. |
| **ENTSO-E Transparency Platform** | HTTPS Web API: `https://web-api.tp.entsoe.eu/api` με security token και query parameters όπως document type, bidding zone και time interval | Day-ahead prices, load, generation και άλλα ευρωπαϊκά market/system data | Καλή vendor-neutral πηγή για Ελλάδα/Ευρώπη. Τα XML responses χρειάζονται adapter, retry/cache και mapping EIC codes. Δεν παρέχει facility telemetry. |
| **HEnEx / EnExGroup** | Δημοσιευμένα DAM/IDM αρχεία και market information από το website | Επίσημα ελληνικά market results | Η HEnEx δηλώνει το website ως κύρια δημόσια πηγή. Δεν εντοπίστηκε ανοικτό, τεκμηριωμένο public REST API αντίστοιχο του ENTSO-E. Ο υπάρχων scraper πρέπει να θεωρείται εύθραυστος και να συνοδεύεται από schema/content validation και licensing review. |
| **ΑΔΜΗΕ** | Public market-statistics pages/downloads | Energy balance, RES share, WAMP και συναφή στοιχεία | Χρήσιμο για validation και analytics, όχι για meter-level control. Οι δημοσιευμένες τιμές μπορεί να μην είναι final settlement values. |
| **Shelly Pro 3EM** | Local HTTP RPC `GET /rpc/EM.GetStatus?id=0` ή JSON-RPC `POST /rpc`; `EMData.GetRecords`/`EMData.GetData`; WebSocket/MQTT notifications | Ανά φάση V, A, active/apparent power, PF, frequency και stored intervals | Η πιο γρήγορη διαδρομή για pilot με εμπορικό meter και local access. Να χρησιμοποιηθεί απομονωμένο IoT VLAN, authentication και όχι έκθεση του device στο Internet. |
| **Schneider PowerLogic PM5xxx** | Modbus TCP ή RTU με model-specific register map | Βιομηχανική τριφασική μέτρηση και counters | Κατάλληλο για σοβαρό pilot/reference. Τα register addresses, scales και byte order είναι model/firmware-specific. Το απλό Modbus TCP δεν πρέπει να εκτίθεται δημόσια. |
| **Άλλοι Modbus meters/BMS** | Modbus TCP port 502 ή Modbus RTU/RS-485 μέσω gateway | Vendor-neutral πρόσβαση σε meters, PLCs και BMS | Πρακτικό foundation για universal connectors. Απαιτεί ανά-device register profile και commissioning test. Για νέα ασφαλή δίκτυα εξετάζεται Modbus Security/TLS ή gateway isolation. |
| **Weather forecast** | Open-Meteo `/v1/forecast` με latitude, longitude, timezone και hourly variables | Θερμοκρασία, υγρασία, νέφωση κ.ά. | Χρήσιμο context για HVAC/load forecast. Πρέπει να αποθηκεύεται η forecast issue time ώστε να μη γίνεται data leakage με μεταγενέστερα actuals. |

### Σημαντική παρατήρηση για παρόχους ηλεκτρικής ενέργειας

Δεν πρέπει να βασιστεί η αρχιτεκτονική στην υπόθεση ότι όλοι οι ελληνικοί προμηθευτές διαθέτουν public real-time customer API. Στην παρούσα έρευνα δεν βρέθηκε κοινό, ανοικτό και σταθερό API για όλους τους παρόχους. Η σωστή σειρά προτεραιότητας είναι:

1. local meter/BMS για live facility telemetry,
2. ΔΕΔΔΗΕ για authorized utility curves και reconciliation,
3. ENTSO-E/HEnEx/ΑΔΜΗΕ για market/system data,
4. supplier-specific adapter μόνο όταν υπάρχει επίσημη σύμβαση και documentation.

Τα production credentials πρέπει να διατηρούνται σε secret store, με rotation, least privilege και audit log. Το backend χρειάζεται authentication/authorization πριν εκτεθεί εκτός ιδιωτικού δικτύου.

---

## 3. Hardware και software requirements

### Hardware — προτεινόμενη διάταξη pilot

**Προτιμώμενη επιλογή για γρήγορη και αξιόπιστη δοκιμή:** πιστοποιημένος τριφασικός meter με local API/Modbus και ανεξάρτητο edge gateway. Το custom ESP32 παραμένει παράλληλο R&D channel μέχρι να βαθμονομηθεί.

| Component | Minimum | Recommended pilot |
|---|---|---|
| Main meter | Τριφασικός active-energy meter | Shelly Pro 3EM για χαμηλού κόστους pilot ή Schneider/ισοδύναμος Modbus meter ως reference |
| Measurement | kW και cumulative kWh | V, A, kW, kVA, kvar, PF, Hz ανά φάση και import/export energy |
| Sampling | 60 s ingestion | 1–10 s live telemetry και 1/15-min aggregates |
| Edge gateway | Υπάρχον ESP32 για acquisition | Industrial Raspberry Pi/IPC, Ethernet, watchdog, ≥16 GB reliable storage |
| Connectivity | Wi-Fi με store-and-forward | Ethernet primary, isolated IoT VLAN, optional 4G failover |
| Time | NTP | NTP + RTC, UTC storage, Europe/Athens rendering |
| Safety | Εγκατάσταση από αδειούχο ηλεκτρολόγο | DIN enclosure, fused supply, isolation, commissioning against reference instrument |
| Control | Κανένα στην πρώτη φάση | Dry-contact/PLC control μόνο μετά το advisory pilot, με interlock και manual override |

Το ESP32 ADC υποστηρίζει continuous sampling, αλλά η επίσημη τεκμηρίωση σημειώνει hardware constraints και κοινή χρήση του ADC2 με Wi-Fi. Για metrology απαιτούνται ADC calibration, anti-alias filtering, κατάλληλο isolated voltage sensing και εργαστηριακή σύγκριση. Ένα hobby voltage module από μόνο του δεν αποτελεί απόδειξη CAT III/CE ασφάλειας.

### Software — minimum production baseline

- **Connector layer:** adapters για `deddie`, `entsoe`, `henex_file`, `shelly_rpc`, `mqtt`, `modbus_tcp`, `modbus_rtu`, `csv`.
- **Canonical schema:** versioned Pydantic models με units, provenance και quality flags. Για building semantics προτείνεται mapping προς **Brick Schema** ή, εναλλακτικά, Project Haystack tags.
- **Ingestion:** idempotency, deduplication, late/out-of-order data, backfill και dead-letter queue.
- **Storage:** PostgreSQL/TimescaleDB ή αντίστοιχη time-series βάση για multi-site production. Το SQLite μπορεί να παραμείνει για demo/single-site pilot.
- **Forecasting:** per-site model registry, rolling backtest, drift detection και simple-baseline fallback.
- **Optimization:** rolling horizon, re-solve μετά από νέα μέτρηση/αλλαγή, uncertainty margins και hard safety constraints.
- **Security:** API authentication, RBAC ανά facility, TLS, encrypted secrets, signed device identity, audit log και rate limiting.
- **Operations:** metrics για data freshness/completeness, structured logs, backups, health checks και alerts.
- **Testing:** recorded-fixture contract tests για κάθε connector, timezone/DST tests, hardware-in-the-loop test και replay σε held-out telemetry.

---

## 4. Universal data support — δεύτερη φάση, αλλά σχεδιασμός από τώρα

«Universal» δεν σημαίνει ότι κάθε vendor στέλνει το ίδιο payload. Σημαίνει ότι κάθε vendor adapter υλοποιεί κοινό contract:

```python
class TelemetryConnector(Protocol):
    async def discover(self) -> list[SourceDescriptor]: ...
    async def fetch(self, start, end) -> list[CanonicalReading]: ...
    async def stream(self) -> AsyncIterator[CanonicalReading]: ...
    async def health(self) -> ConnectorHealth: ...
```

Προτεινόμενη ροή:

```text
Vendor/API/Modbus/CSV
        ↓
Source adapter + unit/timezone mapping
        ↓
Validation + quality flags + deduplication
        ↓
Canonical telemetry/event schema
        ↓
Time-series store
        ↓
Forecast → Optimizer → Recommendation → Verification
```

Κάθε reading κρατά και το raw payload ή hash/reference του, ώστε να είναι audit-able. Το optimization layer δεν πρέπει να γνωρίζει αν η μέτρηση ήρθε από Shelly, Schneider ή CSV.

Για semantic portability, το **Brick Schema** δίνει cross-vendor μοντέλο για buildings, equipment, meters, points και relationships. Το **Project Haystack** είναι ελαφρύτερη tag-based επιλογή και περιλαμβάνει operations όπως `hisRead`, `hisWrite` και subscriptions. Προτείνεται Brick για canonical asset graph και απλό JSON/Pydantic schema για την operational hot path· δεν χρειάζεται πλήρης RDF υποδομή στο πρώτο pilot.

---

## 5. Schedule creator βάσει προτιμήσεων

Το υπάρχον `/api/v1/optimization/solve` δέχεται κυρίως 24 ωριαίες τιμές και demo assets. Χρειάζεται persistent schedule domain model που να μετατρέπει τις ανθρώπινες προτιμήσεις σε hard και soft constraints.

### Preferences που πρέπει να υποστηρίζονται

- Επιτρεπτές ημέρες και χρονικά παράθυρα ανά asset.
- Earliest start, latest finish, διάρκεια και αν επιτρέπεται interruption.
- `must_run`, required cycles/day και minimum rest between cycles.
- Θερμοκρασιακά/comfort όρια και food-safety constraints.
- Μέγιστη ταυτόχρονη ισχύς ή ασύμβατα ζεύγη συσκευών.
- Προτεραιότητα: `safety > operations > comfort > cost > CO2`.
- Soft preference με penalty, π.χ. «κατά προτίμηση μετά τις 14:00».
- Blackout dates, αργίες, έκτακτες παραγγελίες και manual lock.
- Notification lead time και κανάλι ειδοποίησης.
- Αν η πρόταση απαιτεί `manual approval` ή επιτρέπεται auto-execution.

### Προτεινόμενα API routes

```text
POST   /api/v1/facilities/{facility_id}/assets
GET    /api/v1/facilities/{facility_id}/assets
POST   /api/v1/facilities/{facility_id}/schedule-preferences
GET    /api/v1/facilities/{facility_id}/schedule-preferences
PUT    /api/v1/facilities/{facility_id}/schedule-preferences/{preference_id}
POST   /api/v1/facilities/{facility_id}/schedules/preview
POST   /api/v1/facilities/{facility_id}/schedules/publish
GET    /api/v1/facilities/{facility_id}/schedules/{date}
POST   /api/v1/facilities/{facility_id}/schedules/{schedule_id}/accept
POST   /api/v1/facilities/{facility_id}/schedules/{schedule_id}/reject
POST   /api/v1/facilities/{facility_id}/schedules/{schedule_id}/override
```

### Συμπεριφορά scheduler

1. Φορτώνει preferences, equipment constraints, πραγματικό load forecast και tariff forecast.
2. Ελέγχει data freshness/quality. Αν αποτύχει, παράγει conservative fallback ή καμία πρόταση.
3. Λύνει το MILP με hard constraints απαραβίαστα και soft preferences ως weighted penalties.
4. Εκτελεί replay/stress test με forecast uncertainty πριν δημοσιεύσει το schedule.
5. Εμφανίζει στον χρήστη κόστος, peak, confidence, assumptions και ποια preference άλλαξε το αποτέλεσμα.
6. Αποθηκεύει accept/reject/override ως feedback, χωρίς να αλλάζει αυτόματα safety constraints.
7. Κάνει re-optimization όταν αλλάξει tariff, forecast, equipment availability ή manual preference.

Στην πρώτη έκδοση ο schedule creator πρέπει να είναι **advisory-only**. Auto-execution επιτρέπεται αργότερα μόνο για συγκεκριμένο asset, με fail-safe state, local interlock, timeout και φυσικό manual override.

---

## 6. Προτεινόμενη σειρά υλοποίησης

### Phase 0 — Data audit (1 εβδομάδα)

- Προσθήκη canonical schema και quality flags.
- Διόρθωση ορολογίας στα reports: `measured`, `estimated`, `market`, `synthetic`.
- Αυτόματο completeness/freshness report ανά source/facility/day.

### Phase 1 — Real-site pilot (2–6 εβδομάδες)

- Shelly Pro 3EM ή Modbus reference meter.
- Connector και parallel ingestion στο υπάρχον API.
- ΔΕΔΔΗΕ backfill/reconciliation όπου υπάρχει εξουσιοδοτημένη τηλεμετρούμενη παροχή.
- Shadow forecast/schedule και εβδομαδιαίο report, χωρίς control.

### Phase 2 — Universal connectors

- Κοινό connector contract και adapters για Shelly, Modbus, CSV και ENTSO-E.
- Contract-test fixtures, unit/timezone normalization και asset mapping.
- Μετάβαση production storage από SQLite σε PostgreSQL/time-series extension όταν υπάρχει multi-site ανάγκη.

### Phase 3 — Preference scheduler

- Persistent assets/preferences.
- Preview/publish/accept/reject workflow.
- Rolling re-optimization και conservative uncertainty margins.
- Limited closed-loop trial μόνο μετά από επιτυχημένο advisory pilot.

### Product gate

Το προϊόν μπορεί να χαρακτηριστεί field-ready όταν υπάρχουν τουλάχιστον:

- 30–60 ημέρες αξιόπιστης telemetry από πραγματική ελληνική εγκατάσταση,
- documented meter comparison και known uncertainty,
- forecast που νικά σταθερά τα simple baselines,
- μηδενικές παραβιάσεις hard constraints σε replay,
- μετρημένο acceptance rate και operational feedback χρηστών,
- οικονομικό αποτέλεσμα με πραγματικές τιμές/λογαριασμούς και σαφή baseline,
- authentication, backups, monitoring και recovery test,
- ολοκληρωμένος safety/compliance έλεγχος πριν από εγκατάσταση ή control σε ηλεκτρικό πίνακα.

### Schedule Studio: Συμβουλευτικός Προγραμματισμός & Αντιμετώπιση Replay Infeasibility

Στην ανάλυση πραγματικών δεδομένων (Building Data Genome 2) διαπιστώθηκε ότι τα αυστηρά μαθηματικά προγράμματα χωρίς περιθώριο αβεβαιότητας εμφάνιζαν **26,2% έως 33,0% infeasibility** κατά το replay, επειδή τυχαίες διακυμάνσεις του βασικού φορτίου υπερέβαιναν οριακά το όριο συμφωνημένης ισχύος.

Για την επίλυση αυτού του κρίσιμου ζητήματος και την κάλυψη των αναγκών των ΜμΕ, υλοποιήθηκε το **Generic Schedule Studio** (`optimization_engine/scheduling_service.py`, `backend/routes/schedules.py`):

1. **Συντηρητικό Περιθώριο Αβεβαιότητας (+10% Buffer):** Το βασικό φορτίο πολλαπλασιάζεται με συντελεστή ασφαλείας $1.10$ ($P_{\text{base, cons}} = 1.10 \times P_{\text{base}}$) στους περιορισμούς μέγιστης ισχύος, αποτρέποντας την ενεργοποίηση θερμομαγνητικών ασφαλειών σε πραγματικές συνθήκες.
2. **Αυστηρά Συμβουλευτική Λειτουργία (Advisory-Only):** Σύμφωνα με την απόφαση Go/No-Go, αποφεύγεται η αυτόματη ενεργοποίηση φυσικών ρελέ χωρίς ανθρώπινη επίβλεψη. Το σύστημα παρέχει αναλυτικές επεξηγήσεις στα Ελληνικά (`explanation_el`) ανά συσκευή και ο υπεύθυνος διατηρεί τον χειροκίνητο έλεγχο.
3. **Ευέλικτη Χρονική Ανάλυση (Multi-Resolution):** Υποστήριξη διαστημάτων 5, 15, 30 και 60 λεπτών, ευθυγραμμισμένων με τα 15-λεπτα διαστήματα μέτρησης του ΔΕΔΔΗΕ.
4. **Μοντελοποίηση Πραγματικών Συσκευών ΜμΕ:** Συνεχόμενη λειτουργία (non-interruptible) για φούρνους και πλυντήρια, διακοπτόμενη κατανομή για θερμοσίφωνες και EV chargers, υποστήριξη νυχτερινών παραθύρων (π.χ. 22:00-06:00) και διαχείριση προτεραιοτήτων (must-run vs optional).

---

## Πηγές

- [ΔΕΔΔΗΕ Web Services](https://apps.deddie.gr/rps/ws-index/ws-index.html)
- [ΔΕΔΔΗΕ Metering Data Swagger](https://apps.deddie.gr/rps/swagger/index.html?swagger_url=https%3A%2F%2Fapps.deddie.gr%2Fmdp%2Frest%2Fswagger.json)
- [ΔΕΔΔΗΕ Πύλη Μετρητικών Δεδομένων](https://apps.deddie.gr/mdp/intro.html)
- [ENTSO-E API token management](https://transparency.entsoe.eu/content/static_content/download?path=%2FStatic%20content%2FAPI-Token-Management.pdf)
- [ENTSO-E Transparency Platform data-extraction guide](https://transparency.entsoe.eu/content/static_content/download?path=%2FStatic%20content%2Fweb%20api%2FIG-for-TP-data-extraction-process.pdf)
- [HEnEx Spot Trading Rulebook](https://www.enexgroup.gr/documents/20126/144557/20250925_Spot_Trading_Rulebook_v2.4_en.pdf)
- [ΑΔΜΗΕ Weighted Average Market Price](https://www.admie.gr/en/market/market-statistics/key-data/weighted-average-market-price)
- [Shelly Gen2 Energy Meter API](https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EM/)
- [Shelly EMData API](https://shelly-api-docs.shelly.cloud/gen2/0.14/ComponentsAndServices/EMData/)
- [Shelly RPC channels](https://shelly-api-docs.shelly.cloud/gen2/General/RPCChannels/)
- [Schneider PowerLogic PM5000 Modbus register lists](https://www.se.com/uk/en/faqs/FA234017/)
- [Modbus specifications and security](https://www.modbus.org/modbus-specifications)
- [Espressif ESP32 ADC continuous mode](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/adc_continuous.html)
- [Espressif ESP32 ADC calibration](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/adc.html)
- [Brick: uniform metadata schema for buildings](https://brickschema.org/)
- [Project Haystack introduction](https://project-haystack.org/doc/docHaystack/Intro)
- [OpenADR 3.0](https://www.openadr.org/openadr-3-0)
- [Open-Meteo Forecast API](https://open-meteo.com/en/docs)
- [Building Data Genome Project 2](https://github.com/buds-lab/building-data-genome-project-2)

## Repository evidence used

- `reports/real_data/REPORT.md`
- `reports/real_data/forecast_metrics.csv`
- `reports/real_data/data_quality.csv`
- `reports/real_data/manifest.json`
- `backend/routes/telemetry.py`
- `backend/routes/market.py`
- `backend/routes/optimization.py`
- `optimization_engine/models.py`
- `docs/forecasting_data_sources.md`
- `docs/product/field_pilot_protocol_ipmvp.md`

