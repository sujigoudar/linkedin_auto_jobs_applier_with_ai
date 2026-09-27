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
- **Phase 03 (economic journal)**: done and tested. Two halves: (1) a real
  fix in `signal-copier/app/economics.py` for the `completed_trade_win_rate`
  naming issue Phase 00 flagged (see `commercial_state.json`'s notes for the
  exact rename/new-metric split); (2) the four-book append-only ledger
  itself (`app/models/ledger.py`, `app/services/ledger.py`,
  `app/db.py`'s `enforce_append_only`, `tests/test_ledger.py`) -- a real
  Postgres trigger rejects direct UPDATE/DELETE, and corrections are new
  rows, never edits. Not yet wired -- no caller appends real entries, and
  there's no projection/aggregation (NAV, per-book P&L) layer yet.
- **Phase 04 (portfolio research/selection)**: partially done -- only the
  deterministic, data-free half. `app/models/sleeve.py` (the Sleeve unit-
  of-combination), `app/services/portfolio_research.py` (candidate subset
  enumeration with the 12-sleeve/2-5-size limits, single-sleeve
  benchmarks, and the equal-weight recipe with its 35%-cap/extra-cash
  rule). Explicitly NOT built: correlation/complementarity statistics,
  the other three recipes, walk-forward/holdout evaluation, capacity
  stress, or `portfolio_version` itself -- all need real authorized
  historical sleeve data this environment doesn't have, and fabricating
  it would violate the build's own rules. See `commercial_state.json`'s
  `04_portfolio_research_engine` notes.
- **Phases 05-12**: not started. See `commercial_state.json` for phase-specific
  notes carried forward from the original request (e.g. the Collective2 API4
  TIF inconsistency to resolve, PAMM/MAM being simulation-only in this build).

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

Phase 05: publication and customer copy lifecycle (canonical portfolio
events, durable writer/outbox, cohorts, mandates, cancellation/wind-down
without abandoning exposure). Phase 04's remaining data-dependent pieces
(correlation/complementarity, the other three recipes, walk-forward
evaluation, portfolio_version) stay open until real authorized historical
sleeve data is available -- do not fabricate it to unblock them. This is
also a natural point to add a FastAPI `commercial_api` app skeleton and
real Alembic migrations, since the ledger/tenancy/rights/sleeve schemas
are now stable enough to give a first migration something real to cover.
