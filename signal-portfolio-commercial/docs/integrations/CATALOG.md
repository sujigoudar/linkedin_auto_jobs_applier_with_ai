# Integration catalog

Every real external integration point this service has, with an honest
distinction between what actually transmits/receives live traffic and
what is a real, tested adapter with no live credentials.

## signal-copier (the sibling private-execution app) -- via relay ingress

**Status: real, live, bidirectional within this codebase's own scope.**

This is the one integration in this catalog that actually moves data
end to end in a real deployment, not just a request-builder:

- **Transport**: HMAC-SHA256-signed HTTP, `app/api/relay_routes.py`
  (commercial side) receiving from signal-copier's own relay worker
  (`signal-copier/app/relay_worker.py`), verified by
  `app/services/relay_auth.py::verify_relay_signature`. See
  `docs/security/ARCHITECTURE.md` section 4.
- **Identity resolution**: `export_stream_registrations`, keyed by
  `source_stream` -- server-controlled, never trusting a tenant claim from
  the inbound envelope itself (`INTEGRATION_DECISION.md` S6). See
  `docs/security/ARCHITECTURE.md` section 3's "RLS chicken-and-egg"
  discussion.
- **Payload shapes**: `signal_platform_contracts.EventEnvelope`, with
  five implemented event types handled by
  `app/services/integration_inbox.py::_apply_projection`:
  `EXECUTION_APPLIED`, `SOURCE_RECEIPT`, `FEE`,
  `ROUTING_ADMISSION_OUTCOME`, and `POSITION_SNAPSHOT` (handled specially,
  outside the ordinary sequence gate -- see `docs/operations/DR.md`).
  Every other `EventType` this contract defines is received and stored
  honestly but parked as `unimplemented_event_type` (see
  `docs/TROUBLESHOOTING.md`).
- **Ordering/integrity guarantees**: monotonic `export_sequence` per
  `(source_stream, producer_generation)`, idempotent dedup by `event_id` +
  `payload_hash`, generation-rollback detection. Fully documented in
  `docs/operations/DR.md`.
- **Database access**: a distinct, restricted `relay_role` Postgres login
  (never the admin `app_role`) -- see `docs/security/ARCHITECTURE.md`
  section 3.
- **Provisioning**: `ops/bootstrap.py::register_export_stream()` per
  account/source combination -- see `docs/operations/DEPLOYMENT.md`.
- **Related outbound channel (separate from the relay)**: this service's
  public catalog fit-simulator (PU-03) makes its own signed outbound
  requests *to* signal-copier (`SIGNAL_COPIER_BASE_URL` +
  `CATALOG_FIT_SIM_SIGNING_SECRET`, verified in
  `app/services/fit_simulation_client.py`) -- a separate, narrower
  integration in the opposite direction, authenticated with its own
  distinct secret (never the relay signing secret). Honestly reports the
  simulator unavailable when `SIGNAL_COPIER_BASE_URL` is unset or a
  product has no entry in `FIT_SIM_CATALOG_CONFIG_JSON`.

## Publisher destinations: Collective2 and eToro

**Status: real, tested request-builders. Neither transmits live traffic.**
Both are explicitly scoped this way in their own module docstrings --
"No live financial publisher/broker/payment authority during build" holds
regardless of adapter readiness.

### Collective2 (`app/services/collective2_publisher.py`)

- Maps a QUEUED `PublicationIntent` onto the real Collective2 API4 Order
  envelope shape (`spec/docs/06_platform_adapters.md`'s "Collective2
  first" section) -- builds and validates the outbound request, does
  **not** transmit it. No C2 sandbox and no credentials exist in this
  environment.
- **A real, disclosed BLOCKED item, not a guess**: this session attempted
  to fetch Collective2's own current API4 documentation
  (`https://collective2.com/apidoc/v4`) to resolve a flagged conflict in
  the spec (TIF documented as DAY=0/GTC=1 in most places, but one
  conditional example uses `2`) -- the request was refused by this
  environment's egress policy (`collective2.com` is not on the allowed
  domain list), not a transient failure. Per this build's own rule that
  missing evidence keeps a gate BLOCKED rather than assumed passing, the
  `Tif` enum defines **only** the two values the documentation agrees on
  (`DAY = 0`, `GTC = 1`); `build_order` raises rather than silently
  accepting or normalizing a TIF value of `2` or anything else. Real
  vendor confirmation must be captured before this constant set is
  widened.
- Submission (an authenticated HTTP POST to the real API4 endpoint) is
  future work gated on real owner-supplied credentials and CARD-3
  (platform agreements).

### eToro Builders API (`app/services/etoro_adapter.py`)

- Maps a QUEUED `PublicationIntent` onto an eToro trade request shape
  (`spec/docs/06_platform_adapters.md`'s "eToro separately" section).
  Builds (never sends) a request -- no application is registered with
  eToro in this environment.
- **`build_trade_request` structurally refuses any `account_mode` other
  than `"demo"`** -- "Implement DEMO transport... then real read-only
  verification. No code may select a real endpoint merely because demo is
  unavailable" (spec's own rule). There is no fallback branch that would
  ever choose a real endpoint.
- **REDUCE and CLOSE both require an explicit `position_id`** -- "Close-
  by-position-ID is not the same as selling a generic ticker amount;
  preserve position identity and platform units." No code path synthesizes
  a close from just an instrument symbol and a quantity.

### Publisher-destination authorization gate (`app/services/publisher_destination.py`)

Independent of both adapters above: `create_publisher_destination()`
accepts **only** `PublisherEnvironment.LOCAL_SIMULATION` -- `external_test`/
`demo`/`live` are all a real, named `EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED`
blocker. "Collective2 tests are not assumed sandbox" (AD-09's own
acceptance text). See `docs/operations/ENVIRONMENTS.md`.

### One-approved-writer-per-strategy guard

Reuses the existing, already-tested `claim_writer` mechanism
(`app/services/publisher_writer_claim.py`) -- "One approved path per
external account/strategy; No double publisher" is enforced by the same
guard the actual publication pipeline depends on, not a separate, parallel
check that could drift.

## Stripe (billing webhook)

**Status: real, tested signature-verification logic. No live Stripe
account.**

- `app/services/stripe_webhook.py::verify_signature` reimplements
  Stripe's own documented public signing scheme (HMAC-SHA256 over
  `"{timestamp}.{raw_body}"`), built and tested against synthetic,
  Stripe-shaped payloads signed with a local test secret -- the same
  approach the Collective2/eToro adapters use for building against a
  documented shape without live transport.
- `record_event_if_new()` persists `ProcessedWebhookEvent.event_id` for
  webhook-delivery idempotency, independent of the signature check itself.
- **No Stripe SDK call exists anywhere in this codebase** -- there is no
  `stripe.Webhook.construct_event` or any other live Stripe API call.
  Wiring a real `STRIPE_WEBHOOK_SECRET` and receiving real events is
  future work gated on CARD-4 (payment processor approval).

## Supabase Auth (named target, not yet integrated)

**Status: named as the target identity provider. Not present in code.**

`spec/docs/02_architecture_and_tenancy.md` names Supabase (Postgres +
Supabase Auth + RLS) as "the target for new customer identity,
subscriptions and product records" -- explicitly "justified by the new
multi-customer scope, not a retroactive assertion that Supabase exists in
the current repository." No Supabase SDK call, no Supabase project
reference, and no Supabase-issued-JWT verification path exists anywhere in
this codebase. `app/services/auth.py`'s local JWT issuer and
`app/services/local_auth.py`'s password/session system are the real,
currently-running interim substitutes -- see
`docs/security/THREAT_MODEL.md` gap 2.

## Summary table

| Integration | Direction | Transmits live traffic? | Auth mechanism |
|---|---|---|---|
| signal-copier relay ingress | inbound (signal-copier -> commercial) | **Yes, real** | HMAC-SHA256, `RELAY_SIGNING_SECRET`(+`_PREVIOUS`) |
| signal-copier fit-simulator | outbound (commercial -> signal-copier) | **Yes, real** (when configured) | HMAC-SHA256, `CATALOG_FIT_SIM_SIGNING_SECRET` |
| Collective2 API4 | outbound (would-be) | No -- builder only, no submission | n/a (no live credentials) |
| eToro Builders API | outbound (would-be) | No -- builder only, demo-only structurally | n/a (no live credentials) |
| Stripe webhook | inbound (would-be) | No -- verification logic only, no SDK call | HMAC-SHA256 scheme, `STRIPE_WEBHOOK_SECRET` (placeholder) |
| Supabase Auth | identity provider (target) | No -- not integrated at all | n/a |
