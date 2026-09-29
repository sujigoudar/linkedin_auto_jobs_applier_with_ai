# Repository Map

```
signal-portfolio-commercial/
├── app/
│   ├── main.py                # FastAPI app factory; /healthz, /api/v1/me, Stripe webhook
│   ├── config.py               # pydantic-settings; every secret/URL has a safe local default
│   ├── db.py                   # Base, engine/session factories, RLS + append-only DDL, set_tenant_scope
│   ├── rate_limit.py            # slowapi limiter (fit-simulation route)
│   ├── api/
│   │   ├── dashboard_routes.py # every browser/customer/staff/public HTTP route (app_role)
│   │   ├── relay_routes.py     # the one relay ingest route (relay_role)
│   │   └── dependencies.py     # get_db_session, get_relay_db_session, get_current_scope
│   ├── models/                 # SQLAlchemy ORM — one file per table/table-group; see COMPONENTS.md
│   ├── services/                # business logic — one file per subsystem; see COMPONENTS.md
│   └── schemas/                 # pydantic request/response schemas (e.g. rights.py)
├── alembic/
│   ├── env.py
│   └── versions/                # 37 real, applied migrations — initial schema through relay_role access
├── tests/                       # pytest suite; conftest.py spins a real disposable Postgres 16 cluster
├── docs/                        # this documentation tree — see docs/manifest.yaml
│   ├── 00_discovery.md          # historical: build-start reconciliation against signal-copier's own state
│   ├── 12_addendum_post_phase12_work.md  # historical: post-Phase-12 addendum
│   ├── 12_validation_report.md  # historical: Phase 12 validation snapshot
│   └── architecture/            # this agent's docs — ARCHITECTURE, SYSTEM_CONTEXT, COMPONENTS, DEPENDENCIES, DATA_FLOWS
├── spec/                        # the originating spec package (contracts, workbooks, phase docs) — read-only reference, not living docs
├── dashboard_spec/               # the dashboard build's own spec package (screens, contracts, design) — read-only reference
├── ops/                          # operational scripts (e.g. bootstrap, resume notes)
├── deploy/                       # deployment configuration
├── CLAUDE.md                     # slim operating contract for agents working in this repo
├── PROJECT_STATUS.yaml           # machine-readable current health snapshot
├── requirements.txt
├── pyproject.toml                # ruff/mypy config
├── pytest.ini
├── alembic.ini
├── Dockerfile
└── .env.example
```

## Where to look for what

| I need to... | Look in |
|---|---|
| Understand the overall architecture | `docs/architecture/ARCHITECTURE.md` |
| Understand who calls this service and who it calls | `docs/architecture/SYSTEM_CONTEXT.md` |
| Find which module owns a responsibility | `docs/architecture/COMPONENTS.md` |
| Understand the relay ingest event flow | `docs/architecture/DATA_FLOWS.md`, `app/services/integration_inbox.py` |
| Understand a domain term | `docs/GLOSSARY.md` |
| Change a route | `app/api/dashboard_routes.py` or `app/api/relay_routes.py`, then the service it calls |
| Change the schema | add a new Alembic revision under `alembic/versions/`, mirroring `app/db.py`'s RLS/append-only setup for any new tenant-scoped or append-only table |
| Run tests | `pytest -q` (spins its own disposable Postgres — see `tests/conftest.py`) |
| Check current health/test numbers | `PROJECT_STATUS.yaml` |
