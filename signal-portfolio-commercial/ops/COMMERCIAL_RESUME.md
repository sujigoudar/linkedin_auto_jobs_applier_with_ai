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
- **Phases 02-12**: not started. See `commercial_state.json` for phase-specific
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

Phase 02: a `commercial_api` FastAPI service skeleton with Postgres
tenant / user_identity / membership / customer_profile models (RLS-ready
schema design, matching `spec/contracts/*.schema.json` for those entities),
a controlled/local JWT issuer for tests, and Alembic migrations (deferred
until now because the schema was still moving during Phase 01 -- it should
stabilize enough during Phase 02 to set up meaningfully).
