# Commercial signal portfolios: implementation workbook

This is the consolidated documentation; machine-readable contracts, cases, prompts and local reference tests are separate files.

# Scope, actual baseline and the commercialization change

This package is a commercial extension contract, not a new trading engine and not a certification of the existing application. The inspected PR head was `cdbcd1eca999adfaabea76fffda856852f3ecd08` in `sujigoudar/linkedin_auto_jobs_applier_with_ai`, subdirectory `signal-copier/`. Confirm the current revision and preserve later fixes before implementing anything. Only the PR metadata, current dashboard source, economics source and JEV README were inspected for this task. This is not a new end-to-end audit. No broker, payment, source-history or customer workflow was exercised.

The requested new product combines licensed provider/analyst strategies into versioned signal portfolios; researches complementary combinations; sells alert/research access; publishes approved strategies through external copying platforms; and reserves a future broker-managed PAMM/MAM extension. Collective2 is the intended platform name in the request. MAM is the multi-account-management term used here. PAMM and MAM are not interchangeable with ordinary copy subscriptions.

## Existing UI and economics

The current dashboard has owner login, account/provider/analyst/routing controls, account economics, managed-lifecycle coverage, recent signals/orders and local Chart.js/Tabulator assets. Preserve working controls and safe rendering. It is an owner console, not yet the customer storefront described here.

`app/economics.py` calculates average-cost realized P&L from filled order rows. Its docstring explicitly excludes fees and unrealized market marking. Its `completed_trade_win_rate` implementation counts profitable reducing fills, not independently completed trade episodes. Preserve compatibility temporarily, add a correctly named `closing_fill_win_rate`, and deprecate the misleading alias through a versioned API change. Commercial reporting requires actual executions/corrections, fees, product multipliers, cash flows and marks, plus completed-lifecycle metrics and explicit provenance. Do not relabel current gross outputs as net results or a portfolio track record.

The screenshots are visual references only: price panels, equity lines, monitoring, execution timeline and a tablet display. The screenshots' $68-to-$750K and 48-hour claims are unverified. Do not use them as marketing, expected returns, acceptance criteria or evidence that JEV is needed. The unrelated advertisement is out of scope.

## Scope boundary

Previously the application served only the owner's accounts. This request authorizes designing and implementing new customer tenancy, commercialization and managed-account interfaces in non-live environments. It does not itself supply rights to resell a paid provider's signals, customer trading authorization, investment-management registration, a payment processor approval, platform acceptance or custody authority.

No other-account credentials or money are required to implement the deterministic core, customer sandbox, billing test mode, protocol simulators or portfolio research on authorized/synthetic data. Missing rights block the affected publication, not independent coding work. Preserve all previous financial safety requirements, execution repairs and audit cases. An inherited failing risk or fill test is not excused because this work is 'only marketing'.

The implementation is staged: licensed alert products and truthful reports first; approved Collective2 publication next; eToro account/program adapters and other approved copying channels separately; broker-managed PAMM/MAM after its own legal and operational approvals. Customer self-directed copying and discretionary managed accounts have distinct scopes, consent and release records.

## Vocabulary

A provider supplies source instructions. An analyst is a person/model/style within a provider. A sleeve is one qualified analyst/strategy/policy combination. A portfolio version allocates risk/capital across sleeves and cash. A product sells access to one or more portfolio versions. A publication sends a canonical portfolio instruction to a channel. A follower allocation binds a customer's selected account, portfolio, risk limits and version. A venue execution establishes actual financial effect. These identities must not be collapsed into a symbol or an email address.


---

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


---

# Architecture and isolation

## Chosen topology

Keep one repository and the existing private execution application. Add a separate `commercial_api` process and a `portfolio_worker`/`publication_worker` role using shared reviewed Python packages and build artifacts. Do not expose the private owner application's account/flatten endpoints to public customers. Reuse current HTML/JS, Chart.js and Tabulator assets with public/customer/operator shells; no frontend-framework rewrite is required.

Public/customer control plane: PostgreSQL with Supabase Auth and row-level security is the target for new customer identity, subscriptions and product records. This is justified by the new multi-customer scope, not a retroactive assertion that Supabase exists in the current repository. Use an isolated authorized project/schema with appropriate database roles; do not repurpose a project with unrelated writers without explicit isolation review. Local tests use disposable PostgreSQL and a controlled JWT issuer. Login consent and an actual hosted Auth project are external deployment tasks. Do not migrate or reset the owner's current SQLite execution store simply to introduce customer accounts.

Private execution: retain the corrected current store and writer. Export reviewed immutable execution/metric events through a narrow outbox/projection bridge. The commercial API cannot query broker keys or modify this database. Customer performance observations are independently sourced and tenant-scoped; private owner data is never a customer's default dataset.

Publisher: consume only released `PublicationIntent` records in the commercial database. It holds a separate approved strategy-publisher key, never a broad customer's brokerage credential by default. External C2/eToro strategies can affect real followers; a publisher is a financial-effect component even when it sends only 'signals'. One publication owner per strategy/account/channel, enforced by durable claims and actual fencing. No public HTTP worker, billing webhook handler or LLM may call a broker or publisher directly.

Research: read immutable authorized Parquet/snapshot data using the existing numerical stack. Dedicated processes have bounded CPU/memory and no trade keys. No research query runs against mutable live financial projections without a consistent snapshot. Optional model calls receive only permitted redacted data and have no execution tools.

## Tenant model

Objects: `tenant`, `user_identity`, `membership`, `customer_profile`, `product`, `product_version`, `portfolio_version`, `subscription`, `entitlement`, `copy_mandate`, `subscriber_allocation`, `delivery`, `customer_execution_observation`, `report`, `support_case`. Operator roles are separate memberships: owner, researcher, reviewer, publisher_operator, billing_operator, support_readonly. A billing operator cannot release a strategy; researcher cannot grant rights; customer cannot query another tenant; support cannot view broker credentials.

Use opaque identifiers, authenticated principal-derived tenant scope, SQL parameterization and RLS. Never accept a browser tenant ID as proof of access. All child rows have compound foreign keys or equivalent constraints preventing cross-tenant parent references. Public products/reports are sanitized published projections, not broad table reads. Service-role keys bypass RLS and stay server-only with narrower DB roles where possible. Test API authorization and DB row policies independently, including exports, stored objects, SSE, task results and signed URLs.

A customer's registered identity is not automatically an approved trading customer. Email verification, appropriate residence eligibility, accepted agreements, platform identity, exact account mandate and active product entitlement are separate conditions. Withdrawal/custody permissions are not requested. Secrets are encrypted under role/site-separated keys; token revocation is independent of subscription status.

## Persistence and effect contracts

Every original signal has stable provider/channel/message/revision/time identity. Immutable portfolio versions pin component/parser/policy/data references. Publisher intents have unique `(channel, external_strategy, portfolio_version, logical_action, revision)` keys plus body hash. Commit local intent and entitlement/audience snapshot before effects. Attempt records retain request hash, secret-free correlation, unknown/accepted/rejected status, external IDs, observed child families and reconciliation requirements.

Use short transactions and durable job records. No Redis, Kafka, Temporal or Kubernetes is required initially. Small worker pools claim jobs with transactions and lease ownership for scheduling, while financial fencing is independent. A lost lease never proves a previously running publisher can no longer send. Parallel workers may research disjoint candidates, deliver nonfinancial email and render reports; only the selected writer issues strategy-changing commands.

## Environments

LOCAL_SIM, INTEGRATION_ISOLATED, PLATFORM_DEMO, PRIVATE_SHADOW and COMMERCIAL_LIVE are disjoint deployment identities. Database records, secrets, domains, storage and event buses carry environment. No promotion by flipping a restored row. Customer and platform demos must not subscribe to a live strategy. Production events cannot be replayed into publishing during tests. Historical content can create a replay job, never an actionable entry event.

Infrastructure inherits the guarded primary/standby design. Supabase control-plane and commercial worker availability do not imply private execution failover. Independent backup, identity and fencing tests apply per financial writer. Free tiers are candidate development resources, not contractual uptime guarantees. Sources: SRC22, SRC24, SRC25; existing audited deployment obligations remain.


---

# Signal portfolio engine

## Units of combination

Do not combine raw buy messages into a single unbounded feed. Normalize each licensed component into a sleeve with provider, analyst, strategy/horizon, asset/product, parser version, execution policy, cost model, capacity, risk unit and history origin. Maintain its independent virtual book even when the account holds the same instrument through several sleeves. An opposite signal does not automatically liquidate another sleeve. Net only at a deliberately approved execution layer while preserving both owners and conservative stress exposure.

A portfolio version contains an ordered eligible-universe snapshot, selected sleeves, basis-point capital weights plus cash, risk-policy references, asset/channel allowlists, portfolio/cluster concentration limits, target volatility/stress constraints, maximum subscriber capacity, research cutoff, report IDs, consent/disclosure version, deployment artifact and released selector envelope. Every component membership or weight change creates a new immutable version. Historical membership is never overwritten.

## First product templates

Create these DRAFT, NOT_OFFERED templates; attach real providers only after rights and data qualification:

P01 US Equity Intraday: compatible intraday equity sleeves, no inherited overnight permission; portfolio-level order deadlines preserved.
P02 Equity Swing: compatible swing sleeves with explicitly released overnight/gap-risk policy.
P03 FX Diversified: licensed trend, mean-reversion or other demonstrably complementary FX sleeves, exact broker/product units and rollover costs.
P04 Crypto Diversified: compatible spot/derivative sleeves separated by product; never label market-neutral unless measured exposures and stress evidence support that claim.
P05 Multi-Asset Allocation: qualified constituent portfolios plus cash, only on channels/accounts able to implement the actual composition. No synthetic promise that one account supports every asset.

Do not force three different named providers into a portfolio merely for branding. Two analysts from one upstream source may be more dependent than their names suggest. A portfolio may remain unpublished because no credible complementary set exists.

## Data admission

Use complete authorized history ranges, retaining original message revisions, timestamps and all candidate signals, including rejected, unfilled, canceled, open, revised and failed trades. Preserve provider publication, local receipt and information-availability times separately. For every point, distinguish a known flat sleeve from an unavailable/missing sleeve. Only known-flat returns can be zero; missing history must not improve covariance artificially. Align different session calendars on a declared common valuation clock, including stale-market policies and cash/funding effects.

Record actual, forward-paper, platform-model and reconstructed histories separately. No 'all actual' classification merely because an input price came from a real feed. Portfolio replay must simulate shared capital, outstanding orders, margin, late entry, stops, target/stop replacement latency, fees, spreads, borrowing/funding, currency and platform subscriptions. Replay combined decisions rather than summing independent sleeves that each assumed the whole account was available.

## Complementarity measures

Compute eligible pairwise and cluster statistics on common usable periods: return correlation with count/interval, downside correlation, overlapping drawdown intervals, simultaneous direction/underlying/sector/factor exposure, concurrent capital demand, holding-time overlap, turnover, liquidity usage, spread sensitivity and performance by predeclared regime. Flag provider cross-posts as shared provenance rather than fabricated independent opportunities. Retain missingness and confidence intervals. Low historical correlation is not proof of a hedge or stable independence.

Use data cutoffs at the actual decision time. Regime labels must be available causally; never label a crash regime using future peak-to-trough information and then 'predict' it. Portfolio comparisons include adverse co-loss scenarios even when ordinary covariance is low.

## Candidate family and finite evaluation

Research defaults, editable only through a new research-config version: 2–5 sleeves per candidate; no more than 12 eligible sleeves in one approved exhaustive run; 15% fixed cash during initial comparisons; maximum 35% capital weight per sleeve and 50% per correlated cluster; no borrowed capital by default in research. These are deliberately conservative study settings, not changes to the owner's existing financial limits or a live recommendation. Lower limits from an actual account/product always prevail. Two-sleeve candidates may retain additional cash because 2×35% cannot invest 85%.

Enumerate every subset within the approved run universe and size bound, and every declared recipe: equal sleeve capital, inverse-volatility risk proxy, hierarchical risk parity, and constrained minimum-CVaR. Include existing single-sleeve strategies and current production portfolio as benchmarks. Each recipe implements the same cash/concentration/cost/capacity constraints or is recorded INFEASIBLE; do not silently drop awkward subsets. Equal weights are a baseline, not evidence of equal monetary risk. If the approved run would exceed the compute budget, do not randomly sample and call it exhaustive: schedule all deterministic shards or obtain approval for a separately named smaller universe. Every candidate receives a terminal outcome and evidence reference.

Optimization can use the existing SciPy/scikit-learn/skfolio components. Do not install Optuna/River/FinRL. Pin method, hyperparameters, seed, solver tolerances and deterministic tie breaks. Use shrinkage/covariance handling appropriate to sample size, but never generate fabricated returns. Negative sleeve capital weights and leverage are disabled by default; this does not prevent a legitimately short-trading sleeve within its own approved risk limits.

## Evaluation and selection

Chronological outer walk-forward assessment: default rolling training window 252 common valuation sessions and test window63; minimum executable screening of126 usable sessions and30 closed lifecycles per sleeve; otherwise INSUFFICIENT_EVIDENCE with continued forward shadow collection. These counts are screening conventions, not statistical guarantees. The final last20% of available time is held out from selection. Purge crossing trade horizons and embargo at least the maximum relevant holding period plus data-availability lag; compute the exact boundary from sleeve policy. With inadequate data for this structure, no commercial economic-evidence approval is issued.

Within training only, compare recipe parameters and estimator choices. Apply the predeclared multiple-comparison method with the existing arch tooling, using dependence-aware block resampling. Freeze loss series, block method/length, confidence, and number of candidates tried. Keep untouched holdout and forward-shadow results separate. Rerunning until a favorable holdout appears consumes that holdout; it cannot retain its untouched label.

Rank by feasibility and drawdown/stress/capacity first, then Pareto tradeoffs among net growth, tail risk, turnover and execution quality. Do not label one maximum historical Sharpe portfolio 'best forever'. Selection requires a documented comparison to simpler baselines and component sleeves, stability across folds, realistic fee sensitivity and practical minimum account sizes. Failed criteria keep the baseline or cash; do not loosen them automatically.

## Dynamic allocation

Runtime adaptation selects only recipes/weights inside a previously released bounded envelope with exact model hash, inputs, effective times, risk maxima and expiry. Default recomputation is weekly for NEW admissions, not during every quote. Regime changes may reduce allowed new exposure immediately through existing released defensive rules; they may not widen a stop or increase hard limits. Offline research can run daily without changing production weights. Missing inputs select the declared baseline or hold the affected sleeve allocation as cash. Never renormalize missing sleeves upward implicitly.

Open trades retain their original source lineage and management-policy version. A rebalance that changes current positions is a distinct approved transition with costs, quantity commitments and consent; changing a weight document alone does not close/reopen positions. Material changes require customer notice/new mandate where the existing mandate does not cover them.

Outputs: candidate registry, dataset manifest, complete trial enumeration, correlation/co-drawdown charts, capacity stress, fold/holdout/shadow metrics, selected/rejected reasons, publication compatibility, rights intersection and release card. Sources: SRC20, SRC28–SRC31; methodology above is a proposed design, not observed economic superiority.


---

# Accounting, performance and commercial truth

## Four independent books

Keep source recommendations, canonical portfolio model, platform strategy and actual follower execution books separate. An alert delivered is not an executed trade. C2 strategy model performance is not customer actual performance. The owner's discretionary activity is not automatically the portfolio product. A platform's 'verified' status retains its precise definition and cannot be copied into a generic verified badge.

The execution journal is append-only by identity with correction/reversal events; projections can be rebuilt. Record exact instrument, side, quantity step, execution price, multiplier/point value, currency, fee/funding/borrow cashflows, event time, receipt time, source authority and reconciliation state. Use Decimal or integer minor/tick units at accounting boundaries; do not calculate exact money in browser floating point. A corrected partial execution changes the relevant projections once. Foreign-currency P&L and conversion values retain their sources and conversion convention.

NAV is marked assets plus cash less liabilities using an approved valuation policy. Separate reporting marks from executable bids/asks and stale marks from current marks. No mark or missing opening basis makes the affected amount incomplete; exclude nothing silently from totals. Broker statement reconciliation provides an independent check. Customer reports must disclose unresolved residuals rather than insert balancing gains.

## Metric registry

Each result includes metric ID/version, entity (sleeve/portfolio/platform/follower), units/currency, origin, start/end/as-of, denominator, cost convention, valuation clock, sample count, data-quality mask, uncertainty and computation hash.

Total return: use time-weighted return split at actual external cash flows where sufficient valuations exist. Cash deposits are not profit. Money-weighted return may be separately reported with actual dated flows and solver status. Modified Dietz is explicitly labeled an approximation when used; not silently substituted for TWR.

Net P&L: gross trading result minus verified incurred transaction costs, financing and the declared subscription/platform fee allocation. Never subtract estimated fees twice or label modeled fees actual. Report both strategy economics before a customer subscription and representative all-in customer economics with its exact capital/price tier/cost assumptions. A fixed $99 monthly fee has a materially different return impact on $5k and $50k; display the convention.

Realized/unrealized: determined by the frozen accounting convention and exact lot/allocation identity. Preserve the owner's existing average-cost reporting where needed; new commercial journal must implement its declared lot convention. Tax reports are out of scope unless separately qualified.

Completed-lifecycle win rate: net profitable strategy episodes divided by all closed episodes, counting break-even separately. Partial exits and multiple fills are not separate completed trades. Expose `closing_fill_win_rate` as a separate metric if useful. Unresolved/open trades remain in exposure/NAV, not quietly discarded from performance.

Profit factor: gross profit of qualifying net-completed episodes divided by absolute gross losses, with explicit EMPTY, NO_LOSSES and DEFINED states. No-loss is not a finite number to rank above all other candidates without sample warning.

Drawdown: peak-to-trough of the defined cash-flow-adjusted NAV/return series; use nonnegative magnitudes, record underwater duration and valuation gaps. Chart downsampling must not alter the calculation. A strategy-version change does not reset public historical peaks. Real-time drawdown has a separate resolution/source from daily drawdown.

Sharpe/Sortino: periodic excess returns and compatible risk-free/downside target, declared annualization frequency and autocorrelation caveats. Zero variance or insufficient observations returns undefined, not infinity as a success score. Calmar and CAGR require positive compatible starting values and meaningful intervals; avoid annualizing a few days into promotional performance.

Tail risk: expected shortfall/VaR horizon, probability, sample/tail count, method and uncertainty. Report historical worst outcomes and deterministic scenario stress alongside estimates. Do not confuse historical CVaR with contractual maximum loss.

Trade-level: gross/net R using the ORIGINAL entry-time risk denominator, MAE/MFE from valid owned-position observations, hold duration, fill/rejection rate, average win/loss, win/loss streaks, slippage and costs. No-stop history uses only a predeclared replay fallback; hindsight-selected stops cannot define R.

Operational/replication: signal-to-publication latency, channel delivery lag, platform acknowledgment/fill lag, follower tracking error, missed/canceled/late trades, proportion covered by verified native stops, protection deficits and quota incidents. Negative observations remain visible.

Portfolio: sleeve contributions, common-period correlation/downside correlation, marginal stress/risk, cash utilization, simultaneous exposure, turnover, capacity headroom and rights/data completeness. No double counting constituents in an aggregate portfolio-of-portfolios; traverse lineage once.

## Subscriber fees and attribution

Store processor fees, tax, refunds, platform fees and provider royalties in a BUSINESS ledger distinct from trading accounts. Subscription revenue is not investment P&L. Fee price changes do not rewrite prior periods. Coupons, prorations and annual-prepaid recognition are explicit business events. Investor performance-fee calculations, when broker-managed services are later enabled, derive from broker agreements and actual statements, not generic SaaS invoicing.

## Reports and publication

Public reports are approved immutable snapshots with complete disclosures. Customer reports contain only their data. Operators can inspect the raw lineage and recompute. Export includes metric definitions, data-quality notes and source/contract attribution permitted by rights. No API caller can switch a hypothetical row's origin to actual. Archive corrected reports with old/new hashes and explanation; never erase unfavorable history. Sources: SRC09–SRC11.


---

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


---

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


---

# Broker-managed PAMM and MAM extension

Implement the domain and read-only simulator now. Real managed-account activation remains separately blocked until exact broker agreements, account capabilities, entity/legal status, permitted investors/geographies, client mandates and fee authority exist. Do not collect trading capital through Stripe, create an unlicensed wallet/pool, or treat an internal allocation table as a broker PAMM account. Customers fund the approved broker directly through its authorized process.

## Distinct models

MAM allocates orders across separately identified managed accounts under permitted manager authority. PAMM commonly allocates participation/economic results according to a broker's percentage/unit mechanism. Broker-specific contracts can differ. The integration stores an exact `AllocationProgram` with authority, eligible accounts, dealing schedule, allocation math, margin conventions, fees, correction handling and authoritative statement source. No generic 'PAMM=true' checkbox qualifies it.

A signal copier can generate desired trades for a managed program, but the broker's account hierarchy, fill allocation and official NAV/unit statements are authoritative. CopyFactory-style individual copying is not automatically native PAMM. Read back accounts, participation, pending capital flows, order allocations, fills and fees. If the broker has no supported API for a required action, expose the read-only/manual-approved workflow and block automation rather than simulate a successful live effect.

## Fair MAM allocation

Freeze participating accounts, eligibility, available margin and the chosen proportional/risk allocation before submission. The approved recipe determines whether an omnibus or per-account order is permitted. Prohibit assigning good fills to one client and poor fills to another after execution. Partial fills allocate according to a precommitted rule, stable tie breaks and legal lot sizes; unallocatable remainder is explicit. Track rejected/ineligible subaccounts independently. A client below minimum quantity is not rounded up beyond its mandate. A customer deposit cannot retroactively enter an earlier allocation cohort.

Default simulator uses largest-remainder allocation of integer units with deterministic account-ID tie break, constrained by pre-approved maximum allocations. Production uses the actual contractual broker rule instead. Record every rounding residue and prove total allocated units equals actual allocatable fills. Rate/fill fairness analysis is reported across clients without exposing one client's identity to another.

## PAMM/unit accounting

Record valuation timestamp, units outstanding, subscription/redemption requests, dealing cutoffs, official NAV per unit, cashflows and fees. Requests after a cutoff enter the next permitted dealing event. Deposit buys units at the approved dealing NAV after its status is confirmed; withdrawals consume units under the same documented convention. Do not treat a pending transfer as invested capital or give a late deposit earlier gains. Broker-restated NAV causes a versioned correction, not silent overwrite.

Performance fee calculations are disabled by default. For an approved high-water-mark program, define unit/account HWM, crystallization calendar, hurdle, equalization/series treatment, cashflow adjustment, loss carryforward, currency, fee base and correction rules. For the simple test-only no-cashflow interval: fee = positive part of (pre-fee NAV − previous fee-adjusted HWM) × approved rate, new HWM = max(previous HWM, post-fee NAV). Deposits/withdrawals require unit-series/equalization or exact broker convention; this simple formula must reject those inputs rather than generate a wrong fee. Trading losses carry forward; a new month does not reset a high-water mark. A customer's fee may differ from another due to subscription timing and equalization. Never charge a generic SaaS invoice based on an estimated pooled profit.

## Managed-account customer experience

Separate application/signatures, broker onboarding/KYC, mandate, reporting, participation/capital-flow status and fee statement. KYC completion remains the broker's verified record where the broker owns it; the SaaS does not store identity documents unnecessarily. Show money movement only as a broker-hosted action/link and observed status. Warn that local request acceptance is not broker settlement. Support mandate termination, successor manager, account closure and incident handoff under the actual agreement. Fees, costs and risk disclosures use approved versions and evidence.

## Exit and incident authority

Subscription cancellation does not revoke a broker mandate; mandate revocation does not authorize further discretionary trading. Establish a precise state transition and broker acknowledgment. A broken master connection may require suspend-new, preserve native protection and contact authorized operators, not unconditional liquidation of all investors. Reconciliation must handle allocation rejects, trade busts, forced broker liquidations, account restrictions and customer withdrawals during open positions. No auto-hedge or exercise action outside the approved program. Source: SRC16 and applicable legal review SRC09–SRC11.


---

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


---

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


---

# Data contracts and commercial API

The schema files define application-owned interchange contracts. They are not a copy of vendor OpenAPI schemas and are not proof that external operations exist. Fetch and pin each official vendor contract during adapter implementation. Schema validation is only the first layer; cross-field numeric, identity, rights and effect invariants require the code/test checks specified here.

## Required entities and persistence keys

rights_grants: granting party+grantee+source product+contract version; no overlap automatically broadens permissions.
sleeves: provider+analyst+strategy+parser version+management policy+product profile.
portfolio_versions: immutable version ID, source-universe hash, components/cash, policy/research/report refs.
trial_runs/candidates: all evaluated/failed candidates, input/solver hashes, fold/outcome evidence.
releases: artifact+portfolio+channel+customer audience+rights+review signatures, effective/expiry.
publications: unique logical action/revision/destination, parent lifecycle, audience snapshot and remote effect family.
customers/tenants/memberships: verified identity with scoped roles; tenant from authentication, not caller-supplied authority.
subscriptions/entitlements: processor truth and exact paid-through intervals, immutable price version.
mandates/allocations: independent platform/customer permission, exact product/account/risk scope and termination policy.
deliveries: publication/customer/channel/revision unique, delivery attempts and final disposition.
metric_series/metric_values: immutable origin, accounting/valuation/cost version and quality.
managed_programs/capital_requests/fee_statements: broker-native authority and observed status, disabled unless approved.
review_actions/incidents/audit_events: actor, reason, immutable previous/new hashes and evidence.

Use UTC aware timestamps internally, integer basis points for weights, decimal strings for money and explicit integer quantity steps where products require. Preserve legal signed prices for explicitly compatible products; equity/FX price positivity must not be generalized to every future. Client-generated identity is validated for format but is never authorization. Use explicit version fields and optimistic concurrency on editable resources.

## API conventions

Prefix `/api/commercial/v1`. Public reads return only published projections. Customer/staff requests require verified role and tenant scope; sessions require CSRF protection for mutations. `If-Match` or expected_revision is mandatory for changes to a current draft, account setting or mandate. `Idempotency-Key` plus canonical request fingerprint is mandatory for payments, subscriptions, release/publish/wind-down and other effects. Same key+different body returns409. Expired/stale state returns409 with non-sensitive current revision; denied authorization returns403 or indistinguishable404 as appropriate.

List APIs use stable cursors plus snapshot/as-of identity, filters bounded and allowlisted. No browser supplies raw SQL, remote callback URL without validation, processor amount, staff role or external strategy identity it does not own. Responses include object ID, environment, version and timestamps. Errors return code, human-readable safe message, retry classification and correlation ID without secrets. All exports are tenant-bound, expiring and rights-filtered; caches key identity/entitlement/version and invalidate on revocation.

Endpoint inventory is in catalog/endpoints.json with role, exact input fields, response contract, expected effect, idempotency and acceptance tests. Actual OpenAPI must be generated from strict Pydantic request/response models in implementation, with no unrestricted catch-all payloads. API build completion requires every inventory row bound to an actual consumer and tests, and every discovered extra route added to the inventory. Public website and metrics/health routes are also inventoried for leakage and side effects.

## Event envelope

All internal events include event_id, event_type, schema_version, occurred_at, available_at, received_at, tenant_scope, environment, aggregate_id, aggregate_revision, causation_id, correlation_id, payload_hash, provenance, data_quality and typed payload. Never flatten a source edit into another independent entry. Event consumers deduplicate by semantic identity and record progress transactionally. Ordered delivery is enforced per aggregate while unrelated jobs remain concurrent. Out-of-order state-bearing vendor events trigger authoritative readback, not blind last-arrival-wins updates.

## Read versus effects

Creating a research draft can write control-plane data but never reaches a publisher. A model 'preview' stays non-actionable. A release approval is an authority change and must not be a hidden side effect of generating a report. Checkout creation can create a payment-session object only after allowed product/price validation; payment authorization cannot be reused as broker consent. Platform deletion/copy removal/cancel operations must be classified by their actual external financial consequences.


---

# JEV, LLMs and Claude development workflow

No LLM is needed to calculate covariance, choose a constrained portfolio, account for fees, conserve quantities, authenticate customers or process billing. The deterministic optimizer, accounting journal and policy-release workflow are the core implementation. Keep the financial runtime operational when all model providers are disabled or unavailable.

The inspected `jarrodwatts/jev-trader` README describes a TypeSafe Jev MON-USDC/Monad per-block decision demonstration, a mock default, a dry-run deployment and asynchronous live receipts. It is not a diversified signal-product optimizer or a licensed commercial copying service. Its scope is much closer to high-frequency order-book execution than this non-HFT signal business. No evidence connects it to the screenshot's profit claim. Do not adopt its transaction loop or fire-and-forget pattern for customer portfolios.

Potential reuse: after verifying source license, study its separation of intent/receipt/fill events and compact SSE timeline for UI ideas. Do not copy protected branding, deploy its private-key workflow or treat source code availability as performance proof.

## Optional bounded model assistance

Allowed candidates: propose analyst-style tags with source spans; draft parser rules/test fixtures for human review; summarize a frozen research report; explain a portfolio's documented method to a customer; triage a support issue using only that tenant's permitted records; draft public product copy for compliance review. Optional JEV classification may be compared offline with deterministic labels only if access, cost, license and a measurable task-specific advantage exist.

Disallowed: invent signal parameters, missing history or platform APIs; decide rights/jurisdiction eligibility; approve marketing; change live portfolio weights/risk; place/cancel orders; grant entitlements or permissions; alter HWM/NAV/fees; browse other customers; evaluate requests using customer API keys. Model-generated content cannot be automatically published as advice or an approved signal. Support assistant must not recommend a personalized portfolio without separately qualified service authority.

Use a typed ModelGateway with allowed purpose, version, maximum input/output/budget, tenant scope and data-use agreement. Provider API keys are read-only research role secrets, not trading keys. Free models are not guaranteed private or zero-cost; data retention/training terms and fallback spending require approval. No automatic commercial API fallback if an allowance expires. Prompt/response logs redact identifiers and obey retention rights. Raw provider content may require explicit model-training/external-processing permission.

Evaluate on independently labeled, held-out authorized messages and adversarial prompt injection. Exact extraction fields must meet strict consistency requirements; uncertain output abstains. A misleading explanatory summary is a failed test even when its JSON validates. Use optional Promptfoo for regression/evaluation only where a model is actually enabled. Logs include model/version, evidence refs, abstention/cost/latency and review disposition. Omit this dependency when no model feature is selected.

## Claude Code execution

Use bundled project skill `/portfolio-commercial-build` as coordinator, with focused rights, research, publishing, SaaS, and verification skills loaded only when relevant. Create at most two disjoint implementation workstreams after canonical contracts stabilize. One integration owner controls schemas, execution identity and financial migrations. Reviewers cannot edit code or approve their own financial release. Inspect installed CLI syntax/model/permissions before relying on a particular command. Do not automatically install global plugins, grant unrestricted tools or place production secrets in the coding context.

Persist phase, current commit, branch/worktree, requirements, evidence, failures, blockers, next command and active long-running jobs in `ops/commercial_state.json` and `ops/COMMERCIAL_RESUME.md`. Stop after safe checkpoints on environment interruption; resume without replanning settled decisions. Use deterministic tools for full case enumeration and dataset scans, not repeatedly paste a huge catalog into model context. Use a capable default coding model already authorized by the owner; selective independent higher-effort review for financial/tenancy ambiguity. No unverified model-name/pricing recommendation is embedded.

Sources: SRC17–SRC19 and SRC23. These are implementation instructions, not claims that all optional models have been evaluated.


---

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


---

# Operations, deployment and ongoing cost

Keep the current app's guarded one-active-writer recovery rule. Public site traffic, payment processors and commercial research may be independently available, but none can activate a second financial writer. An external publisher can cause trades even without direct broker credentials. Fencing applies to it as strictly as private execution.

Deploy commercial API and private execution under distinct hostnames/process identities, with private DB and metrics. Customer auth redirects, billing ingress and platform callbacks get their own allowlisted origins and raw-body validation. No wildcard CORS. Secrets only in approved deployment channels, never JSON catalogs, browser assets, logs or examples. Use readonly public projections, protected exports, access logs with retention limits and no raw provider/private financial text in analytics tools.

The new commercial PostgreSQL database must not share transaction authority with SQLite through dual writes. Outbox exports and acknowledgment/checkpoint preserve provenance; subscription updates do not directly update the private position table. Publication identity and control-plane database are backed up with legal retention and credential separation. A backup restore must revoke stale customer sessions as appropriate while retaining financial deduplication and open obligations.

Monitoring: source last usable observation; stale/unknown rights; publication backlog and unknown effect age; model vs actual tracking gap; subscribed account capacity; due safety updates; billing event lag; tenant-denial anomalies; per-channel error/quotas; cost budgets; backup generation age; active writer evidence. Alert delivery is durable and retried under policy. A healthy homepage does not establish protected portfolios. Keep independent monitor incapable of publishing trades.

Failover: stop/fence old publisher, preserve broker/platform accepted-but-unobserved effects, recover exact portfolio/rights/mandate/event lineage, reconcile external state, establish new authority, then resume permitted new entries. No automatic catch-up entry stream from downtime. Recipients receive appropriate safety corrections without duplicate openings. Failback repeats the same checks. An expired source grant or revoked mandate cannot be restored as active by an old backup.

Capacity defaults for non-live load testing: 100 concurrent website users, 1000 total registered tenants,100 concurrent paid portfolio subscribers,20 sleeves and10 products,10 normalized events/second sustained with a100-event burst. These are benchmark profiles, not a claim of free-host capacity or authorized subscription capacity. Bound research CPU/memory and notification concurrency independently. Load-test at the selected cloud profile, identify queue/broker/platform bottlenecks, and lower declared commercial capacity if measurements fail; do not raise financial limits. Core command latency/service SLO must be set from selected strategy/route and measured, not an arbitrary web SLA.

Maintain cost records for cloud/database/backup bandwidth, identity email, source licenses/data redistribution, external strategy-manager plans, platform follower fees, notifications/SMS, payment processing/refunds, optional model calls and support. The external platforms are not free merely because the local software is open source. No paid upgrade on quota exhaustion without approval. Retain receipts/plan versions and capacity alarms.

Before charging: actual live price IDs and tax/refund policy; processor acceptance; source grants; platform provider acceptance; permitted customer geography; truthful published reports; support/incident owner and termination continuity. Unresolved field is a visible release blocker, not a placeholder hidden in the UI. All prototypes/test customers are clearly synthetic, and test billing keys can never charge real cards.

A customer-ready launch also needs versioned Terms, Privacy, risk/disclosure, complaint handling, contact/status pages, cancellation path, data access/deletion with legal holds, backup restoration, incident response and business-continuity owner. Claude creates policy drafts and implementation, but applicable professional/legal approval remains in the owner card. Third-party code and data licenses are separate acceptance checks, including commercial use of vectorbt and copied UI components.


---

# Completion order and gap register

The existing copier's recent progress is not discarded. Do not assert an old defect is still present without checking current code. Use its audit as inherited obligations and compare every repair to the actual checkout. New commercialization adds requirements, not a license to rush unresolved order/stop/transaction failures into a customer product.

Initial confirmed differences in this task's inspected source:
- Owner-only dashboard is not a customer signup/subscription/portfolio site.
- Economics remain gross realized average-cost reporting; no verified net multi-account portfolio record.
- The metric named completed_trade_win_rate counts reducing fills.
- The screenshots supply no verified performance or relationship to the inspected JEV repository.
- No executed provider redistribution grant, managed-account authority, chosen live subscription price or processor approval was supplied in the request.

Build phases:
00 inventory current code, audits, dependencies, sources, licensed datasets and actual dashboards; create reuse/requirements map.
01 implement immutable contracts and scoped rights/approval records, preserving old APIs through explicit versioning.
02 commercial PostgreSQL/auth/RLS, roles, service isolation and safe customer/public projections.
03 authoritative sleeve/portfolio/customer accounting and corrected metric definitions.
04 full authorized history and portfolio candidate research, fixed-scope exhaustive evaluation, reports and shadow jobs.
05 canonical portfolio event publication and subscriber lifecycle, effectful adapter boundaries and safety wind-down.
06 Collective2 API4 selected publisher, local protocol tests and permitted isolated strategy qualification.
07 eToro/CopyFactory adapters, demo and actual eligibility states; no implied provider approval.
08 product catalog, Stripe test billing, entitlements, customer support and business cost views.
09 all public/customer/operator website journeys, true snapshots, fees/metrics, rights and accessibility.
10 broker-managed PAMM/MAM domain and simulator; broker contract-specific integration when available.
11 optional model gateway/evaluation only for an approved useful task; no dependency for financial launch.
12 full actual app tests, independent security/financial review, deployed inactive service and evidence-backed owner handoff.

Parallel work only after interfaces stabilize. UI-safe components or read-only data ingestion can run alongside bounded backend changes. Financial schemas and publication-state semantics have one owner. No task may mark a feature done merely because the method exists, a plan is written or a simulator passes. The final report separates SPECIFIED, IMPLEMENTED, LOCAL_TESTED, INTEGRATION_TESTED, PLATFORM_QUALIFIED, DEPLOYED_INACTIVE and LIVE_RELEASED.

## Explicit improvements beyond the request

1. Data-rights expiry and jurisdiction are enforced at delivery, not only checkout.
2. New subscriber joining, existing position synchronization and safety handoff are distinct workflows.
3. Payment failure never silently abandons open exposure.
4. External publication is treated as a financial effect, including test strategy subscriber isolation.
5. Separate canonical model, owner trades, external model and actual subscriber results.
6. Portfolio version changes cannot retroactively improve the displayed track record.
7. Model assistance cannot approve rights, change money math or control customer accounts.
8. Customer capital/fee scales and aggregate capacity are explicit, preventing unrealistic small-account claims.
9. Failed/retired portfolios and rejected candidates stay in the research/marketing audit trail.
10. Product demand and subscription conversion can be optimized separately without modifying trading risk.

Do not add a second trading engine, opaque super-agent coordinator, pooled wallet, unrestricted webhook pass-through, cross-tenant service API or generic model execution tool. Dependencies are selectively reused, with actual code/license/compatibility verification. Feature parity with every commercial copier is not claimed; qualified operation is scoped and evidenced.


---

# Sources and verification limits

Current documentation was reviewed on27 September2026. Exact account/product entitlement and operational behavior must still be verified through authorized access. Public documentation is not an executed customer agreement. No returns claimed in the screenshots were verified. No new application/broker/payment/customer GUI tests ran in this preparation.

## SRC01 Collective2 API4 overview

https://trade.collective2.com/c2-api

API4 bearer keys; no separate sandbox; Strategies test strategies differ from General/AutoTrade real data.

## SRC02 Collective2 signal guide

https://api-docs.collective2.com/guides/how-to-submit-signals

Order wrapper, child IDs/OCA, integer quantity, documented price-only changes; TIF sample inconsistency requires resolution.

## SRC03 Collective2 developer products

https://collective2.com/build-with-c2.html

Strategies, subscribers, integration modes and separately approved WhiteLabel.

## SRC04 eToro Builders trading

https://builders.etoro.com/products/trading

Official real/demo trading API; API availability does not confer regulated advisory/management status.

## SRC05 eToro social discovery

https://builders.etoro.com/products/social-discovery

Social/profile information and investor discovery; verify applicable account/program eligibility.

## SRC06 BuyAlerts terms

https://signup.buyalerts.com/terms

Personal-use and republishing/redistribution limitations. No user-specific resale grant established.

## SRC07 TradeAlgo terms

https://www.tradealgo.com/legal/terms-of-service

Personal noncommercial use and prior-written-consent requirement for sharing alerts/info.

## SRC08 KamdenAI

https://kamdenai.com/

Own-account automation capability is not evidence of commercial redistribution rights.

## SRC09 SEC investment adviser marketing

https://www.sec.gov/resources-small-businesses/small-business-compliance-guides/investment-adviser-marketing

Applicable adviser marketing, substantiation, gross/net and hypothetical-performance requirements; legal applicability requires review.

## SRC10 NFA hypothetical performance interpretive notice

https://www.nfa.futures.org/rulebooksql/rules.aspx?Section=9&RuleID=9025

Hypothetical results and combined programs require careful labeling and applicable treatment.

## SRC11 NFA CTA registration overview

https://www.nfa.futures.org/registration-membership/who-has-to-register/cta.html

Compensated commodity-interest advice/management may trigger registration or exemption analysis.

## SRC12 Stripe restricted businesses

https://stripe.com/legal/restricted-businesses

Financial businesses can require additional review; no merchant approval assumed.

## SRC13 Stripe subscription webhooks

https://docs.stripe.com/billing/subscriptions/webhooks

Authoritative subscription lifecycle and asynchronous billing events.

## SRC14 CopyFactory external signals

https://metaapi.cloud/docs/copyfactory/models/externalSignal/

External signals and copier identifiers; verify effects and current API.

## SRC15 CopyFactory close-only

https://metaapi.cloud/docs/copyfactory/features/tradeCopyingSettings/closeOnly/

by-symbol is not necessarily strict no-new-position semantics; immediate mode has closing effects.

## SRC16 MAM/PAMM broker illustration

https://www.cwgmarkets.co.uk/mam-pamm

Broker-operated managed allocation; not a universal public API or eligibility confirmation.

## SRC17 Claude Code skills

https://code.claude.com/docs/en/skills

Project skills and progressive disclosure; actual installed CLI must be checked.

## SRC18 Claude Code best practices

https://code.claude.com/docs/en/best-practices

Focused context, verification, scoped tasks and independent review.

## SRC19 JEV reference repository

https://github.com/jarrodwatts/jev-trader

MON-USDC/Monad per-block demo, mock model default and dry-run deployment described; not proof of screenshot profits.

## SRC20 skfolio

https://github.com/skfolio/skfolio

Portfolio research components; pinned-license and integration review required.

## SRC21 Stripe Python

https://github.com/stripe/stripe-python

Official payment client; no funds custody or merchant acceptance guarantee.

## SRC22 Supabase Python

https://github.com/supabase/supabase-py

Official control-plane/auth/storage client; service role must remain private.

## SRC23 Promptfoo

https://github.com/promptfoo/promptfoo

Optional isolated model evaluations; not live trading authority.

## SRC24 PostgreSQL row security

https://www.postgresql.org/docs/current/ddl-rowsecurity.html

RLS supplements API authorization; table owners/superusers bypass unless architecture addresses it.

## SRC25 Supabase RLS

https://supabase.com/docs/guides/database/postgres/row-level-security

Customer row isolation and privileged service-key boundaries.

## SRC26 Chart.js

https://github.com/chartjs/Chart.js

Reuse pinned existing frontend charts; verify data and attribution separately.

## SRC27 Tabulator

https://github.com/tabulator-tables/tabulator

Reuse existing safe tables, pagination and filtering.

## SRC28 arch

https://github.com/bashtage/arch

Bootstrap and multiple-comparison research; methodological assumptions remain explicit.

## SRC29 DuckDB

https://github.com/duckdb/duckdb

Offline immutable research snapshots, not competing live accounting authority.

## SRC30 Pandera

https://github.com/unionai-oss/pandera

Dataset validation; cannot create missing evidence.

## SRC31 vectorbt license

https://github.com/polakowo/vectorbt/blob/master/LICENSE.md

Commercial-use license review required; not assumed unrestricted for paid SaaS.
