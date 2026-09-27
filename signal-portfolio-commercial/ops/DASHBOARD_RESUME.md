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

Full suite is 377 tests, ruff and mypy both green (**use `python3 -m
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
`twelfth_slice`/`thirteenth_slice`/`fourteenth_slice` for the exact
file lists and what was deliberately left unbuilt in each. 51 of 66
screens (AD-01, AD-02, AD-03, AD-04, AD-08, PU-03, PU-05, ID-04, AD-16,
CU-14, AD-09 partially done) remain -- pick the next one following the
steps below. **If you add a new table to
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
