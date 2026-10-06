# BTM-EMS

[![check](https://github.com/NikosVat/Behind-the-Meter-EMS/actions/workflows/check.yml/badge.svg)](https://github.com/NikosVat/Behind-the-Meter-EMS/actions/workflows/check.yml)

An advisory energy platform for Greek commercial sites with low-voltage connections
between 25 and 250 kVA on demand-metered, reactive-metered or dynamic tariffs.

**Status: Prototype. Not deployed at any customer site.** The fork is being restructured
(see [docs/PHASES.md](docs/PHASES.md)), and interfaces will change.

## What it does

Today:

- Ingests 3-phase telemetry over a FastAPI service and stores it in SQLite (WAL), in UTC.
- Builds next-day load schedules with a MILP optimizer (SciPy HiGHS).
- Raises threshold alerts and sends them over Telegram or Viber.
- Ships a load simulator for development.

Planned:

- A new cost engine (`billing_core`) that prices every 15-minute interval under the
  site's real tariff and regulated charges. The Europe/Athens local day has 92, 96 or
  100 quarter-hours.
- A read-only poller for certified meters (`meter_bridge`).
- A `price_basis` on every plan. No advice will be produced from synthetic or seeded prices.

Regulatory values (charges, taxes, time windows, thresholds) belong in versioned rule
files under `rules/`, each with a source. When a value is missing, computation stops
instead of guessing. Sources and their status are listed in
[docs/RULES_SOURCES.md](docs/RULES_SOURCES.md).

## Advisory only

BTM-EMS **never switches equipment**. People act on the advice.

- Modbus access uses read function codes only (FC 3/4).
- Shelly access uses read-only RPC methods only. No `Switch.*` calls and no `*.Set*` calls.
- `tests/test_no_actuation.py` enforces this and must stay green.

## Layout

| Path                   | Purpose                                                  |
|------------------------|----------------------------------------------------------|
| `tariff_engine/`       | Legacy tariff engine, to be replaced by `billing_core`   |
| `optimization_engine/` | MILP scheduler and decision cards                        |
| `backend/`             | FastAPI service, SQLite store, market data, routes       |
| `bot/`                 | Telegram and Viber clients, alert dispatch and debounce  |
| `simulator/`           | Synthetic load profiles for development and tests        |
| `rules/gr/`            | Versioned regulatory rule files (YAML)                   |
| `docs/`                | Contract, architecture, phases, glossary, rule sources   |

## Quickstart

Requires Python 3.12+.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Put secrets in a local `.env` file and never commit it. Authentication fails closed:
with no `API_KEY` set, every protected route returns 503.

```bash
# .env
API_KEY=change-me
ENVIRONMENT=development
# Keyless local access. Only honoured when ENVIRONMENT=development.
ALLOW_OPEN_DEV_ACCESS=false
```

Run the API:

```bash
uvicorn backend.main:app --reload
```

Send `X-API-Key: <API_KEY>` with every request. `/health` and the `/dashboard` shell
are public.

Feed it simulated telemetry:

```bash
python -m simulator.cli --profile bakery --url http://127.0.0.1:8000/api/v1/telemetry --max-readings 20
```

Simulated data is for development only. It is never a basis for advice.

## Checks

```bash
python tools/check.py
```

This runs ruff, mypy and pytest. Tests never touch the network.

## Documentation

- [docs/CONTRACT.md](docs/CONTRACT.md): conventions, storage, interfaces and invariants
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): components, data flow and deployment
- [docs/RULES_SOURCES.md](docs/RULES_SOURCES.md): regulatory values and their sources
- [docs/GLOSSARY.md](docs/GLOSSARY.md): Greek terms and their names in code
- [docs/PHASES.md](docs/PHASES.md): build plan
- [CLAUDE.md](CLAUDE.md): rules for contributors and coding agents

`docs/legacy/` holds the original upstream documentation. It contains known errors and
is not a reference.

## License

MIT. This is a fork of
[DimThanasoulias/Behind-the-Meter-EMS](https://github.com/DimThanasoulias/Behind-the-Meter-EMS).
See [NOTICE.md](NOTICE.md) and [LICENSE](LICENSE).
