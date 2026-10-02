# Signal Copier — End-to-End Decision, Capital, Lifecycle, and Verification Contract

Prepared: October 2, 2026. Intended consumer: Claude Code working in the existing signal-copier application.
Status: **IMPLEMENTATION SPECIFICATION; NOT AN IMPLEMENTATION AUDIT OR LIVE-READINESS CERTIFICATE.**

This contract is intended to prevent an apparently complete copier from omitting its portfolio allocator, ownership ledger, real sizing, partial-fill protection, or exit recovery. Every supported decision must have a deterministic result, monetary effects, an explanation, and executable evidence. A broker accepting one order is not end-to-end success.

## 0. Authority, scope, and what must not change

Preserve the latest October 2 owner decisions: existing SQLite/private HTML-JavaScript application, direct adapters where actually qualified, one canonical signal selecting one eligible physical account, exact lifecycle ownership, and separate paper/live environments. Do not require a rebuild around the older Supabase/Dash/SignalStack architecture. Do not add infrastructure, buy services, move funds, enable additional live routes, place validation trades, cancel real orders, or raise live risk limits under this specification. Implement and test the requested advanced sizing, pyramiding, and runner modes in non-live environments; promotion is a separate scoped release.

The September 22 v3.4 workbook contains useful numerical starting recommendations and lifecycle requirements, but it is not evidence of the current effective live configuration. Its architecture is superseded where it conflicts with the October 2 decisions. The September 26 commit-pinned audit of `sujigoudar/linkedin_auto_jobs_applier_with_ai`, application `signal-copier/`, revision `fccf57affd8515fe059b1af854856828078a737e`, is historical evidence, not a claim that those defects remain today. Locate the actual current application and deployed revision before mapping requirements to code. Do not create missing-looking files merely to pretend a historical build contract exists.

Authority order: exact current owner authorization and mandatory account/venue constraints; released financial policy; this implementation contract; older compatible reference documents; provider preferences. Hard limits are intersections, not last-writer-wins settings. A child analyst setting cannot turn on a disabled provider. An incoming message, research model, parser confidence, or UI request cannot expand financial authority.

### 0.1 Definition of completeness

Enumerate every value in the declared supported domains; every valid and invalid state/event transition; every boundary around every monetary/time threshold; all combinations in each bounded critical model; and all causally permitted schedules of the defined critical concurrent events. Add regression, mutation, property, replay, integration, browser, recovery, and exact-route tests. Unknown inputs must map to a specified non-entry/incident outcome.

The attached named scenarios and generated vectors are a starting acceptance inventory, not a mathematical proof about unlimited future messages or external market events. Completion requires a traceability inventory derived from the actual code, adapters, schemas, configuration fields, and UI routes. Record domain bounds, excluded configurations, proof assumptions, and all unexecuted tests. Pairwise tests can supplement this inventory but cannot replace exhaustive tests of the bounded money/ownership/order-state models. [S09, S10]

### 0.2 Deployment modes

`REPLAY` uses a virtual clock and non-network execution. `SHADOW` observes signals and decisions without simulated fills unless an explicitly labeled simulator is attached. `PAPER` submits to a named non-live implementation or qualified broker paper endpoint. `LIVE_CANARY` and `LIVE` require an existing exact-route authorization. A paper account with the same display name as a live account is not the same account. Paper success cannot grant live permissions. Historical imports can never become live orders through a UI filter or a mode-field edit.

No arbitrary daily trade-count target is introduced. Execute all eligible opportunities subject to real capital, risk, liquidity, source semantics, account rules, and capacity. Skipping an uneconomic or unsafe trade is an intended outcome, not a defect to fix by increasing risk.

## 1. Canonical entities and ownership

| Entity | Required identity and responsibility |
|---|---|
| Owner | Aggregate capital/risk authority across all the owner's real accounts; excludes other people's funds. |
| PhysicalAccount | Broker legal entity, immutable broker account ID, environment, base currency, product/margin type and restriction state. One real account is counted once even when exposed by several connections. |
| AccountBinding | A credential/integration path to a PhysicalAccount; versioned, revocable, no independent capital. |
| CapabilityProfile | Exact account + API/version + instrument family + session + operation + order recipe + evidence tier. Unknown is not supported. |
| Portfolio | A virtual budget with explicit backing allocations to physical accounts. Portfolio equity is not additional money. |
| StrategySleeve | A provider/analyst/strategy/asset/horizon/exit-policy risk and capital allocation within a portfolio. |
| SourceIdentity | Transport, authenticated tenant/channel, provider/product, analyst, source aliases and permissions. |
| SourceEnvelope | Original event ID, revision, event/received times, origin lineage, content hash, attachment references and collection mode. |
| Instruction | One semantic action, its exact instrument/legs, field provenance, parser version and causal dependencies. |
| OpportunityGroup | Independent, atomic-combo, either/or, sequential roll/reversal, or explicitly released basket semantics. |
| Lifecycle | One economic trade, including its seed, adds, partial exits, terminal exit and accounting adjustments. |
| PositionAllocation | Exact lifecycle ownership of quantity in a PhysicalAccount, separate from broker tax lots and other analysts' quantities. |
| Budget/Reservation | Resource vector: cash, buying power, initial/maintenance capacity, planned risk, stressed risk, notional, concentration, close quantity, and slots. |
| OrderIntent | Immutable logical operation, account/binding identity, client correlation ID, release/config hash and resource reservation. |
| BrokerOrder/Fill | Authoritative external IDs, order-family relationships, execution IDs and correction history. |
| ProtectionPlan | Initial stop, current desired and broker-confirmed protection, quantity coverage, trigger basis and deadlines. |
| PolicyRelease | Immutable approved parameter bundle, exact scope, evaluation evidence and rollback rules. |
| DecisionTrace | Every input snapshot, eligibility result, sizing cap, selected account, rejection reason and downstream effect. |

A lifecycle is never identified by ticker alone. An adequate key includes owner, environment, provider, analyst, strategy, source lifecycle identity, exact instrument/group and an immutable internal ID. The account becomes immutable before the first potentially accepted order. An exit inherits that account; it does not run the new-entry router.

### 1.1 Non-negotiable invariants

I01. One canonical entry opportunity has at most one selected physical account in the current single-destination mode.
I02. Multiple credentials or matching route rules do not duplicate capital or execution.
I03. A source replay/retry is idempotent; a genuine subsequent entry is not accidentally deduplicated.
I04. No broker effect precedes a durable intent and applicable reservation.
I05. A possible acceptance with missing acknowledgment remains UNKNOWN; neither resubmission nor cross-account failover is allowed without authoritative resolution.
I06. Signed broker quantity equals signed known managed allocations plus explicitly identified unmanaged quantity plus disclosed reconciliation differences. Differences are incidents, not silently invented allocations.
I07. Managed exits never consume another lifecycle's or manual holder's inventory; total potentially executable close capacity is controlled at the account/instrument level.
I08. All applicable account, owner, portfolio, sleeve, provider, underlying and correlated-risk constraints are checked together.
I09. Buying power, loan capacity and derivative collateral are never called equity or a guaranteed loss bound.
I10. Every owned fill has an explicit protection state; an unprotected fill starts a deadline and recovery incident, not a green status.
I11. A long protective floor never decreases; a short protective ceiling never increases, except an explicit instrument transformation such as a split with full economic equivalence and evidence.
I12. A pending cancel/replace is not final; reservations and fill responsibility remain until resolved.
I13. Entry halts do not disable valid scoped exits, protection, reconciliation or emergency management.
I14. Closed/rejected/expired lifecycles cannot reopen from late entry events; a new trade needs a distinct authorized lifecycle.
I15. Paper, replay, historical imports and development credentials cannot reach live effect paths.
I16. Add-on orders obey whole-lifecycle and portfolio caps and never reset original-risk accounting.
I17. Unknown restrictions, stale mandatory data, unresolved account identity and unsupported products block affected new exposure rather than becoming zero or permissive defaults.
I18. Policy/configuration changes are versioned and cannot silently modify open-trade commitments.
I19. Daily/weekly halt latches, deadlines, floors, ownership and unresolved intents survive restart, backup restoration and deployment.
I20. Broker corrections/busts/corporate actions are compensating ledger events; execution history is not destructively rewritten.
I21. No unavailable route is labeled supported merely because a class, method, logo or mocked response exists.
I22. Every skipped/rejected/held signal has an unsampled business decision record; telemetry sampling does not remove financial evidence.
I23. Fractional quantities, multipliers, currencies, tick sizes and price domains are product-specific and exact.
I24. A risk-reducing trade is evaluated by the resulting portfolio, not simply whether its order side says SELL.

## 2. End-to-end pipeline

| Step | Required decision and successful output | Failure or alternate branch |
|---|---|---|
| 01 | Authenticate transport; check payload size/type, replay window, sender/channel/product permission. | Reject transport failures before financial interpretation. Valid signature alone is not analyst authorization. |
| 02 | Persist immutable envelope and source sequence/cursor; acknowledge intake only after durable storage. | Storage unavailable: do not acknowledge accepted delivery or submit an order; rely on documented retry/recovery. |
| 03 | Resolve transport, original provider and analyst independently. | Unknown provider/analyst: quarantine or observation-only draft; never borrow another provider's identity. |
| 04 | Deduplicate original event/revision and known cross-post lineage. | Join prior result; changed material revision enters amendment logic, not another entry. |
| 05 | Classify semantic action and segment the full message. | Commentary, recap, negation, flow observation and unverifiable media remain non-entry. |
| 06 | Build independent/grouped instructions and dependency graph. | Ambiguous shared stops/quantities or incomplete atomic legs block the relevant group. |
| 07 | Resolve exact instruments and units against reference data and source semantics. | No stock-to-option, option-to-stock, FX-to-crypto or continuous-future-to-contract guess. |
| 08 | Match each update to its existing lifecycle, or validate a distinct new-entry lifecycle. | Ambiguous exit generates urgent incident; unknown entry stays blocked. |
| 09 | Freeze effective permissions, released financial rules, source constraints and policy hashes. | Disable is inherited; conflicting incompatible constraints produce rejection. |
| 10 | For reductions, dispatch to owning account's coordinated close/protection path. | No owned position: no sell; reconcile pending entry and record orphan/terminal event. |
| 11 | For new risk, check source age, session, event gates, executable quote and trigger. | Wait only in a durable bounded trigger state; expired opportunities terminate. |
| 12 | Enumerate eligible physical accounts and collapse duplicate bindings. | No eligible approved account: explicit unrouteable/shadow outcome, not implicit fallback. |
| 13 | Compute feasible quantity and incremental portfolio risk separately for every eligible candidate account. | Account with insufficient minimum feasible size is excluded with cap details. |
| 14 | Rank feasible opportunity/account pairs using released priorities, robust edge when available, risk and costs. | Unknown edge uses conservative policy priority, not made-up expected P&L. |
| 15 | Select exactly one account and atomically reserve all hierarchical resources. | Version/race conflict: recompute from fresh state; no stale-plan submit. |
| 16 | Persist account-bound execution and protection intent; send via its qualified recipe. | Known pre-send failure may release safely; possible send/acceptance becomes UNKNOWN. |
| 17 | Consume acknowledgment/fills, deduplicate executions and reconcile all order-family members. | Reject/no-fill releases only proven-unused resources; partial fill remains a live position. |
| 18 | Protect actual filled quantity and verify protection state/deadline. | Protection failure: cancel remaining entry safely, reconcile, attempt authorized scoped reduction; retain incident until resolved. |
| 19 | Manage lifecycle: provider updates, adds, partial exits, trailing, runner and deadlines. | All actions share ownership/risk/close-capacity control. |
| 20 | Reconcile final orders, positions, fees, cash, collateral, deadlines and settlements. | Zero position alone is not closure if an entry can still fill. |
| 21 | Finalize performance attribution, evidence and learning dataset. | Incomplete/corrected histories remain labeled and are excluded from unsupported inference. |

Persist a reason code at every step. At minimum: AUTH_REJECTED, UNKNOWN_PROVIDER, PARSE_AMBIGUOUS, NONACTIONABLE, DUPLICATE, INSTRUMENT_UNRESOLVED, WAIT_TRIGGER, EXPIRED, UNROUTABLE, POLICY_DISABLED, CAPITAL_LIMITED, RISK_LIMITED, MARGIN_UNKNOWN, LIQUIDITY_REJECTED, SUBMISSION_UNKNOWN, PARTIAL_UNPROTECTED, STOP_BREACHED, OWNERSHIP_CONFLICT, EXIT_ORPHAN, RECONCILIATION_REQUIRED, CLOSED_PENDING_ACCOUNTING, CLOSED.

## 3. Intake, interpretation and compound messages

### 3.1 Full-message interpretation

Parse action before side: BUY can mean opening long stock, buying a call, buying a put, covering a short, closing an option, or buying a hedge. Store `position_effect` separately from `order_side` and `economic_direction`. “Sell puts” can be opening short puts or closing long puts; ambiguity does not authorize either. A large option print/flow alert is an observation unless the source's released grammar explicitly identifies it as an actionable recommendation.

Retain all material fields, including negative/conditional language, price units, quantity semantics, date/time zone, targets, stops, Max Buy and references. Do not search only for a trade-shaped substring. Reject unconsumed material fragments. If trusted structured fields contradict message text, follow an explicit provider protocol; without one, quarantine the conflicting instruction.

Provider size is not the user's size. “Bought 500”, “10% position”, “half size”, “add 25%” and “risk $100” require declared semantics. A provider's absolute Max Buy and explicit price range are hard entry constraints, not suggestions to remove when the market moves.

### 3.2 Unknown/new provider path

Store and tag UNKNOWN_PROVIDER. Resolve channel ownership and original attribution from authorized configuration, not from a display name. Create an onboarding draft with permitted product types, source grammar, sample corpus, history coverage, action taxonomy and route proposal. Label sample ground truth, test on held-out complete messages/revisions, establish permissions, then release a versioned parser. Run shadow, followed by a scoped paper evaluation with independent monitoring. A later promotion must not replay historical entries. A live signal during onboarding remains non-live. A new analyst in an existing provider does not inherit another analyst's profitability record.

No LLM belongs in the live order path by default. An optional permitted research parser may propose a structured interpretation offline, but deterministic schema/provenance/permission gates remain mandatory and uncertain interpretations abstain. Model confidence is never a trading win probability. Provider text is never executable code, a system prompt, a request to change limits, or authority to reveal secrets.

### 3.3 Compound-message rules

Independent trades become child instructions with distinct IDs and their own risk/route results; preserve a common parent. “AAPL and MSFT; each with its own stop” can allow one valid child while the other is rejected. If a stop, quantity or condition cannot be assigned unambiguously, block the affected children. Multiple assets in one message are classified individually, not from a channel-wide default.

A spread is one exact combo with leg ratios, signed price convention and payoff model. It is not two unrelated signals. Require a qualified combo recipe; if the venue provides only individually submitted legs, do not label that atomic or enable it under the current conservative route. A basket needing several venues cannot be truly atomic; keep unsupported unless a separate staged-execution/contingency policy is released. An either/or opportunity has one exclusive opportunity token so simultaneous triggers cannot open both.

A roll is linked close-old/open-new with a released transition plan, temporary exposure bound, collateral treatment and repair policy; opening the new leg is not guaranteed by merely requesting the old close. A reversal is close/reconcile old position, then independently authorize the new opposite position. If the close can still fill or the old position is unresolved, do not submit the new side. For all-or-none same-account baskets, reserve group resources before effects; a partial external outcome still needs a contingency policy.

### 3.4 Revisions and order of arrival

Edits before submission replace the uncommitted interpretation and force revalidation. Edits after possible submission are amendments to the same lifecycle. Deleted messages are tombstones, not automatically exits. EXIT-before-ENTRY marks its referenced lifecycle terminal, cancels/reconciles its pending entry where authorized, and rejects a subsequently late OPEN. An old exit must not close a new same-symbol lifecycle. Reply context may supply missing fields only where the provider grammar defines that inheritance and the exact parent revision is available.

An “all out” or “close everything” instruction is scoped to its documented provider/analyst/strategy book; it is not authority to liquidate the owner's entire account. A trusted owner account-wide emergency command is a different authenticated action and explicit scope.

## 4. Instrument and account capability decisions

Reference metadata includes economic asset family, broker instrument ID, trading/settlement currency, multiplier, tick and lot step, minimum/maximum quantities, sessions, expiration, exercise/settlement style, deliverable, marginability and current trading restrictions. A display ticker is never sufficient for an option or expiring derivative.

| Product | Mandatory sizing/lifecycle semantics | Default on missing capability or release |
|---|---|---|
| Long equity/ETF | Shares, actual tick/lot rules, cash/margin eligibility, corporate actions, executable quote, supported protection. | Block affected new entry. |
| Short equity | Locate/borrow confirmation, recall risk, borrow costs, short-sale restrictions, buy-to-cover protection and account permission. | Disabled unless separately released; never infer from SELL. |
| Leveraged/inverse ETF | Instrument leverage/direction and concentration stress, not just ticker-sector mapping. | Explicit profile required. |
| Long option | Underlying, expiry, strike, call/put, multiplier/deliverable, premium quote, full debit at risk, exercise and expiration capacity. | Missing contract component: reject, not choose nearest. |
| Defined-risk option combo | All legs/ratios, same/different expiry semantics, net debit/credit sign, payoff graph, assignment and broken-leg exposure, qualified combo order. | No naked leg-in fallback. |
| Covered call/cash-secured put | Explicitly allocated cover/cash, ownership lock, assignment workflow and opportunity cost. | Manual shares/cash cannot silently provide collateral. |
| Naked short option | Unbounded/nonlinear stress and specialized permission/capital. | Disabled under inherited baseline. |
| Futures | Exact contract and exchange, tick value, multiplier, initial/maintenance/intraday/overnight margin, variation margin, delivery/notice dates, price-limit behavior. | Continuous symbols observation-only; no guessed front-month execution. |
| FX | Base/quote, lot-to-unit conversion, fresh account-currency conversion, spread/swap, rollover/session and jurisdiction eligibility. | No share formula or inferred leverage. |
| Spot crypto | Venue/pair, base/quote units, available inventory, minimum notional, fee currency, 24/7 calendar plus maintenance, custody/venue risk. | No automatic borrowing, perpetual substitution or unqualified stop emulation. |
| Crypto derivatives | Linear/inverse payout, contract size, mark/index/last trigger basis, liquidation model, isolated/cross collateral, funding and jurisdiction. | Dedicated gated extension; not inferred from spot capability. |
| Future options/other assets | Exact nonlinear payoff, exercise/delivery/underlying transition and full risk model. | Observation/shadow until supported end to end. |

Futures margin is collateral rather than a loss bound; the required amount can change with market conditions. [S07] Short options retain assignment exposure, and exercise/assignment can create underlying obligations. [S06] These facts require lifecycle controls, not merely a new `asset_class` enum.

Broker selection must depend on exact account and recipe evidence, not a brand ranking. Robinhood's documented public API examined for this contract is explicitly a Crypto Trading API; that evidence does not certify an equity/options execution path. [S04] Any Robinhood equity route therefore requires its own entitled, qualified order/readback/protection path. No unrelated broker is automatically activated to work around a missing path.

## 5. Account routing, portfolio/sleeve allocation and competition

### 5.1 Account eligibility

Filter in this order: owner/environment; exact approved physical identity; entry-enabled state; source/strategy/asset binding; instrument and session; product permissions and restrictions; full order/protection/exit recipe; fresh account/quote/FX evidence; no unresolved exposure/reconciliation incident in the relevant scope; allowable settlement/margin regime; sufficient funds and all hierarchical risk limits; valid minimum executable quantity.

A cash account, margin account, retirement account, and subaccount behind a shared gateway are not interchangeable. Setting “default account” in an SDK cannot substitute for transmitting/verifying the intended broker account ID. Two bindings to the same account yield one candidate; choose the approved binding without duplicate account equity.

### 5.2 Compute feasibility before ranking

For each candidate calculate the actual feasible size, net costs, account-currency risk, stress headroom, fill/protection quality, and funding cost. Rank only feasible alternatives. A superficially preferred broker that can buy one unit may be worse than an equally approved account that can safely execute the intended economically meaningful size, but account selection never exceeds any source or owner preference that was released as a hard requirement.

Default deterministic ranking when robust edge is unavailable: explicit released strategy/account preference; full recipe qualification; lower incremental concentration and stress; lower conservative execution/funding cost; stronger fresh operational evidence; stable account ID. Persist every candidate's inclusion/exclusion reason. Do not invent latency numbers or expected return to select a winner.

After one selection, alternative accounts are not broadcast destinations. Separate multi-account replication or split execution would require an explicitly different policy, child intents, one owner-wide aggregate budget and evidence; leave those modes disabled here. Exits remain on the original account even when preferences change.

### 5.3 Capital hierarchy

Let verified automation equity be E_owner = sum of nonduplicated backed automation equity across physical accounts. The backing assigned to portfolios cannot exceed each account's dedicated equity. A sleeve capital allocation is a subdivision, not additional cash. Every resource check applies simultaneously at owner, account, portfolio, sleeve, provider, analyst/strategy as configured, underlying and correlated cluster levels. A hard sleeve reservation cannot be borrowed by another sleeve; an explicitly soft unused reservation can be borrowed only under released policy and expiry, with no forced sale later to restore it.

Portfolio allocation is a joint opportunity problem: select feasible opportunity/account/size combinations that fit concurrent budgets. One useful offline candidate objective is conservative incremental expected log growth after costs, subject to scenario loss, capital, concentration and liquidity constraints. In production start with an auditable deterministic allocator and a bounded optimizer fallback, not an unbounded optimizer as a safety dependency. Account-specific constraints matter: cash at Broker A is not immediate buying power at Broker B.

Do not sum returns from separately funded strategy backtests and call them a feasible combined portfolio. Replay all candidates chronologically through one shared-capital allocator. Record missed opportunities and idle capacity reasons. No requirement to invest a fixed percentage of cash, meet a trade quota, buy unrelated assets, transfer funds, or increase leverage to “use capital.”

### 5.4 Simultaneous and sequential arrivals

Within an explicitly small, released intake arbitration window, collect concurrent candidates without violating their deadlines. Batch ranking prevents raw callback order from deciding allocation accidentally. Different horizons can use different bounded windows; no artificial delay is required for an already eligible urgent exit. Process urgent exits/protection first, then resource-releasing confirmed transitions, then new exposure.

If two signals each need $10 risk and only $15 remains, either select one at full eligible size or allocate feasible smaller whole-unit sizes under the released policy. Do not give each $10. One transaction observes/rechecks all applicable budgets, claims the opportunity, and reserves chosen resources. A later better signal cannot automatically force an earlier position closed; rotation is a separately evaluated policy including close costs and path risk.

Signals referencing the same underlying through shares, calls, puts, futures and ETFs are not independent simply because their asset labels differ. Delta/gross/notional screens and coherent multi-factor stress scenarios must supplement correlation estimates. Provider agreement on the same ticker can be correlated information, not several independent votes multiplying Kelly fractions.

## 6. Capital definitions, risk measurement and reservations

Maintain separate fields for verified net liquidation, dedicated automation equity, risk-sizing equity, settled/unsettled cash, withdrawable funds, broker-reported buying power, margin debit, collateral/initial margin, maintenance requirement, notional, pending commitments, planned stop loss, stress loss, fees/funding and unallocated/manual exposures. Do not subtract debt twice if broker net liquidation already accounts for it.

Risk-sizing equity should follow the current released rule. The older reference uses min(current verified automation equity, cashflow-adjusted session-start automation equity), preventing automatic same-session risk escalation from mark-to-market gains. A deposit/withdrawal is not P&L and does not reset a loss halt. Stale/error snapshots are UNKNOWN, never an empty portfolio.

### 6.1 Three distinct risk measures

**Original entry/lifecycle planned loss** measures seed/add losses from their cost basis to the planned protective execution scenario. **Current mark-to-protection loss** measures how much present equity may be given back before the protective execution scenario. **Stress loss** uses adverse gap, slippage, volatility, spread, currency, collateral and liquidation scenarios. A profitable stop can reduce original capital-at-risk without making current drawdown or gap risk zero. Never use a negative stop-loss number from one profitable trade to automatically subsidize unrelated risk.

For a long lot i with entry e_i, quantity q_i, multiplier m_i and confirmed stop s_i, a conservative original-risk contribution is q_i*m_i*max(0,e_i-s_i) plus allocated adverse execution/cost allowance. For short linear lots use max(0,s_i-e_i). Sum nonnegative lot/lifecycle contributions for conservative gross planned risk; measure scenario portfolio netting separately only when the hedge model is qualified. Mark-to-stop loss uses the current executable reference instead of entry. For options/nonlinear products use full repricing/payoff scenarios, not this linear formula.

A desired tighter stop does not release risk until its effective protection is confirmed. Even then, released headroom is not permission to pyramid unless a separate add policy authorizes it. For trailing floors, track both “promised by policy” and “confirmed working at venue,” with discrepancy deadlines.

### 6.2 Resource reservation vector

Reserve the worst admitted outstanding combination of: cash/debit, initial/maintenance capacity, gross/net/underlying exposure, planned and stress risk, slots, and closeable inventory. An OCO pair can share close capacity only under its certified mutual-exclusion/overfill semantics; two unlinked full-size exits cannot both reserve the same shares. Pending child legs and conditional orders may consume risk/capital even before a fill.

Broker buying-power fields often already include some working-order commitments. Normalize a snapshot with explicit `reflected_intent_ids` or a conservative reconciliation method; subtract only local commitments not already represented. Never blindly subtract every local reservation twice, and never omit a reservation merely because a request is in flight. A stale snapshot plus unknown membership requires blocked admission or conservative over-reservation with an explicit reason.

Reservation transitions: DRAFT -> HELD -> COMMITTED_TO_PENDING_ORDER -> PART_FILLED/HELD_REMAINDER -> FILLED_EXPOSURE -> RELEASE_PENDING -> RELEASED. An UNKNOWN submission stays committed/held. Unsent, expired reservations may release after proving no effect was dispatched. Reservations after possible dispatch cannot expire on a timer. Fills transfer commitments to position exposure; they do not make the money free. Cash only becomes usable according to current broker/account settlement and funding rules.

### 6.3 SQLite transaction and effect boundary

Use short durable transactions, foreign keys and uniqueness constraints, exact Decimal/integer-scaled monetary storage, version/CAS checks and an outbox. SQLite WAL still has a single writer and is not a multi-host shared-filesystem coordination solution. [S08] Keep the current single-host architecture unless separately changed.

The transaction claims the canonical opportunity, validates account/budget versions, writes selection, reserves resources, creates the immutable order intent and outbox item, then commits. A coordinated account writer marks the item dispatching in another short transaction, performs the network request outside the DB transaction, then persists the response. A crash at any boundary enters idempotent recovery. The account writer and outbox dispatcher must be fenced against duplicate process ownership. A worker that lost its lease cannot continue broker writes. Short global ledger transactions enforce owner-wide constraints even when separate account workers are active.

Exactly-once external effects cannot be promised solely by a database transaction. Use documented broker idempotency/correlation and readback. If the venue cannot establish whether an order exists after an ambiguous send, keep the intent unresolved and block conflicting new exposure rather than creating a second order.

## 7. Modified Kelly: learn the edge, then constrain the risk

Kelly is a sizing framework under a modeled return distribution, not a way to infer that a provider has an edge. Risk-constrained variants explicitly trade growth against drawdown under assumptions; those mathematical guarantees do not automatically hold under bad estimates, nonstationary returns or unmodeled gaps. [S01]

### 7.1 Unit convention

If X is the net lifecycle payoff in multiples of one dollar of original planned risk, and f is the fraction of equity committed to that planned risk, the growth calculation is E[log(1+fX)]. If instead X is return on invested notional, f is an investment/exposure fraction. Record the convention in the model schema and tests. Never put a risk-fraction Kelly result into a notional allocator, or vice versa.

In the special binary model with probability p of +bR and probability (1-p) of -1R, f_K = p - (1-p)/b. This formula assumes those two outcomes. Replacing b with average win/average loss does not make it exact for a general trading distribution. With stops, partial exits, gaps, runners and pyramiding, use the full net lifecycle payoff distribution. Gaps can yield X < -1; require 1+fX > 0 across the modeled support/stress constraints. Wins, losses and breakevens all contribute; do not discard zeros or fees.

### 7.2 Profile hierarchy and evidence

Profile key: provider × analyst × strategy × asset family × horizon × execution/exit policy. Add option DTE/moneyness, liquidity or regime only when data supports the finer conditioning. Version profiles whenever execution, entry filtering, stops or add/runner rules materially change. A stock-scalp edge does not automatically transfer to that analyst's swing options.

Estimate from actual complete, correctly attributed lifecycles; use qualified as-of replay and shadow observations as separately labeled evidence, not interchangeable real fills. Include stopped-out and deleted alerts, unfilled entries, source gaps, every material revision, slippage, spread, commissions, borrow, financing and funding. Preserve all received opportunities so selection bias and missed opportunities can be examined. Do not treat chosen fills as an unbiased sample of all provider opportunities.

Shrink thin child profiles toward a conservative compatible parent, rather than fitting hundreds of unstable cells. Use effective independent sessions/trade blocks, not just raw trade count. Estimate dependence with chronological/session blocks; overlap across providers and the same underlying must not create false sample size. Track data coverage, missing outcomes, uncertainty and model expiration. Source-reported win rate alone is insufficient.

### 7.3 Robust candidate procedure

1. Freeze the feature/price/source-availability cutoff, profile definition, cost model and admissible f range.
2. Construct the net outcome distribution and conservative stress tail; block nonfinite or incoherent data.
3. For each candidate f, calculate log growth across outcomes and dependence-preserving bootstrap/model-uncertainty draws.
4. Select a conservative optimum using a predeclared lower confidence criterion or explicit ambiguity-set objective. This is an engineering choice needing validation, not a universal theorem.
5. Apply a separately configured fractional-Kelly multiplier lambda, bounded between 0 and 1. Candidate research values such as 0.10–0.25 are examples, not changes to current live settings.
6. Intersect with the released lifecycle hard ceiling and remaining owner/account/portfolio/sleeve/provider/underlying/stress/capital constraints.
7. Apply only bounded downward drawdown/liquidity/uncertainty/regime modifiers; never stack multipliers that silently exceed the hard maximum.
8. Convert the resulting cash-risk budget to product-correct executable quantity; floor to the permitted step and recheck every cap using exact arithmetic.
9. Save p/outcome distribution, effective sample, shrinkage parent, costs, candidate f, lambda, all reductions and binding constraints.
10. Publish a candidate policy only after chronological held-out and forward-shadow comparison, with explicit owner release before live use.

No-history, statistically inconclusive and negative-edge are different states. An already released fixed-risk baseline can continue under its existing authorization when Kelly is unavailable; a new profile remains shadow/paper until released. A convincingly negative after-cost edge produces no new exposure for that Kelly policy. Do not turn “missing data” into either invented positive edge or a permanent implementation blocker for the software's no-data branch.

### 7.4 Hierarchical risk budget

A typical scalar risk budget is:

`B_trade = min(E_risk * released_trade_fraction, E_risk * lambda * max(0, f_robust), remaining_owner_risk, remaining_account_risk, remaining_portfolio_risk, remaining_sleeve_risk, remaining_provider_risk, remaining_underlying_risk)`.

Apply the Kelly term only when that mode is released and statistically eligible. Fixed-risk mode omits that term rather than assigning it a fake number. Notional, margin, stress, liquidity and position-count constraints are separate constraints on quantity; do not add quantities with different units into this scalar minimum.

For simultaneous correlated positions, independently adding each profile's Kelly fraction overstates diversification. Prefer a common-horizon joint scenario portfolio objective `max E[log(1 + Σ w_i r_i)]` under capital/risk constraints, or conservative fixed sleeve budgets and correlation/stress caps until a joint model is qualified. Different holding horizons need time-aligned cashflow/scenario replay; raw scalp R-multiples and month-long option R-multiples are not automatically commensurate portfolio-period returns.

### 7.5 Worked synthetic example

Assume a binary research model p=0.55 and b=1.4. Full Kelly is 0.228571, or 22.8571% of equity in the binary risk convention. Quarter Kelly would be 5.7143%. That is NOT permission to risk those amounts: an illustrative released 0.25% lifecycle ceiling still wins.

For hypothetical E=$6,000, B=$15. Entry limit $50, stop $49, adverse per-share cost allowance $0.05: quantity from stop risk is floor(15/1.05)=14 shares. Notional=$700 and planned risk=$14.70. If a different account/capital constraint permits only 10 shares, use 10. If no full permitted unit fits, skip; never round up to one. These are synthetic test inputs, not current account facts or a trading recommendation.

### 7.6 Cold start, losses, winning streaks and policy drift

A winning streak does not multiply risk without a scheduled, versioned update and all ceilings. A losing streak may activate a pre-released drawdown reduction or halt; it does not trigger martingale sizing. Old history loses relevance only under a declared decay/window rule; do not delete losses to improve estimates. A provider's new strategy or exit style receives an explicitly revised profile with uncertainty. Re-estimation is offline/as-of and cannot change parameters during an open lifecycle.

## 8. Product-correct quantity calculation

For each candidate size q, compute actual order economics and post-trade portfolio resources. The safe answer is the largest permitted stepped quantity satisfying every constraint, not always the minimum of independent ratios: tiered fees, initial margin, nonlinear risk and combo ratios may require bounded discrete search. Use the simple ratio as an initial bound only where its assumptions hold.

### 8.1 Linear long equity

For permitted entry e, stop s<e and nonnegative adverse cost per share c: stop risk/unit = e-s+c. Calculate risk-bound quantity, cash/borrowing bound, per-symbol notional bound, portfolio gross bound, broker quantity bound, participation/liquidity bound and any explicit source-quantity ceiling. Floor using exact lot size, then recompute all costs/risk/margin at that quantity. A stop equal to entry is not zero risk: adverse cost, gap/stress, concentration and margin caps still bind. A very tight stop cannot produce unlimited size.

A valid source stop is not widened merely to obtain a desired size. A missing stop may use only a released compatible fallback. Invalid, contradictory or already-crossed stops are distinct from absent stops and cannot be silently replaced to make a trade pass. Stop rounding must preserve the policy's risk bound; if the venue cannot accept the economically required level, reduce size/reject or use a separately qualified recipe rather than violate the floor.

### 8.2 Long options

Use full premium at risk plus costs as a binding original-capital-loss measure under the inherited conservative policy: q_contracts ≤ floor(B / (premium * multiplier + costs_per_contract)). Also check exercise/delivery obligations and funded expiration handling. A tight premium stop does not erase full debit risk. An option price of $2.00 with multiplier 100 means $200 debit per contract before fees, not a $2 stock-like purchase. In the synthetic $15 risk-budget example, zero such contracts fit; skip instead of raising the budget.

A long put is bearish underlying exposure but a BUY_TO_OPEN order. Stops can refer to option premium or underlying price; store both domains explicitly. Nonstandard adjusted deliverables and multipliers require exact metadata. Do not universally hardcode 100 or choose a nearby expiry/strike.

### 8.3 Option combinations

For a standard intact same-expiry vertical, use its validated payoff/max-loss calculation plus costs as one bound, but also stress early assignment, exercise timing, adverse closing liquidity, temporary stock obligations and broken legs. Calendars, diagonals, ratio spreads and butterflies need their actual payoff/volatility/time models; “spread width minus credit” is not universal. Reserve all legs' worst permitted temporary capital state. Net credit is proceeds with liability, not free risk budget.

### 8.4 Futures/FX/crypto

For a linear futures contract, stop loss/contract is |entry-stop|*point_value + costs, while margin and stressed variation margin are independent bounds. For FX, calculate base quantity, quote-currency P&L and fresh account-currency conversion, including swap. For spot crypto, account for base-denominated fees reducing acquired inventory and quote-denominated fees reducing cash. Fractional steps and dust must be conserved. Inverse derivatives require their specific inverse payout formula and collateral valuation scenarios; do not reuse linear quantity math.

## 9. When margin is permitted, unnecessary or prohibited

Margin is a capacity mechanism, not the risk-budget denominator and not an instruction to borrow. Holding an approved margin account does not require using a loan. If the computed trade fits allowed cash, use no incremental borrowing. If it requires additional buying power, it can proceed only through a released margin policy whose account/asset/session/horizon and whole-account stressed headroom all pass.

| Condition | Required behavior |
|---|---|
| Cash account or borrowing disabled | Size to eligible funded cash and settlement restrictions; do not open a loan. |
| Margin approved, cash sufficient | Same risk size, no borrowing merely to exhaust buying power. |
| Margin approved, incremental debit needed | Include interest over conservative holding period, account/asset margin eligibility, post-trade and stressed maintenance headroom, and leverage ceiling. |
| Missing/stale maintenance or restriction data | No additional margin exposure; preserve management of existing positions. |
| Broker offers more leverage than owner policy | Owner ceiling binds. |
| Owner permits more than broker currently allows | Broker restriction binds. |
| Overnight/weekend hold | Recompute overnight collateral, gaps, financing and session protection; intraday capacity cannot be assumed to persist. |
| Concentrated/nonmarginable security | Apply current instrument/house requirement; no generic 50% assumption. |
| Manual holdings exist | Reserve their capital and maintenance effects; no unauthorized liquidation or free-collateral assumption. |
| Several virtual portfolios share account | All compete for the same real margin buffer; sleeves do not isolate forced-liquidation risk. |
| Current loss/operational halt or maintenance stress fails | Cancel safely cancellable unfilled entries, stop new exposure, preserve protection and approved reductions. |
| Derivative margin | Apply exact contract collateral/liquidation model; margin deposit is not max loss. |
| Expected edge does not survive funding/spread/slippage | Skip that opportunity/size. |

Define current headroom as eligible account equity minus maintenance requirement, with broker definitions. For each stress scenario ω compute stressed eligible equity minus stressed maintenance and funding/operational buffer; require it to remain above the released buffer. Simultaneously enforce owner borrowing and gross exposure ceilings. Same-day notional, margin debit, overnight notional and collateral are different fields.

As of this contract's date, FINRA's replacement intraday-margin standards are effective June 4, 2026, with broker phase-in through October 20, 2027. Store `LEGACY_PDT`, `NEW_INTRADAY`, or `UNKNOWN` per actual account with dated broker evidence; do not universally assume the old $25,000 rule or universally assume its removal for every account. Unknown regime blocks affected new entries, not protective exits. [S02] Use an account-aware settlement calendar; T+1 applies to most covered U.S. securities transactions, not every asset or every balance field. [S03]

The older workbook's example standard equity gross-exposure ceiling is 1.25x, not a direction to borrow 25% on every trade. Preserve current approved values and reconcile their provenance before applying any historical example.

## 10. Entry timing, price, triggers and pending orders

### 10.1 Entry policy resolver

An entry plan includes explicit limit/range/trigger semantics, maximum chase, quote/spread/depth requirements, source/reference timestamp, earliest/latest entry, unfilled lifetime, partial-fill handling, stop basis, targets/runner policy, time exit and full protection recipe. A limit price is a constraint, a signal reference is evidence, and a breakout trigger is a condition; these are not interchangeable.

For long entries use executable ask/available depth for estimated entry and bid/exit conditions where the policy specifies; short entries use the corresponding sides. Distinguish consolidated, direct venue, last, mark, index and midpoint data. Entry at a bar's closing value is not assumed executable at that value. Reject crossed/corrupt/implausible quotes or apply the feed's explicit qualified exception; do not call missing quotes zero.

A marketable limit bounds acceptable entry price but does not guarantee a fill. Passive versus aggressive execution is a released cost/fill-probability trade-off. A provider's tighter entry maximum always binds. If the stop becomes invalid or the economic target is already passed while waiting, cancel/expire the remaining entry intent rather than chase. An entry timed out after 30 seconds is not equivalent to cancel confirmed at 30 seconds.

### 10.2 Trigger state

Store trigger predicate, observation domain, crossing/confirmation/hysteresis semantics, validity window, source version and current trigger token. Untriggered candidates ordinarily hold soft/expiring capacity, not indefinite hard capital; immediately before order submission take a fresh hard reservation. Group-exclusive triggers use one atomic claim. Trigger fires once per lifecycle. A data disconnect cannot infer that a historical threshold crossing happened and place a stale catch-up trade.

### 10.3 Revalidation and repairs

Immediately before dispatch recheck account/config versions, quote age, source validity, trigger, current caps and protection recipe. After each actual fill update average basis and residual resources. A worse-than-planned fill or cost that breaches a cap is an incident: cancel remainder, recalculate allowed exposure and follow the preauthorized reduction policy. Do not enlarge the risk cap retroactively. Better fills do not authorize increasing the entry remainder unless an explicit still-valid plan permits the same original quantity and resources.

For partial fills, either protect each confirmed increment via a verified recipe or prohibit the entry type that cannot be protected acceptably. Alpaca's published bracket behavior illustrates why generic “bracket supported” is insufficient: contingent exits activate when the parent is fully filled, and rapid market conditions may cause both exits to execute before cancellation. The exact partial-fill and overfill behavior must therefore be qualified. [S05]

## 11. Order state, ambiguity and recovery

Track transport acceptance separately from broker business acceptance and fills. Recommended order states: CREATED, RESERVED, DISPATCHING, ACKNOWLEDGED, WORKING, PART_FILLED, CANCEL_REQUESTED, REPLACE_REQUESTED, SUBMISSION_UNKNOWN, REJECTED, CANCELED, EXPIRED, FILLED, DONE_FOR_SESSION, CORRECTED. An order family can contain multiple old/new external IDs; an order marked replaced is not permission to forget older fills.

A 2xx response containing rejection is rejected, not pending. A cancellation receipt is cancel-requested unless documentation proves finality. A lookup returning not-found may mean eventual consistency or limited retention; it is not proof no order exists. A timeout before known network dispatch may safely return to unsent under a qualified boundary; a timeout after possible dispatch remains UNKNOWN. Reconcile using documented client references, broker IDs, execution IDs, open orders and paginated history. Symbol/time similarity alone cannot establish identity.

Deduplicate execution events by authoritative execution/correction IDs. A duplicate event does not change position or P&L twice. A fill arriving before order acknowledgment still belongs to the persisted intent through qualified correlation; otherwise quarantine and reconcile without losing inventory. A fill arriving after cancel requested is valid economic exposure. A late correction/bust revises quantity/P&L by compensating events and re-runs protection/ownership checks.

Retry safe reads with bounded backoff and jitter. Order creates/cancels/replaces are not wrapped in a generic retry decorator. If documented idempotent submission is used, preserve the same immutable client key and payload; changing either can create a new order. Do not rehome an UNKNOWN entry to another broker. Protect exit/reconciliation API capacity from new-entry bursts.

### 11.1 Recoverable lifecycle state

Maintain distinct economic and control states: NOT_OPEN, ENTRY_PENDING, OPEN_PARTIAL, OPEN_PROTECTED, OPEN_UNPROTECTED, EXIT_PENDING, CLOSE_RECONCILING, CLOSED_PENDING_ACCOUNTING, CLOSED, INCIDENT. A filled quantity can coexist with pending entry remainder and pending exit; model them explicitly rather than forcing one simplistic status. Closure requires no owned remaining exposure and no unresolved order capable of opening/reopening that lifecycle. Accounting finality can trail economic closure.

## 12. Stops, targets, trailing and provider exits

### 12.1 Initial stop and fallback hierarchy

Resolve: mandatory owner/venue protection constraints; compatible released source-specific stop; compatible released fallback only when the source stop is genuinely absent; otherwise reject entry. Preserve both stop price and stop trigger domain. A malformed or crossed stop is not missing. A protective policy can override provider preference only within the released precedence contract, never by arbitrary parser behavior.

The older Kamden reference initializes a long floor as max(valid source stop, 0.95 × actual lifecycle average fill), activates a 0.35% trail after a +0.50% gain, and applies it to that lifecycle's remainder. That means a maximum 5% price-distance fallback in that reference, not a universal minimum 5% stop and not a 5% account-loss allowance. It is not automatically current live configuration and must not be inherited by unrelated providers/assets. Its schedule reference uses verified current strategy timing, otherwise a 10:05 America/New_York fallback; 11:00 is an emergency backstop, not permission to wait after a failed normal exit.

### 12.2 Protection state and execution

Record desired stop, last submitted stop, last confirmed working stop, executable quantity covered, trigger basis, native/synthetic mode, pending replace family and deadline. Protect using actual filled quantity, not requested quantity. A database row saying stop=49 does not establish a live stop at 49. A GTC order's continued existence, corporate-action adjustments, session behavior and possible expiration must be monitored.

If stop creation fails, preserve any valid existing stop, cancel remaining entries safely, reconcile and attempt the released scoped close. If a halt/closed market/unknown order prevents action, show unresolved exposure, retain available native orders and alert. Never fabricate a successful flatten. Tightening a stop requires no capital-growth permission; loosening a locked long floor or short ceiling is prohibited absent a separately authorized policy migration with risk handling.

### 12.3 Trailing algorithm

For long positions persist a qualified executable high-water H and committed policy floor F. Under an approved percentage trail d after activation, desired floor is max(initial floor, previous desired floor, H*(1-d)). Under an ATR candidate, desired floor is max(initial floor, previous desired floor, H-k*ATR). For short positions use the mirrored low-water and nonincreasing protective ceiling. Time/ATR/percentage regimes can change only within a frozen pre-released rule; a larger ATR does not lower an already locked floor.

A floor at or above the relevant valid current price under its trigger semantics indicates a breach, not permission to clamp the stop downward. Native trigger semantics may differ from a custom bid-based rule. A historical/late quote cannot move high-water in violation of event-time policy. Bad-tick filtering is predeclared and auditable; it cannot discard real adverse movements because they are inconvenient. Missing ATR uses the released fallback, not model-generated volatility.

A rejected replacement leaves the old confirmed stop in place if the broker says it remains working. Pending replacement cannot be shown as stronger confirmed protection. Cancel-then-create has a protection gap; simultaneous full-size unlinked stops can oversell. Select and qualify one recipe that manages these competing hazards; otherwise block the affected feature.

### 12.4 Partial exits and target allocation

A partial-exit percentage must state its denominator: original authorized size, total cumulative fills, quantity remaining at signal event time, or provider-specific semantics. Freeze that interpretation before submitting. A duplicate “sell half” must not repeatedly halve the position. Multiple targets allocate integer quantities deterministically and total no more than owned remainder. A one-contract position cannot execute a half-contract trim; record no feasible partial or follow an explicit all/hold policy, never silently round up.

Use close-quantity reservation and reconcile competing stop/target/provider/timer/manual-owner commands. For a partial close, maintain protection on remaining quantity during cancellation/replacement transitions. No policy may remove the existing full stop, lose connectivity and assume the trim happened. Under a combo position, closing a protective long leg first may increase risk: close the combo or use the qualified leg-risk procedure.

### 12.5 Exit precedence

Priority: actual broker/exchange liquidation or assignment facts; risk incident and protection breach; explicit authenticated owner scoped emergency command; valid lifecycle provider exit under its released policy; mandatory expiry/session/holding deadline; partial target; ordinary trailing updates; add-on/new entries. The precise first two execution actions depend on order-family state and must not create competing full-size exits. Priority does not mean submit two orders and hope one cancels.

An exit is not rejected because its source timestamp exceeds the entry-age threshold. Resolve its lifecycle and current actionable scope. If there was no entry fill, do not send a sell; cancel/reconcile pending entry and retain a terminal marker. An ambiguous exit does not authorize liquidation of every same-ticker position. Pause-new-entries and provider disable preserve protective/scoped exit management.

## 13. Scaling into winners and letting winners run

These are separate strategy changes with potentially different return distributions. Implement both, but never presume they improve net profitability or silently replace an active source policy.

### 13.1 Four separate scaling decisions

(1) Account-level compounding changes future trade budgets when allowed risk equity changes. (2) Profile-level scaling changes a future trade's fraction after evidence/release. (3) Entry staging splits a previously authorized original size into scheduled/price-dependent tranches. (4) Pyramiding adds incremental exposure to an already profitable lifecycle. None is martingale averaging down; each needs its own state, cap and tests.

### 13.2 Pyramiding admission

Require an exact released add policy and a qualified protected existing lifecycle. Validate current executable profit relative to the declared seed/last-add reference after costs; source or rule trigger; favorable trend/continuation conditions only if pre-released; no active exit/breach; no unresolved order/replace; adequate remaining time; allowed add count/cooldown; no loss halt; fresh margin/quote state and liquidity. Preserve the original seed R and policy version.

Calculate post-add weighted basis and risk per lot, gross/mark-to-stop/stress losses, total underlying/concentration, provider/sleeve/owner budgets, margin and fees. New quantity is the minimum feasible amount under all of them. Require a recipe that protects incremental fills while preserving old protection. Each add is a child OrderIntent of the same lifecycle, not a fresh lifecycle that resets limits. An existing position above entry but a new add below its prior add level needs explicitly defined semantics; do not relabel an averaging-down action as pyramiding.

No add if the same tick satisfies an exit or stop breach. No add solely because the trail tightened. No borrowed “house money” exemption: unrealized gains can disappear on a gap. No netting profitable old lots' locked gains against an unlimited new loss. An unused planned add reserve expires explicitly; it does not permanently block other strategies or guarantee future fill.

Example candidate for testing only: divide a fixed total lifecycle planned-risk budget into seed/add/add maxima of 50%/30%/20%, activated at +1R/+2R with at most two adds and unchanged total risk ceiling. This describes portions of planned risk, not shares or cash. Compare against 100% seed and no-add baselines; it is not an optimized recommendation and is not automatically live.

### 13.3 Synthetic add calculation

Original lot: 10 shares at $50, confirmed stop $50.50, current bid $52. Candidate add: 5 shares at $52, stop $50.50. Ignoring costs only to expose the arithmetic, original-capital risk for the add is $7.50; old profitable lots contribute zero, not negative risk. But current equity giveback to the stop across all 15 shares is $22.50, and a gap to $48 loses $60 from current $52 marks. The add can pass one measure and fail another. Tests must evaluate all three and actual fees/margin.

### 13.4 Runner lifecycle

Define runner creation from the outset: no fixed profit target, a released target-plus-remainder recipe, or a released dynamic continuation rule. A mean-reversion trade should not automatically inherit a trend-following runner. A runner has owned quantity, active protection, original R, high-water, maximum giveback rule, holding deadline and event/expiry constraints. “Let it run” does not mean remove the stop or ignore a mandatory deadline.

Possible candidate comparisons: provider exit only; inherited baseline trail/time exit; partial target plus trailing remainder; volatility-adjusted trail; structure-break exit with hard backstop; time-decaying giveback. Add exact activation, lookback, data source, favorable/adverse direction, cooldown and missing-data behavior. Research them as bounded candidate families rather than fitting dozens of arbitrary thresholds to a few trades.

A provider EXIT closes the matching remainder under the baseline. Holding a runner past that instruction is a distinct owner-released derived strategy, with separate attribution and profile history, never faithful signal copying. Overnight holding requires its own capital/protection/cost/event permission. For options near expiration, theta, gamma, spread, exercise and liquidity gates can forbid a runner even when underlying price is trending.

### 13.5 Profitability validation

Measure actual or properly simulated net expectancy, median/log return, drawdown/tail loss, turnover, financing, capital-hours, fill/miss rates, adverse/favorable excursion and capture ratio. Compare the same observable signal universe, entry availability, capital and costs. Preserve all attempted research trials, use chronological out-of-sample and dependence-aware uncertainty, and perform forward shadow. Pyramiding/runner variants are promoted only when the predeclared evidence supports them within the unchanged risk appetite; high in-sample win rate alone is not evidence.

## 14. Overlapping providers, manual activity and netted accounts

Same-direction overlap is permitted only with separate per-lifecycle allocations and a qualified shared-account order manager. For example, A owns 100 shares and B owns 50; B's full exit means at most its owned 50, not broker close-position(150). The account's native net position is still 150. A newer entry cannot overwrite A's stop, basis, owner or deadline. A provider's signal should not sell excluded manual inventory because it shares a ticker.

Opposite-direction new exposure in a netted account is rejected unless a separately released conflict/netting policy can preserve economic and attribution semantics. A sell that nets out another analyst's long is not an independent short hedge. A hedging-mode broker still requires ticket-specific routing, gross and net risk controls, financing and exact close direction.

Manual trades are observed as external ledger facts and reconciled. If a manual sell consumes shares the bot previously attributed, freeze conflicting entry/close commands, reconcile broker orders and determine the declared allocation repair policy. Never keep obsolete stops that could sell nonexistent shares, and never buy back shares just to make the internal ledger look right. A manually canceled/replaced bot stop causes protection reconciliation; the bot must follow the owner-intervention policy rather than endlessly fight the owner.

Account delete/disconnect/broker-change is blocked while positions, unresolved orders, reservations, deadlines or uncertain effects remain. Offer pause-new-entry and drain states independently. Connection credential rotation can change the binding after verification without changing the physical account/lifecycle identity. New credentials to a different account are not a safe edit of an existing live binding.

## 15. Scheduled, exceptional and end-of-life events

All deadlines are durable, timezone-aware and based on the instrument's actual session calendar. Test daylight-saving transitions, holidays, early closes, auctions, overnight sessions, weekend crypto maintenance and UTC/local date disagreement. A restart processes overdue actionable deadlines after account reconciliation; it does not reschedule them tomorrow. An expired entry is never carried to the next session unless the source policy explicitly permits it.

| Event | Mandatory decision and recovery |
|---|---|
| Trading halt, price limit or closed market | Stop new exposure; retain valid available protection; record inability to execute the intended exit; retry only under qualified reopen/session logic. Do not invent a fill at the stop. |
| Gap through stop | Use actual fill/outcome and gap risk; do not backfill at trigger price or clamp protection to a lower level. |
| Earnings/macro/dividend event | Apply only the released event policy and verified calendar. Unknown mandatory event data blocks new exposure; optional analytics failure does not stop exits. |
| Stock split/reverse split | Transform quantities, prices, stops, multipliers/deliverables and ownership economically; reconcile broker-canceled/adjusted orders and fractional cash-in-lieu. No synthetic trade P&L from the split. |
| Symbol change/merger/spinoff/delisting | Follow authoritative instrument identity and successor/deliverable events; freeze ambiguous new orders and reconcile transformed holdings. |
| Option expiration | Enforce pre-expiry cutoff, exercise/assignment instructions and funding/collateral; do not assume an unclosed option simply disappears. |
| Early assignment/exercise | Create underlying and cash events, update option allocation, reserve obligations and manage new exposure under preapproved policy; no false CLOSED status. |
| Broken combo/one leg assigned | Reprice actual remaining legs and underlying, protect/reduce the resultant portfolio; theoretical intact-spread max loss is no longer the only relevant state. |
| Futures first notice/last trade/roll | Stop entries before deadline; close/roll only via explicit plan with temporary risk bounds; never accidentally enter physical delivery. |
| Borrow recall/short restriction | Cancel prohibited new shorts, preserve buy-to-cover management and perform authorized recall response with costs. |
| Futures/derivative funding/variation margin | Post actual cash/collateral events, rerun account stress and capacity, and halt/reduce according to policy when resources fall. |
| Stablecoin depeg or collateral impairment | Revalue quote/collateral in account currency; do not hold conversion at 1.00 by assumption. |
| Broker liquidation | Ingest actual broker effects, cancel/reconcile conflicting bot orders, update ownership and incident status; the bot must not reopen liquidated exposure automatically. |
| Deposit/withdrawal or fee adjustment | Separate external flows from trading P&L; update available backing, not historical performance or halt latches. |
| Broker trade bust/correction | Compensating quantity/cash/fee changes; reopen incident/accounting state where needed, not a fabricated new provider instruction. |

## 16. Failure domains and operational continuity

### 16.1 Startup and recovery gate

Verify credentials/environment identity; acquire the single-writer/dispatch lease; load durable unresolved intents, order families, allocations, reservations, floors, policy versions and deadlines; fetch complete broker positions/orders/executions/account restrictions; reconcile using IDs and paginated cursors; establish available protection; process overdue exits; only then admit new exposure. Partial or failed broker reads are not empty lists. Inconsistency blocks its relevant scope and stays visible.

If the database is unavailable, no new entry is allowed. Native working stops may persist independently, but do not claim the application can safely coordinate new effects without its ledger. A separately engineered preauthorized disaster-recovery control can be used only with independent ownership evidence and fencing; this specification does not invent a second unsynchronized emergency writer. Retain whatever native protection exists, use external alerts and recover the authoritative state.

### 16.2 Fault-specific behavior

Network timeout: classify read versus possible effect; preserve UNKNOWN intent. Rate limit/quota: reserve capacity and prioritize protection/exit/reconciliation; stop new entries first. Expired access token: refresh through the qualified mechanism without changing account identity; if unavailable, retain incident/protection. Feed outage: no new entries or invented trail observations; preserve verified native stops. Missing optional model: frozen baseline or skip that optional overlay, not failed exits. Research/UI/email outage: execution protection continues where core state and broker connectivity remain sound. Email delivery is not a financial safety interlock.

Double worker: only one may dispatch for the fenced account identity; losers stop effect work. Disk full or durability failure: no acknowledged intake/intent without persistence. Process kill: restart from durable state, never reset counters/floors. Backup restore: reconcile post-backup broker effects before any dispatch and prevent duplicate orders. Deployment: drain/hand off the existing writer, maintain schema compatibility and protection, and preserve state. Failed migration: do not start a partially compatible writer. Resource overload: backpressure intake, bound memory and queues, prioritize existing exposure, preserve durable deduplication and source cursors.

A green HTTP health check must not mean all routes are safe. Display independent liveness, intake freshness, quote freshness, broker readback, reconciliation, writer ownership, protection coverage, unresolved effects, cash/risk capacity, deadline processing and notification delivery. UNKNOWN is different from zero and degraded is different from healthy.

## 17. UI/API, reporting and audit acceptance

The GUI must expose the same backend decisions; a preview is not an alternative sizing implementation. Every UI/API write validates permission, schema, resource/version state and CSRF/authentication as appropriate. UI-only hiding is not enforcement.

Required journeys: source onboarding and parser examples; unknown-provider quarantine; exact account binding and capability evidence; portfolio/sleeve budgets and backing; new-signal trace; per-candidate routing rejection; size breakdown showing all caps; partial-fill protection discrepancy; live/paper status; pending cancel/replace/UNKNOWN outcome; overlapping lifecycle ownership; pause/drain/disconnect; scoped manual close; policy candidate comparison/promotion; daily/weekly halt acknowledgment; backup/restart recovery status; no-data and insufficient-evidence research states; mobile and desktop operation.

Display both notional and planned/stressed risk. Show actual fill average separately from provider quote and planned limit. Show desired and broker-confirmed stops separately. Explain why capital is idle: no eligible signals, cash settlement, risk, concentration, margin, data, minimum unit, unresolved intent or reserved future capacity. Never display historical balances as live. An estimate has its as-of time, source and uncertainty.

The decision record must contain: source/origin/revision; parser/field evidence; canonical instrument and lifecycle; mode; config/release/adapter hashes; all eligibility candidates; raw-normalized account snapshot linkage; source/quote/FX timestamps; risk equity; edge model eligibility; complete sizing cap table; chosen quantity/account; resources reserved/reflected; outgoing intent and redacted payload; order-family/fill IDs; protection/exit timeline; P&L/cost attribution; final status and evidence links. Sensitive credentials are never written into these records.

Render untrusted source/broker strings as text, not executable HTML. Test malicious symbols, descriptions, error messages, filenames, CSV export formula injection, logs and deep links. Signatures verify a transport, not content authority. Never pass a provider message into shell execution or dynamically generated production code.

## 18. Research, learning and avoiding false profitability

Keep actual broker results, paper broker results, simulator results, provider claims and research counterfactuals in separate datasets. A position with several trims is one lifecycle outcome for ordinary trade-level edge estimation, not several wins with the remaining loss omitted. Pyramiding changes the entire outcome distribution; analyze seed and add attribution while retaining the combined lifecycle result.

Replay original received-time availability, source edits, missed messages, real market sessions, executable bid/ask/depth where available, order latency, fees, funding, margin, partial fills and shared capital. A signal known at 10:05 cannot be traded at 10:00. If both stop and target occur within one OHLC bar and intrabar order is unknown, use a declared conservative or interval outcome; do not always assume target first. Missing options bid/ask or corporate-action-adjusted data is an evidence limitation, not permission to fabricate precise fill performance.

Track the research trial registry and freeze train/validation/holdout boundaries. Block-bootstrap dependent days, use common opportunities and paired comparisons when appropriate, and report uncertainty, model selection, regime sensitivity and cost stress. Do not re-tune on the final holdout. Forward shadow is needed for new policies, but paper results still do not establish live fills. A no-improvement result is a valid outcome; do not repeatedly expand risk or search trials until an attractive backtest appears.

Useful strategy comparisons include modified Kelly versus bounded fixed risk; joint allocator versus arrival-order allocation; seed-only versus staged entry/pyramiding; provider exit versus baseline trail versus runner; wider cost-aware stop with smaller size versus tighter stop; and funded versus margin-assisted identical-risk execution. No model may auto-promote itself, change live limits or generate unrelated trades.

## 19. Formal testing requirements — not smoke tests or sampling

### 19.1 The acceptance oracle

For each named scenario specify exact Given state, When event(s), Then decisions, broker calls allowed/forbidden, quantity/cash/risk deltas, resulting states, persisted deadlines, reason codes and evidence. A test must inspect backend financial state and outgoing adapter payloads, not just a toast, status code or function call count. Compare application output to an independently implemented reference ledger/state model where possible.

The attached catalog supplies named Given/When/Then requirements. The generator supplies fully enumerated bounded input vectors and simple independent oracles for a restricted gate model and a synthetic linear-sizing model, plus all defined event permutations for five critical race families. They are not tests of the actual application until an adapter binds them to the real implementation. This is stated in every manifest; an unbound contract runner fails rather than reporting success.

### 19.2 Required layers

| Layer | Full required scope and acceptance |
|---|---|
| Source grammar | Every permitted collected source revision, labeled held-out corpus, compound/negation/numeric/date/action mutations, unknown templates and permission failures. |
| Domain/schema | Every field's valid/invalid/absent/null/type/range/unit variants and cross-field relationships; no NaN/Infinity/bool money. |
| Money mathematics | Every active numeric policy boundary minus one exact unit, equal, plus one; currencies/multipliers/rounding and all binding/tied caps. |
| State-machine | Every declared state × every event × every guard outcome, including invalid transitions with explicit expected no-effect/incident. |
| Critical concurrency | Every causally permitted schedule in bounded account-selection, reservation, submit/recovery, cancel/fill, protection/exit and add/exit models. |
| Property/invariant | Check I01–I24 after every event, not only at end; deterministic seeds and saved failing traces. |
| Metamorphic | Duplicating an event changes no financial result; permuting independent events preserves results except declared arbitration; lowering a hard cap never increases permitted size; adding a duplicate binding never increases equity or creates a second destination. |
| Mutation testing | Deliberately remove/weaken each critical guard and prove a test fails; equivalent mutants need reviewed proof, not silent exclusion. |
| Persistence | Kill before/after every durable/effect boundary; backup restore; stale lease; SQLite locks, disk full, foreign-key/unique/version failures and migration rollback. |
| Adapter contract | Exact account/product/session/order recipe, correlation, pagination, statuses, partial fills, cancel/replace, price triggers, rounding, quotas and errors. |
| Replay | Same source/event chronology, shared budgets, actual trade lifecycle and cost assumptions; deterministic repeated outputs. |
| API/browser | Every registered backend route and UI action, success/error/loading/stale/unknown/permission states, ownership scope and mobile/desktop. |
| Operational/chaos | Feed/broker/email/UI/research/database/process failures separately and in declared critical combinations, without live fault injection. |
| Release evidence | Per source × strategy × physical account × asset × adapter × protection recipe × policy version × environment. No inheritance by broker brand. |

Property-based random exploration is additive; it does not replace exhaustive enumeration over declared finite domains. Code coverage can reveal missed code but does not prove financial correctness. “All tests passed” must include exact counts of executed/passed/failed/not-run/blocked and the scope, code hash, config hash, dataset hash, adapter and environment.

### 19.3 Critical mutation inventory

Remove deduplication; key it only by ticker; regenerate source IDs; let a child override disabled provider; treat unknown capability as true; merge paper/live identities; count duplicate bindings as accounts; remove owner-wide cap; ignore reservations; subtract reflected reservations twice; round size up; ignore option multiplier; use margin as equity; ignore fees; allow negative costs; omit gap scenarios; sum correlated Kelly fractions; let profitable lots create negative free risk; reset risk at each add; reset trail on restart; clamp crossed stop downward; treat desired stop as confirmed; use original requested rather than filled quantity; release on cancel request; retry uncertain create with a new key; fail over after timeout; delete old replace-family ID; route exit by current preferences; close entire ticker; ignore external manual quantity; ignore late fills; clear halt on restart; use wrong timezone; replay history live; accept partial broker snapshot as complete; display green on quote failure; render source strings as HTML. Every non-equivalent critical mutant must be killed before affected release.

### 19.4 Exhaustive finite interaction models

The bundled admission model has seven finite axes: authorization, interpretation, route count, hard-budget state, regime known/unknown, portfolio halt, and existing uncertain effect. All 4,608 combinations must run; every negative case proves zero new-entry effects. This restricted model assumes other gates are valid. Expand it from actual code rather than calling its domains universal.

The synthetic sizing model enumerates risk budget, per-unit risk, capital, entry price and source maximum over fixed explicit values, using integer cents and whole units. Every vector calculates exact min/floor quantity independently. These 4,500 vectors are arithmetic fixtures, not actual broker or portfolio stress simulations.

Five race families each enumerate all 24 permutations of four labeled event observations: cancel/fill, submit/recovery, stop/provider exit, add/exit and competing reservation. All 120 vectors are required; impossible causal orderings must be tested as rejected/quarantined observations or explicitly proven unreachable, not silently skipped. For actual effect interleavings, add causal event edges and enumerate every valid linear extension, including each crash boundary. These starter counts total 9,228 generated vectors; counts are verified by the manifest-generation tools and should be updated if domain values change.

In addition, enumerate all exact routes and supported instruments/order recipes/configurations from the implementation registry. Add dimensions such as partial quantities, hedge/net mode, currencies, session, permission, expiration, halt, quote freshness and correction state to the relevant bounded submodels. State-explosion control must use documented abstraction/symmetry and proof obligations, not undocumented random sampling. Separate smaller exhaustive critical models plus integration invariants from a misleading claim of one universal Cartesian product.

## 20. Release gates and staged implementation

Build in place, retaining existing behavior and regression evidence. Phase order is dependency order, not a multi-week estimate.

G0 — Read-only inventory and effective configuration/provenance; actual architecture, deployed commit, legacy writers, accounts and route evidence identified. No mutations to real trading.
G1 — Canonical schemas, account identities/bindings, source/lifecycle deduplication, exact instrument resolution and no-live development isolation.
G2 — Backed portfolios/sleeves, exact money/risk calculations, owner-wide reservations and independent sizing tests.
G3 — Single-destination allocator and deterministic competition; exact exit account binding.
G4 — Durable effect/outbox/order-family state machine, partial fills, ambiguity and restart reconciliation.
G5 — Protection, targets, overlap, manual actions, loss halts, calendars and exceptional lifecycle events.
G6 — UI/API traces wired to actual decisions; health/recovery/notifications and backup/deployment tests.
G7 — Modified Kelly, joint allocation research, staged entry/pyramiding and runners in non-live modes with no-data and negative-edge branches.
G8 — All applicable scenario, exhaustive model, mutation, integration, browser and route-specific non-live qualification evidence; unresolved blockers remain explicit.
G9 — Separate owner-operated bounded live release only if already specifically authorized later; no automated promotion or new permission inferred from this document.

A released equity route need not wait for an unrelated unsupported crypto-derivative adapter, but unsupported scope remains visible as unfinished. Conversely, all generic capital/ownership/protection invariants apply before any live route can be declared ready. Do not call all requested asset scope complete because one equity path works.

## 21. Required deliverables from Claude Code

Produce or reconcile: requirement-to-code-to-test manifest; effective policy/provenance report; account/capability registry; canonical source/instrument schemas; state machines; hierarchical budget/ledger design and migrations; sizing/Kelly model contracts; routing/reservation logic; protection/add/runner recipes; exact scenario catalog; all generated test vectors; independent reference models; fault/mutation suites; browser journey inventory; release evidence manifest; source/license inventory; operator runbook; and next actionable blockers.

Every requirement status must be one of NOT_IMPLEMENTED, IMPLEMENTED_NOT_WIRED, WIRED_NOT_TESTED, TESTED_SIMULATOR, TESTED_BROKER_PAPER, TESTED_OWNER_LIVE, BLOCKED, or NOT_APPLICABLE_WITH_REASON. Report financial safety defects separately from economic-performance uncertainty. Do not use package-helper results as evidence that the actual application is fixed.

## 22. Primary references and historical inputs

Accessed October 2, 2026. External facts are limited to the referenced topics; all other normative behavior in this document is a proposed engineering contract, not a claim that a vendor supports it.

- S01: Busseti, Ryu, Boyd, *Risk-Constrained Kelly Gambling*, Journal of Investing 25(3), 2016; author page: https://www.web.stanford.edu/~boyd/papers/kelly.html . Supports risk-constrained growth/drawdown modeling, not profitability guarantees or a chosen live fraction.
- S02: FINRA Regulatory Notice 26-10, April 20, 2026: https://www.finra.org/rules-guidance/notices/26-10 . Effective June 4, 2026; transition through October 20, 2027.
- S03: SEC Investor.gov, *New T+1 Settlement Cycle*: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin . Covered securities and effective date; do not generalize to all products.
- S04: Robinhood Crypto Trading API documentation: https://docs.robinhood.com/ . Crypto API scope does not certify stock/options order access.
- S05: Alpaca, *Placing Orders*: https://docs.alpaca.markets/us/docs/orders-at-alpaca . Exact partial/bracket/cancel/trigger semantics remain recipe- and account-specific.
- S06: Options Industry Council, *Options Assignment*: https://www.optionseducation.org/referencelibrary/faq/options-assignment . Assignment/expiration exposures.
- S07: CME Group, *Margin: Know What's Needed*: https://www.cmegroup.com/education/courses/introduction-to-futures/margin-know-what-is-needed.html . Futures collateral and changing margin.
- S08: SQLite, *Write-Ahead Logging*: https://www.sqlite.org/wal.html . Single-writer and same-host constraints.
- S09: NIST, *Combinatorial Coverage Measurement*: https://www.nist.gov/publications/combinatorial-coverage-measurement . Finite interaction-coverage terminology.
- S10: NIST, *Testing Event Sequences*: https://csrc.nist.gov/Projects/automated-combinatorial-testing-for-software/combinatorial-methods-in-testing/event-sequence-testing . Ordered interaction/sequence testing.

Historical private inputs reviewed: `Signal_Copier_Claude_Code_Workbook_v3_4.md` (prepared September 22, 2026; reference contract, not deployed software) and `Signal_Copier_fccf57a_Detailed_Findings.md` (commit-pinned audit, all findings marked open at that snapshot). October 2 owner decisions take precedence over conflicting historical architecture and do not themselves establish present code or live configuration.
