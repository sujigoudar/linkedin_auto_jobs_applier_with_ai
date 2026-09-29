# Routing — where work belongs in this monorepo

`linkedin_auto_jobs_applier_with_ai` is a monorepo hosting several real
apps side by side. This app owns `signal-portfolio-commercial/` (plus,
for genuinely cross-cutting changes, the shared
`signal_platform_contracts/` package one level up). Routing a task
correctly means knowing which of those a change actually belongs to
before opening a worktree.

## The real directory boundaries

- **`signal-portfolio-commercial/`** — this app: the customer/owner/
  admin web platform (`app/api/dashboard_routes.py`), the restricted
  relay ingress (`app/api/relay_routes.py`), the tenant-scoped Postgres
  schema (RLS, append-only ledger, `alembic/`), billing/entitlement/
  publication/managed-program domain logic. Its own CI
  (`.github/workflows/signal-portfolio-commercial-ci.yml`) is
  path-scoped to exactly this directory plus its own workflow file.
- **`signal-copier/`** — the sibling trading engine this platform
  reports on and relays from (fit-simulation, live capital allocation,
  order lifecycle, backtesting). This app reuses several of its real
  computations rather than re-implementing them (`compute_max_drawdown`,
  `CapitalAllocator.admit()`, the vendored Chart.js asset) — see
  `docs/process/DEVELOPMENT.md`. A task that changes trading logic
  itself belongs there, not here.
- **`signal_platform_contracts/`** — the pure cross-service event
  envelope/identity contract both apps import (`signal-portfolio-commercial/
  Dockerfile` copies it in before installing this app's own
  requirements because it's an editable sibling dependency). A change
  to the wire contract between the two apps touches this package, and
  usually both apps in the same commit (e.g. `c244dbd` "Integration
  slice 1: signal_platform_contracts").
- **`.github/workflows/`** — each app's CI lives in its own
  path-scoped workflow file (`signal-portfolio-commercial-ci.yml`,
  `signal-copier-ci.yml`); a change to one app's CI step should not
  touch the other's file unless the fix is genuinely shared.

## How to decide where a task's worktree should touch

1. **Read the task's own language first.** Requirement IDs give it
   away directly: `CU-`, `AD-`, `PU-`, most `INT-` items, and anything
   referencing tenants/customers/RLS/billing/publication/managed
   programs is this app. Anything about order execution, broker
   fills, capital sizing, or the writer lease/command ledger is
   `signal-copier`.
2. **When a task genuinely spans both** (e.g. secret rotation across
   both signature-verification paths, the relay wiring in
   `docker-compose.yml`, a `signal_platform_contracts` schema change),
   keep it in one worktree and one commit — this repo's own history
   does this deliberately (`9ddc681`, `d6613bb`) rather than splitting
   a single coherent change across two uncoordinated sessions that
   could land out of order.
3. **When in doubt, grep before assuming.** `docs/process/DEVELOPMENT.md`'s
   "understand current behavior first" step applies to routing too —
   check whether the concept already has a real implementation in one
   app before routing a task to build a second one in the other.
4. **Never edit another app's files as a side effect.** If a task
   *seems* to require touching `signal-copier/` but the actual
   assignment is scoped to this app, stop and flag the cross-cutting
   need explicitly rather than silently expanding scope.

## Requirement-ID vocabulary this app's own history uses

`CU-` (customer-facing screens), `AD-` (admin/owner/operator screens),
`PU-` (public site), `INT-` (integration correction pack items,
spanning both apps), `TR-`/`B` (trading-report/backtest features that
live in `signal-copier` but may be *surfaced* here), `ID-` (identity/
auth), `S<n>` (integration pack slice numbers). See
`INTEGRATION_ACCEPTANCE_STATUS.md` at the repo root and
`docs/12_validation_report.md` for the authoritative status of each.
