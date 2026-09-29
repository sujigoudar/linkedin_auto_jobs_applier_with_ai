# Functional Requirements — What Is Actually Implemented

This document describes only what real code in this repository does today.
Anything marked "simulation only" or "declared, not yet wired" is a disclosed scope
boundary, not a gap to read past.

## Rights registry

`app/models/rights.py` (`RightsGrant`), UI at `/ops/rights`
(`dashboard_routes.py::rights_register_page`). Tracks, per source, which grantee
entity has been granted which `uses`/`channels`/`jurisdictions`/`assets`, with
`status` (UNKNOWN/GRANTED/DENIED/REVOKED), an `attribution_policy_id`, a
`wind_down_policy_id`, and the `review_id` that approved it. Not tenant-scoped via
RLS — looked up by `source_id`.

## Research & selection

- **Research runs** (`app/models/research_run.py`, `/ops/research/new`,
  `/ops/research/runs/{id}`): configures and records subset-search research
  parameters (sleeve universe, recipes, subset size bounds, cash/sleeve/cluster bps
  caps, train/test session counts, holdout fraction, cost scenarios).
- **Sleeve catalog** (`app/models/sleeve.py`, `/ops/research/universe`): the
  provider/analyst/asset-class universe research draws from, each with its own
  execution/cost/capacity/risk-unit policy references.
- **Candidate comparison** (`/ops/research/compare`,
  `dashboard_routes.py::candidate_comparison_page`): compares candidate portfolio
  drafts before a release.
- **Portfolio versions** (`app/models/portfolio_version.py`): the append-only,
  versioned output of research — a portfolio's weight composition
  (`portfolio_version_sleeves`) is never edited in place; a reweight is a new
  version's own rows.

## Publication lifecycle

- **Products** (`app/models/product.py`): a tenant's own published offering, with a
  `lifecycle_state` (draft → review → published, per `ProductLifecycleState`) and a
  `revision` counter. Publicly visible once `PUBLISHED` (bespoke RLS, ADR-0001).
- **Release review** (`app/models/release_review.py`, `/ops/reviews`): the approval
  gate a product revision must pass — proposer, evidence manifest, audience policy,
  reviewer decision (`ReleaseReviewState`) — before that revision can go live.
- **Publication intents** (`app/models/publication.py`): the durable, idempotent
  record of an actual outbound instruction to a channel (OPEN/ADD/REDUCE/CLOSE/etc.
  actions against an `external_strategy_id`), carrying policy/audience/body hashes
  and its own `idempotency_key`.
- **Publisher destinations** (`app/models/publisher_destination.py`): where a
  tenant's publication actually goes — platform, environment, credential reference,
  publication mode.
- **Content documents** (`app/models/content_document.py`, public at `/help`,
  `/status`, `/portfolios/{slug}`): methodology, risk, and legal documents, with the
  same publish/visibility pattern as products.

## Subscriptions & billing

- **Subscriptions** (`app/models/billing.py`): tier (`ProductTier`:
  FREE_RESEARCH/ALERTS_ONE/PORTFOLIOS_THREE/PRO_RESEARCH_API), state
  (`SubscriptionState`: PENDING_PAYMENT → TRIAL_AUTHORIZED → ACTIVE_PAID →
  CANCEL_AT_PERIOD_END/PAST_DUE/SUSPENDED_NEW_ENTRIES/ENDED/DISPUTED/
  MANUAL_REVIEW), tied to a `processor_subscription_id` (Stripe).
- **Price versions** (`app/models/price_version.py`): real SKUs, `amount_minor`,
  billing interval, `mode` (TEST/LIVE), and whether a tier is unlimited-portfolio.
- **Stripe webhook** (`app/services/stripe_webhook.py`): HMAC-verified, using
  `processed_webhook_events` for dedup — the same pattern the relay ingress later
  reused for its own signature verification (see `docs/design/INGEST_PIPELINE.md`).

## Copying & mandates

- **Copy mandates** (`app/models/copy_mandate.py`): a customer's own instruction to
  copy a selected product into a connected platform account — allocation
  amount/currency, optional risk/loss caps, start mode, and consent/policy version
  references. State machine via `CopyMandateState`.
- **Platform connections** (`app/models/platform_connection.py`): a customer's
  declared brokerage/platform account. **Declared only today** — no real
  authorized-observation channel exists yet (`follower_connection_id`/
  `external_observation_id` on `ledger_entries` are ready for it, unpopulated).

## PAMM/MAM domain model — simulation only

`app/models/managed_program.py` (`ManagedProgram`, AD-14 "Managed-program setup")
records a tenant's own *inactive* program configuration (allocation/NAV/dealing/fee
policy references) — "UI built even when approvals absent; real cash handling
remains through broker." This model **never touches money**.

The actual accounting math lives in `app/services/pamm_accounting.py` and is
explicitly disclosed as **simulation only**, "same scope boundary as
`app/services/mam_allocation.py` — Production uses the actual contractual broker
rule instead." Its own fee formula (`compute_simple_hwm_fee`) is the spec's own
"simple test-only no-cashflow interval" case and deliberately **raises** rather than
computing a number if a deposit/withdrawal occurred during the interval — a real,
cashflow-aware fee (unit-series/equalization accounting) is out of scope and is
never silently approximated. Program activation/release (moving a `ManagedProgram`
out of DRAFT into a real, money-moving state) is a separate, not-yet-built admission
decision.

## Customer portal

- Portfolio selection (`app/models/portfolio_selection.py`), display preferences
  (`customer_display_preferences.py`), notification preferences
  (`notification_preferences.py`), eligibility assessment
  (`app/models/eligibility.py`), and support cases (`app/models/support_case.py`).
- Local sign-in/sign-up/verify/recover (ADR-0007) — a real, working flow, but email
  delivery of verification/reset tokens is not wired to any provider yet.

## Admin console

`/ops/*` routes (`app/api/dashboard_routes.py`): operations overview, integration
status, platform performance, source coverage, trading performance, product
lifecycle management, release review queue, research run management, candidate
comparison, rights register, sleeve catalog. Role-gated (`_require_product_admin`,
`_require_release_reviewer`, `_require_research_run_admin`, etc.) via
`app/services/permissions.py` against `MembershipRole`.

## Integration ingest (from signal-copier)

See `docs/design/INGEST_PIPELINE.md`, `docs/design/LEDGER_MODEL.md`, and
`docs/design/ATTRIBUTION.md` for the full design. In summary: a restricted relay
worker delivers signal-copier's exported events (executions, source receipts, fees,
routing outcomes, position snapshots) through HMAC-verified transport into an
idempotent, ordered inbox, projected into a four-book append-only ledger, with
real FIFO-lot per-analyst attribution and a real source-coverage report over the
whole pipeline.
