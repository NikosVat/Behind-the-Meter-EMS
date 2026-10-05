# Phase plan (fork)

One phase = one Claude Code session = one commit. Run `/clear` before each phase, then
`/phase <n>`. Order: clean and secure the fork, build the new pure core beside the legacy
engine, switch callers over, then market data, meters, pilot tooling, deployment.

| # | Phase | Needs |
|---|---|---|
| 0 | Human prep + bootstrap | - |
| 1 | Tooling, CI, trim out-of-scope code | - |
| 2 | Honesty and fail-closed auth | - |
| 3 | billing_core: time and money | - |
| 4 | billing_core: rule book and cost engine | Q1-Q4 for VERIFIED golden test |
| 5 | Telemetry v0.2 and 15-min interval energy | - |
| 6 | Switch callers to billing_core, delete legacy tariff engine | phase 4 |
| 7 | Optimizer adapter: 15-min prices, price_basis, durable cards | phase 6 |
| 8 | Market data: ENTSO-E 15-min, no synthetic advice | Q7 |
| 9 | meter_bridge for certified meters | Shelly on the bench |
| 10 | Pilot tooling: IPMVP baseline, bill reconciliation | Q6 |
| 11 | Deploy and harden for one pilot | Q9 |
| 12 | Shadow pilot and report | Q6, Q8 |

---

## Phase 0

Human tasks. Done when every box is ticked.

- [ ] Fork the upstream repo on GitHub into your account (Fork button), clone your fork.
- [ ] Run the bootstrap script from this kit (see kit README). Push the result.
- [ ] Install Python 3.12 and Claude Code. In the repo run `claude`, then `/memory` and check CLAUDE.md loaded.
- [ ] Five owner interviews: tariff colour, demand or reactive charges on the bill, which messenger they read.
- [ ] One real anonymised commercial bill plus its tariff sheet (Q6).
- [ ] ENTSO-E Transparency Platform API token (Q7). Buy one Shelly Pro 3EM for the bench.
- [ ] Start `docs/RULES_SOURCES.md` from official sources (Q1 to Q4).

---

## Phase 1

```
Read CLAUDE.md and docs/FORK_MANIFEST.md (grep the rows marked "phase 1").
Do not read docs/legacy/.

Task: make the fork a clean, checked baseline.
1. Create tools/check.py (cross-platform): runs `python -m ruff check .`,
   `python -m mypy billing_core` (skip with a notice if billing_core does not exist yet),
   `python -m pytest -q`, and exits non-zero on any failure.
2. pyproject.toml: requires-python ">=3.12"; remove the "firmware" and "research" extras;
   dev extras: pytest, pytest-asyncio, hypothesis, ruff, mypy; runtime: add pyyaml.
   Keep requirements.txt consistent. Delete uv.lock if it no longer matches, and say so.
3. Delete tariff_engine/adapters/german.py, spanish.py, tariff_engine/benchmark_dataset.py,
   tariff_engine/validator.py; update tariff_engine/__init__.py and
   tests/unit/test_market_adapters.py so nothing references them.
4. Create tests/test_no_actuation.py: scan all .py files outside tests/ and docs/ for
   write_register(s), write_coil(s), "Switch.", ".Set" RPC names and any route or client
   that sends commands to devices; fail if found.
5. GitHub Actions workflow running `python tools/check.py` on push and pull request.
6. Fix ruff findings only where trivial; list the rest in your report, do not mass-reformat.
Verification: `python tools/check.py` passes. Baseline after bootstrap was 647 passing tests; report the count before and after.
Commit: "chore: fork baseline, tooling, trim out-of-scope modules"
```

---

## Phase 2

```
Read CLAUDE.md. Grep docs/CONTRACT.md for I2 and I10 in "## C9".

Task: remove false claims and close the auth hole.
1. Write a new README.md (max 120 lines): what it does, the advisory-only rule, status
   "TRL 4, no field deployment yet", quickstart, link to NOTICE.md and docs/.
   No badges except CI. No law numbers, no standards claims, no accuracy claims.
2. Replace every "5068/2023" outside docs/legacy/ with "5066/2023" only where it refers to
   the special (green) tariff law; delete other legal citations in code and docstrings that
   are not listed in docs/RULES_SOURCES.md, replacing them with "see docs/RULES_SOURCES.md".
3. backend/security.py: fail closed. If API_KEY is empty, every protected route returns 503
   with "API key not configured", unless ENVIRONMENT == "development" AND a new explicit
   setting ALLOW_OPEN_DEV_ACCESS is true. Update backend/config.py and tests.
4. Tests: protected routes reject requests when API_KEY is unset in production mode.
Verification: `python tools/check.py`; `grep -rn "5068" --include=*.py .` returns nothing.
Commit: "fix: remove unsupported claims, fail-closed authentication"
```

---

## Phase 3

```
Read CLAUDE.md. Grep docs/CONTRACT.md: read "## C1" and I4, I5, I7 in "## C9".

Task: create the pure package billing_core/ with timeutil.py and money.py.
- timeutil: floor_to_interval, local_day_intervals(date) -> list of UTC starts,
  interval_index and inverse, to_local/to_utc rejecting naive datetimes.
- money: to_decimal(x) per C1, quantize helpers (6 dp internal, 2 dp display),
  price unit conversion EUR_MWH <-> EUR_KWH with explicit units, negative prices allowed.
- No imports from backend/, bot/, optimization_engine/, tariff_engine/.
Tests in tests/billing_core/ with hypothesis: 2026-03-29 -> 92, 2026-10-25 -> 100,
2026-10-04 -> 96; round trips; negative and zero prices; no float in money outputs.
Verification: `python tools/check.py` (mypy strict on billing_core passes).
Commit: "feat(billing_core): interval and money primitives"
```

---

## Phase 4

```
Read CLAUDE.md. Grep docs/CONTRACT.md: "## C4", "## C5", I2, I5. Grep docs/RULES_SOURCES.md
for its table only.

Task: rule book and cost engine in billing_core.
1. billing_core/rules.py: load rules/gr/*.yaml and (dev only) rules/illustrative/*.yaml into
   a validated RuleBook; select by period; reject overlaps; RulesIncomplete on PLACEHOLDER.
2. billing_core/cost.py: compute_costs() per C5. Components: supplier energy, dist fixed,
   dist variable (1/cos_phi_period only if has_reactive_metering), system charges, PSO,
   RES levy, excise, VAT (percent_of). Municipal fees and other flat lines: flat_month.
3. rules/gr/: one PLACEHOLDER file per rule set in RULES_SOURCES.md; never type a number
   that is not marked VERIFIED there.
4. rules/illustrative/: one complete demo set with obviously fake round numbers and
   status ILLUSTRATIVE.
5. Tests: synthetic fixtures; PLACEHOLDER raises; ILLUSTRATIVE refused outside development;
   Decimal preserved. If tests/fixtures/real_bill/ exists, golden test within 1 percent;
   otherwise create it with a README explaining what to drop there.
Do not touch tariff_engine/.
Verification: `python tools/check.py`.
Commit: "feat(billing_core): rule book and cost engine"
```

---

## Phase 5

```
Read CLAUDE.md. Grep docs/CONTRACT.md: "## C2" (rows marked phase 5), "## C3", I3, I6.

Task: telemetry v0.2 and interval energy.
1. backend/models/telemetry.py: add optional cumulative_reactive_kvarh, cumulative_export_kwh,
   meter_model. Keep extra="forbid" and allow_inf_nan=False. Old payloads stay valid.
2. backend/database/sqlite_store.py: migrations for the phase-5 columns and the
   interval_energy_15m table, idempotent on existing databases.
3. New backend/aggregation.py: build interval_energy_15m from counter deltas with coverage,
   reset detection and no negative energy; replace the naive hourly averaging upstream used.
4. Mark facilities whose latest data is not "meter_measured" as not advice-eligible
   (a function the later phases call).
5. Tests with the simulator: gaps, a counter reset, DST days, coverage values.
Verification: `python tools/check.py`.
Commit: "feat(telemetry): v0.2 counters and 15-minute interval energy"
```

---

## Phase 6

```
Read CLAUDE.md. Grep docs/CONTRACT.md: "## C5", "## C7", I2, I5.
Callers of the legacy engine: backend/routes/dashboard.py, facilities.py, telemetry.py,
bot/command_handlers.py, bot/dispatcher.py, backend/market/service.py, scraper_rae.py.

Task: move every caller from tariff_engine to billing_core, then delete tariff_engine.
Post a plan listing each call site and its replacement first; wait for OK.
- Peak/off-peak checks come from rule-file time_windows, not code constants.
- Cost endpoints and /cost_today return Decimal-backed values with is_estimate and the
  rule versions used; when rules are PLACEHOLDER they return a clear "rules incomplete" state.
- Replace legacy tariff tests with billing_core-based tests; delete tests that only checked
  legacy invented constants (list them in the report).
- Finally delete tariff_engine/ and confirm nothing imports it.
Verification: `python tools/check.py`; `grep -rn "tariff_engine" --include=*.py .` is empty.
Commit: "refactor: replace legacy tariff engine with billing_core"
```

---

## Phase 7

```
Read CLAUDE.md. Grep docs/CONTRACT.md: "## C6", I6, I8, I9.

Task: adapt optimization_engine without rewriting the MILP.
1. optimization_engine/price_inputs.py: returns a 15-min price vector (92/96/100) and
   price_basis from stored prices; raises if only seeded or synthetic prices exist.
2. decision_support.py: every card carries price_basis; savings come from billing_core
   applied to baseline vs optimized schedules; DAM_ONLY savings labelled estimate.
3. Persist cards, acknowledgements and verifier outcomes in recommendation_cards (C2).
4. Refuse to generate advice for facilities that are not advice-eligible (phase 5).
5. Hypothesis property tests: no window, contiguity or demand-cap violation; savings never
   exceed baseline minus optimized cost.
Verification: `python tools/check.py`.
Commit: "feat(optimization): 15-min inputs, price basis, durable cards"
```

---

## Phase 8

```
Read CLAUDE.md. Grep docs/CONTRACT.md for the market_dam_prices row of "## C2" and I6.

Task: replace the market data path.
1. backend/market/entsoe.py: day-ahead prices for the Greek bidding zone (read the zone
   code from docs/RULES_SOURCES.md; stop if it is not VERIFIED), 15-min and 60-min
   resolution, UTC output, token from ENTSOE_API_TOKEN.
2. backend/market/henex_files.py: parser for the HEnEx results file as backup source
   (fixture provided by us in tests/fixtures/henex/; skip with reason if absent).
3. Store into market_dam_prices idempotently. Remove the seeded and synthetic tiers from any
   path that feeds recommendations; keep them only behind a DEMO flag for the dashboard,
   visibly labelled.
4. Delete the unverified JSON endpoint in fetcher_henex.py.
5. CLI: `python -m backend.market.entsoe --date YYYY-MM-DD` prints count, min, max.
Tests use recorded fixtures only.
Verification: `python tools/check.py`; one live CLI run pasted in the report.
Commit: "feat(market): ENTSO-E 15-minute prices, no synthetic advice"
```

---

## Phase 9

```
Read CLAUDE.md. Grep docs/CONTRACT.md: "## C3", I1.

Task: meter_bridge/ package (minimal deps: httpx, pymodbus).
1. Shelly Pro 3EM reader using local read-only RPC (EM.GetStatus, EMData.GetStatus);
   Modbus TCP reader using only read_holding_registers / read_input_registers, register
   maps in YAML per meter model.
2. Builds the C3 payload with power_measurement_method "meter_measured", posts every 60 s,
   7-day SQLite buffer with backfill, exponential backoff, per-device token header.
3. systemd unit and a Windows service note for running on an always-on PC.
4. Tests with a fake Shelly HTTP server and a pymodbus simulator; no-actuation test covers
   meter_bridge/.
Verification: `python tools/check.py`; 30-minute bench run with the real Shelly; report
energy delta from the meter vs the stored interval_energy_15m sum (under 0.5 percent).
Commit: "feat(meter_bridge): read-only certified meter bridge"
```

---

## Phase 10

```
Read CLAUDE.md and docs/pilot/field_pilot_protocol_ipmvp.md. Grep docs/CONTRACT.md for the
bills row of "## C2".

Task: pilot measurement tooling.
1. Baseline model per the protocol (Option B), using interval_energy_15m and the facility's
   pre-pilot weeks; report savings with uncertainty, never a single unqualified number.
2. Bill upload (PDF stored, lines entered by hand) and reconciliation: billing_core total vs
   bill total per line; flag any gap above 2 percent.
3. A report generator (Markdown) for the weekly pilot review.
Verification: `python tools/check.py`; a sample report from simulator data.
Commit: "feat(pilot): IPMVP baseline and bill reconciliation"
```

---

## Phase 11

```
Read CLAUDE.md and docs/ARCHITECTURE.md section "## 3. Daily timeline".

Task: deploy one pilot safely.
- Scheduler for the timeline jobs; every job idempotent.
- Per-device tokens (hashed in DB) replacing the shared key for telemetry.
- Caddy reverse proxy with HTTPS, systemd units, nightly SQLite online backup with a tested
  restore, log scrubbing of personal data, data export and delete for one facility.
- deploy/README.md runbook.
Verification: `python tools/check.py`; restore of a backup into a scratch file succeeds.
Commit: "chore(ops): pilot deployment and hardening"
```

---

## Phase 12

```
Read CLAUDE.md and PROJECT_STATE.md "Open questions". Stop if Q6, Q8 or Q9 is OPEN.

Task: support a 14-day shadow pilot: plans go only to us, not the customer. Produce the
pilot report from phase 10 tooling: computed vs real bill, savings estimate with uncertainty,
data quality, and a go/no-go recommendation.
Commit: "docs: shadow pilot report"
```
