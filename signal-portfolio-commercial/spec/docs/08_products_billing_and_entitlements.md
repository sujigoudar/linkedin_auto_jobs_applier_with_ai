# Products, prices, subscriptions and access

## Product model

A commercial product is access to specified released portfolios/reports/channels, not a guarantee of performance. Product versions have immutable descriptions, included portfolios, audience/territories, disclosure, support channel, delayed-data convention, copy integration eligibility, provider royalties and cost allocation. Store exact display price IDs and currency, not a hardcoded browser amount. Historical product pricing remains auditable.

Provide four configured drafts:
FREE_RESEARCH: non-actionable educational/product information and approved delayed public metrics only; no default live entry stream.
ALERTS_ONE: one selected qualified portfolio, web/email alerts, own delivery history and metrics; proposed test-mode price USD39/month, USD390/year.
PORTFOLIOS_THREE: up to three qualified portfolios, comparisons and report exports; proposed test-mode price USD99/month, USD990/year.
PRO_RESEARCH_API: qualified catalog access within capacity, private API/webhook delivery and advanced reports; proposed test-mode price USD199/month, USD1990/year.

These are founder-review pricing hypotheses and test fixtures, not user-approved prices or market-researched conversion estimates. Live amounts require CARD-4 approval. Auto-copy platform costs are separately disclosed, not magically included. Native managed-account fees are outside these plans. Avoid 'VIP earns more' and prioritizing performance claims by price. A premium webhook requires a verified endpoint and data redistribution agreement; it does not grant the subscriber resale rights.

## Billing implementation

Use official Stripe Python for Checkout, Billing and Customer Portal in test mode first. Processor approval for the exact financial business is required before live charges. Use hosted payment collection; never store card details. Store customer/subscription/invoice/payment IDs with environment and tenant. Only the server chooses whitelisted live/test price IDs. Checkout success pages are not proof of payment or entitlement.

Verify raw-body webhook signature, timestamp/replay tolerance and correct account/environment. Persist event IDs before asynchronous processing. Handle duplicates and reordered events by fetching/reconciling current authoritative subscription/invoice state, not comparing only arrival order. Never grant a more privileged entitlement from an old invoice success after a newer cancellation/refund/restriction. A verified paid interval creates the precise entitlement interval; no local clock drift extends it silently.

States: PENDING_PAYMENT, TRIAL_AUTHORIZED (disabled by default), ACTIVE_PAID, CANCEL_AT_PERIOD_END, PAST_DUE, SUSPENDED_NEW_ENTRIES, ENDED, DISPUTED and MANUAL_REVIEW. Model refunds/chargebacks separately from access; follow agreed law/policy, not an automated retaliatory financial command. Provide cancellation and billing-history self-service. Proration, tax, coupons, credits and annual upgrades use server-side processor objects and approved policies. Disclose renewal/cancellation rules. Do not copy a provider's no-refund policy as the user's policy without approval.

Default grace: billing read access and safety obligation visibility persist; new premium entries require a valid paid-through entitlement. Past-due retry cadence follows configured processor policy, not an invented financial grace period. Customer can view own account and lifecycle history after cancellation subject to retention law. Keep paid feature status separate from copy mandate. A payment outage does not stop existing position management or revoke the broker's protective orders.

## Entitlement and safety separation

Authorization for a new entry requires all applicable dimensions: verified identity/tenant, active product access, source/data rights, approved audience, portfolio release, platform/asset capability, capacity, explicit account mandate and risk admission. No purchase can bypass them. A pending platform connection may coexist with paid alert access but must display 'copy not active'. Do not charge for promised unavailable functionality without the approved policy/consent.

Risk-reducing management uses the original episode/mandate and lawful continuity policy, not merely current premium subscription. If the customer revokes authority, stop discretionary actions outside the agreed termination flow and hand off safely. If downstream platform maintains copies independently, show its actual subscription/connection status and require its own termination procedure; local logout must not pretend it disconnected the account.

## Business economics

Track collected subscription revenue, taxes, processor/refund/chargeback fees, source licensing/royalties, platform costs paid by the business, cloud/data/notification/model costs and support burden. Unit contribution = revenue net of taxes/refunds/processor fees minus attributable variable costs and royalties. Break-even uses approved fixed costs divided by positive unit contribution; negative/unknown values remain a warning. Prices are evaluated separately from portfolio investment performance. Affiliate links/revenue sharing require disclosed conflicts and approved agreements. No affiliate payouts or provider royalties are transferred until properly authorized. Source: SRC12–SRC13 and SRC21.
