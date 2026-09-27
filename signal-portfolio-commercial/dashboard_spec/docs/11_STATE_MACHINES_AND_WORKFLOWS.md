# State machines, not disconnected forms

## Universal draft and command workflow

`ABSENT -> DRAFT -> VALIDATED -> PREVIEWED -> SUBMITTED -> IN_PROGRESS -> SUCCEEDED / FAILED / UNKNOWN`.

Saving a partial draft is allowed if every supplied field is typed and scoped. Readiness validation returns all known blockers. Editing invalidates validation and preview. A preview binds object, version, proposed action, exact scope, audience, data/price/rights snapshot and expiry. Confirmation revalidates current prerequisites and claims an idempotency record before any financial or billing action. A duplicate request returns the same operation. A changed body using that key conflicts. No failed or unknown operation becomes successful because the browser navigated to a success page.

`UNKNOWN -> RECONCILING -> SUCCEEDED / FAILED / UNKNOWN` is the only ordinary recovery path for a possibly accepted effect. Browser retries cannot directly send it again. User can leave and reopen the operation using a stable URL. Logout stops personal polling, not the already authorized worker's safety management.

## Product and portfolio lifecycle

A Product draft may exist with no PortfolioVersion. A PortfolioVersion draft may contain no released research and may be entirely cash while being edited. Those are incomplete internal records, never a published track record. `DRAFT -> READY_FOR_REVIEW -> APPROVED -> PUBLISHED -> PAUSED_FOR_NEW -> RETIRED`. Edits create a successor draft; no silent mutation of a published version. Publishing additionally checks all rights, legal, channel, cost, audience and performance-evidence conditions. A public projection is a separate whitelist, not direct serialization of internal models.

## Customer onboarding

`IDENTITY_PENDING -> VERIFIED -> ELIGIBILITY_PENDING -> ELIGIBLE/REVIEW_REQUIRED/UNAVAILABLE`. Portfolio selection, paid entitlement, connection and mandate follow separate dimensions, not one global “active” bit. Selection does not copy. Subscription does not create a mandate. A mandate does not prove the connected account is currently executable. The customer overview presents the exact next step and every independent blocker.

Connection: `NOT_CONFIGURED -> AUTHORIZING -> VERIFYING -> CONNECTED / NEEDS_REAUTH / DEGRADED / DISCONNECTED`. A hosted callback is checked before binding identity; exact account/environment is re-read. Disconnect is a workflow with open-obligation checks, not deleting a credential row.

Mandate: `DRAFT -> VALIDATED -> CONSENTED -> ACTIVATION_PENDING -> ACTIVE -> PAUSED_NEW -> HANDOFF_PENDING -> ENDED`. Actual program names can map to existing domain enums with a reviewed migration; do not create aliases that change semantics. Existing-position sync is a separate explicit proposal. Revocation ends authority according to the governing terms while triggering required notices and handoff; it is not permission to continue arbitrary new management indefinitely.

## Billing and rights

Billing is driven by verified canonical processor state, not UI state, webhook order or redirect. Current receipt deduplication must gain actual apply/reconciliation state before “processed” is displayed. Upgrades/downgrades show future entitlement, effective date, invoice impact and open obligations. Same subscription does not simultaneously map to test and live price IDs.

Rights: `DRAFT -> IN_REVIEW -> ACTIVE -> EXPIRED/REVOKED`. Each sleeve, channel, geography, asset, audience and time must qualify. Cosmetic product naming, role changes, subscriptions or an LLM summary cannot override those conditions. Existing exposure has a separate legal/operational wind-down state, not disappearance from screens.

## Research and publication

Research: `DRAFT -> QUEUED -> RUNNING -> PARTIAL/COMPLETED/FAILED/CANCELED`. Show complete candidate denominator and all outcomes. Resume preserves input hashes and completed shards; modifying inputs creates a new run. A replay can correctly conclude insufficient evidence. Missing implementation cannot claim that conclusion.

Publication: `PLANNED -> AUTHORIZED -> SUBMITTING -> ACKNOWLEDGED/UNKNOWN -> RECONCILING -> VERIFIED/FAILED`. An acknowledgment is not a follower fill. Customer-visible publication and actual account execution remain different books. Cohorts are frozen for a given action; joining later does not receive an old entry as a new one.

## Managed programs

Program eligibility, broker mandate, subscribed capital, dealing schedule, units/NAV and fees are separate records. UI offers a permitted broker-bound request and status, never an unapproved investment-payment form. Deposit/withdrawal cutoffs use broker timestamps. A corrected NAV produces a restated version and auditable downstream adjustment. Cashflow-sensitive performance fees require a specified valid convention before display or execution.

## Screen-to-screen workflow definitions

The exact 24 inherited commercial journeys plus 12 private trading journeys are in catalog/journeys.json. Test every stated step and all relevant events against the real application. Screens display current domain state; they do not create a parallel browser state machine with financial authority.
