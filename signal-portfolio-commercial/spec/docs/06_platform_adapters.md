# External platform integration decisions

## Collective2 first

Implement API4 from current official documentation and actual authenticated entitlement. API2/API3 keys and examples are not interchangeable with API4. Use a scoped API4 Bearer key held only by the publisher. Resolve approved StrategyId, channel mode, instruments and quantity/TIF conventions. Preserve local logical action versus external SignalId, parent and stop/target IDs/OCA group separately.

The published guide uses an Order envelope, integer OrderQuantity, OrderType 1/2/3 and Side1/2. It documents TIF0 day/1 GTC, but one conditional example uses2. This is a documentation conflict, not permission to infer2; capture current authoritative schema/vendor confirmation before using unsupported values. Either C2Symbol or ExchangeSymbol is supplied, not both. Exact exchange/maturity/option contract/FX units are resolved before submission. Do not map arbitrary spot crypto or combination orders into an unsupported symbol type.

The guide documents price-only modifications, not general direction/duration/quantity replacement. A quantity resize requires an explicitly verified operation recipe, potentially involving cancellation/new children and uncertain outcomes. Native stop/target/OCA publication does not itself guarantee follower broker atomicity. Editing/canceling one linked child may affect its sibling; preserve and reconcile the group. Do not assume the local broker manager can safely repeat its existing amendment algorithm through C2.

Choose API_STRATEGY_PUBLISHER as the initial product mode. A separate source mirror/BrokerTransmit mode is an alternative, never a concurrent duplicate writer. Model publication stays independent of the owner's discretionary account. Platform model statements and actual connected follower observations are separate metric series. Use a dedicated external strategy for each independently sold portfolio/version policy where platform rules permit; a material change's historical treatment is approved, never a fresh strategy created just to erase losses.

There is no separate C2 sandbox. Local protocol simulators are the default. External Strategies testing requires an explicitly authorized, isolated test strategy with no subscribers, no AutoTrade links and no unintended platform exposure. General and AutoTrade APIs use real data. A nominal test strategy must not be assumed harmless if followers can attach. Read and assert isolation before every allowed external test; stop if it changes. Do not use production AutoTrade methods as tests. External credentials cannot be invented and tests remain BLOCKED when absent.

Payment ownership: local SaaS membership and C2 strategy/platform fees are distinct unless an approved partnership contract integrates them. Show both and prevent duplicate billing for the same promised service. Do not promise that one local price buys every platform charge. WhiteLabel is an optional approved integration, not the first launch dependency. Implement the transport-independent publication contract now; qualify authenticated operation details when access is supplied. Sources: SRC01–SRC03.

## eToro separately

eToro now publishes official Builders APIs with real and demo trading, market/limit workflows, portfolio reads and social discovery. Do not carry forward a stale assertion that eToro has no API. Register an application and verify the exact jurisdiction/entity/account scopes. A request identifier is not presumed idempotency unless the current contract establishes it. Close-by-position-ID is not the same as selling a generic ticker amount; preserve position identity and platform units.

A custom app, platform CopyTrader strategy and approved investor-provider program are distinct surfaces. Use the official API for an approved dedicated provider account and support program onboarding/status without promising the account is eligible. eToro's applicable program/account requirements determine whether/how it can be copied and compensated. Its App Store technology access does not grant advisory/management authorization. Multiple separately marketed portfolios must not silently share one mixed discretionary provider account. Obtain platform-approved account/profile structure rather than mass-register accounts.

Implement DEMO transport and data conversion, then real read-only verification. No code may select a real endpoint merely because demo is unavailable. Platform demo results are not verified live performance. The same local mandate must not send direct eToro trades and simultaneously enable external CopyTrader for the same allocation. Keep local subscriptions separate from platform fees/remuneration; do not manufacture an extra CopyTrader subscription charge under the platform's name. Sources: SRC04–SRC05.

## MetaApi CopyFactory and other channels

Use existing authorized MetaApi integration as a separate extension, not duplicate terminal wiring. External signals, strategies, subscribers and stopout events have distinct identities and permissions. Removing an external signal can cause positions to close, not just delete a database row. Read its actual operation contract. Map close-only carefully: by-position, by-symbol and immediately are not equivalent. A 'by-symbol' mode can allow new positions in an already held symbol and must not satisfy a strict no-new-position gate without further evidence. The platform's shared resources and subscriber slots create capacity/cost limits.

For additional platforms use `PublisherAdapter` and `ManagedAccountAdapter` contracts with explicit capability matrix and effect classification. Unsupported operations return typed unsupported before effects; no warning-only no-op. No promise that all copying platforms support option combinations, fractional sizes, stock shorting or specific crypto products. Platform eligibility remains per entity/account/asset/region. Sources: SRC14–SRC15.

## Required interface

Each selected adapter supplies documentation_revision, approved_identity, environment, canonical_instruments, quota/cost profile, effect-capability record, submit/lookup/cancel/amend recipes actually supported, strategy positions/history readback, customer-link state only when authorized, and shutdown/uncertain-command handling. Methods that can affect downstream customer accounts are marked financial effects regardless of name. Public GUI never receives these keys. Method presence is not qualification. All selected operation types get real application tests and controlled protocol fault cases before permitted vendor testing.
