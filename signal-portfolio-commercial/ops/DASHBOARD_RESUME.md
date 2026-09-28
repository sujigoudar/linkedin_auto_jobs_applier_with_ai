# Resuming the dashboard build

Read `ops/dashboard_state.json` first. This file is the human-readable
companion.

## What this is

A 66-screen dashboard design package (`signal_platform_dashboard_v2`,
extracted alongside `spec/` in this same directory tree during the
session that started this work) across four interfaces: private
signal-trading console, commercial admin/research console, customer
portal, and public website. Verified by direct inspection: the
package's own claimed counts (66 screens, 41 forms, 284 fields, 170
actions, 1270 test cases) all check out against `catalog/*.json`.

This is a specification/design handoff, not implemented application
code -- exactly like the `spec/` package that drove Phases 00-12 of the
commercial platform itself. The same standing practice applies: build
one real, bounded, tested vertical slice at a time; never claim a
screen is "done" without real DB-backed queries/commands and passing
tests; never fabricate data to make an empty state look populated.

## Where things stand

Seven slices are DONE:
- AD-07 Products admin screen + PU-02 public catalog: real model/
  service/routes/templates/migration, 26 tests, load-bearing verified
  (stale-revision guard, `product_visibility` RLS policy).
- AD-02 Rights and service approvals: a real, tested, read-only grant
  register (`GET /ops/rights`), deliberately NOT including create/
  attach-evidence/approve -- those need document-upload/malware-scan
  infrastructure and an audit-logged approval workflow that don't
  exist yet.
- AD-03 Research universe and sleeves: real create/list of sleeve
  lineage records (`GET`/`POST /ops/research/universe`), reusing the
  Phase 04 `Sleeve` model as-is. Deliberately NOT including a
  DRAFT/QUALIFIED lifecycle or coverage/overlap analysis -- those need
  real historical sleeve data this environment doesn't have.
- AD-01 Commercial operations overview: a real cross-subsystem summary
  at `GET /ops` (now the header's home link) -- release blockers per
  product, active subscriptions, unknown-state publications (via a
  real tenant-scoped join through PortfolioVersion). "Open incidents"
  has no backing model, so it's rendered as an explicit UNSUPPORTED
  state, never a fabricated zero.
- AD-08 Release and change approvals: a real release-review queue.
  Requesting review (from AD-07's own product detail page) is refused
  unless the product's blockers are genuinely empty; deciding enforces
  a real independent-reviewer gate and a real stale-review-target
  check. Approving moves a Product to APPROVED, never PUBLISHED.
  **A genuine migration-ordering bug was found and fixed while
  verifying this slice's migration end-to-end** -- see
  `dashboard_state.json`'s `fifth_slice.real_bug_found_and_fixed_along_
  the_way` for the full story; short version: never let an EARLIER
  migration call a shared RLS-application helper with the CURRENT,
  ever-growing `_TENANT_SCOPED_TABLES` tuple -- pin it to an explicit,
  frozen snapshot of the tables that existed at that point in history.
- AD-04 Portfolio Lab builder: a real research-run declaration
  (`GET`/`POST /ops/research/new`) and a real preview that recomputes
  the exact candidate denominator by reusing Phase 04's own
  combinatorics (extended with configurable min/max subset size, kept
  backward compatible). Only "equal_capital" is an implemented recipe
  -- any other requested recipe is a real, named blocker, never
  silently accepted. No job queue exists, so "Confirm: Enqueue
  research job" is not implemented.

- PU-01 Public home: the anonymous landing page (`GET /`), sharing
  PU-02's own `list_published_products` query, with a real "Service
  status" checklist computed from actual config state (environment tag,
  whether Stripe billing is genuinely connected) rather than a
  decorative banner.
- PU-08 Help and compatibility guide: the anonymous help page
  (`GET /help`), with a real compatibility directory grounded in the
  already-audited state of the three real publisher adapters
  (Collective2, eToro, MetaApi CopyFactory close-only) -- never a
  marketing claim of universal broker coverage.
- PU-03 Portfolio detail: one published product's real page
  (`GET /portfolios/{slug}`, linked from PU-02's catalog rows), with
  real identity/version/risk/methodology facts. A draft or unknown slug
  returns a scoped 404. No NAV/marks-history model exists, so the
  performance/drawdown panel always honestly reports the track record
  as unavailable rather than a guessed curve.
- PU-05 Pricing and service compatibility: `GET /pricing` gates the
  real ProductTier/TEST_MODE_MONTHLY_PRICE_CENTS fixtures (documented
  as "not user-approved prices") behind the same real billing-connected
  signal PU-01 already computes -- this deployment (billing
  unconfigured) truthfully shows the real empty state instead of
  leaking fixture prices publicly.
- ID-04 Service eligibility onboarding: `GET`/`POST /onboarding/
  eligibility` (CUSTOMER role only) -- a customer saves real residence/
  service facts; eligibility is always computed live from those facts
  plus the real published-product catalog, never a stored verdict. Only
  "US" residence is supported and entity onboarding is a real named
  UNSUPPORTED reason.
- AD-16 Staff roles and access reviews: `GET /ops/access` +
  `POST /ops/access/invite` + `POST /ops/access/{user_id}/revoke`
  (OWNER only) -- real invite/revoke of a colleague's operator role
  using the existing Membership model (no new table). OWNER can never
  be granted or revoked through this form, and a caller can never
  revoke their own membership (a real self-lockout guard).
- CU-14 Support and incident case: `GET`/`POST /app/support`
  (CUSTOMER role only, the first customer-portal screen built) -- a
  customer's own support cases. `related_object_id`, when given, must
  reference a real Subscription in the caller's OWN tenant -- a real,
  enforced cross-tenant guard, not just documented.
- AD-09 Publisher channels and strategies: `GET`/`POST /ops/publishers`
  (OWNER, PUBLISHER_OPERATOR) -- a real "save inactive destination"
  that reuses the EXISTING claim_writer mechanism (from an earlier
  phase, previously uncalled by any screen) for real single-publication-
  authority enforcement. Only local_simulation is accepted; every other
  mode is a named EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED blocker.
- CU-16 API delivery, keys and exports: `GET`/`POST /app/developer` +
  `POST /app/developer/{key_id}/revoke` (CUSTOMER only) -- real scoped
  API key generation. Only a fixed safe scope allowlist (no trading/
  admin scope) and a required future expiry are accepted; only a
  SHA-256 hash is ever persisted -- the raw secret is shown once, in
  the create response, and never again. Revoke is idempotent.
- AD-12 Business economics and royalties: `GET /ops/business`
  (OWNER, BILLING_OPERATOR) -- real booked revenue per currency from
  existing Subscription rows, restricted to genuinely payment-
  recognized states (never LedgerEntry's trading P&L). Fees/refunds/
  royalties/cost attribution/margin are all explicit UNSUPPORTED --
  no such model exists yet.
- AD-11 Customers and scoped support record: `GET /ops/customers` +
  `GET /ops/customers/{user_id}` (OWNER, SUPPORT_READONLY) -- a
  read-only staff view built entirely from ID-04/CU-14's already-real
  data (eligibility decisions, support cases), no new model. A
  cross-tenant or non-customer user_id returns a scoped 404.
- AD-17 Integrations, data rights and quotas: `GET`/`POST /ops/
  integrations` (OWNER, PUBLISHER_OPERATOR) -- a real "save inactive
  config" gated by a reviewed-provider allowlist (only stripe for
  billing; collective2/etoro/copyfactory for publication -- every
  other purpose has no reviewed adapter yet) and by environment (only
  "test" accepted).
- AD-13 Pricing, entitlements and billing operations: `GET`/`POST
  /ops/billing` (OWNER, BILLING_OPERATOR) -- a real test-mode price
  draft. Features are restricted to CU-16's own real API scopes plus
  two real catalog facts (no implicit trading right); mode="live" is
  always refused (no merchant-approval workflow exists).
- AD-19 Content and disclosure publishing: `GET`/`POST /ops/content` +
  `POST /ops/content/{document_id}/request-review` (OWNER, REVIEWER) --
  a real plain-text content draft + submit-for-review. Any `<`/`>`
  character in the body is always refused (no raw HTML/script editor);
  methodology/status documents require at least one source_evidence_id.
- AD-14 Managed-program setup: `GET`/`POST /ops/managed-programs` +
  `POST /ops/managed-programs/{program_id}/request-review` (OWNER,
  REVIEWER) -- a real inactive PAMM/MAM program config + submit-for-
  review. No raw percentage/rate field exists at all, only opaque
  policy-id references; agreement_evidence_ids must be nonempty.
- AD-18 Audit log and release evidence: `GET /ops/audit` (OWNER,
  REVIEWER) -- a real, append-only audit store (retroactively resolves
  the "no audit-log store exists" gap AD-16/AD-11 both documented).
  AD-16's invite/revoke is its first real writer. The DB itself refuses
  any UPDATE/DELETE against audit_events -- proven for real against a
  live cluster, not just asserted. Also fixed a latent version of the
  same migration-ordering bug found during AD-08, this time in
  `_apply_append_only` rather than `_apply_row_level_security`.
- AD-20 Workspace customization and configuration: `GET`/`POST /ops/
  settings` (OWNER only) -- a real shared tenant-wide cosmetic default
  (one row per tenant, like `CustomerProfile`'s own precedent, not a
  per-user personal view). `MANDATORY_PANEL_IDS` (AD-01's own two real
  panel headings) can never be hidden via `visible_panel_ids` -- "no
  field here touches a permission, policy, or financial value" holds by
  construction, not by a runtime check.
- AD-05 Research run and full results: `GET /ops/research/runs/
  {research_run_id}` (OWNER, RESEARCHER, REVIEWER) -- reuses AD-04's
  own `get_research_run`/`compute_research_run_preview`, no new model
  or migration needed. Progress/shards/results/failures always render
  the real, honest "this run has not started" state -- there is no
  job-queue/shard-execution model in this build at all, so Resume
  failed shard/Cancel research job are an explicit UNSUPPORTED note,
  never implied-working controls.
- AD-10 Publication intent and cohort detail: `GET /ops/publications/
  {intent_id}` (OWNER, PUBLISHER_OPERATOR) -- real intent/revision
  facts from `PublicationIntent`, scoped through the same
  no-tenant-id-on-this-table join through `PortfolioVersion` AD-01's
  own slice already solved. Recipient cohort, delivery attempts,
  external order-family state and the correction form are all
  explicit UNSUPPORTED -- `Subscription` has no field linking a row to
  any product/portfolio version at all in this build.
- CU-02 My portfolios: `GET`/`POST /app/portfolios` +
  `POST /app/portfolios/{selection_id}/cancel` (CUSTOMER only) -- the
  first real customer-selection foundation. A new `PortfolioSelection`
  model records only a customer's own intent to copy a PUBLISHED
  product of their OWN tenant -- refuses a draft/unpublished/
  cross-tenant product id, refuses a duplicate active selection, and
  allows cancel-then-reselect via a Postgres partial unique index
  (a table-level UniqueConstraint was tried first and found to wrongly
  block reselecting after cancel -- caught by the test written for
  exactly that path, fixed before committing). No quantity/broker/
  execution field exists anywhere on this model.
- CU-12 Alert delivery preferences: `GET`/`POST /app/settings/
  notifications` (CUSTOMER only) -- a customer's own delivery
  destinations, categories, timezone and quiet hours. A fixed
  `MANDATORY_CATEGORIES = {"safety"}` can never be excluded, the same
  discipline as AD-20's own `MANDATORY_PANEL_IDS`. `webhook_endpoint_id`
  is an opaque id reference, never a raw URL, so there is no SSRF
  surface to secure. Verify endpoint/Send labeled test and delivery
  failures are explicit UNSUPPORTED -- no outbound delivery or
  delivery-attempt-tracking infrastructure exists in this build.
- CU-13 Profile, security and display preferences: `GET`/`POST /app/
  settings` (CUSTOMER only) -- a customer's own display_name/timezone/
  theme/density/locale/currency-display/reduce-motion preferences,
  Profile/Display steps only. Reuses AD-19's own "no raw HTML/script"
  discipline for display_name -- the first time it applies to a
  customer's own input rather than staff-authored content. Sessions/
  security, data export/deletion, and disclosure history are all
  explicit UNSUPPORTED -- no session store, export-job pipeline, or
  disclosure-acknowledgment model exists in this build.
- AD-15 Managed allocations, NAV and dealing: `GET /ops/managed-
  operations` (OWNER, PUBLISHER_OPERATOR, REVIEWER) -- reuses AD-14's
  own `list_managed_programs`, no new model or migration needed. Every
  panel about broker-originated dealing/NAV/cashflow/fee/restatement
  activity is an explicit UNSUPPORTED -- no such tracking model exists
  anywhere in this build (Phase 10's own domain model is
  simulation-only configuration, never a NAV ledger).
- PU-04 Portfolio comparison: `GET /compare` (anonymous, up to 4 `slug`
  query params) -- reuses PU-03's own `get_published_portfolio_detail`
  per slug, no new model needed. Requesting more than 4 slugs is a
  named refusal, never a silent truncation; an unknown/unpublished
  slug is reported by name, never silently dropped. Normalized return
  and co-drawdown panels are explicit UNSUPPORTED -- no NAV/marks-
  history model exists (the same gap PU-03's own slice documented).
- PU-07 Public service status: `GET /status` (anonymous, no database
  query) -- shares PU-01's own `get_service_status`. Active incidents/
  maintenance/history are explicit UNSUPPORTED, deliberately NOT the
  spec's own literal "No published service incidents." zero-count
  text, since there is no completed incident query behind it (the same
  "never fabricate a zero" discipline AD-01's own slice established).
- AD-22 Commercial deployments and recovery: `GET /ops/system` (OWNER,
  PUBLISHER_OPERATOR) -- shares PU-01/PU-07's own `get_service_status`.
  Workers/queues, backup generations, and fencing evidence are all
  explicit UNSUPPORTED -- no deployment/backup/worker-tracking
  infrastructure exists in this build; "No deployment has been
  qualified for this service" is used as-is since it is genuinely and
  completely true here.
- CU-07 Platform connections + CU-08 Connection wizard: `GET /app/
  connections`, `GET`/`POST /app/connections/new`,
  `POST /app/connections/{id}/disconnect` (CUSTOMER only) -- a new
  PlatformConnection model records only a customer's own DECLARED
  intent to connect a reviewed platform (collective2/etoro/
  metaapi_copyfactory, the same three AD-09/AD-17 already use).
  environment is restricted to `local_simulation` only -- no real
  hosted OAuth authorization or account-identity readback exists.
  Begin authorization/Verify connection and the Capability report/
  Reauthorization tasks panels are all explicit UNSUPPORTED.
- CU-09 Copy setup and mandate wizard: `GET`/`POST /app/copy/new`
  (CUSTOMER only) -- closes the "no copy-mandate model exists at all"
  gap AD-11's own slice already documented. A new CopyMandate model
  refuses any selection_id/connection_id that isn't both real AND
  currently eligible (an ACTIVE CU-02 selection, a DECLARED CU-07/
  CU-08 connection). There is no ACTIVE mandate state at all -- only
  DRAFT/CANCELLED -- since real activation needs a scoped publisher/
  execution pipeline this build does not have. Preview activation,
  Confirm authorized activation, Agreements/step-up, and Operation
  status are all explicit UNSUPPORTED.
- CU-10 Pause copying and position handoff: `GET /app/copy/
  {mandate_id}/manage` + `POST /app/copy/{mandate_id}/cancel`
  (CUSTOMER only) -- extends CU-09's own CopyMandate service, no new
  model needed. Pause new entries/Review wind-down/Request handoff (in
  the real sense) are all explicit UNSUPPORTED since no activation
  pipeline exists; cancelling a never-activated draft is the one real
  action honestly buildable, and it refuses to re-cancel an
  already-cancelled mandate.
- CU-01 Customer overview: `GET /app` (CUSTOMER only, the customer's
  own landing page) -- no new model or migration at all, purely a
  read-only join over CU-02's/CU-07's/CU-09's own already-real queries
  plus `product_admin.get_product`. `required_action` is derived from
  each row's own real mandate/connection state (never a fabricated
  aggregate), and a connection only counts as present while it is still
  DECLARED, so a mandate whose connection was later disconnected
  correctly shows "Reconnect platform..." instead of "No action
  required". Actual performance card, Observed open episodes, and
  Alerts are all explicit UNSUPPORTED -- no execution/NAV ledger or
  alert-delivery-to-customer link exists in this build.
- CU-03 Selected portfolio detail: `GET /app/portfolios/{selection_id}`
  (CUSTOMER only) -- no new model or migration needed, joins CU-02's
  own `get_own_portfolio_selection` (scoped not-found), PU-03's own
  `get_published_portfolio_detail`, and CU-01's own
  `list_own_copy_mandates_by_selection` + a DECLARED-only connection
  filter (the same pattern CU-01 already established). Identity/
  version, Effective settings and Changes/safety-action links are
  real; Actual versus model, Alerts/trades, and all three metrics
  (Observed net P&L, Open episodes, Delivery lag) are explicit
  UNSUPPORTED -- no execution/NAV ledger exists in this build.
- PU-06 Methodology, risk and legal documents: `GET /methodology`
  (anonymous), closing AD-19's own missing admission decision. A new
  `publish_content_document` (the ONLY function anywhere that ever sets
  ContentDocument to PUBLISHED) only accepts a SUBMITTED_FOR_REVIEW
  document. `content_documents` got its own bespoke
  `content_document_visibility` RLS policy (tenant match OR
  state='PUBLISHED'), the same shape as `products`' own
  `product_visibility` -- verified end-to-end against a live disposable
  Postgres cluster: an unscoped `app_role` session genuinely sees only
  a PUBLISHED row, never a DRAFT one, at the database layer.
- AD-11's own Mandates read view (extension, not a new screen): its own
  earlier slice documented "no copy-mandate model exists at all" as a
  gap, which CU-09's own CopyMandate closed since. `get_customer_
  support_record` now also calls CU-09's own `list_own_copy_mandates`
  -- no new table, no new query. Load-bearing verified: a raw
  same-tenant-only mandate query (no user_id filter) let another
  customer's own mandate leak into the first customer's support
  record; the real scoped query never does.

Full suite is 612 tests, ruff and mypy both green (**use `python3 -m
ruff`/`python3 -m mypy`/`python3 -m pytest` explicitly** -- this sandbox
has a stray `uv tool`-installed `ruff`/`mypy` shadowing the project's
real declared versions on bare `PATH`, which produced a false-negative
locally once already; CI always uses the `requirements.txt`-installed
versions via `pip install -r requirements.txt`).

A real, end-to-end screenshot walkthrough of the first 3 built screens
was also done once (disposable Postgres + real seeded data through the
actual service functions + Playwright with a real signed JWT in
`extra_http_headers` since auth is Bearer-token, not cookie-based) --
see git history around that point for the seed/screenshot scripts if
you need to repeat this for a future demo; nothing from that throwaway
demo is checked into the repo itself.

See `dashboard_state.json`'s `current_slice`/`second_slice`/
`third_slice`/`fourth_slice`/`fifth_slice`/`sixth_slice`/`seventh_slice`/
`eighth_slice`/`ninth_slice`/`tenth_slice`/`eleventh_slice`/
`twelfth_slice`/`thirteenth_slice`/`fourteenth_slice`/`fifteenth_slice`/
`sixteenth_slice`/`seventeenth_slice`/`eighteenth_slice`/
`nineteenth_slice`/`twentieth_slice`/`twentyfirst_slice`/
`twentysecond_slice`/`twentythird_slice`/`twentyfourth_slice`/
`twentyfifth_slice`/`twentysixth_slice`/`twentyseventh_slice`/
`twentyeighth_slice`/`twentyninth_slice`/`thirtieth_slice`/
`thirtyfirst_slice`/`thirtysecond_slice`/`thirtythird_slice`/
`thirtyfourth_slice`/`thirtyfifth_slice`/`thirtysixth_slice`/
`thirtyseventh_slice`/`thirtyeighth_slice`/`thirtyninth_slice` for the
exact file lists and what was deliberately left unbuilt in each. 26 of
66 screens remain.
Several of those are genuinely infrastructure-
blocked and NOT to be force-built without fabricating a capability:
CU-04 (no customer-to-alert linkage), CU-05/CU-06/CU-15 (need a real
NAV/execution ledger), CU-11 (needs Stripe hosted checkout +
Subscription-to-customer linkage), AD-06 (needs completed research
candidates), AD-21 (needs a real incident model -- unlike PU-06's own
already-real ContentDocument, no other module anywhere in this build
raises a real incident, so there is no natural, non-fabricated trigger
to build a create action around), ID-01/ID-02/ID-03 (real
password/email/MFA auth explicitly deferred to Supabase Auth per
`app/services/auth.py`'s own docstring), and TR-01 through TR-16
(belong to the separate private signal-copier system, which must stay
untouched). Pick the next honestly-buildable one following the steps
below. **If you add a
new table to
`_TENANT_SCOPED_TABLES` in app/db.py, pin any EARLIER migration that
already calls `_apply_row_level_security` with no explicit `tables`
argument to its own frozen snapshot of the tables that existed at that
point** (see `04c418cbb547`'s own docstring/code for the pattern) --
otherwise a fresh `alembic upgrade head` replay breaks.

## How to pick the next screen

1. Read `signal_platform_dashboard_v2/catalog/screens.json` for the
   full list and `signal_platform_dashboard_v2/catalog/phases.json` for
   the package's own suggested build order.
2. Read that screen's `signal_platform_dashboard_v2/screens/<ID>.md` in
   full -- it has the exact layout, table columns, action bindings,
   form fields, all 12 state obligations, and the panel/component
   contracts.
3. Check what backend already exists (models in `app/models/`, services
   in `app/services/`) before writing anything new -- reuse, don't
   duplicate.
4. Build: model/migration (if needed) -> query/command service -> a
   real HTTP route -> a real (minimal, semantic-HTML) template -> real
   tests covering at minimum the empty state, the create/save/reload
   path if the screen has one, and cross-tenant denial.
5. Load-bearing-verify the one or two most safety-critical checks
   (break it, confirm a test fails, restore) before committing.
6. Update `ops/dashboard_state.json`'s `screens_done` list and this
   file, then commit and push.

## Conventions to keep following

- FastAPI + Jinja2 + the existing Chart.js/Tabulator conventions from
  `signal-copier`'s dashboard -- no new frontend framework.
- Every screen's queries/commands are tenant-scoped via the existing
  RLS mechanism (`app/db.py`'s `set_tenant_scope`) -- never trust a
  caller-supplied tenant_id.
- A cross-tenant object access returns 404 ("scoped not-found"), never
  403 (which would leak that the object exists).
- Real empty states are not a UI blocker to defer past -- "no products
  exist yet" is a valid, testable, real state.
- Draft/save/preview/confirm are genuinely separate operations; preview
  never has a side effect.
