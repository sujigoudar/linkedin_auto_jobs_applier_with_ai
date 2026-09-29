# Component Catalog

One entry per real module under `app/`. "Responsibility" is the single
job the module owns; see each module's own docstring for the full
reasoning — this table is a map, not a replacement for reading the code.

## `app/api/` — HTTP routers

| Module | Responsibility |
|---|---|
| `dashboard_routes.py` | Every browser/customer/staff-facing HTTP route: `/ops/*` (staff), `/app/*` (customer), public catalog/marketing (`/`, `/portfolios`, `/pricing`, ...), local auth (`/auth/*`). Bound to `app_role`. |
| `relay_routes.py` | `POST /internal/relay/ingest-batch` — the sole inbound route for signal-copier's export relay. Bound to `relay_role`. |
| `dependencies.py` | FastAPI `Depends` providers: `get_db_session`, `get_relay_db_session`, `get_current_scope` (JWT/cookie → `TenantScope`). |

## `app/models/` — SQLAlchemy ORM tables

| Module | Responsibility |
|---|---|
| `tenancy.py` | `Tenant`, `UserIdentity`, `Membership` (role), `CustomerProfile` — the compound-FK isolation core everything else hangs off of. |
| `ledger.py` | `LedgerEntry` — the append-only four-book economic journal. |
| `integration_inbox.py` | `ExportStreamRegistration`, `InboxEvent` — the relay ingest inbox. |
| `token_revocation.py` | `IssuedToken`, `RevokedToken` — JWT `jti` issuance/denylist. |
| `local_auth.py` | Password hash + email-verification fields (on `UserIdentity`) and web-session records. |
| `audit_event.py` | `AuditEvent` — append-only actor/object/action log. |
| `product.py` | `Product` — the sellable unit, its lifecycle state and publication blockers. |
| `portfolio_version.py` | `PortfolioVersion`, `PortfolioVersionSleeve` — append-only versioned sleeve membership. |
| `sleeve.py` | `Sleeve` — a research-universe lineage record. |
| `rights.py` | `RightsGrant` — a source/analyst rights grant a sleeve depends on. |
| `research_run.py` | A declared Portfolio Lab research-run and its candidate universe. |
| `release_review.py` | `ReleaseReview` — the independent-reviewer approval queue for a Product. |
| `publication.py` | `PublicationIntent` — a queued/admitted action against a real publisher. |
| `publisher_destination.py` | A declared (inactive) external channel/strategy destination. |
| `publisher_writer_claim.py` | The `(channel, external_strategy_id) → writer identity` uniqueness fence. |
| `real_account_route.py` | Declared real-account routing config (build-only). |
| `platform_connection.py` | A customer's declared (not yet live) platform connection. |
| `copy_mandate.py` | A customer's copy-mandate draft/lifecycle record. |
| `portfolio_selection.py` | A customer's selected portfolio. |
| `eligibility.py` | A customer's residence/service eligibility facts. |
| `support_case.py` | `SupportCase` — a customer's support/incident case. |
| `incident.py` | `Incident` — the operator-facing incident register (AD-21). |
| `content_document.py` | `ContentDocument` — methodology/status/legal content with its own public-visibility RLS policy. |
| `price_version.py` | A priced entitlement/plan draft. |
| `billing.py` | `Subscription` and test-mode pricing fixtures. |
| `webhook_event.py` | Idempotent Stripe webhook event record. |
| `managed_program.py` | A declared PAMM/MAM program config. |
| `integration_configuration.py` | A declared (inactive) integration/provider config. |
| `api_key.py` | A customer's own scoped API key (hashed). |
| `notification_preferences.py` | A customer's alert delivery preferences. |
| `customer_display_preferences.py` | A customer's display/profile preferences. |
| `workspace_settings.py` | Tenant-wide cosmetic/workspace defaults. |

## `app/services/` — business logic

| Module | Responsibility |
|---|---|
| `auth.py` | JWT issuance/verification, `TenantScope`. |
| `local_auth.py` | Argon2 password hashing, web-session create/delete (ID-01/ID-03). |
| `token_revocation.py` | Issued-token bookkeeping + denylist (fail-closed on lookup error). |
| `permissions.py` | The single explicit role→action allow-list. |
| `staff_access.py` | Invite/revoke a colleague's operator `Membership`. |
| `audit_log.py` | The one writer to `audit_events`; query/list support. |
| `evidence_manifest.py` | Synchronous, verbatim, hash-verifiable audit-event export (AD-18). |
| `ledger.py` | `append_entry` / `append_correction` — the only sanctioned ledger writers. |
| `platform_performance.py` | Blended VWA-cost book performance replay. |
| `analyst_attribution.py` | Per-analyst FIFO-lot P&L replay + completed-episode/win-rate derivation. |
| `follower_observation.py` | Customer-account observation boundary (not yet populated — no live connector). |
| `business_economics.py` | Subscription-revenue unit-contribution/break-even formulas (never blended with ledger P&L). |
| `sleeve_mapping.py` | Resolves an inbound `SOURCE_RECEIPT`'s (provider, analyst, parser_version) to a `Sleeve`. |
| `portfolio_research.py` | Research-run candidate universe + combinatorics. |
| `candidate_comparison.py` | Shadow/compare report across candidates. |
| `portfolio_selection.py` | Customer portfolio-selection lifecycle. |
| `portfolio_rights.py` | `check_portfolio_rights` — every member sleeve must independently qualify. |
| `rights_registry.py` | Rights-grant register queries. |
| `sleeve_admin.py` | Sleeve draft create/list. |
| `product_admin.py` | Product draft create/save/reload + publication-blocker computation. |
| `publication.py` | Publication-intent queue/state. |
| `publication_admission.py` | `admit_publication_intent` — real rights + entitlement recheck at the effect boundary. |
| `publisher_destination.py` | Destination draft save (inactive-only). |
| `publisher_writer_claim.py` | Enforces the single-publishing-mode uniqueness claim. |
| `collective2_publisher.py` | C2 API4 order-envelope builder (build-only). |
| `etoro_adapter.py` | eToro Builders API trade-request builder (demo-only). |
| `copyfactory_close_only.py` | CopyFactory close-only-mode classifier. |
| `real_account_route.py` | Real-account routing declaration logic. |
| `entitlement.py` | Payment/safety entitlement checks (new-exposure vs. risk-reducing actions). |
| `eligibility.py` | Live jurisdiction/service eligibility computation. |
| `onboarding.py` | Customer onboarding flow support. |
| `release_review.py` | Review-request/decision logic (independent-reviewer gate, staleness check). |
| `integration_inbox.py` | The relay ingest state machine — see ARCHITECTURE.md §3. |
| `integration_status.py` | Owner/staff-facing relay health summary. |
| `source_coverage.py` | Per-tenant `SOURCE_RECEIPT` disposition report (INT-027). |
| `integration_configuration.py` | Integration config draft save (allowlisted providers/environments only). |
| `relay_auth.py` | HMAC verification for the relay ingress (with secret-rotation support). |
| `fit_simulation_client.py` | Signed cross-service call to signal-copier's public fit-simulator. |
| `mam_allocation.py` / `pamm_accounting.py` | Managed-program allocation/NAV accounting policy references. |
| `managed_program.py` | Managed-program draft create/submit-for-review. |
| `customer_managed_programs.py` | Customer-facing managed-program eligibility/status view. |
| `customer_overview.py` | Customer landing-page summary. |
| `customer_performance_report.py` / `customer_performance_state.py` | Customer-facing reconciled performance (reads `Book.FOLLOWER`). |
| `customer_selection_detail.py` | Customer's own selection detail view. |
| `customer_alerts.py` | Customer alert/delivery-history view. |
| `customer_billing.py` | Customer-facing billing/invoice/plan view. |
| `customer_display_preferences.py` | Customer display-preference persistence. |
| `customer_support_view.py` | Staff-facing scoped customer support record (AD-11). |
| `support_case.py` | Support-case create/list/detail. |
| `notification_preferences.py` | Notification-preference persistence. |
| `workspace_settings.py` | Tenant-wide workspace-settings persistence. |
| `api_key.py` | Scoped API-key create (hashed secret)/revoke. |
| `content_document.py` | Content draft/submit-for-review, HTML-injection refusal. |
| `publication_admin.py` | Admin-facing publication-intent detail view. |
| `operations_overview.py` | AD-01 cross-subsystem summary. |
| `incident.py` | Incident register CRUD/actions. |
| `price_version.py` | Priced-entitlement draft. |
| `entitlement.py` | (see above) |
| `stripe_webhook.py` | Stripe signature verification + idempotent event recording. |
| `history_origin.py` | Composite-label weakest-link computation (CP-009). |
| `model_gateway.py` | Optional AI-assist gateway (Phase 11, bounded). |
| `public_site.py` | Public catalog/marketing page data assembly. |

## `alembic/`

`env.py`/`versions/` — 37 real, applied migrations from initial schema
through RLS/append-only setup, `relay_role` access, and every table
above (see `docs/database/` for the sibling agent's migration-history
doc; `docs/architecture/DEPENDENCIES.md` lists the schema-evolution
direction).

## `tests/`

Per-module unit/integration tests plus `tests/conftest.py`, which spins
a real disposable PostgreSQL 16 cluster once per test session (dropping
and recreating tables per test function) — see PROJECT_STATUS.yaml for
current pass/fail counts.
