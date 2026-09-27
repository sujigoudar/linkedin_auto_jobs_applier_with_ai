# Build every signal-trading, commercial-admin and customer dashboard

Work in the genuine current application repository. This is an in-place UI and service-integration assignment, not another architecture proposal or permission to activate trading. Read START_HERE.md, catalog/decisions.json, docs/00 through docs/15, the current phase, and relevant screen/form/action contracts. Use programmatic queries for large catalogs rather than loading every test into model context.

## Binding correction: real empty states are not fabrication

The commercial app was inspected at revision 2de2d7e2522af2554d438de580a5e0635e23899b with three routes: healthz, scoped me and Stripe receipt. Its lack of released products does not justify deferring pages, database models or configuration workflows. Verify current HEAD and preserve newer work.

Implement real empty list/detail queries, real Product/PortfolioVersion drafts, saved preferences, eligibility state and actual command validation. A user must be able to create an unreleased product, refresh and read the persisted draft, and see exactly why it cannot be published. The public catalog stays truthfully empty. Do not return hardcoded empty responses forever, seed fake production returns, or label an inert button complete.

Isolated synthetic fixtures are expressly authorized for unit, integration and browser testing with disposable databases, controlled identity and external protocols. They must be marked as fixtures and never seeded, published, charged or traded in production. Actual external service qualification remains separate. Missing external credentials cannot block the independently testable UI/service work.

## Scope and architecture

Deliver all 66 screens, 41 typed form families, 170 visible actions, shared states/components, metric definitions, the 24 inherited CP commercial journeys and 12 private journeys. Preserve CP-001..114 and all current trading safety constraints. Map the exact CP-091..114 text to actual consumers and evidence, not just a document.

Keep private signal-copier execution, SQLite and owner auth separate from signal-portfolio-commercial, PostgreSQL and tenant auth. Reuse the current FastAPI, local Chart.js/Tabulator and HTML/JS conventions. Add Jinja2 layouts/macros and small versioned shared CSS/ES modules. Do not create a second trading engine, second frontend server, arbitrary plugin platform or forced React/Dash rewrite. Optional Tom Select/GridStack require a demonstrated need and accessible alternatives.

Four interfaces are distinct: public site, customer portal, commercial admin/research console and private trading console. Commercial owner is not private account authority. Customers never gain a proxy to private close/flatten routes. The initial concrete routes, read-model contracts, proposed APIs and exact action bindings are in the catalogs. Reuse compatible existing domain APIs through explicit adapters; do not implement two financial command paths.

## Authentication and permissions

The commercial JWT /me endpoint is not a complete browser login. Wire the existing issuer/tenant services to a revocable opaque same-origin server session, secure HttpOnly cookie, explicit trusted origins/proxies, CSRF and current membership/object authorization. Keep tokens server-side and private-owner sessions independent. Preserve legitimate provider callbacks with their own verified signatures/state; do not expose all callbacks or require browser auth on signed webhook ingress.

Read roles do not imply mutation roles. Enforce per-action and per-subaction predicates, especially incident recovery, report books, releases and access grants. Derive tenant/account scope from the authenticated context; reject caller-supplied permission flags. Test with real non-superuser PostgreSQL RLS and cross-tenant foreign keys. No support impersonation, raw credential viewer or arbitrary role assignment.

## Implement the domain and UI together

First complete the actual empty-database vertical slice described in docs/12. Then implement each phase's models, migrations, query services, commands and screens. Inventory existing domain functions before writing equivalents. Missing Product/PortfolioVersion/report/job models are implementation tasks, not permission to leave the frontend absent.

Every screen has a required layout/reading order, filters, columns, metric contract, empty-state copy, next action, permissions and state behavior. Every configuration field has a key, type, label, validation, default/inheritance and persistence rule. Use supplied schemas plus full service/domain checks. Partial drafts can save typed provided fields; confirmation requires all current conditions.

Use draft -> validate -> preview -> confirm -> operation status. Preview cannot reserve capital, publish, charge or alter mandate. Confirmation claims scoped idempotency and rechecks exact account/product/version/rights/entitlement before effects. UNKNOWN means status reconciliation, not another automatic POST. Financial success must come from authoritative service outcome, not optimistic client state.

All actions in catalog/actions.json need real navigation, read or command bindings. Direct action endpoints are allowlisted handlers, not generic reflection into internal services. No dead “coming soon” control presented as completed functionality. An unavailable integration has a working blocked state and a named unmet requirement; the positive feature remains blocked, not passed by refusal.

## Financial and commercial truth

Use actual execution/accounting read models. Keep requested,filled,owned,still-executable and protected quantities distinct. Maintain source/model/platform/follower/business books, fees, marks, timestamps and provenance in tables, charts and exports. Unknown is not zero. Reducing fills are not completed episodes. Chart decimation cannot change reported drawdown. No price/return computed in the browser beyond approximate rendering coordinates.

Paid is not connected; connected is not authorized; authorized is not currently copying; signal receipt is not a fill. New copying defaults to new entries only. Existing-position synchronization is separately consented and previewed. Payment failure, grant expiry, pause, revocation and handoff have different effects and cannot automatically abandon protection or create a new financial action.

Portfolio Lab must enumerate its complete declared candidate scope, keep failures and historical information cutoffs, preserve holdout and cost evidence, and create only private drafts until review. Product edit/release does not silently alter existing open episodes. Source rights, merchant/platform/legal approvals remain independent gates. PAMM/MAM UI supports the actual broker program and explicit dealing/NAV/fee conventions; no SaaS custody or guessed performance fees.

No JEV or runtime LLM is needed. Optional text assistance cannot invent performance, calculate financial truth, access secrets, grant rights or approve itself.

## Shared design and accessibility

Follow tokens, spacing, typography, dark/light themes, responsive layouts and 44px control targets. Shared components include scope/context, quality/origin, metric card, data grid, chart+table, empty/error state, form/wizard, version diff, action preview, operation receipt, incident banner and approved disclosure.

Implement all 12 states for each relevant resource and action. Public session expiry must not require login to public content. Loading/denied/failure are not an empty successful query. Cancel old requests and verify context generation before rendering late responses. Stable snapshots/cursors prevent pagination drift. Signed/view preferences cannot hide mandatory account,origin,risk,cost,quality and safety information.

Use safe text/DOM and server autoescaping. No raw HTML in broker/source/user fields, no inline handler strings, no real secret in browser storage, logs or exported fixtures. Use CSP defense in depth, accessible dialogs, focus restoration, labels/error summaries, keyboard alternatives and reduced motion. Charts always have corresponding accessible values and definitions.

## Verification

Run the package validator to inspect design consistency, but do not treat those tests or SCREEN_ATLAS.html as application evidence. The atlas is an offline reference only.

Implement collected parameterized drivers for all 1,270 test specifications and their complete nine-browser/viewport project scope (11,430 baseline instances), plus every documented field-boundary/mode/action expansion and applicable inherited execution/security tests. The master scope is explicit; complete sharding is allowed, sampling is not a substitute. New actual endpoints, fields or capabilities extend the scope. Disabled intended positive features stay in the denominator as blocked.

Exercise actual apps, DB queries, auth, CSRF, API serialization, persisted drafts, independent external protocol state and rendered controls. Build populated fixtures locally without a live portfolio. Include zero/one/many data, two tenants, roles, expired rights/previews/sessions, duplicate/out-of-order events, price tampering, stale responses, lost command responses, open-position wind-down, unavailable fees/marks and recovery. Keep production credentials and egress out.

Record actual case IDs, drivers, commit/config/schema/fixture hashes, JUnit, observations, screenshots and failing attempts. Expected answers cannot be imported into the application to manufacture observed outcomes. Repair consumers and rerun full impacted and final release scopes. Missing library/driver/data/provider access is BLOCKED/NOT_RUN, not PASS. A static screenshot and a returned status code alone do not establish a workflow.

## Execution and handoff

Follow all 12 phase prompts. One coordinator owns integration; at most two disjoint implementation worktrees after shared contracts stabilize. Merge the provided namespaced skill/rules with existing Claude files; do not overwrite existing project instructions or bypass tool permissions. Maintain ops/dashboard_state.json and ops/DASHBOARD_RESUME.md with exact state, decisions, commands, evidence, blockers and next eligible task.

Continue independent implementation without another broad questionnaire. Group unavoidable owner actions for identity/platform/cloud/rights/merchant/live release. No live orders/cancellations, customer charges/subscriptions, live publication, production resets, credential rotation or unapproved infrastructure spending from this build task.

Deliver actual code and migrations, a usable real empty-state site, working persisted drafts/configuration, populated isolated test flows, every screen/form/action status, CP traceability, full test evidence and exact remaining external blockers. After authorized deployment access, verify the inactive origins/TLS/auth/callback/asset configuration. Label designed,implemented,API/DB integrated,isolated-tested,externally-qualified,deployed-inactive and live-released separately.
