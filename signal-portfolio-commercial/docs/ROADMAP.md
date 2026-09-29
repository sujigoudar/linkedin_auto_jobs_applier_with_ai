# Roadmap

Real next steps, not aspirational ones — drawn from this app's own
disclosed gaps (`docs/state/tasks.json`,
`INTEGRATION_ACCEPTANCE_STATUS.md`) and the standing directive in
`ops/COMMERCIAL_RESUME.md`. Nothing here implies a committed timeline;
this is pre-1.0, development-branch software (`docs/process/RELEASE.md`).

## Near-term — buildable without external accounts

Work that doesn't depend on any of the six owner-only action cards
(`docs/PENDING_DECISIONS.md`) and can be built and load-bearing-verified
today:

- Publication cohorts/consent/audience determination, follower
  allocation, and capacity/fairness tracking (TASK-07) — the durable
  `PublicationIntent` write-path and state machine already exist;
  nothing yet determines *who* a queued intent should reach.
- A dedicated production migrator Postgres role (`CREATEROLE` +
  schema ownership) to replace the superuser connection
  `docker-compose.yml` currently uses for local/paper convenience
  (TASK-10).
- Continued re-verification passes (in the style of `1945c2e` and
  `77a6fef`) re-checking RLS boundaries, `relay_role`'s zero-write
  guarantee, and command-authority checks against everything added
  since the last such pass — this app's own history treats "proven
  once" as insufficient; it re-proves standing guarantees as the
  codebase grows.

## Blocked on real credentials/accounts (owner action, not more code)

- Real Collective2 API4 transport, StrategyId/symbol resolution, OCA
  reconciliation (blocked on CARD-3 + a real scoped API4 key).
- Real vendor confirmation of Collective2's TIF value 2 (blocked on the
  egress-policy denial documented in `docs/state/BLOCKERS.md` —
  needs either a policy change or an out-of-band confirmation).
- Real eToro application registration and live/read-only verification
  path (blocked on CARD-3 + a registered eToro application).
- Real Stripe account wiring — Checkout/Billing/Customer Portal, a
  real exposed webhook endpoint (blocked on CARD-4).
- Real broker integration behind PAMM/MAM's currently simulation-only
  accounting (blocked on CARD-1/CARD-3 approvals — real managed-account
  activation remains blocked on those regardless of what's built).
- Real licensed historical market/sleeve data for portfolio research's
  correlation/complementarity statistics, the three non-default
  recipes, and walk-forward evaluation.

## Longer-term, once the owner cards clear

- Entitlement wiring into real publication/adapter call sites
  (`entitlement.py` exists and is tested standalone; nothing calls it
  from a real publish/charge path yet).
- Real production deployment infrastructure behind AD-22's currently
  honest "no deployment infra" disclosure.
- The remaining GUI journeys tracked `NOT_RUN` in
  `docs/12_validation_report.md`, once their dependent subsystems
  (real publication delivery, real billing) are real.

## What "done" does not mean here

Per the standing directive: nothing on this roadmap becomes a release
candidate without the owner explicitly being notified before crossing
into anything live — a real charge, a real publish, a real
broker-managed account. See `docs/process/RELEASE.md`.
