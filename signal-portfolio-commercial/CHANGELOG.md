# Changelog

All notable changes to `signal-portfolio-commercial`, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style. This
app has no tagged releases — see `docs/process/RELEASE.md` — so
entries are grouped by date instead of version. Dates and short hashes
are real, from `git log -- signal-portfolio-commercial`.

## [Unreleased]

Everything in this file. This is pre-1.0, development-branch software;
nothing here has shipped to a live production deployment
(`docs/process/RELEASE.md`).

### 2026-10-01

#### Added
- `app/api/dependencies.py`'s `require_tenant_scope`: a FastAPI
  dependency that centralizes `app/db.py`'s `set_tenant_scope()`,
  replacing ~85 individual `set_tenant_scope(session, scope.tenant_id)`
  calls previously made at the top of each dashboard route handler in
  `app/api/dashboard_routes.py` with `scope: TenantScope =
  Depends(require_tenant_scope)` in place of `Depends(get_current_scope)`.
  No public behavior changes -- every route that set tenant scope still
  does, through the exact same `set_config(..., is_local=true)`
  mechanism, just once, during dependency resolution instead of
  imperatively in the handler body. A handful of genuinely different
  call sites are left as explicit, documented exceptions rather than
  forced into this one shape: ~8 routes that call `session.rollback()`
  mid-handler (the rolled-back transaction clears the session-local GUC,
  so they still set it again by hand after the rollback); 3 routes
  (`rights_register_page`, `deployment_status_page`,
  `platform_connection_wizard_page`) that need the caller's role for a
  permission check but run no tenant-scoped query at all; the
  pre-authentication `sign_in_submit`/`verify_email_page` bootstrap flow
  (ADR-0009), which has no `TenantScope` yet by construction; and
  `app/api/relay_routes.py`, which never sets `app.tenant_id` at all --
  it runs on the separate, restricted `relay_role` connection instead.
- `/terms` and `/privacy` routes and templates
  (`app/templates/pu09_terms.html`, `app/templates/pu10_privacy.html`):
  ID-01's signup checkbox has always required accepting "the Terms and
  Privacy Policy", but no such route or content ever existed. Both
  pages are explicit, honestly-labeled PLACEHOLDER content pending real
  legal review (not real Terms of Service/Privacy Policy -- no one on
  this team is positioned to author that), with a few generic,
  uncontroversial structural sections (who this applies to, data
  collected, how to contact us). `id01_auth.html`'s signup checkbox now
  links both terms in place of unlinked plain text.
- Risk-of-loss disclosure on the copy-mandate wizard
  (`app/templates/cu09_copy_wizard.html`): the screen where a customer
  sets `allocation_amount`/`max_trade_risk`/`max_loss` had no risk
  disclosure anywhere on the page. Added a "Risk and fee notice" panel
  near the top of the form, matching the wording/tone of the existing
  disclosures on `pu01_home.html` and `pu05_pricing.html`. This build's
  mandates can only ever reach DRAFT/CANCELLED state (no real activation
  pipeline exists), so no real money moves from this screen today, but
  it is still the natural place for this disclosure.

#### Fixed
- Accessibility gaps in the shared layout (`app/templates/_base.html`,
  used by every dashboard/auth screen): added a skip-to-content link
  (`.skip-link` → `#main-content`), wrapped the header's navigation
  link in a real `<nav aria-label="Primary">`, and gave `<main>` an
  explicit `id="main-content" role="main"`. Also added `role="alert"
  aria-live="assertive"` to every template's `.conflict` error banner
  (the shared failed-submission pattern, confirmed in 27 templates --
  `id01_auth.html` among them -- not just one), so a screen reader now
  announces a failed submission instead of staying silent. No
  axe-core/pa11y harness exists in this repo; verified by reading the
  rendered template source and by a new test
  (`test_base_layout_has_skip_link_and_landmark_roles`) asserting these
  attributes appear in a real rendered page.
- Portfolio version `version_number` race: a database-level unique
  constraint on `(tenant_id, portfolio_id, version_number)` for
  `portfolio_versions`, plus a catch-and-retry-once around it in
  `create_portfolio_version_draft_from_candidate` -- two concurrent
  draft-creation calls for the same portfolio can no longer both claim
  the same version_number; a persistent conflict now raises a clear
  `PortfolioVersionNumberConflictError` instead of leaking a raw DB
  error.
- Startup guard refusing to boot with `ENVIRONMENT=COMMERCIAL_LIVE`
  while any of `LOCAL_JWT_SECRET`, `RELAY_SIGNING_SECRET`,
  `CATALOG_FIT_SIM_SIGNING_SECRET`, or `STRIPE_WEBHOOK_SECRET` is still
  at its repo-committed placeholder default, naming exactly which
  secret(s) are still unrotated.
- HWM fee calculation (`compute_simple_hwm_fee`) now quantizes to 2
  decimal places (cents, `ROUND_HALF_EVEN`) instead of returning an
  unrounded Decimal with arbitrary precision.
- CU-02 "My portfolios" now shows the real product name instead of the
  raw product UUID in the "My selections" table.

### 2026-09-29

#### Added
- Revocable JWT/Bearer-token sessions: a real `jti`-keyed denylist,
  fail-closed verification, and owner/customer-facing revoke-all-tokens
  UI (`72efeae`).
- AD-18 evidence manifest export: a real, synchronous filtered JSON
  export of audit events with a content-hash manifest header
  (`f752904`).
- CURRENT+PREVIOUS dual-secret signature verification, enabling
  zero-downtime secret rotation across both apps' signing boundaries
  (`9ddc681`).
- CU-06: real max drawdown and completed-episode win rate, computed
  from the real FIFO-lot equity/episode data (`64d596d`).
- INT-027: real routing/admission/fill outcome encoding per
  `SOURCE_RECEIPT` (`edbe3a8`).

### 2026-09-28

#### Added
- PU-03: the fit simulator's real equity curve, now rendered as a real
  Chart.js line chart (`e95fc35`), building on the public "Try our fit
  simulator" panel (`7592e67`).
- Real session audit wired into AD-16/AD-11; AD-01 incident linking
  confirmed (`76cf4d9`).
- Customer-portal screens CU-04/CU-05/CU-06/CU-11/CU-15 (`fc25d37`).
- AD-06 candidate comparison/draft and AD-21 commercial incidents
  (`956bedb`).
- Real local sign-in/sign-up/verify/recovery system, ID-01/ID-02/ID-03
  (`e303af9`) — email delivery not wired (link shown on page instead).
- INT-001: real single-command installation via Docker Compose,
  spanning both apps plus the in-process relay (`777dee1`).
- INT-033: real-account single-writer enforcement — a durable claim
  above `PublisherWriterClaim` so a direct route and an external alias
  for the same real brokerage account can't both claim write authority
  (`3b8cf40`).
- INT-008/INT-009 bootstrap snapshot/manifest mechanism (`b462a77`).
- Producer-generation binding: reject rollback and reused sequence
  slots, INT-010 (`b330d3d`).
- Real, append-only control-plane audit trail on every existing
  command endpoint, S12 step 7 (`89a32b0`).
- Real FOLLOWER observation ingestion, idempotent, S12 step 6
  (`7c29651`).
- Portfolio Lab source feed: export and project `SOURCE_RECEIPT`, S12
  step 5 (`e89464f`).
- Per-analyst P&L attribution for `Book.PLATFORM`, FIFO-lot method,
  INT-026 (`d846626`).
- Real end-to-end late-fee correction path through the inbox, INT-012
  (`420b0e8`).
- Real ordering and gap detection on the commercial inbox, slice 13
  (`0f14efe`).
- A rendered owner "Trading & Integration Status" page, slice 11
  (`407157a`).
- Real owner trading performance (gross realized P&L) reporting, slice
  10 (`7b6c107`).
- The owner/staff Integration Status report, slice 8 (`0697319`).
- The restricted relay worker connecting `signal-copier` to the
  commercial inbox, slice 5 (`3ca33a7`).
- Commercial inbox for the Signal Platform Integration Correction Pack,
  slice 4 (`5a620a7`).
- `signal_platform_contracts` — the pure cross-service event envelope
  and identity contract, integration slice 1 (`c244dbd`).
- AD-11 real Mandates read view (`712cd08`); PU-06 Methodology, risk
  and legal documents (`893a03c`).
- CU-03 selected portfolio detail, CU-01 customer overview, CU-10
  pause copying/position handoff, CU-09 copy setup and mandate wizard,
  CU-07/CU-08 platform connections and wizard, AD-22 commercial
  deployments and recovery, PU-07 public service status, PU-04
  portfolio comparison, AD-15 managed allocations/NAV/dealing, CU-13
  profile/security/display preferences, CU-12 alert delivery
  preferences, CU-02 my portfolios, AD-10 publication intent/cohort
  detail, AD-05 research run and results, AD-20 workspace
  customization, AD-14 managed-program setup, AD-19 content and
  disclosure publishing, AD-13 pricing/entitlements/billing
  operations, AD-17 integrations/data rights/quotas — the bulk of the
  original CU-/AD-/PU- screen build-out (see `git log --oneline` for
  each individual commit).

#### Fixed
- Two real deployment gaps found by actually running the containers
  (`2c1079a`).
- CI smoke test: read the owner token from a file instead of masked
  logs (`43e1ac0`).
- Genericized `platform_performance` to be book-agnostic (`ec9beba`).
- Parked unsupported `schema_version` events, visibly rather than
  silently, INT-007 (`06b2252`).

#### Changed
- `docker-compose.yml`: load `signal-copier`'s real `.env`, wire
  `SESSION_SECRET`, document remaining commercial secrets (`d6613bb`).
- Added the missing `.env.example` for this app (`76984ab`).
- Closed the INT-019 navigation gap and added the INT-027 source
  coverage report (`a3c1cd1`).
- Re-verified the rights registry and command authority against new
  integration boundaries, INT-021/INT-031 (`77a6fef`).
- An acceptance-case verification pass against the integration pack's
  own 40 cases, slice 12 (`1945c2e`).
- Replaced CU-01/CU-03's blanket `UNSUPPORTED` state with precise
  performance states, slice 9 (`ee89ca6`).
- Integration slice 2: `evidence_class` + nullable fee on the ledger,
  S7 (`e640795`).

### Earlier

The original 13-phase build (rights registry, tenancy/RLS, the
four-book append-only ledger, portfolio research's deterministic half,
publication intent write-path, Collective2/eToro/CopyFactory adapter
request-shape mapping, subscriptions/entitlement/Stripe-webhook
verification, onboarding, PAMM/MAM simulation-only accounting, the
model-gateway permission boundary) — see `ops/COMMERCIAL_RESUME.md`,
`ops/commercial_state.json`, and `docs/history/ENGINEERING_LOG.md` for
the full, honest per-phase account.

[Unreleased]: https://github.com/sujigoudar/linkedin_auto_jobs_applier_with_ai/commits/claude/signal-copier-redesign/signal-portfolio-commercial
