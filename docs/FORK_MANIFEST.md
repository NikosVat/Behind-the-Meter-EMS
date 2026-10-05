# Fork manifest: what happens to every upstream path

Bootstrap verified on a fresh clone of a9f04b9 (4 Oct 2026): clean commit, 647 tests passed.

Upstream: https://github.com/DimThanasoulias/Behind-the-Meter-EMS (main, commit a9f04b9,
cloned 4 Oct 2026). The complete upstream tree stays on branch `archive/upstream-20261005`.
"Bootstrap" means `bootstrap.sh` / `bootstrap.ps1` does it automatically.

| Upstream path | Decision | When | Why |
|---|---|---|---|
| `optimization_engine/` | KEEP | - | Working MILP (HiGHS), Schedule Studio, cards, verifier. Adapted in phase 7. |
| `simulator/` | KEEP | - | Bakery, cold-store, hotel profiles for tests and demos. |
| `bot/` | KEEP | - | Debounce, cooldown, hysteresis, Greek templates. Viber disabled by config. |
| `backend/main.py`, `backend/routes/`, `backend/models/`, `backend/templates/` | KEEP, ADAPT | phases 2, 5, 6 | Ingest and API work; switch to billing_core and fail-closed auth. |
| `backend/database/sqlite_store.py` | KEEP, EXTEND | phases 5 to 10 | New tables per CONTRACT C2. |
| `backend/security.py` | REPLACE | phase 2 (fail closed), 11 (per-device tokens) | Open access when API_KEY is empty. |
| `backend/market/service.py`, `clients.py`, `fetcher_henex.py` | REPLACE | phase 8 | Hourly only; endpoint and zone code unverified; silent synthetic fallback. |
| `backend/market/data/market_fallback_seed.json` | DEMO ONLY | phase 8 | May feed the demo dashboard, never advice. |
| `backend/market/scraper_rae.py` | KEEP, REVIEW | phase 8 | Useful start for monthly green/yellow price ingestion; verify target pages. |
| `tariff_engine/units.py` | KEEP until phase 6 | phase 6 | Explicit unit conversion idea moves into billing_core. |
| `tariff_engine/contracts.py`, `penalties.py`, `regulated_charges.py`, `cost_calculator.py`, `billing.py`, `green_tariff.py`, `yellow_dynamic.py`, `market_feed.py` | REPLACE, then DELETE | phase 6 | Unsourced constants, invented penalties, wrong contract definitions, float money. |
| `tariff_engine/benchmark_dataset.py`, `validator.py` | DELETE | phase 1 | Circular "bill audit" built from self-authored expected values. |
| `tariff_engine/adapters/german.py`, `spanish.py` | DELETE | phase 1 | Out of scope. |
| `tariff_engine/adapters/base.py`, `greek.py`, `registry.py` | DELETE with tariff_engine | phase 6 | Superseded by rule files. |
| `firmware/` | REMOVE from main (archive branch) | bootstrap | CT-only, assumes 230 V and cos phi 0.95; not for customer sites. |
| `calibration/` | REMOVE from main | bootstrap | Belongs to the ESP32 track. |
| `scripts/run_e2e_verification.py` | MOVE to `tools/` | bootstrap | Useful end-to-end runner; its test path is updated. |
| other `scripts/`, `reports/` | REMOVE from main | bootstrap | BDG2 and ML benchmarks, plots, synthetic bill report. Not Greek, not SME, not pilot. |
| `tests/unit/test_firmware_runtime.py`, `test_edge_forecast_math.py`, `test_hardware_calibration.py`, `test_benchmark_protocol.py`, `test_tariff_validation.py`, `test_baseline_time_and_energy_savings_proof.py` | REMOVE | bootstrap | Depend on removed folders or external datasets. |
| `tests/integration/test_neural_network_monthly.py`, `test_bdg2_real_data_dataset.py` | REMOVE | bootstrap | Depend on removed scripts and downloaded datasets. |
| `tests/unit/test_market_adapters.py` | EDIT | phase 1 | Drop German/Spanish cases. |
| other `tests/` | KEEP | - | Baseline regression net; legacy tariff tests are replaced in phase 6. |
| `docs/product/field_pilot_protocol_ipmvp.md` | MOVE to `docs/pilot/` | bootstrap | The savings-measurement method we adopt. |
| all other `docs/` | MOVE to `docs/legacy/` | bootstrap | Contain known errors; Claude Code is denied read access. |
| `README.md` | MOVE to `docs/legacy/README_original.md`, REWRITE | bootstrap, phase 2 | Overclaims (badges, law number, TRL, compliance). |
| `AGENTS.md` | DELETE | bootstrap | Instructs agents to push without review. |
| `LICENSE` | KEEP unchanged | - | MIT terms require keeping the notice. |
| `pyproject.toml`, `requirements.txt`, `uv.lock` | EDIT | phase 1 | Drop firmware/research extras; add pyyaml, hypothesis, ruff, mypy. |
| `TEST_INFRA.md`, `PROJECT_OVERVIEW_ECE.txt`, `CONTRIBUTING.md` | MOVE to `docs/legacy/` | bootstrap | Superseded by CLAUDE.md and PHASES.md. |
