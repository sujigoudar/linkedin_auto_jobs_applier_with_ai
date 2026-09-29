# System Context

External actors and boundaries around signal-portfolio-commercial.
None of these integrations run live in this environment (no real
Supabase project, Stripe merchant account, or platform credentials
exist here) — every adapter is real, tested code that builds and
verifies requests without transmitting them, per the standing "no live
financial publisher/broker/payment authority during build" rule.

```
                          ┌────────────────────────────┐
   signal-copier          │  signal-portfolio-commercial │
 (owner's private          │                              │
  execution app,           │  relay_role ──▶ inbox ──▶     │
  UNCHANGED/UNTOUCHED) ────┼─▶ POST /internal/relay/       │
  export outbox            │     ingest-batch (HMAC)       │
  (own process)            │                              │
                          │  app_role ──▶ dashboard_routes │◀── staff/operators
                          │              (RLS-scoped)      │    (/ops/*, admin API)
                          │                              │◀── customers
                          │                              │    (/app/*, public API)
                          │                              │◀── anonymous public
                          │  Stripe ◀── webhook (HMAC) ── │    (/, /portfolios, /pricing)
                          │  signal-copier (2nd hop) ◀──── │    (fit-simulation proxy)
                          │  Collective2 / eToro /        │
                          │  CopyFactory (build-only,     │
                          │  never transmitted)           │
                          └────────────────────────────┘
```

## 1. signal-copier (sibling app) — via the relay/export pipeline

The **only** connection between the two apps. signal-copier's relay
worker (its own separate process, not covered by this repo's docs)
POSTs signed batches of exported envelopes
(`SOURCE_RECEIPT` / `EXECUTION_APPLIED` / `FEE` /
`ROUTING_ADMISSION_OUTCOME` / `POSITION_SNAPSHOT`) to
`POST /internal/relay/ingest-batch`. Signed with `RELAY_SIGNING_SECRET`
(HMAC, `app/services/relay_auth.py`, with a documented two-secret
rotation window via `RELAY_SIGNING_SECRET_PREVIOUS`) — a secret
distinct from every other credential in this service, so "a stolen
telemetry credential cannot become a trading credential"
(`INTEGRATION_DECISION.md` S11). This connection is **strictly
inbound-observational**: `relay_role` has no code path that can submit,
cancel or modify a broker order, and no route exists for this service
to call back into signal-copier's execution surface. A **second,
separate** hop exists in the other direction for one narrow,
non-owner-authenticated purpose: the public catalog's "Try our fit
simulator" (`app/services/fit_simulation_client.py`) makes a signed,
read-only `POST` to signal-copier's own
`/catalog/providers/{source}/fit-simulation`, using yet another distinct
secret (`CATALOG_FIT_SIM_SIGNING_SECRET`).

## 2. Customers — via the public/customer-portal API

Anonymous visitors reach the public catalog and marketing routes
(`/`, `/portfolios`, `/portfolios/{slug}`, `/pricing`, `/compare`,
`/methodology`, `/help`, `/status`) with no tenant scope at all — they
see only `PUBLISHED` products/content via the bespoke visibility
policies (see ARCHITECTURE.md §2). After signing up/in
(`/auth/signup`, `/auth/signin`, local Argon2 password auth), a customer
reaches the `/app/*` surface: their own portfolio selections, copy
mandates, platform connections (declared only), alerts, performance,
billing, support cases, notification/display preferences, and a
CU-16 scoped API key store (`app/services/api_key.py` — SHA-256 hashed,
one-time raw secret, safe scope allowlist only, no trading/admin scope).

## 3. Staff/operators — via the admin API

Tenant staff (roles: `OWNER`, `RESEARCHER`, `REVIEWER`,
`PUBLISHER_OPERATOR`, `BILLING_OPERATOR`, `SUPPORT_READONLY`) reach the
`/ops/*` surface: products and release reviews, rights register,
sleeves/research runs, candidate comparison, publisher-destination
config, integration config, pricing/entitlement drafts, content
publishing, managed-program setup, business economics, customer support
records, staff access management, audit log + evidence-manifest export,
incidents, and workspace settings. Every `/ops/*` action is gated
through `app/services/permissions.py`'s explicit allow-list — there is
no implicit "owner can do everything" shortcut.

## 4. External publisher destinations

Reviewed, build-only adapters — none holds real credentials in this
environment:

- **Collective2** (`app/services/collective2_publisher.py`) — maps a
  `QUEUED` `PublicationIntent` to the API4 `Order` envelope shape.
  Builds and validates; never transmits. A documented TIF-value
  ambiguity in C2's own docs was left deliberately unresolved (egress to
  `collective2.com` was blocked when this build tried to confirm it) —
  the adapter refuses any TIF value outside the two the documentation
  agrees on, rather than guessing.
- **eToro** (`app/services/etoro_adapter.py`) — Builders API trade-request
  shape. Structurally refuses any `account_mode` other than `"demo"` (no
  fallback branch can select a real endpoint), and requires an explicit
  `position_id` for REDUCE/CLOSE (never synthesizes a close from a bare
  symbol+quantity).
- **MetaApi CopyFactory** (`app/services/copyfactory_close_only.py`) —
  does not call CopyFactory at all; classifies a close-only mode string
  (`by-position` / `by-symbol` / `immediately`) for whether it is strong
  enough evidence to satisfy a strict no-new-position gate.
- **Stripe** (`app/services/stripe_webhook.py`) — the one integration
  actually exercised end-to-end over real HTTP in this build: signature
  verification (with timestamp staleness rejection) and idempotent event
  recording, called from `app/main.py`'s
  `POST /api/v1/billing/webhook/stripe`. No real Stripe merchant account
  exists here; the webhook secret is a local placeholder.

Every other named "provider" surface in the admin API
(`AD-09`/`AD-17`) only accepts `local_simulation`/`test` environment —
any other selection is a real, named `EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED`
blocker, never silently accepted.
