# Glossary (Greek term -> code name)

| Greek | English meaning | Code name |
|---|---|---|
| Αριθμός παροχής | supply point number | `facility_configs.supply_number` (phase 5) |
| Συμφωνημένη ισχύς | contracted capacity (kVA) | `facility_configs.contracted_kva` |
| Χαμηλή / Μέση Τάση (ΧΤ / ΜΤ) | low / medium voltage | `voltage: LV / MV` |
| ΔΕΔΔΗΕ | distribution network operator (HEDNO) | `DSO` |
| ΑΔΜΗΕ | transmission system operator (IPTO) | `TSO` |
| ΡΑΑΕΥ | energy regulator (RAAEY, formerly RAE) | `regulator` |
| ΕΧΕ / HEnEx | Hellenic Energy Exchange | `source: HENEX` |
| Αγορά Επόμενης Ημέρας | day-ahead market | `market_dam_prices` |
| ΧΧΔ | distribution network use charge | `rule_set: gr-dist-network` |
| ΧΧΣ | transmission system use charge | `rule_set: gr-system` |
| ΥΚΩ | public service obligations charge | `rule_set: gr-pso` |
| ΕΤΜΕΑΡ | renewables levy | `rule_set: gr-res-levy` |
| ΕΦΚ | electricity excise tax | `rule_set: gr-excise` |
| Συντελεστής ισχύος (συνφ) | power factor (cos phi) | `system_power_factor`, `cos_phi_period` |
| Άεργος ενέργεια | reactive energy | `cumulative_reactive_kvarh` |
| Έναντι λογαριασμός | estimated bill | `bills` (phase 10) |
| Εκκαθαριστικός λογαριασμός | settlement bill | `bills` (phase 10) |
| Μπλε / Πράσινο / Κίτρινο / Πορτοκαλί τιμολόγιο | fixed / special monthly / variable / dynamic tariff | `tariff_kind` |
| Ευφυής μετρητής | DSO smart meter | `facility_configs.has_dso_smart_meter` |
