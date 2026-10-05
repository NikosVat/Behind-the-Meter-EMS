# Project
BTM-EMS: an advisory energy platform for Greek commercial sites (launch segment:
25 to 250 kVA low-voltage connections on demand-metered, reactive-metered or dynamic
"orange" tariffs). It reads certified meters, prices every 15-minute interval under the
site's real tariff and regulated charges, and produces next-day schedules and alerts.
It NEVER switches equipment. Humans act on the advice.

This repo is a fork of DimThanasoulias/Behind-the-Meter-EMS (MIT, see NOTICE.md).
We keep its scheduler, simulator and alerting, and replace its tariff engine.

# Layout
- `billing_core/`        NEW pure package: timeutil, money, rules, cost. Replaces `tariff_engine/`.
- `tariff_engine/`       LEGACY. Do not extend. Callers move to billing_core in phase 6.
- `optimization_engine/` MILP (SciPy HiGHS), Schedule Studio, decision cards. Keep.
- `backend/`             FastAPI, SQLite WAL store, market service, routes. Keep, adapt.
- `bot/`                 Telegram/Viber clients, dispatcher, debounce. Keep.
- `simulator/`           Load profiles. Keep.
- `meter_bridge/`        NEW (phase 9): read-only poller for certified meters.
- `rules/gr/`            Versioned regulatory rule files (YAML).
- `docs/legacy/`         Original upstream docs. Contain known errors. Never cite or follow them.

# Stack
Python 3.12, FastAPI, Pydantic v2, SQLite (WAL) for the pilot, NumPy/SciPy (HiGHS),
httpx, PyYAML, pytest, hypothesis, ruff, mypy (strict on billing_core only).

# The contract
`docs/CONTRACT.md` defines conventions, storage, telemetry payload, rule-file schema,
engine interfaces and invariants. Do NOT modify it unless the task says so, and then only
as specified. If code and the contract disagree, stop and report.

# Hard rules
- ADVISORY ONLY. No code writes to a device: Modbus FC 3/4 only; Shelly read-only RPC
  methods only (never Switch.*, never *.Set*). `tests/test_no_actuation.py` must stay green.
- Never invent regulatory numbers (charges, taxes, time windows, thresholds, multipliers,
  legal citations). Values live in `rules/` with a source URL. Missing value means
  `status: PLACEHOLDER` and `billing_core` raises `RulesIncomplete`. Never guess.
- Never produce advice from synthetic or seeded prices. Every plan carries `price_basis`.
- Timestamps: store UTC; use Europe/Athens only for tariff windows and display.
  A local day has 92, 96 or 100 quarter-hours.
- Money and prices in billing_core use `decimal.Decimal`, never float.
- Secrets only in `.env`. Never print, log or commit them. Auth fails closed.
- Tests never touch the network; use fixtures in `tests/fixtures/`.
- Never push. The humans review and push.
- One task per session, one commit per task, conventional commit messages.

# Working rhythm
- Read only the CONTRACT.md sections the task names (grep `## C<n>`).
- For tasks touching more than 3 files, post a short plan first and wait for "OK".
- Finish with `python tools/check.py` (exists from phase 1) and report pass/fail counts,
  the last 30 lines of any failure, and `git log -1 --stat`.
- Pipe long output through `tail -n 60` (PowerShell: `| Select-Object -Last 60`).

# Docs index (grep these; never load them whole)
- `docs/CONTRACT.md`: C1 conventions, C2 storage, C3 telemetry, C4 rule files, C5 cost engine,
  C6 optimizer I/O, C7 alerts, C8 notifiers, C9 invariants
- `docs/ARCHITECTURE.md`: components, data flow, daily timeline, deployment
- `docs/PHASES.md`: the exact prompt per phase (`## Phase <n>`)
- `docs/FORK_MANIFEST.md`: every upstream path and whether it is kept, replaced or dropped
- `docs/RULES_SOURCES.md`: regulatory constants, sources, status
- `docs/GLOSSARY.md`: Greek terms to code names
- `docs/pilot/field_pilot_protocol_ipmvp.md`: savings-measurement protocol (upstream, kept)
- `PROJECT_STATE.md`: progress, decisions, open questions (conductor-owned)
