# Regulatory constants and external identifiers register

Statuses: OPEN (not researched), FOUND (source located, not cross-checked), VERIFIED
(checked against the official text AND, for charges, reproduced on a real bill).
Only VERIFIED values may appear in `rules/gr/` or code. Press articles are leads, not sources.
Upstream values in `docs/legacy/` and the legacy `tariff_engine/` are NOT sources.

| Rule set / item | What to find | Lead | Status |
|---|---|---|---|
| gr-dist-network: dist_fixed | ΧΧΔ fixed charge per kVA-year, per LV category | Latest RAAEY ΧΧΔ decision; DEDDIE charges page | OPEN |
| gr-dist-network: dist_variable | ΧΧΔ variable charge per kWh, per LV category | Same | OPEN |
| gr-dist-network: cos phi rule | Which categories have reactive metering; kWh x charge / cos phi; any floor or threshold | PPC services form (formula, older text); confirm in current decision | FOUND (formula only) |
| gr-system | ΧΧΣ per category, decision E-189/2025, in force 1 Feb 2026 | ΦΕΚ of E-189/2025 | FOUND |
| gr-pso | ΥΚΩ unit charges for non-residential | RAAEY / ministerial decision | OPEN |
| gr-res-levy | ΕΤΜΕΑΡ for commercial LV | RAAEY | OPEN |
| gr-excise | ΕΦΚ for business use | Customs code | OPEN |
| gr-dete | ΔΕΤΕ 5 per mille: exact base it applies to | Law 2093/1992 art. 9 as currently applied on bills | OPEN |
| gr-vat | VAT rate on electricity | AADE | OPEN |
| gr-losses | LV loss multiplier 1.1517 from 1 Nov 2026 (E-224/2026); does it reach customer bills? | ΦΕΚ of E-224/2026 | FOUND |
| gr-time-windows | Current reduced-rate / time-of-use windows per product | DEDDIE, supplier sheets | OPEN |
| green tariff mechanism | Parameters of the special tariff formula (law 5066/2023 art. 17 and its ministerial decision, ΦΕΚ Β 6600/2023) | ΦΕΚ Β 6600/2023 | FOUND (law and ΦΕΚ); parameters OPEN |
| supplier orange formulas | How PPC, Protergia, Zenith, Heron, Enerwave form the hourly price | Supplier product sheets | OPEN |
| ENTSO-E bidding zone EIC for Greece | Exact code (upstream used 10YGR-HTSO-----8; believed correct is 10YGR-HTSO-----Y) | ENTSO-E area code list | OPEN |
| HEnEx results file | URL pattern and column layout of the daily DAM results file | enexgroup.gr publications | OPEN |
