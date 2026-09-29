# Pending decisions

Real open items this codebase itself discloses — decisions only a
human (usually the product owner) can make, or technical ambiguities
genuinely blocked on evidence this environment doesn't have. Nothing
here is invented; each item traces to a real commit, docstring, or
`ops/` document.

## The six standing owner-only action cards

From `ops/COMMERCIAL_RESUME.md` (`docs/12_validation_report.md`'s own
six owner cards, still outstanding as of the most recent real work
reviewed for this snapshot):

1. **CARD-1 — Legal entity / jurisdiction / assets.** No legal entity
   has been established in this build. Any managed-account (PAMM/MAM)
   activation is blocked on this regardless of what code exists.
2. **CARD-2 — Source rights contracts.** The rights registry
   (`app/services/rights_registry.py`) enforces `check_rights()`
   fail-closed against whatever grants exist, but no real rights
   contract has been signed with any signal source; the three named
   UNKNOWN providers (buyalerts, tradealgo, kamdenai) are seeded as
   explicitly unresolved, not silently approved.
3. **CARD-3 — Platform agreements.** Blocks real Collective2/eToro
   transport (`TASK-01`, `TASK-03` in `tasks.json`) — the adapters
   build request shapes correctly but have no credentials to send
   them with, by design, pending this agreement.
4. **CARD-4 — Payment processor / tax / bank (merchant approval).**
   Blocks real Stripe wiring (`TASK-04`) — signature verification and
   event-idempotency recording are real and tested against synthetic
   payloads, but no Stripe SDK call or real account exists anywhere in
   this codebase.
5. **CARD-5 — Customer agreements / disclosures.** Needed before any
   real customer onboarding beyond the current paper-trading/demo
   flow.
6. **CARD-6 — Final financial production release decision.** The
   ultimate go-live gate — explicitly not something more coding can
   advance. `entitlement.py`'s own payment/safety separation exists
   specifically so that whenever this decision is made, a payment
   outage still cannot revoke management of exposure that already
   exists — but the decision itself is the user's alone.

**None of these six can be advanced by writing code.** A session
picking up work on this app should recognize when a task actually
requires one of these (e.g. "wire real Stripe checkout") and stop to
surface that explicitly, per the standing directive in
`ops/COMMERCIAL_RESUME.md` and `docs/agents/HANDOFF.md`, rather than
attempt a workaround.

## Genuine technical ambiguities, blocked on evidence

- **Collective2 API4 TIF value 2.** The vendor's own documentation is
  internally inconsistent (documents TIF0=day/1=GTC, but one
  conditional example uses value 2). A `WebFetch` to
  `https://collective2.com/apidoc/v4` to resolve this was refused by
  this environment's egress policy — a real, confirmed organization
  denial, not a transient failure (see `docs/state/BLOCKERS.md`). The
  `Tif` enum in `app/services/collective2_publisher.py` deliberately
  excludes value 2 and raises rather than accepting it. **Decision
  needed**: real, captured vendor confirmation (support ticket,
  updated documentation, or a direct API response) before this enum
  can be safely widened. Do not infer an answer from the conflicting
  documentation alone.

## Real, still-stub publisher integrations (disclosed, not hidden)

Per the task's own honesty framing — these are genuinely partial, and
each module's own docstring says so plainly:

- `app/services/collective2_publisher.py` — builds, never transmits.
- `app/services/etoro_adapter.py` — builds, never transmits; demo-only
  enforced structurally (no fallback branch to a real endpoint exists).
- `app/services/copyfactory_close_only.py` — classification logic only;
  does not call CopyFactory.
- `app/services/stripe_webhook.py` — real signature-scheme
  reimplementation against synthetic payloads only; no Stripe SDK, no
  real account.
- `app/services/mam_allocation.py`, `app/services/pamm_accounting.py`
  — simulation-only by the spec's own explicit scope boundary;
  production is meant to use "the actual contractual broker rule
  instead."

See `docs/KNOWN_ISSUES.md` and `docs/TECH_DEBT.md` for the fuller
technical account of each.
