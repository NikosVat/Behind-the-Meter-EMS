# Project State: BTM-EMS (fork)

The toggle below controls how the planning chat (the "conductor") works.
BRAIN_RESET: yes
<!-- yes = each work session starts a fresh planning chat that reads this file first.
     Change to "no" for one long planning conversation. -->

## One-liner
Advisory energy platform for Greek 25 to 250 kVA commercial sites, built on a fork of
Behind-the-Meter-EMS. Done = one shadow pilot where computed cost is within 2% of the real
bill and savings are measured with the IPMVP protocol.

## Contract
- File: `docs/CONTRACT.md` v0.2
- Governs: storage changes, telemetry payload, rule files, engine I/O, invariants I1-I10
- Last amended: fork bootstrap

## Environment & access
- Stack: Python 3.12, FastAPI, SQLite WAL, SciPy HiGHS, httpx, PyYAML
- Upstream: DimThanasoulias/Behind-the-Meter-EMS @ a9f04b9; full copy on branch archive/upstream-20261005
- Heavy reference material: `docs/` (grep only); `docs/legacy/` is read-denied for Claude Code
- Secrets: `.env` locally; server env file in production. Names in `env.example`.

## Phase plan & status

| # | Phase | Status | Commit | Verified by |
|---|---|---|---|---|
| 0 | Human prep + bootstrap | TODO | - | checklist |
| 1 | Tooling, CI, trim | TODO | - | - |
| 2 | Honesty, fail-closed auth | TODO | - | - |
| 3 | billing_core time and money | TODO | - | - |
| 4 | billing_core rule book and cost | TODO | - | - |
| 5 | Telemetry v0.2, 15-min energy | TODO | - | - |
| 6 | Callers to billing_core, delete tariff_engine | TODO | - | - |
| 7 | Optimizer adapter | TODO | - | - |
| 8 | Market data | TODO | - | - |
| 9 | meter_bridge | TODO | - | - |
| 10 | Pilot tooling | TODO | - | - |
| 11 | Deploy and harden | TODO | - | - |
| 12 | Shadow pilot report | TODO | - | - |

## Current phase: 0
- Goal: fork, bootstrap, interviews, one real bill, ENTSO-E token, bench meter.
- Verification gate: every box in PHASES.md "## Phase 0" ticked; bootstrap commit pushed.
- Status right now: not started.
- Next action: fork on GitHub, clone, run bootstrap.

## Decisions log (append-only)

- **D1:** Launch segment 25 to 250 kVA LV sites with demand, reactive or orange tariffs.
  Rationale: penalties and intra-day prices apply there; savings are measurable in euros.
- **D2:** Advisory only; all device access read-only. Rationale: safety, liability, compliance load.
- **D3:** Python only; upstream FastAPI + server-rendered dashboard kept. Rationale: one language,
  existing working UI.
- **D4:** SQLite WAL for the pilot; Postgres/TimescaleDB only after about 10 sites.
  Rationale: upstream already runs on SQLite; the stable payload keeps migration cheap.
  (Supersedes the original kit's TimescaleDB-from-day-one choice.)
- **D5:** HTTPS POST from a read-only meter_bridge; MQTT deferred. Rationale: matches upstream
  ingest; fewer moving parts for one pilot. (Supersedes the original kit's MQTT broker.)
- **D6:** Regulatory values only from sourced, versioned YAML; PLACEHOLDER blocks computation.
  Rationale: upstream hard-codes unsourced constants, wrong citations and a circular bill audit.
- **D7:** Telegram live for the pilot; Viber code kept but disabled. Revisit after interviews:
  Viber is the dominant messenger in Greece, but bots cost EUR 100/month plus message fees.
- **D8:** Keep the upstream HiGHS MILP instead of writing a greedy optimizer.
  Rationale: it works, is tested, and solves in milliseconds. (Supersedes the original kit's
  greedy-first plan.)
- **D9:** 15-minute canonical interval. Rationale: DAM MTU is 15 min since 1 Oct 2025.
- **D10:** Fork upstream (MIT) rather than start from zero. Keep optimization_engine, simulator,
  bot, backend; replace tariff_engine with a new billing_core; remove firmware, calibration,
  scripts, reports and foreign adapters from main. Rationale: reuse working code, remove the
  unverifiable parts. Full mapping in docs/FORK_MANIFEST.md.
- **D11:** No advice from estimated, simulated, seeded or synthetic data. Rationale: upstream
  silently fell back to synthetic prices and assumed 230 V / cos phi 0.95 on ESP32 data.
- **D12:** BRAIN_RESET yes. Rationale: 12 phases; a single long chat is costly and drifts.

## Open questions & external blockers

- **Q1:** Current ΧΧΔ charges per LV category and the cos phi rule. Before: phase 4 golden test. OPEN.
- **Q2:** ΧΧΣ charges (E-189/2025 ΦΕΚ text). Before: phase 4. OPEN.
- **Q3:** ΥΚΩ, ΕΤΜΕΑΡ, ΕΦΚ, ΔΕΤΕ base, VAT for commercial LV. Before: phase 4. OPEN.
- **Q4:** Current time-of-use windows per product. Before: phase 6. OPEN.
- **Q5:** Orange price formula of the five obligated suppliers. Before: any savings claim. OPEN.
- **Q6:** Pilot site, one anonymised real bill, tariff sheet. Before: phase 4 golden test, phase 12. OPEN.
- **Q7:** ENTSO-E token and verified Greek bidding-zone EIC. Before: phase 8. OPEN.
- **Q8:** Licensed electrician partner. Before: pilot install. OPEN.
- **Q9:** Legal entity, privacy notice, data processing agreement, retention. Before: phase 11. OPEN.
- **Q10:** Which messenger pilot owners read (Viber vs Telegram). Before: phase 11. OPEN.
- **Q11:** Is the upstream author informed or involved? MIT allows the fork; courtesy and
  possible collaboration are a team decision. OPEN.

## Verification log
(empty)

## How to resume (BRAIN_RESET: yes)
1. Read this file, then CLAUDE.md, then the contract sections the next phase needs.
2. Skim the decisions log; do not re-argue settled choices.
3. Do not build past an OPEN blocker for the current phase.
4. Go to "Current phase" -> "Next action".
