# Resuming the commercial platform build

Read `ops/commercial_state.json` first -- it has per-phase status, notes, and
the standing constraints that apply to every phase. This file is the human-
readable companion: how to actually pick the work back up.

## Where things stand

- **Phase 00 (discovery)**: done. See `docs/00_discovery.md`.
- **Phase 01 (rights registry)**: done and tested (`app/models/rights.py`,
  `app/schemas/rights.py`, `app/services/rights_registry.py`,
  `tests/test_rights_registry.py`, `tests/conftest.py`). Not yet wired into
  anything -- there are no callers of `check_rights()` yet, because there is
  no Phase 02 API and no Phase 04-07 candidate/publication/delivery code yet.
- **Phase 02 (tenancy/architecture)**: done and tested
  (`app/models/tenancy.py`, `app/services/auth.py`, `app/services/permissions.py`,
  `app/db.py`'s `enable_row_level_security`/`set_tenant_scope`,
  `tests/test_tenancy_models.py`, `tests/test_cross_tenant_foreign_key.py`,
  `tests/test_row_level_security.py`, `tests/test_permissions.py`,
  `tests/test_auth.py`). Real Postgres RLS, a compound-FK cross-tenant
  guard, a local JWT issuer, and an explicit role-permission allow-list.
  Not yet wired into anything -- no FastAPI app/routes exist yet, and no
  Alembic migrations (still `Base.metadata.create_all` in tests).
- **Phases 03-12**: not started. See `commercial_state.json` for phase-specific
  notes carried forward from the original request (e.g. the Collective2 API4
  TIF inconsistency to resolve, the Phase 03 accounting-journal scope
  boundary against `signal-copier/app/provider_value.py`, PAMM/MAM being
  simulation-only in this build).

## How to run the tests

```
cd signal-portfolio-commercial
pip install -r requirements.txt
python3 -m pytest tests/ -v
```

Tests require real `postgresql-16` server binaries (present in this sandbox
at `/usr/lib/postgresql/16/bin`) -- `tests/conftest.py` starts and tears down
a disposable cluster per test session. If `initdb` fails with a permission
error, check that the `postgres` system user can traverse every ancestor
directory of pytest's tmp dir (this bit us once; the fix is in
`conftest.py`'s `postgres_cluster` fixture, which chmods ancestors to 0711).

## Conventions to keep following

- New tables/enums/fields are copied field-for-field from
  `spec/contracts/*.schema.json`, not redesigned.
- Every service function that gates a real action (rights, later: risk
  limits, capital allocation, publication eligibility) must fail closed, and
  that must be a temporarily-broken-then-restored test, not just an assertion
  that reads correctly.
- This service is Postgres-only (see `app/db.py`, `ARRAY(String)` columns in
  `app/models/rights.py`) -- never add a SQLite fallback or a mock session
  for these tests.
- Six owner-only action cards are not yet needed (no phase has reached a
  point requiring legal entity setup, a real rights contract, a real platform
  agreement, real merchant approval, real customer agreements, or an actual
  financial production release) -- when a phase does reach that point, stop
  and surface the specific card to the user rather than assuming or
  fabricating an answer.

## Next step

Phase 03: the economic journal (four-book accounting -- source/model/
platform/follower) and corrected metrics. Must NOT simply wrap or
re-export `signal-copier/app/provider_value.py` -- see `docs/00_discovery.md`
and `commercial_state.json`'s `03_economic_journal` notes for why (it's a
narrower, single-owner tool) and for the `completed_trade_win_rate` naming
fix this phase also carries forward. This is also a natural point to add a
FastAPI `commercial_api` app skeleton (routes calling into the Phase 01/02
services) and real Alembic migrations, once the accounting schema gives a
fuller picture of what the first migration needs to cover.
