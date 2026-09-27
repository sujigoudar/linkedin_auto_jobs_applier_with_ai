# Publication and subscriber-copy lifecycle

## Canonical sequence

Qualified source revision -> sleeve decision -> portfolio admission/reservation -> canonical model action -> release/rights/audience check -> durable publication intent -> destination adapter -> external acknowledgment/readback -> platform events -> optional independently authorized follower observations. The private owner's fills are not a hidden extra eligibility step unless the product is explicitly an owner-fill-mirror strategy. Do not mix both models within one track record.

Actions: OPEN, ADD, REDUCE, CLOSE, STOP_UPDATE, TARGET_UPSERT, TARGET_REMOVE, TARGET_CLEAR, ENTRY_CANCEL, STATUS_CORRECTION and STRATEGY_PAUSE. Each has exact scope, original episode/parent ID, sequence, revision, quantity meaning, units, valid-from/expires time, policy version and original source lineage. Missing targets are allowed only with the complete approved exit policy. A delta action and desired-target-position action are different delivery contracts; don't infer one from the other.

## State and idempotency

Publication states: DRAFT, ELIGIBLE, QUEUED, SENDING, ACKNOWLEDGED, UNKNOWN, REJECTED, RECONCILING, SUPERSEDED, TERMINAL. 'Acknowledged' means accepted at that external boundary, not a follower fill. Retain original body hash, request correlation, remote strategy/signal/child IDs and every attempt. Same key/same body joins one operation; same key/different body conflicts. Unknown outcomes cannot be blindly retried even if a later subscription event changes.

Retries of definitely unsent reads/transports are bounded with jitter/quotas. For possibly accepted writes, use documented lookup/client-reference idempotency where available; otherwise require reconciliation/manual resolution. No invented universal idempotency header. Never publish the same strategy through both API and broker/account-transmit concurrently. Allow exactly one publication authority per external strategy/account, including aliases.

## Cohorts and consent

A customer's delivery cohort is determined at the action's eligibility time under a versioned entitlement. New customer default is NEW_ENTRIES_ONLY after onboarding; historical alerts are research, not trades. Synchronizing an existing model position is a separate priced/risk-checked transition with fresh data, local account inventory, protection plan and explicit consent. A customer who did not receive/open a lifecycle must not receive an executable orphan close that can create a short position.

Every follower allocation binds customer, exact account, broker/API, asset/product, selected portfolio version, risk ceiling, scaling rule, current permissions, jurisdiction approval, source rights, data entitlement and mandate. Platform-hosted copying may hold this authority itself; do not invent local read/write authority for accounts not connected to the app. Copy recommendations are not proof the follower acted. Preserve customer manually altered positions and exceptions rather than force them back to a model automatically.

## Quantity and overlapping products

Allocate per episode and portfolio, conserve customer holdings and all outstanding executable closes. Same symbol across products retains separate allocation. Subscription to two correlated portfolios can create combined risk; the configured customer account requires an aggregate limit. A public alert subscription with no account link can show a warning but cannot claim to enforce unseen account limits. Minimum tradable quantities may make partial exits impossible; use a released small-account policy or decline, never round up beyond risk to recreate an analyst trim.

A broker-netted close may reduce another allocation unless ownership is explicitly coordinated. A simultaneous long/short recommendation on a netting account needs compatible handling or blocks the new entry. Partial fills, child stops, terminal remainders, fees deducted in asset units and corporate actions flow through the existing corrected financial core. Adding commercialization cannot bypass EX01–EX14 or current audit blockers.

## Subscription, rights and incident transitions

Subscription expiration stops future premium entries but does not mean 'cancel every order now'. Preserve customer access to their executed-trade history and a limited safety update channel for previously admitted episodes as agreed. Automated strategies continue only the management authorized by the still-valid mandate. If consent is revoked, the app cannot simply keep trading: execute the preagreed cancel-new/handoff/owner-takeover process, with any permitted risk reduction explicitly scoped and remaining native protection documented.

Source-license expiration and lawful retention/wind-down rights are separate from customer billing. Negotiate continuity rights before admitting positions that could outlive a grant. If unexpected revocation eliminates a right to retransmit proprietary updates, do not override the contract in the name of safety; use the existing independent released protection/deadline procedure and approved broker/operator handoff. Escalate and freeze new exposure. No automatic deletion of records subject to required retention/legal hold.

Cancel-on-disconnect, platform close-only and unsubscribe may have destructive financial effects. Classify and test them as trading operations. An email delivery success, HTTP200 or strategy display is not evidence that customers are protected.

## Capacity and fairness

Track published model capital, external follower capacity where accessible, observed fill degradation, platform quotas and per-channel cost. A new subscriber must not exceed an approved liquidity/copy capacity. Where external follower amount is unobservable, disclose incomplete capacity evidence and use conservative admission limits rather than claiming no limit. Do not give founder accounts price priority secretly; implement precommitted equal-cohort publication and fair allocation. Different subscription tiers may differ in features/portfolio access, not delayed risk-reducing updates that endanger lower-tier positions.

Order bursts should use per-channel quotas and priority for existing-position reconciliation/protection over new signals and marketing. If capacity fails, stop new entries and display the reason. Do not relabel skipped strategy trades as missing data to improve the public return curve.
