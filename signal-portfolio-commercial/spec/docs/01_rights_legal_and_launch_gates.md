# Rights, legal review and release gates

## Source and dataset grants

Implement `RightsGrant` records with granting party, grantee legal entity, exact source/product, governing contract version and evidence hash, signed date, effective/expires timestamps, permitted uses, customer jurisdictions, assets, channels, retention, attribution, sublicensing, model-training permission, capacity limits and post-termination wind-down obligations. Uses are independent: private research, derived-product research, public performance display, commercial alerts, automated third-party publication, discretionary management and model training. A personal subscription is not a commercial grant. A granted data-display right is not permission to resell signals or raw market data.

The named providers start with commercial rights UNKNOWN. BuyAlerts public terms restrict personal-use materials and redistribution; TradeAlgo explicitly restricts third-party sharing absent prior written consent. Kamden's public automation offering is not proof of resale permission. Actual executed agreements may change the result, but only after recorded review. Combining providers, hiding their names or paraphrasing alerts with an LLM does not manufacture authorization. If rights are denied, develop against synthetic fixtures and the owner's independently owned strategies; do not publish restricted source material.

Check rights at portfolio candidate admission, research export, product publication, subscriber delivery and outbound financial publication. A grant changed during a job must be rechecked before external delivery. Data downloaded earlier does not gain permanent future commercial rights. A portfolio's usable territory/channel is the intersection of its component grants and market-data entitlements. Do not fall back to removing attribution, renaming a provider or switching channels to bypass a denial.

Provider names can remain private intellectual property where the contract and applicable disclosure rules allow it. Compliance reviewers retain complete lineage. Customer product disclosures must remain accurate about methodology, third-party dependencies, conflicts and required attribution. Hiding a name is not a reason to make an unsupported claim of original research.

## Legal applicability

Default operating assumption for planning: the owner is US-based; customer jurisdictions are NOT approved by default. Record an owner-selected legal entity and counsel's scope-specific determination. Securities alerts, personalization, performance fees, futures/retail-FX advice, discretionary authority, pooling and solicitation can have different requirements. Analyze federal/state investment-adviser rules, applicable commodity adviser/pool rules, platform terms, market-data licenses, privacy, sanctions, tax and marketing rules for the intended service. Do not hardcode a publisher exemption, exemption based on small client count, or an 'education only' disclaimer as an exemption.

Software records review status; it does not decide legal eligibility. `LegalApproval` binds entity, product type, assets, distribution mode, customer residence/entity and marketing audience. A change in those fields requires review of the affected scope, not deletion of unrelated approved functionality. Unknown or expired approval blocks new enrollment and new exposure in that scope. Existing exposure invokes the preapproved wind-down/handoff procedure; it must not be abandoned or liquidated indiscriminately.

## Marketing and economic evidence

Store an immutable `PerformanceSeries` origin: RECONSTRUCTED_BACKTEST, FORWARD_MODEL, VENUE_PAPER, PLATFORM_MODEL, VERIFIED_OWNER_LIVE or VERIFIED_CUSTOMER_LIVE. Never concatenate these into a single unlabeled live curve. A portfolio built today from historically successful providers did not actually trade as a combined strategy last year. Public historical composites remain hypothetical even when their individual inputs were live accounts. SEC/NFA rules apply according to the entity/service determination. Public hypothetical marketing is disabled until approved audience, disclosures, assumptions and review are recorded.

Keep every candidate and rejected result. Do not choose a favorable start date, remove a failed sleeve from old history, reset drawdown by publishing a new version, or show only active/surviving products. Explain material strategy changes at their actual effective dates. Net versus gross cost conventions and subscriber/model differences must be visible. Marketing claims require report IDs, period, full cost/mark methodology, approved disclosure version and reviewer. Suppress 'guaranteed', 'risk-free', 'every dislocation is profit', unexplained verified badges and unsupported AI-performance claims. Actual publication requires human reviewer authorization, not LLM copy approval.

## Six owner-only action cards

CARD-1: legal entity, target customer jurisdictions, asset classes and service modes approved by appropriate counsel.
CARD-2: written source/market-data commercialization grants, attribution and termination provisions.
CARD-3: Collective2 strategy identities/API4 roles; eToro entity/program/API eligibility; any other copier or broker-managed account agreements.
CARD-4: payment-processor approval for the actual business, tax setup, live prices/refund policy and bank details supplied through hosted secure flow.
CARD-5: customer agreement/consent, privacy, marketing disclosures and complaint/escalation processes.
CARD-6: exact financial production release: artifact, model/portfolio versions, accounts, channels, limits, capacity and incident/continuity authority.

Group unresolved actions into these cards. Do not repeatedly ask for architectural choices. Login/MFA, signing agreements, professional judgments and money authority cannot be inferred by Claude. All independent development continues using explicit disabled gates. Sources: SRC06–SRC13 and SRC16.
