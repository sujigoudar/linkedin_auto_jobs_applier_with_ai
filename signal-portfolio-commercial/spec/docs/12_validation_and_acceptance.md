# Verification and release evidence

This package provides application acceptance specifications and tested reference calculations, not a completed application or executed external integration. Every application case begins NOT_RUN. No file count certifies completeness. Preserve the current copier's known regression cases and remaining release blockers; this add-on cannot close them by replacing execution with a model portfolio.

## Scope ledger

Inventory the union of intended, implemented, configured, GUI-advertised and externally connected products/portfolios/rights/tenants/roles/accounts/channels/mandates. Bind every requirement to consuming implementation and executable driver, and every effectful code path back to a requirement. Discovered extra routes, exports, event types, role actions and platform account types require tests. Disabled intended features remain implemented-but-blocked or missing, not silently excluded from the denominator.

Execute every fixed case, gate combination and declared GUI project. Full sharding is allowed only with exact set equality to a frozen case-instance manifest. No smoke tests or statistical sampling stand in for the full defined finite scope. Stateful/random tests are supplementary and require reproducible seeds and permanent minimized regressions. Do not claim all indefinitely long market/network sequences are covered.

## Required test boundaries

1. Pure money/weight, unit and permission contracts, including bool/number/null confusion, wrong currency, zero/negative/infinite values, missing vintage and mismatched identifiers.
2. Actual PostgreSQL transactions and RLS for two or more tenants, all roles, orphan/cross-tenant foreign keys, service-role boundaries, restored state and migrations.
3. Actual API/SSE/export/download/queue paths with real auth tokens from isolated issuer, CSRF, replay, forged role/body/price/platform IDs, expired grants and deterministic concurrency.
4. Billing provider protocol: bad signatures, raw-body mutation, duplicate/reordered events, refund/dispute, annual downgrade, canceled mandate and paid-but-platform-pending.
5. Publication protocol: acknowledgment loss, accepted child IDs, changed quantities, no-resend unknown, out-of-order fills, strategy isolation, duplicate transmit modes, lifecycle cohorts and safe wind-down.
6. Portfolio research: common history, gap/flat distinction, revisions, complete subset/recipe enumeration, shared capital, holdout/purge/embargo, missing costs, lookahead and version reset attempts.
7. Actual connected public/customer/operator GUI in three browser engines and three screen sizes, all defined states; keyboard/accessibility; raw source injection; direct endpoint checks; correct fee/performance labels.
8. Broker-managed simulator: fair partial allocation, deposits/withdrawals around cutoff, HWM/equalization, broker restatement, account eligibility and no unintended money transfers.
9. Ops/faults: process failure at each local commit and external effect boundary, DB outage, queue delay, stale license, payment/vendor outage, region failover, key revocation, clock rollback, container limits and backup restoration.
10. Approved external integration: exact environment/strategy identity, no unauthorized subscribers, precise vendor accounts/capabilities, actual event readback and documented limitations. Simulation cannot count as external certification.

## Evidence

For every case record code/config/schema/artifact version, dataset snapshot, driver/test node ID, role/tenant/channel/environment, fixture hash, observations, expected values, assertion operators, start/end, captured errors, cleanup and final disposition. A pass cannot be a free-form claim. A case with no executed driver, wrong evidence layer, missing observations or contradictory expected/observed values is rejected. Expected values come from independent reasoning/fixtures, not the production function under test. Preserve failed attempts and exact repair evidence.

Four statuses have different meanings: PASSED, FAILED, BLOCKED_EXTERNAL and NOT_RUN. A denied prohibited action may PASS its negative test. An unavailable positive integration remains blocked even when it correctly returns 'unsupported'. No blanket xfail to hide a defect. At release, review every skip in the applicable selected scope; no skipped mandatory case yields commercial live readiness.

## Milestone gates

G0: private execution safety and authoritative ledger repaired/qualified for selected route.
G1: commercial database/auth/API tenant isolation and all local policy tests.
G2: complete licensed portfolio research, metric correctness and non-hypothetical labeling.
G3: billing test-mode lifecycle and entitlement safety, merchant/legal/rights reviews recorded before live charges.
G4: externally permitted isolated publisher/demo qualification with exact documentation/schema and no false fill/protection claims.
G5: actual authenticated customer/operator GUI and deployed inactive service, backup/restore, monitoring and incident drill.
G6: owner-approved limited commercial scope with exact customers/audience/channel/portfolio/capacity, legal and platform authorization.
G7: broker-native managed accounts only after dedicated legal, broker, NAV/fees and custody tests.

No release may reuse another product's approval merely because the engine is the same. All selected cases for the released scope are run; later channels are not advertised as working until separately qualified.
