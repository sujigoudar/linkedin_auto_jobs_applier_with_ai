# Website and dashboard contract

## Three surfaces, shared design system

PUBLIC: home, methodology, portfolio catalog, individual product detail, risk/cost disclosures, approved track record, pricing, status and signup. No owner account data or raw restricted provider content. Start with a restrained trading interface rather than imitating the social screenshot's neon scenery or extraordinary claims. Drawdown, basis, fees and update age must be as visible as gains.

CUSTOMER: Overview, My Portfolios, Alerts, Performance, Connections, Subscription/Billing, Safety/Incidents and Settings/Support. Authentication is managed customer identity, not the private OWNER_PASSWORD. The top bar always names tenant, environment, data-as-of, mode (alerts/model/platform copy/customer actual) and degraded state. Customer displays are read-only unless an explicitly authorized action has a preview and durable operation ID.

PRIVATE OPERATIONS: existing owner console plus Portfolio Lab, Publisher Control, Rights/Reviews, Capacity, Customer Support and Business Economics. Staff navigation is role-based; hidden links are not the security boundary. No customer can reach private owner controls with a guessed URL, exported token or service key.

Reuse local Chart.js/Tabulator. Add semantic HTML components and CSS tokens, accessible focus, dark/light contrast and explicit label/tooltips. No new React/Dash stack merely for appearance. A live feed uses authenticated SSE with replay cursor/snapshot fallback; it cannot duplicate financial actions. Polling fallback has snapshot/version checks and backoff. Disconnecting the UI never stops a worker.

## Portfolio catalog cards

Show portfolio name/version, asset/horizon, release status, accessible territories, investment risks, source-data rights status at appropriate disclosure level, number of sleeves, capital/capacity constraints, minimum implementation capital per channel, subscription/platform costs and one clearly classified performance panel. Primary figures: net return for the labeled series/period, maximum drawdown, closed episodes, duration, cash/capital usage, last update and tracking-quality caveats. Never a green 'verified live' badge for a reconstructed composite. Drafts are private. Public unapproved hypothetical results do not render behind a cosmetic disclaimer.

## Portfolio detail

Tabs: Overview, Performance, Composition, Risks/Capacity, Trade History, Methodology/Changes, Costs, Compatibility and Disclosures. Composition shows approved descriptions and risk contribution without exposing restricted provider identities; reviewer view shows full lineage. Correlation/co-drawdown matrix is interactive with sample sizes and common-period data coverage. Historic weights and version changes are shown at effective time. Benchmark uses compatible currency/frequency/cost origin. Performance origin switching must change labels, query and eligibility, not just recolor a curve.

Trade detail expands source-action class, canonical model decision, desired/actual quantities, effective stop/targets, publication attempts and authorized subscriber observations, timestamps and costs. Unknown/rejected/unfilled trades are not hidden by the default profitable view. Filter/export respects the same cohort and metric definitions.

## Portfolio Lab

Six-step wizard: eligible licensed sleeves and complete history; common-period/data-quality check; candidate universe/recipe/constraints; complete chronological run and capacity estimates; fold/holdout/shadow comparisons; approval/publication card. Persist drafts and resumable job state. Show all tried/rejected/insufficient/infeasible candidates, not just the winner. Pareto/correlation/underwater/exposure charts use precomputed backend results. Dataset and trial hashes are downloadable for authorized reviewers. A 'learn' button starts an offline job, never updates live weights.

## Customer onboarding

Signup -> verify identity/email -> residence and approved audience eligibility -> product/portfolio selection -> full cost/risks -> hosted test/live-approved payment -> entitlement verified -> alert preferences -> optional platform authorization -> risk/capacity/mandate preview -> explicit copy activation. Every intermediate state is resumable. Do not call a customer live merely because payment succeeded. New-only is the default. Joining current positions requires separate consent and feasible prices/capital. On incompatible jurisdiction or platform, explain what is unavailable; do not route through another country's account.

## Billing and connection UX

Show SaaS charges separately from C2/eToro/broker charges and managed-account fees. Plan downgrade previews loss of new-entry access without surprise liquidation. Subscription cancellation displays remaining episode safety/handoff obligations and platform-specific disconnect instructions. OAuth callbacks/state and credential reconnect are scoped to the actual tenant/account; a stale browser tab cannot change another account. Customer broker passwords/private keys are never requested through a generic support form.

## Publisher control

Rows: portfolio version, channel strategy ID, writer identity, approval/rights validity, last intended/acknowledged/reconciled action, external positions and outstanding child families, follower/capacity observations, data age, quota, incident. Commands: pause new publications, review backlog, resolve unknown via evidence, controlled release and approved wind-down. A 'retry' button is absent for potentially accepted orders unless the verified reconciliation plan makes it safe. Strategy deletion/unsubscribe/close-only commands warn about their actual external effect.

## Alerts and availability

Delivery ledger distinguishes created, eligible, queued, sent, provider-accepted, delivered where supported, expired, failed and superseded. Email opened is not trade filled. Order sequence preserved per lifecycle; late entry notices expire, but applicable cancellation/exit safety notices remain deliverable under policy. User-configured quiet hours may suppress promotional alerts, not silently hide time-critical safety obligations they accepted; show exact delivery preference semantics and fallback. Equal entitlement cohorts get fair scheduled dispatch, not secret founder priority.

## Accessibility and mobile

Test Chromium/Firefox/WebKit at1440×900,768×1024 and390×844; include keyboard and applicable touch inputs, focus order, labels, status/live region updates, reduced motion, chart tables and accessible errors. Right-scroll financial tables retain identity columns and units. All important actions are usable without hover. Chart text/table alternatives and data downloads include the same quality warnings. Screen width cannot hide drawdown or fees while leaving returns visible.

## Required journey states

Every journey in catalog/journeys.json gets normal, empty, denied, stale, partial, timeout, recoverable error and unsupported variants. Repeated clicks, reordered responses, two tabs, session expiry, browser restart, SSE reconnect, billing webhooks and source revisions must not cross tenant boundaries or duplicate effects. Financial preview/draft/report/export paths are non-effectful. Each test checks actual backend observations, not screenshots alone. Render testing is not broker certification.
