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
- **Phase 05 (publication/copy lifecycle)**: partially done -- the durable
  `PublicationIntent` write-path only. `app/models/publication.py`
  (mirrors `spec/contracts/PublicationIntent.schema.json` field-for-
  field, with the idempotency_key and natural-key uniqueness
  constraints enforced at the DB level), `app/services/publication.py`
  (idempotent `enqueue_intent`, fail-closed `transition` state machine).
  Explicitly NOT built: any destination adapter to actually send a
  queued intent anywhere, cohorts/consent/audience determination,
  follower allocation, capacity/fairness tracking. See
  `commercial_state.json`'s `05_publication_customer_copy_lifecycle` notes.
- **Phase 06 (Collective2 API4 publisher)**: partially done --
  `app/services/collective2_publisher.py` builds (never sends) the API4
  Order envelope. The TIF documentation conflict was investigated (not
  guessed): a WebFetch to collective2.com was blocked by this
  environment's egress policy (an organization denial, confirmed via
  `/root/.ccr/README.md` -- not retried, per that file's own rule). The
  `Tif` enum therefore defines only the two undisputed values (DAY, GTC);
  the disputed "2" is not a member and is rejected if ever produced. See
  `commercial_state.json`'s `06_collective2_publisher` notes for what's
  still missing (real transport/credentials, StrategyId/symbol
  resolution, OCA reconciliation).
- **Phase 07 (eToro/CopyFactory adapters)**: partially done --
  `app/services/etoro_adapter.py` (demo-only enforced structurally,
  position-id required for REDUCE/CLOSE) and
  `app/services/copyfactory_close_only.py` (by-symbol never satisfies a
  strict no-new-position gate). No real eToro app/credentials exist in
  this environment -- request-shape mapping only, same bounded scope as
  Phase 06. See `commercial_state.json`'s
  `07_etoro_copyfactory_adapters` notes.
- **Phase 08-12**: not started. See `commercial_state.json` for phase-specific
  notes carried forward from the original request (e.g. PAMM/MAM being
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

Phase 08: subscriptions/billing and business operations
(docs/08_products_billing_and_entitlements.md) -- this phase needs real
owner decisions before much can be built safely: a Stripe test-mode
account, product/price tiers, and the explicit billing rules already
flagged throughout this build (payment is never a trading mandate;
cancellation/payment failure must never abandon open exposure or
auto-flatten; no investment deposits through SaaS billing). Surface
CARD-4 (payment processor/tax/bank) to the user before assuming any
processor account exists. This is also a natural point to add a FastAPI
`commercial_api` app skeleton and real Alembic migrations, since the
ledger/tenancy/rights/sleeve/publication schemas are now stable enough
to give a first migration something real to cover.
