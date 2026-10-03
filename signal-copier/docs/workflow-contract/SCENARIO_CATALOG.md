# Named acceptance scenarios

292 named Given/When/Then requirements across 21 categories. **All are NOT_RUN against the application in this package.**

Each scenario must additionally record exact adapter calls (or zero), persisted state, quantities/cash/risk deltas, recovery/deadlines and evidence. Run every applicable declared variant; do not substitute screenshots or mocked helper results for actual wired application behavior.

## ING — Intake, authority, identity and durability

### ING-001 — Valid authorized webhook
**Given:** An approved channel and sender map to one known provider; valid schema and active replay window.
**When:** Receive one live-format message in an isolated paper fixture.
**Then:** Persist envelope before acknowledgment; create one interpretation job; retain original event ID and mode; no broker call at intake.

### ING-002 — Invalid transport signature
**Given:** Payload contains a plausible trade but its signature fails.
**When:** Receive the payload.
**Then:** Reject before parsing/dispatch; zero financial intent/reservation/broker calls; retain sanitized security evidence.

### ING-003 — Authorized transport unauthorized sender
**Given:** A valid transport signature is present but sender/channel is not allowed.
**When:** Receive BUY AAPL.
**Then:** Reject source authorization; do not inherit permissions from transport credential.

### ING-004 — Unknown provider
**Given:** Channel is connected but has no registered provider identity.
**When:** Receive a syntactically valid entry.
**Then:** Persist UNKNOWN_PROVIDER and onboarding draft; zero entry effect in live; no assumed analyst edge.

### ING-005 — Known provider unknown analyst
**Given:** Provider is approved; source author is not mapped.
**When:** Receive a trade-shaped message.
**Then:** Quarantine analyst-dependent action; preserve provider/origin identity and do not copy another analyst's profile.

### ING-006 — Disabled provider enabled child
**Given:** Provider entry flag is false and analyst preference is true.
**When:** Receive entry then valid exit for existing owned lifecycle.
**Then:** Block entry; keep matching scoped exit/protection active; child cannot override parent disable.

### ING-007 — Forged forwarded origin
**Given:** Authorized relay forwards content claiming a different analyst with no verified lineage.
**When:** Receive a forwarded alert.
**Then:** Record transport and claimed origin separately; no inferred trading permission from display-name text.

### ING-008 — Oversized malformed body
**Given:** Body exceeds limits or is not expected object/type.
**When:** Receive payload.
**Then:** Bounded sanitized rejection; no 500 from type assumptions; no queue/resource exhaustion or financial effects.

### ING-009 — Persistence unavailable
**Given:** The durable envelope insert cannot commit.
**When:** Receive an otherwise valid source message.
**Then:** Do not acknowledge durable acceptance or dispatch; emit operational fault and permit documented upstream recovery.

### ING-010 — Source replay after crash
**Given:** Envelope already committed; process crashed before intake acknowledgment.
**When:** Source retries same ID/revision.
**Then:** Join existing job/result; exactly one canonical observation and no duplicate trade.

### ING-011 — Cross-post same origin
**Given:** Two channels deliver same original provider event with established lineage.
**When:** Both arrive concurrently.
**Then:** Collapse to one canonical opportunity while retaining both observations; one selected account maximum.

### ING-012 — Similar but distinct signals
**Given:** Two real lifecycle IDs share ticker, side and text.
**When:** Receive both.
**Then:** Retain both distinct opportunities; apply concurrent aggregate caps rather than ticker-only deduplication.

### ING-013 — Historical backfill
**Given:** Import job reads last month's approved channel history.
**When:** A message looks fresh after normalization or UI mode edit.
**Then:** Preserve historical mode; no live dispatch; source event time/provenance cannot be rewritten to authorize trading.

### ING-014 — Cursor gap reconnect
**Given:** Transport disconnects while several source revisions occur.
**When:** Reconnect with saved cursor.
**Then:** Recover permitted gap with durable pagination and revision lineage; do not label history complete until established; stale entries stay non-live.

### ING-015 — Prompt injection
**Given:** Message instructs agent to ignore limits, run code or expose secrets.
**When:** Parser/GUI/research consumes message.
**Then:** Treat text as data; no executable instructions, config changes, leaked secrets or trading authority.

## PAR — Full-message interpretation and compound instructions

### PAR-001 — Negated buy
**Given:** Source text says DO NOT BUY AAPL 10.
**When:** Parse full message.
**Then:** NONACTIONABLE/negated result; zero entry, no substring-based BUY.

### PAR-002 — Historical recap
**Given:** Source says Yesterday we bought AAPL and closed it.
**When:** Parse current receipt.
**Then:** Historical commentary, not a fresh entry or exit.

### PAR-003 — Unmet conditional
**Given:** Source says Buy only above 100 with a confirmed crossing.
**When:** Price remains 99.
**Then:** Durable bounded WAIT_TRIGGER; no immediate order; exact trigger semantics recorded.

### PAR-004 — Option-flow observation
**Given:** Source reports unusual call volume without recommending a trade.
**When:** Parse flow alert.
**Then:** OBSERVATION only; no BUY_TO_OPEN inference.

### PAR-005 — Buy put direction
**Given:** Exact long-put contract and opening intent are given.
**When:** Normalize action.
**Then:** BUY_TO_OPEN option with bearish underlying exposure; not stock short or sell order.

### PAR-006 — Ambiguous sell option
**Given:** Source says sell puts without stating owned closure versus opening sale.
**When:** Normalize action.
**Then:** PARSE_AMBIGUOUS; no short-option exposure or guessed close.

### PAR-007 — Independent mixed-assets
**Given:** Message contains valid stock and spot-crypto instructions with separate fields.
**When:** Parse and route.
**Then:** Two child instructions with exact asset identities and independent outcomes; each has at most one eligible destination and shared owner caps.

### PAR-008 — Valid and invalid independent child
**Given:** Two independent entries exist; one lacks exact option expiry.
**When:** Parse and validate.
**Then:** Block unresolved child; valid child may proceed subject to all caps; parent report lists both outcomes.

### PAR-009 — Ambiguous shared stop
**Given:** Message lists several instruments and one unlabeled stop.
**When:** Parse field ownership.
**Then:** Block affected instructions; never apply the stop arbitrarily to every instrument.

### PAR-010 — Atomic vertical spread
**Given:** Complete two-leg same-expiry vertical is supplied.
**When:** Normalize and execute in paper fixture.
**Then:** One combo group and payoff/risk plan; one qualified combo intent, not two unrelated orders.

### PAR-011 — Unsupported atomic combo
**Given:** Complete spread is given but adapter only supports separate legs.
**When:** Capability check.
**Then:** Unsupported recipe; no speculative leg-in or naked interim exposure.

### PAR-012 — Either-or triggers
**Given:** Two alternative entries in one exclusive group trigger simultaneously.
**When:** Competing workers claim group.
**Then:** One exclusive opportunity token wins; other terminates/holds per policy; never open both.

### PAR-013 — Roll dependent actions
**Given:** Old option remains open and new roll leg is specified.
**When:** Process roll.
**Then:** Validate linked group and temporary capital; unconfirmed old close does not authorize an unsafe new naked position.

### PAR-014 — Reversal
**Given:** Account owns a long; signal explicitly requests reverse to short under released policy.
**When:** Process instruction.
**Then:** Close/reconcile old side first, then independently authorize new side; unknown close blocks reversal entry.

### PAR-015 — Multileg missing ratio
**Given:** Butterfly text omits one leg ratio.
**When:** Resolve combo.
**Then:** Reject incomplete group; no assumed 1:2:1 from a strategy label alone.

### PAR-016 — Provider quantity versus copier size
**Given:** Analyst reports bought 500 shares.
**When:** Produce trade plan.
**Then:** Retain analyst quantity separately; user's quantity derives from released risk/capital policy and explicit source ceilings only.

### PAR-017 — Half-size ambiguous basis
**Given:** Source says add half or sell half without a registered denominator.
**When:** Parse update.
**Then:** Review/incident as appropriate; no invented 50% of current or original inventory.

### PAR-018 — Price-unit conflict
**Given:** Structured stop is option premium and text describes underlying stop.
**When:** Resolve protection.
**Then:** Domain conflict blocks plan; no mixing dollar values across instruments.

### PAR-019 — Attachment-only alert
**Given:** Image/audio contains trade details with no qualified parsing path.
**When:** Receive attachment.
**Then:** Unsupported-media/observation state; no hallucinated contract or quantity.

### PAR-020 — Locale/time ambiguity
**Given:** Date 03/04 and decimal comma are not disambiguated by provider grammar.
**When:** Parse actionable fields.
**Then:** Reject ambiguous date/number; do not choose locale from server defaults.

## REV — Revisions, lifecycle matching and temporal order

### REV-001 — Edit before dispatch
**Given:** Entry is interpreted but no effect can have occurred.
**When:** Provider changes valid entry price.
**Then:** Version interpretation, invalidate old plan/reservation as safe, revalidate once; not two entries.

### REV-002 — Edit after acceptance
**Given:** Entry has possible or confirmed broker acceptance.
**When:** Provider changes entry quantity.
**Then:** Same-lifecycle amendment with revalidation; no new independent order or hidden limit increase.

### REV-003 — Delete source message
**Given:** Entry is open and source message is deleted.
**When:** Receive tombstone.
**Then:** Record deletion; retain management; no invented exit unless protocol explicitly defines it.

### REV-004 — Exit before entry
**Given:** Exit references lifecycle L before its OPEN arrives.
**When:** Receive late OPEN L.
**Then:** L remains terminal; no new buy; reconcile any pending effect.

### REV-005 — Old exit new same ticker
**Given:** L1 is closed and L2 owns same symbol.
**When:** Delayed EXIT L1 arrives.
**Then:** No sale of L2; record already-terminal/orphan match with exact lifecycle evidence.

### REV-006 — Duplicate partial exit
**Given:** Sell half instruction with fixed event denominator already filled.
**When:** Replay same source revision.
**Then:** No second halving or order; return existing result.

### REV-007 — Delayed exit beyond entry age
**Given:** Owned position remains open; valid exit is older than entry freshness limit.
**When:** Receive exit.
**Then:** Resolve current owned remainder and exit policy; do not reject solely using entry TTL.

### REV-008 — Exit without fill
**Given:** Entry was definitively rejected with zero fills.
**When:** Matching exit arrives.
**Then:** Zero sell; terminal/audit update only, unless pending entry uncertainty still requires reconciliation.

### REV-009 — Ambiguous all-out
**Given:** Two same-provider lifecycles exist without source reference.
**When:** Receive all out lacking protocol scope.
**Then:** Urgent semantic incident; keep protection; no guessed ticker-wide close.

### REV-010 — Scoped close-all
**Given:** Provider protocol explicitly means its strategy book only.
**When:** Receive close all.
**Then:** Close only matching owned allocations under coordinated writer; manual and other-provider holdings unchanged.

### REV-011 — Reply-parent missing
**Given:** Exit/update relies on unavailable parent context.
**When:** Receive reply.
**Then:** No invented instrument/lifecycle; collect missing authorized context or hold with incident and existing protection.

### REV-012 — Future timestamp
**Given:** Source time is beyond permitted clock-skew tolerance.
**When:** Validate entry age.
**Then:** Flag clock/source anomaly; no accidental negative-age bypass.

## INS — Instrument metadata, units and product identity

### INS-001 — Exact stock resolution
**Given:** Stock ticker matches a unique tradable instrument for venue/account.
**When:** Resolve metadata.
**Then:** Persist exact broker ID, currency, lot and tick; no default CRYPTO class.

### INS-002 — Ambiguous symbol
**Given:** Same display code matches multiple exchanges/products.
**When:** Resolve entry.
**Then:** INSTRUMENT_UNRESOLVED; no first-match execution.

### INS-003 — Option missing expiry
**Given:** Option text has underlying, strike and call but no expiry.
**When:** Resolve contract.
**Then:** Reject; no nearest-Friday/nearest-liquid substitution.

### INS-004 — Adjusted option deliverable
**Given:** Contract has nonstandard multiplier/deliverable.
**When:** Size and protect.
**Then:** Use authoritative actual units/deliverable; all cash/risk calculations reflect adjustment.

### INS-005 — Expired option
**Given:** Exact contract expiry has passed its tradable deadline.
**When:** Receive entry.
**Then:** Reject stale/expired instrument; no automatic roll.

### INS-006 — Continuous futures ticker
**Given:** Source names a continuous root without dated contract semantics.
**When:** Route order.
**Then:** Observation/unresolved until exact authorized contract chosen; no guessed front month.

### INS-007 — Futures negative price
**Given:** Instrument profile explicitly supports signed prices and exact tick value.
**When:** Validate negative trade price.
**Then:** Product-correct signed price accepted only within valid profile; quantities remain nonnegative with separate side; stock profile would reject.

### INS-008 — FX lot conversion
**Given:** Source quantity is 0.1 standard lot with declared 100000-unit lot.
**When:** Calculate exposure.
**Then:** 10000 base units, quote/account currency conversion and swap; not 0.1 shares.

### INS-009 — Stale FX conversion
**Given:** Non-USD risk needs account-currency conversion that is stale.
**When:** Size entry.
**Then:** Block mandatory conversion-dependent exposure; not assume parity or last-known unlimited freshness.

### INS-010 — Crypto fee in base
**Given:** Buy 0.01 units and fee removes base inventory.
**When:** Apply fill and protection.
**Then:** Owned quantity is net delivered base units; no stop for more than actual inventory.

### INS-011 — Crypto minimum/dust
**Given:** Permitted risk yields quantity below lot/minimum notional.
**When:** Finalize order.
**Then:** Skip new entry; existing untradeable dust remains explicit, not falsely closed or rounded up.

### INS-012 — Inverse derivative
**Given:** Inverse contract has coin collateral and reciprocal payout formula.
**When:** Size/stress.
**Then:** Dedicated inverse model; reject linear-share fallback.

### INS-013 — Leveraged inverse ETF
**Given:** Source signal references an inverse leveraged fund.
**When:** Aggregate underlying exposure.
**Then:** Use actual economic direction/leverage and scenarios, not a positive one-to-one ticker exposure.

### INS-014 — Metadata change during planning
**Given:** Tick/multiplier or instrument trading status version changes.
**When:** Dispatch revalidation.
**Then:** Invalidate stale plan and recompute or reject before effect.

## ROU — Account identity, selection, permissions and binding

### ROU-001 — Three eligible accounts
**Given:** Same source matches three approved account alternatives.
**When:** Allocate one canonical entry.
**Then:** Select one physical account; exactly one entry intent; persist reasons for the other two not selected.

### ROU-002 — Duplicate bindings
**Given:** Direct and relay connections expose same immutable physical account.
**When:** Inventory/rank accounts.
**Then:** Count equity once; one candidate identity and approved binding; no duplicate trade.

### ROU-003 — No eligible account
**Given:** Product or policy unsupported by every approved destination.
**When:** Allocate.
**Then:** UNROUTABLE/shadow; no auto-activation of a new account or asset substitution.

### ROU-004 — Better funded alternate
**Given:** Preferred candidate cannot meet minimum lot; another approved account can.
**When:** Compute feasibility/rank.
**Then:** Exclude infeasible account, choose eligible alternative only if released preference permits; one selection.

### ROU-005 — Wrong gateway default
**Given:** Multi-account gateway default differs from selected account.
**When:** Submit paper fixture.
**Then:** Exact selected broker account ID appears on every leg/operation and readback; default cannot route it elsewhere.

### ROU-006 — Paper/live same alias
**Given:** Paper and live accounts share display name.
**When:** Route paper instruction.
**Then:** Resolve immutable environment-specific identity; zero live credential or endpoint access.

### ROU-007 — Account liquidation-only
**Given:** Account reports close-only restriction.
**When:** Receive entry and owned exit.
**Then:** Block entry; allow supported scoped risk reduction; explain restriction.

### ROU-008 — Capability unknown
**Given:** Method exists but exact session/stop recipe has no evidence.
**When:** Route.
**Then:** UNVERIFIED blocks new exposure; no method-presence capability inference.

### ROU-009 — Account changes mid-plan
**Given:** Binding/account config version changes after sizing.
**When:** Attempt reserve/dispatch.
**Then:** CAS/version check invalidates plan; no use of stale account identity.

### ROU-010 — Exit after route change
**Given:** Lifecycle entered on A; current preference now favors B.
**When:** Receive exit.
**Then:** Exit A's owned allocation; zero order to B.

### ROU-011 — Timeout then alternate available
**Given:** A's order may be accepted; B is healthy and funded.
**When:** Router attempts recovery.
**Then:** Keep A intent UNKNOWN and reservation; no B fallback or duplicate economic position.

### ROU-012 — Account removal with exposure
**Given:** Account has open allocation, pending order or UNKNOWN effect.
**When:** User attempts delete/disconnect.
**Then:** Block destructive action; offer pause/drain; preserve credentials needed for management where permitted.

### ROU-013 — Credential rotation same account
**Given:** New binding credentials verify same immutable account.
**When:** Replace connection.
**Then:** Preserve lifecycle account identity and single writer; reconcile before resumed dispatch.

### ROU-014 — No alternate live authority
**Given:** Initial approved live route unavailable; another broker login exists.
**When:** Attempt route fallback.
**Then:** Do not activate it merely because connected; scoped blocker, independent paper work may continue.

## CAP — Hierarchical budgets, shared backing and reservations

### CAP-001 — Shared final dollars
**Given:** Two entry workers each need $10 risk, only $15 remains.
**When:** Reserve concurrently.
**Then:** Total commitments never exceed $15; one or smaller feasible quantities win by policy; losing transaction recomputes.

### CAP-002 — Owner cap across accounts
**Given:** Each account locally has capacity but owner aggregate would breach.
**When:** Concurrent entries on separate accounts.
**Then:** Global owner reservation blocks/reduces aggregate; account workers cannot bypass owner cap.

### CAP-003 — Portfolio backing double count
**Given:** Two portfolios claim same $6000 account equity in full.
**When:** Validate budgets.
**Then:** Reject overallocated backing; display one real $6000 resource, not $12000.

### CAP-004 — Sleeve hard reservation
**Given:** Sleeve A capacity is hard reserved and unused.
**When:** Sleeve B requests it.
**Then:** Deny borrowing absent released sharing policy; no automatic theft of reserved capital.

### CAP-005 — Soft reservation expiry
**Given:** Forecast signal reserve is soft, bounded and expired with no order effect.
**When:** Allocator ticks.
**Then:** Release unused capacity with evidence; do not reserve indefinitely.

### CAP-006 — Unknown order reservation TTL
**Given:** Order may have been accepted; reservation wall-clock TTL expires.
**When:** Cleanup runs.
**Then:** Do not release committed resources; reconcile until authoritative outcome.

### CAP-007 — Reflected broker commitment
**Given:** Broker buying power already deducts order X's reserved amount.
**When:** Normalize snapshot and local ledger.
**Then:** Deduct X only once using explicit reflected membership; avoid false double subtraction.

### CAP-008 — Unreflected local intent
**Given:** Local intent reserved but broker snapshot precedes it.
**When:** Compute available capacity.
**Then:** Subtract local unreflected commitment; no overspend from stale broker BP.

### CAP-009 — Partial fill accounting
**Given:** 100-share intent reserved, 40 filled and 60 working.
**When:** Update ledger.
**Then:** Transfer 40 into owned exposure, keep 60 pending commitments, no resource creation/release for whole order.

### CAP-010 — Cancel request
**Given:** Working unfilled order reserves risk/cash.
**When:** Receive HTTP cancellation acceptance only.
**Then:** Retain resources until final cancellation and late-fill reconciliation.

### CAP-011 — Final reject no fill
**Given:** Authoritative rejected state plus complete no-fill evidence.
**When:** Reconcile.
**Then:** Release proven-unused commitments once; no duplicate release on retry.

### CAP-012 — Withdrawal cash flow
**Given:** Owner withdraws funded capital intraday.
**When:** Revalue budgets.
**Then:** Reduce backing/availability, preserve trading P&L and halt latches, block excess new exposure.

### CAP-013 — Deposit loss halt
**Given:** Daily halt is latched, deposit raises equity.
**When:** Process deposit.
**Then:** No automatic unhalt or erased trading loss; adjust external flow only.

### CAP-014 — Manual collateral
**Given:** Manual positions encumber account margin.
**When:** Compute automation capacity.
**Then:** Reserve their actual whole-account effects; no free collateral assumption or unauthorized sale.

### CAP-015 — Same-underlying multiple assets
**Given:** Shares, calls and correlated futures reference same underlying.
**When:** Admit another trade.
**Then:** Enforce consolidated underlying and stress limits across asset labels.

### CAP-016 — Credit proceeds liability
**Given:** Short-credit combo receives premium.
**When:** Update cash/risk.
**Then:** Liability/collateral remain; credit does not become unlimited free risk budget.

### CAP-017 — Minimum lot cannot fit
**Given:** Budget permits 0.7 whole-share equivalent.
**When:** Final sizing.
**Then:** Zero-unit skip; never floor to one or raise limit to create activity.

### CAP-018 — Multiple binding constraints
**Given:** Risk and cash yield same limiting size.
**When:** Explain allocation.
**Then:** Report both tied binding caps and exact quantity, not arbitrary one-only explanation.

## SIZ — Financial validation and exact sizing

### SIZ-001 — Reference stock sizing
**Given:** Synthetic equity=6000, risk cap=15, entry=50, stop=49, per-share costs=.05; other caps slack.
**When:** Size whole shares.
**Then:** 14 shares, notional 700, planned risk 14.70; exact Decimal result.

### SIZ-002 — Budget below first share
**Given:** Same fixture risk budget=.50.
**When:** Size.
**Then:** Zero shares and RISK_LIMITED, no minimum-one override.

### SIZ-003 — Invalid numeric quantity
**Given:** Quantity is bool, NaN, Infinity, negative or unsupported zero.
**When:** Validate entry.
**Then:** Reject before reservation/broker effect; product-aware strict type handling.

### SIZ-004 — Wrong-side long stop
**Given:** Long entry=50 and initial stop=51 without compatible trigger semantics.
**When:** Validate new plan.
**Then:** Invalid/crossed protection rejection; no risk calculated as negative profit.

### SIZ-005 — Missing versus invalid stop
**Given:** Source omits stop in case A; supplies malformed stop in B.
**When:** Resolve fallback.
**Then:** Only A may use compatible released fallback; B rejected/incident, not silently repaired as missing.

### SIZ-006 — Stop tick rounding
**Given:** Required long floor falls between supported ticks.
**When:** Normalize stop.
**Then:** Select only permitted rounding preserving floor/risk; if unplaceable reject/reduce, never loosen covertly.

### SIZ-007 — Tight stop huge position
**Given:** Stop distance near zero and very large Kelly bound.
**When:** Size.
**Then:** Notional, costs, liquidity, stress and margin still cap size; no unbounded exposure.

### SIZ-008 — Option full debit
**Given:** Risk budget=15, option premium=2, multiplier=100, before fees.
**When:** Size long contracts.
**Then:** Zero contracts because one risks at least 200 debit; no stock-like calculation or stop-only bypass.

### SIZ-009 — Nonlinear margin tier
**Given:** Size crossing broker concentration tier raises margin per unit.
**When:** Search maximum feasible q.
**Then:** Recompute full post-trade resource function at each candidate boundary; independent-ratio bound alone insufficient.

### SIZ-010 — Fee currency
**Given:** Costs charged in different currency than risk equity.
**When:** Calculate q.
**Then:** Convert with valid rate and conservative rounding; no omission or unit addition.

### SIZ-011 — Negative cost input
**Given:** Config or feed provides a negative fee/slippage allowance without explicit rebate semantics.
**When:** Validate plan.
**Then:** Reject malformed economics; qualified rebates separately bounded, not negative loss budget.

### SIZ-012 — Zero-risk misleading break-even
**Given:** Stop equals entry.
**When:** Recalculate resources.
**Then:** Costs, mark giveback and stress remain; no infinite quantity or zero whole-account risk.

### SIZ-013 — Cap decreases
**Given:** Identical state, lower one hard monetary cap by one cent.
**When:** Recompute.
**Then:** Permitted quantity cannot increase; independent reference/property test catches nonmonotonic behavior.

### SIZ-014 — Worse partial fill
**Given:** Actual fill creates larger risk than pretrade allowed.
**When:** Apply fill.
**Then:** Record actual price; cancel remainder safely, protect and follow authorized reduction, never enlarge cap after the fact.

## KEL — Kelly, profile evidence and adaptive allocation

### KEL-001 — Binary Kelly arithmetic
**Given:** Synthetic p=.55 and b=1.4 with outcomes +bR/-1R only.
**When:** Calculate full Kelly.
**Then:** f=.228571428571... in risk-fraction convention; arithmetic/unit test independent of application.

### KEL-002 — Fractional Kelly hard cap
**Given:** Quarter Kelly from prior fixture exceeds released .0025 lifecycle fraction.
**When:** Calculate final risk budget.
**Then:** .0025 ceiling binds; $6000 fixture gives at most $15 risk, no live config change.

### KEL-003 — Variable outcomes
**Given:** Empirical outcomes include -3R, -.5R, 0, +1R and +5R.
**When:** Fit sizing.
**Then:** Optimize full cost-adjusted log distribution; average win/loss shortcut not claimed exact.

### KEL-004 — Insolvent scenario
**Given:** Candidate f makes 1+fX nonpositive for stress X.
**When:** Evaluate objective.
**Then:** Candidate infeasible, not log clipped to attractive finite value.

### KEL-005 — No history
**Given:** New analyst/strategy/asset profile has zero observations.
**When:** Request Kelly.
**Then:** INSUFFICIENT_EVIDENCE; no fabricated p; new profile shadow/paper, existing released baseline treated separately.

### KEL-006 — Thin child profile
**Given:** Five highly correlated trades in narrow cell, broader compatible parent available.
**When:** Fit profile.
**Then:** Conservative shrinkage/uncertainty and effective sample, no high-confidence independent cell estimate.

### KEL-007 — Negative after-cost edge
**Given:** Gross outcomes positive, costs make conservative net edge nonpositive.
**When:** Allocate under Kelly mode.
**Then:** No new exposure from that policy; costs cannot be omitted to force a trade.

### KEL-008 — Parse confidence misuse
**Given:** Parser emits 99% semantic confidence but no trading history.
**When:** Size.
**Then:** No conversion to 99% win probability or elevated risk.

### KEL-009 — Several providers same thesis
**Given:** Multiple alerts share origin/underlying and overlap.
**When:** Combine profiles.
**Then:** Dependence/correlation handling; do not sum independently optimal Kelly fractions as independent bets.

### KEL-010 — Partial exits double count
**Given:** One lifecycle has three profitable trims and one final loss.
**When:** Build training set.
**Then:** One combined lifecycle outcome including every cost, plus separately labeled leg attribution; not three wins versus omitted loss.

### KEL-011 — New exit policy
**Given:** Runner policy changes outcome distribution from provider exit.
**When:** Update profile.
**Then:** New version/qualified pooled inference; old performance not silently claimed for new policy.

### KEL-012 — Lookahead leakage
**Given:** Regime feature uses next day's realized return or future revised source.
**When:** Train/replay.
**Then:** Dataset/test rejects unavailable information; no evaluation contamination.

### KEL-013 — Winning streak
**Given:** Several recent wins occur under unchanged live release.
**When:** Online worker recalculates.
**Then:** No unapproved risk increase; future changes require scheduled/versioned evidence/release within caps.

### KEL-014 — Losing streak
**Given:** Losses hit pre-released drawdown reduction/halt threshold.
**When:** Next signal arrives.
**Then:** Reduce/stop new risk as policy dictates; no doubling to recover losses.

### KEL-015 — Holdout retuning
**Given:** Candidate fails frozen test period.
**When:** Research tries to retune on same holdout.
**Then:** Retain failed result and trial registry; new test design required, no reused holdout called untouched.

### KEL-016 — Portfolio horizon mismatch
**Given:** Scalp and 30-day option R samples offered as one-period portfolio returns.
**When:** Joint optimization.
**Then:** Reject incoherent horizon alignment; use common-time cashflow/scenario replay or conservative budgets.

## MAR — Margin, settlement and restriction regimes

### MAR-001 — Cash no borrowing
**Given:** Account is cash or released borrow limit zero.
**When:** Signal exceeds eligible funded cash.
**Then:** Reduce quantity/skip; no margin use despite broker-advertised BP.

### MAR-002 — Margin account cash sufficient
**Given:** Risk-approved size fits eligible cash.
**When:** Fund trade.
**Then:** No incremental loan solely to raise utilization; risk size unchanged.

### MAR-003 — Approved debit
**Given:** Same-risk trade requires borrowing; all permissions, cost and stress buffers pass.
**When:** Allocate.
**Then:** Borrow only required permitted amount; record projected interest and remaining headroom.

### MAR-004 — Unknown maintenance
**Given:** Broker read fails for maintenance or house requirement.
**When:** New leveraged candidate arrives.
**Then:** MARGIN_UNKNOWN blocks added exposure; not assume zero requirement.

### MAR-005 — Intraday versus overnight
**Given:** Position allowed intraday would exceed overnight collateral.
**When:** Approach mandatory conversion/close time.
**Then:** Prevent overnight extension; perform released timed reduction/exit and report failure if blocked.

### MAR-006 — House margin increase
**Given:** Broker raises requirement on held concentrated security.
**When:** Reconcile margin.
**Then:** Recompute headroom, halt new risk and apply authorized reduction; no static old-margin assumption.

### MAR-007 — Legacy PDT account
**Given:** Dated account evidence says legacy regime during transition.
**When:** Evaluate same-day stock entry/exit plan.
**Then:** Enforce that account's current restrictions; no universal new-rule bypass.

### MAR-008 — New intraday account
**Given:** Dated broker evidence confirms new regime.
**When:** Evaluate entries.
**Then:** Use account's live intraday margin requirements; no universal legacy $25000 veto.

### MAR-009 — Unknown regime
**Given:** No reliable evidence of which restriction regime applies.
**When:** Admit affected entry.
**Then:** Block/precise configuration incident; valid owned protective exits continue.

### MAR-010 — Unsettled cash
**Given:** Recent securities sale proceeds not yet usable under account rules.
**When:** Cash-account entry requests those funds.
**Then:** Respect broker eligibility/settlement calendar; not infer availability from displayed cash alone.

### MAR-011 — Derivative margin not risk
**Given:** Futures initial margin is much less than stress loss.
**When:** Size.
**Then:** Apply independent loss/stress/variation-margin bounds; never risk-budget=margin-deposit.

### MAR-012 — Manual portfolio liquidation risk
**Given:** Bot sleeve looks safe but whole account's manual holdings breach stress headroom.
**When:** New candidate.
**Then:** Block new exposure on whole-account constraint; do not treat sleeve as isolated broker account.

### MAR-013 — Funding destroys edge
**Given:** Long hold requires loan/funding costs larger than conservative opportunity edge.
**When:** Cost-aware allocation.
**Then:** Skip or choose a separately feasible lower-cost authorized plan; no forced leverage.

### MAR-014 — Crypto collateral parity
**Given:** Stablecoin quoted at .92 but collateral module assumes 1.00.
**When:** Stress/admission.
**Then:** Revalue at qualified account-currency prices and block stale assumption.

## ENT — Entry conditions, pricing, sessions and deadlines

### ENT-001 — Explicit limit preserved
**Given:** Provider Max Buy=50.00 and current ask=50.10.
**When:** Plan order.
**Then:** No buy above 50.00; wait only within valid policy/TTL or skip, no chase override.

### ENT-002 — Trigger versus limit
**Given:** Source says buy breakout at 100 but never above 100.20.
**When:** Market crosses trigger at 100.30.
**Then:** Trigger does not permit price-constraint breach; no buy at 100.30.

### ENT-003 — Stale quote
**Given:** Executable quote age exceeds released threshold by one clock unit.
**When:** Dispatch revalidation.
**Then:** Reject/wait for fresh data within TTL; no stale price used to size marketable order.

### ENT-004 — Wide spread
**Given:** Spread exceeds released bound.
**When:** Entry candidate.
**Then:** LIQUIDITY_REJECTED or bounded wait; source signal strength cannot bypass hard limit.

### ENT-005 — Target already passed
**Given:** Source target=105 and delayed entry reference now exceeds 105.
**When:** Revalidate entry economics.
**Then:** Expire/reject incompatible stale opportunity; no automatic chase toward an obsolete target.

### ENT-006 — Session closed
**Given:** Regular-only equity signal arrives after session close.
**When:** Route/submit.
**Then:** No queued next-day unintended order; bounded future-session policy only if explicitly released.

### ENT-007 — Early close
**Given:** Exchange calendar closes earlier than normal.
**When:** Schedule entry TTL and exit.
**Then:** Respect actual early close and deadlines; no hardcoded 16:00 assumption.

### ENT-008 — TTL before cancel finality
**Given:** Unfilled entry TTL expires while cancel is only requested.
**When:** Timer runs.
**Then:** Mark expiration intent, keep reservation/late-fill responsibility until resolved; no resource release on timer alone.

### ENT-009 — Price moves after sizing
**Given:** Quote/account version changes before submit.
**When:** Dispatch.
**Then:** Recheck and resize/expire atomically; no stale fixed plan bypass.

### ENT-010 — Partial fill remainder expires
**Given:** 40/100 filled, deadline reached.
**When:** Cancel remainder.
**Then:** Keep/manage/protect 40; reconcile cancellation and any late extra fills, not mark whole trade canceled.

### ENT-011 — No valid entry reference
**Given:** Source has no valid price/reference and baseline requires one.
**When:** Resolve plan.
**Then:** Reject no-guess; do not substitute arbitrary old last trade.

### ENT-012 — Trigger repeated ticks
**Given:** A once-only breakout condition remains true across many ticks.
**When:** Feed updates.
**Then:** One lifecycle trigger claim; no repeated entry or add without separate policy.

## ORD — Broker order states, effects, deduplication and corrections

### ORD-001 — Accepted acknowledgment
**Given:** Durable selected-account intent exists and broker returns accepted with order ID.
**When:** Consume response.
**Then:** Persist ID/raw state and keep exposure commitment; accepted is not filled/protected.

### ORD-002 — HTTP success business rejection
**Given:** Broker returns 200 with rejected order status.
**When:** Map response.
**Then:** REJECTED not WORKING; release only proven-unused resources after no-fill reconciliation.

### ORD-003 — Timeout possible acceptance
**Given:** Network times out after possible submit.
**When:** Handle error.
**Then:** SUBMISSION_UNKNOWN, retained account/reservation/client ID; no resend or failover.

### ORD-004 — Known failure before send
**Given:** Qualified transport proves no request was dispatched.
**When:** Handle failure.
**Then:** Mark unsent/retryable under same intent and policy; no fabricated broker order; distinction evidenced.

### ORD-005 — Duplicate acknowledgment
**Given:** Same order acknowledgment delivered twice.
**When:** Apply responses.
**Then:** One broker order linkage; no repeated accounting or allocations.

### ORD-006 — Duplicate fill
**Given:** Same authoritative execution ID appears through stream and polling.
**When:** Apply events.
**Then:** Quantity/cash/P&L change once; retain observation lineage.

### ORD-007 — Fill before acknowledgment
**Given:** Execution arrives before original HTTP response.
**When:** Correlate by qualified IDs.
**Then:** Apply fill once to durable intent, protect owned quantity and merge later acknowledgment.

### ORD-008 — Fill after cancel request
**Given:** Cancel pending while 20 additional shares fill.
**When:** Apply fill.
**Then:** Own/protect 20, update remainder/reservations; cancel request not zero-position proof.

### ORD-009 — Not-found eventual consistency
**Given:** Lookup momentarily returns no order after possible acceptance.
**When:** Recovery polls.
**Then:** Preserve UNKNOWN; no resubmit based on one absent lookup.

### ORD-010 — Pagination hides order
**Given:** Relevant order/fill is on later page.
**When:** Reconcile.
**Then:** Follow complete pagination/cursors; no empty/first-page false-finality.

### ORD-011 — Unknown broker enum
**Given:** API introduces a status not in adapter mapping.
**When:** Consume event.
**Then:** Unknown state with conservative commitments and incident; not default canceled/success.

### ORD-012 — Replacement family
**Given:** Old order replaced by new external ID; old may have late fills.
**When:** Reconcile family.
**Then:** Retain all IDs and total cumulative fills; no duplicate exposure or lost old executions.

### ORD-013 — Order correction bust
**Given:** Broker cancels/corrects a prior execution.
**When:** Apply correction.
**Then:** Compensating ledger event, recalculated owned quantity/cash/protection and performance; no destructive history rewrite.

### ORD-014 — Duplicate client key different payload
**Given:** Retry attempts same key with changed quantity or price.
**When:** Validate dispatch.
**Then:** Reject immutable-intent conflict; no reuse of idempotency key for a different trade.

### ORD-015 — Generic retry decorator
**Given:** Simulated timeout would trigger normal HTTP retry middleware.
**When:** Submit intent.
**Then:** Effect operation bypasses blind retry; safe read retries remain independent.

### ORD-016 — Done for session
**Given:** Broker says done_for_day but GTC could reactivate.
**When:** Resource cleanup.
**Then:** Preserve future effect responsibility per actual semantics; not terminal canceled by assumption.

## PRO — Protection, partial fills and replacement safety

### PRO-001 — Partial fill before bracket activation
**Given:** Parent partially filled and native bracket exits inactive until full fill.
**When:** Protection check.
**Then:** Explicit uncovered quantity and deadline; qualified supplemental recipe or cancel/reduce response, never assume protected.

### PRO-002 — Stop confirmed
**Given:** Actual 40-share fill and broker confirms working 40-share stop.
**When:** Update protection.
**Then:** Confirmed quantity/floor=40 at exact level; not requested 100-share size.

### PRO-003 — Stop rejected
**Given:** Newly filled quantity cannot obtain its protective order.
**When:** Incident handler.
**Then:** Halt further entries, safely cancel remainder, reconcile and attempt authorized scoped reduction; retain unresolved exposure if unsuccessful.

### PRO-004 — Desired versus confirmed floor
**Given:** Desired long floor=51, old confirmed=50, replacement pending.
**When:** Render/risk check.
**Then:** Use confirmed protection for risk release; show desired 51 separately, no false guaranteed coverage.

### PRO-005 — Replace rejection
**Given:** Old working stop remains valid; tighter replacement rejected.
**When:** Handle response.
**Then:** Retain old stop, pending discrepancy/deadline and incident as needed; do not mark new floor confirmed.

### PRO-006 — Crossed long floor
**Given:** Committed floor=50.50 and qualified trigger price drops to50.20.
**When:** Trail update.
**Then:** STOP_BREACHED/exit path; never lower floor to50.20 to submit an acceptable order.

### PRO-007 — Unlinked full exits
**Given:** One 100-share allocation has fixed stop100 and proposed independent trail100.
**When:** Reserve close capacity.
**Then:** Reject unsafe duplicate executable close exposure or use qualified exclusive recipe.

### PRO-008 — Cancel-create gap
**Given:** Stop cancellation final before new stop submission fails.
**When:** Fault injection in simulator.
**Then:** Detect uncovered quantity immediately; apply recovery, not assume zero-gap replacement.

### PRO-009 — Partial exit resizes stop
**Given:** 100 owned, 30 confirmed exit fill.
**When:** Reconcile stop quantities.
**Then:** Remaining protection and close capacity reflect70 plus any still-live family semantics; no stop100 oversell.

### PRO-010 — Late incremental entry fill
**Given:** Partial position already protected; additional entry shares fill during stop resize.
**When:** Apply event.
**Then:** Additional shares receive explicit coverage/recovery; not block lifecycle and leave them unmanaged.

### PRO-011 — Native trigger mismatch
**Given:** Custom policy uses bid but native stop uses consolidated trade.
**When:** Qualify recipe.
**Then:** Do not label semantics identical; use separately released compatible recipe or block feature.

### PRO-012 — GTC disappeared
**Given:** Broker cancels protective order for corporate action/session/venue reason.
**When:** Reconciler sees no working stop.
**Then:** Mark protection missing, restore qualified cover/reduce and report; no trust in old database row.

### PRO-013 — Feed outage with native stop
**Given:** Quote feed fails but broker confirms native stop.
**When:** Worker cycle.
**Then:** No invented trail observation/new entry; retain native protection and degraded status.

### PRO-014 — Protection deadline exceeded
**Given:** Fill unprotected beyond released critical threshold.
**When:** Durable timer fires/restarts.
**Then:** Critical incident and exact remediation status; no resetting deadline on restart.

### PRO-015 — Short ceiling monotonicity
**Given:** Qualified short lifecycle has protective ceiling above market.
**When:** Favorable/adverse price updates.
**Then:** Ceiling may tighten downward but never loosen upward; mirrored trigger/risk semantics correct.

## EXT — Exits, targets and close-quantity arbitration

### EXT-001 — Provider full exit
**Given:** Owned matching lifecycle has 50 remaining shares.
**When:** Receive valid full exit.
**Then:** Close at most50 via its original account/writer; other lifecycles unchanged.

### EXT-002 — Stop and exit simultaneous
**Given:** Both stop-fill observation and provider exit arrive for same shares.
**When:** Explore every permitted ordering.
**Then:** One net economic close; no oversell, proper cancel/reconcile family behavior.

### EXT-003 — Timer and owner close
**Given:** Mandatory time exit coincides with authenticated scoped manual close.
**When:** Both handlers run.
**Then:** Join serialized close intent or reconcile existing close, not duplicate full sell.

### EXT-004 — Partial percentage denominator
**Given:** Registered protocol means50% of original filled100, current remainder70.
**When:** First partial event arrives.
**Then:** Sell50 (subject to current owned/close-capacity), not silently35; save denominator and event id.

### EXT-005 — Insufficient remaining for reduction
**Given:** Source asks reduce50 but only20 legitimately remain.
**When:** Apply released partial policy.
**Then:** Reduce at most20 only if capped-reduction semantics are authorized, otherwise reject; never sell other owners' shares.

### EXT-006 — Fractional contract trim
**Given:** One option contract owned and source asks half.
**When:** Size partial close.
**Then:** No fractional option or automatic full close; explicit no-feasible-partial or released alternative with explanation.

### EXT-007 — Multiple target allocations
**Given:** Targets distribute integer quantities across three levels.
**When:** Build target plan for7 shares.
**Then:** Deterministic quantities sum to at most7 and residual rule explicit; rounding cannot oversell.

### EXT-008 — Exit rejected by broker
**Given:** Scoped close receives authoritative rejection.
**When:** Handle result.
**Then:** Position remains owned/open with available protection and incident; never report flat.

### EXT-009 — Exit during entry remainder
**Given:** Some entry fills exist and remainder still working.
**When:** Provider exits all.
**Then:** Cancel/reconcile remaining entry, close actual owned fills safely, catch late entry fills; lifecycle not closed while reopening possible.

### EXT-010 — Closing hedge increases risk
**Given:** Combo has short and protective long option legs.
**When:** Request close long protection only.
**Then:** Evaluate resulting portfolio; block unsafe new uncovered liability despite SELL label.

### EXT-011 — Orphan exit unknown order
**Given:** No known fill but associated submission is UNKNOWN.
**When:** Receive exit.
**Then:** Do not send guessed sell; preserve terminal marker and reconcile possible fill/entry before scoped action.

### EXT-012 — Pause new entries
**Given:** Account/source paused but position remains open.
**When:** Exit/stop/timeout occurs.
**Then:** Manage valid exit/protection; pause flag cannot starve reductions.

## ADD — Staged entry and pyramiding into winners

### ADD-001 — Pyramiding not released
**Given:** Position profitable; code supports adds but live release does not.
**When:** Add trigger arrives.
**Then:** No live add; optional shadow evaluation only, no new authority from feature presence.

### ADD-002 — Valid profitable add
**Given:** Released add trigger, all caps/headroom/coverage pass and no pending exit.
**When:** Evaluate incremental child.
**Then:** Add child intent to same lifecycle with full post-add risk plan and coverage; originalR unchanged.

### ADD-003 — Averaging down disguised
**Given:** Existing position below declared profit reference; provider says add.
**When:** Evaluate winner policy.
**Then:** Reject under winner-only policy; no martingale or fresh lifecycle to bypass prohibition.

### ADD-004 — Profitable stop negative risk
**Given:** Old lot locked apparent profit and proposed large add.
**When:** Compute aggregate risk.
**Then:** Old risk contribution floored at0; no negative-risk credit funding unlimited new exposure; giveback/stress still bind.

### ADD-005 — Synthetic add measures
**Given:** 10 shares at50, confirmed stop50.50, bid52; propose5 at52.
**When:** Compute before costs.
**Then:** Original add risk7.50, current mark-to-stop giveback22.50, gap-to48 loss60; three separate metrics.

### ADD-006 — Add cap across children
**Given:** Seed+two adds have exhausted whole-lifecycle budget.
**When:** Third add signal.
**Then:** Reject/reduce within remaining total; no risk reset or new trade ID loophole.

### ADD-007 — Desired stop frees budget
**Given:** Add would fit only assuming unconfirmed tighter stop.
**When:** Evaluate add.
**Then:** Use confirmed floor; reject until confirmed if all other policy requirements then pass.

### ADD-008 — Add and exit same tick
**Given:** Add profit condition and valid provider exit both true.
**When:** Arbitrate.
**Then:** Exit wins; no new exposure; cancel/reconcile any pending add safely.

### ADD-009 — Add partial fill protection
**Given:** New add fills incrementally while seed remains protected.
**When:** Consume fill/replace events.
**Then:** Preserve old coverage, cover new quantities, conserve close capacity and reservations.

### ADD-010 — Add margin stress fail
**Given:** Add fits planned stop budget but fails stressed maintenance.
**When:** Size.
**Then:** Block add; retain seed management unchanged.

### ADD-011 — Add near time cutoff
**Given:** Profitable signal arrives too near mandatory lifecycle exit.
**When:** Evaluate holding-time gate.
**Then:** Reject uneconomic/ineligible add; do not extend deadline to justify it.

### ADD-012 — Staged entry allocation
**Given:** Original approved total risk split50/30/20 over planned tranches.
**When:** First tranche fills and next trigger never occurs.
**Then:** Use only filled planned exposure; unused reservation expiry explicit; no forced later tranche.

### ADD-013 — Account compounding intraday
**Given:** Mark gains lift current equity but released risk-equity rule caps at session start.
**When:** Size new trade.
**Then:** Do not automatically expand risk on unrealized intraday gains.

## RUN — Runners, trailing policies and time/expiry limits

### RUN-001 — Runner disabled
**Given:** Provider exit arrived and no released derived runner strategy exists.
**When:** Manage remaining profitable position.
**Then:** Close matching remainder; do not ignore exit to pursue more profit.

### RUN-002 — Target plus runner
**Given:** Released plan assigns partial target and protected residual.
**When:** Target fills.
**Then:** Only confirmed remaining quantity becomes runner with its own durable protection/deadline, no quantity creation.

### RUN-003 — No fixed target trend mode
**Given:** Released trend recipe uses trail and time backstop only.
**When:** Position rises without target event.
**Then:** Keep valid remainder protected; no arbitrary forced profit target or removal of stop.

### RUN-004 — Widening ATR
**Given:** ATR increases after long floor has locked higher.
**When:** Recompute trail.
**Then:** New desired floor=max(previous floor,candidate); never loosen to new lower ATR value.

### RUN-005 — Runner overnight unauthorized
**Given:** Position remains profitable at session boundary.
**When:** Time exit fires.
**Then:** Exit/reduce under deadline; no overnight extension because trade winning.

### RUN-006 — Runner option near expiry
**Given:** Long option profitable but exit/exercise cutoff is near.
**When:** Runner gate.
**Then:** Apply exact expiry/collateral/liquidity rule; do not rely only on underlying trend.

### RUN-007 — Missing ATR input
**Given:** Runner requires ATR but current feature unavailable.
**When:** Update.
**Then:** Frozen compatible baseline fallback or no nonurgent update, preserving confirmed stop; no invented volatility.

### RUN-008 — Bad tick high-water
**Given:** Feed gives objectively invalid high tick under predeclared validation.
**When:** Trail calculation.
**Then:** Reject anomalous observation with provenance; do not ratchet on bad data or discard real adverse ticks opportunistically.

### RUN-009 — Restart high-water
**Given:** Runner persistsH, floor and activation before crash.
**When:** Restart/reconcile.
**Then:** Restore values and policy version; never reset protection or activation to current price.

### RUN-010 — Derived runner attribution
**Given:** Owner separately releases holding past provider exit.
**When:** Provider exits but derived runner remains.
**Then:** Attribute to derived strategy/profile, not faithful-copy P&L; all unchanged hard caps and deadlines apply.

### RUN-011 — Tiny remainder economics
**Given:** Remainder too small for meaningful trim or fee/minimum constraints.
**When:** Runner policy chooses action.
**Then:** Explicit dust/whole-unit decision, no impossible fractional order or hidden extra purchase.

### RUN-012 — Giveback threshold
**Given:** Current equity-to-stop potential loss exceeds released runner giveback cap.
**When:** Reprice.
**Then:** Tighten/reduce/exit per rule with confirmed effects; do not call locked entry profit zero risk.

## OWN — Overlapping ownership and manual intervention

### OWN-001 — Two analysts same direction
**Given:** A owns100 and B owns50 same stock in one account.
**When:** B exits.
**Then:** At most50 sold forB; A's100 basis/stop/deadline retained.

### OWN-002 — Same-symbol new entry overwrite
**Given:** A open; B new entry arrives with new stop.
**When:** Create allocation.
**Then:** B gets distinct lifecycle and protection; no overwrite ofA owner or plan.

### OWN-003 — Opposing netted positions
**Given:** A long100; B proposes short50 in same net account without released netting policy.
**When:** Route.
**Then:** Reject conflict; sell50 cannot be labeled independent hedge while consumingA holdings.

### OWN-004 — Hedging-mode ticket close
**Given:** Qualified account has distinct long/short tickets.
**When:** Close one allocation.
**Then:** Exact ticket/account/side selected; gross and net risk both conserved.

### OWN-005 — Manual shares reserved
**Given:** Account contains80 manual shares and20 bot-owned.
**When:** Bot full exit.
**Then:** At most20 bot shares closed; no account-wide close100.

### OWN-006 — Manual sell consumes inventory
**Given:** Owner manually sells shares overlapping bot attribution.
**When:** Reconcile broker quantity.
**Then:** Freeze conflicting commands, repair using declared ownership procedure, resize obsolete exits; no automatic buyback.

### OWN-007 — Manual stop cancel
**Given:** Owner cancels a bot protective order outside app.
**When:** Reconciler detects it.
**Then:** Apply owner-intervention/protection policy with visible incident; do not silently ignore or endlessly fight user.

### OWN-008 — Manual unknown new position
**Given:** Broker reveals same-symbol quantity with no bot fills.
**When:** Reconcile.
**Then:** Classify unmanaged/unattributed; do not auto-adopt into latest analyst lifecycle.

### OWN-009 — Tax lot distinction
**Given:** Internal allocation attributes shares to analystB.
**When:** Generate report/close.
**Then:** Do not claim that virtual allocation selected broker tax lot; actual tax-lot instruction separate.

### OWN-010 — Fractional split difference
**Given:** Corporate action creates fractional shares/cash-in-lieu.
**When:** Reconcile allocation sums.
**Then:** Apply explicit transformation and cash events; no false unexplained trade or deletion of remainder.

## EVT — Corporate actions, derivatives and market exceptions

### EVT-001 — Gap through stop
**Given:** Price gaps well below sell-stop trigger.
**When:** Stop executes.
**Then:** Record actual worse fill and realized loss; no backtest fill at stop or guaranteed cap claim.

### EVT-002 — Halted market exit
**Given:** Position held when exchange halts and exit deadline occurs.
**When:** Attempt qualified reduction.
**Then:** Retain available protection, explicit unable-to-exit incident, no fabricated flatten; manage reopen.

### EVT-003 — Stock split
**Given:** 2-for-1 split occurs with working orders.
**When:** Corporate-action reconciliation.
**Then:** Quantities double and economically corresponding basis/floors adjust; verify broker order changes without phantom P&L.

### EVT-004 — Reverse split dust
**Given:** Reverse split leaves fractional economic interest.
**When:** Apply transformation.
**Then:** Track whole quantity and cash-in-lieu separately; no rounding to create shares.

### EVT-005 — Merger symbol successor
**Given:** Old symbol changes into cash plus successor securities.
**When:** Instrument lifecycle event.
**Then:** Exact deliverable/cash allocation mapping; freeze ambiguous orders and no ticker-only substitution.

### EVT-006 — Early short-option assignment
**Given:** Short leg assigned before expected expiry.
**When:** Broker event.
**Then:** Post option closure and underlying/cash obligation, reprice actual portfolio, protect/reduce authorized exposure.

### EVT-007 — Long option automatic exercise
**Given:** ITM long remains at cutoff without approved funding plan.
**When:** Expiration workflow.
**Then:** Preempt by timely authorized policy or handle actual exercised underlying; never assume expiration removes all obligations.

### EVT-008 — Calendar spread broken state
**Given:** Near leg assigned while farther hedge remains.
**When:** Risk update.
**Then:** Dedicated resultant-risk/collateral handling; not intact-vertical max-loss formula.

### EVT-009 — Futures delivery deadline
**Given:** Contract nears first notice/last trade under no-delivery policy.
**When:** Durable cutoff.
**Then:** Block entry and execute allowed close/roll plan; failure remains critical, not silent delivery exposure.

### EVT-010 — Borrow recall
**Given:** Broker recalls stock borrow.
**When:** Receive recall/restriction.
**Then:** Halt new shorts, preserve authorized cover workflow/cost attribution; no indefinite assumption borrow remains.

### EVT-011 — Derivative funding debit
**Given:** Funding/variation margin reduces collateral materially.
**When:** Post event.
**Then:** Adjust cash/headroom and halt/reduce under released limits; no omission from P&L/risk.

### EVT-012 — Broker forced liquidation
**Given:** Broker liquidates part/all positions.
**When:** Reconcile fill.
**Then:** Update ownership, cancel/reconcile conflicting bot orders and latch incident; do not reopen to restore old target.

## OPS — Failure, restart, coordination and operational health

### OPS-001 — Crash after reserve before send
**Given:** Intent/outbox committed, dispatch not begun.
**When:** Restart.
**Then:** Recover same intent, verify no possible effect, revalidate TTL before safe dispatch; no duplicate reservation.

### OPS-002 — Crash after possible send
**Given:** Broker may accept before response persistence.
**When:** Restart.
**Then:** UNKNOWN reconciliation on original identity/client key; no fresh create or other-account fallback.

### OPS-003 — Crash after fill before protection
**Given:** Fill exists externally but local protection commit absent.
**When:** Restart.
**Then:** Reconcile fill, restore ownership, start/continue overdue protection incident and qualified repair.

### OPS-004 — Crash after exit fill
**Given:** Position flat externally but local exit state stale.
**When:** Restart.
**Then:** Deduplicate/readback fill, release only final resources, cancel/reconcile remaining entry/protection families.

### OPS-005 — Dual workers
**Given:** Two processes try same physical-account effect stream.
**When:** Compete for lease and dispatch.
**Then:** Only fenced owner writes; stale worker cannot send after lease loss.

### OPS-006 — SQLite busy
**Given:** Another short transaction holds write lock during global reserve.
**When:** Reserve attempt.
**Then:** Bounded retry of entire safe transaction or explicit failure, no stale read-then-write overspend.

### OPS-007 — Disk full
**Given:** Ledger cannot persist intent/response.
**When:** Event arrives.
**Then:** No new effect without durable intent; preserve unresolved state/incident and recover real broker facts after service restored.

### OPS-008 — Partial broker snapshot
**Given:** Positions endpoint errors halfway through pagination.
**When:** Reconciler runs.
**Then:** Snapshot incomplete/UNKNOWN; never delete missing allocations or label account flat.

### OPS-009 — Feed outage
**Given:** All quote reads fail but worker loop still completes.
**When:** Health update.
**Then:** Quote readiness false with age/error evidence; HTTP liveness may remain true; no green protected-trading claim.

### OPS-010 — Email outage
**Given:** Core ledger, broker and native protection remain healthy.
**When:** Critical email send fails.
**Then:** Record notification failure and alternate existing incident evidence; do not disable valid exit logic.

### OPS-011 — Optional research failure
**Given:** Kelly training dependency errors.
**When:** Existing lifecycle needs exit.
**Then:** Frozen released baseline and exit manager continue; optional analytics cannot block risk reduction.

### OPS-012 — Rate-limit pressure
**Given:** Entry burst threatens order/lookup quotas.
**When:** Scheduling.
**Then:** Reserve exit/protection/reconciliation capacity, backpressure new entries; no infinite retry storm.

### OPS-013 — Expired credential
**Given:** Broker token expires with managed exposure.
**When:** Refresh/readback.
**Then:** Qualified refresh to same account or degraded incident/protection; no switch to first accessible account.

### OPS-014 — Restore older backup
**Given:** Broker effects happened after backup snapshot.
**When:** Recover server.
**Then:** Reconcile all external post-backup effects and fencing before new entry; never replay old outbox blindly.

### OPS-015 — Failed migration
**Given:** New schema migration aborts mid-upgrade.
**When:** Deployment.
**Then:** Compatible rollback/blocked startup; no mixed-version writer corrupting ownership/reservations.

### OPS-016 — UI unavailable
**Given:** Browser/server UI crashes while worker and state healthy.
**When:** Timer/stop event.
**Then:** Financial management continues independently; incident status later visible.

### OPS-017 — Lost source connection
**Given:** No new provider alerts received, open positions remain.
**When:** Source health degrades.
**Then:** Block source-dependent new entries; stops/time exits/reconciliation continue.

### OPS-018 — Overdue deadline restart
**Given:** App restarts after mandatory exit time.
**When:** Recovery gate completes.
**Then:** Process overdue exit immediately as feasible, not reschedule next day; preserve inability-to-exit incident.

## LOS — Loss controls, P&L and calendar integrity

### LOS-001 — Daily threshold exact
**Given:** Net realized/unrealized/cost loss equals released daily halt threshold.
**When:** Revalue account.
**Then:** Latch policy-defined breach at exact inclusive boundary; cancel safe unfilled entries and preserve exits.

### LOS-002 — Weekly halt restart
**Given:** Weekly halt previously latched.
**When:** Restart or calendar day changes.
**Then:** Halt persists until released reset procedure; no accidental daily reset clears weekly control.

### LOS-003 — Fees cross threshold
**Given:** Price P&L alone under loss cap, fees/interest push it over.
**When:** Accounting update.
**Then:** Include costs and latch halt; no win-rate-only bypass.

### LOS-004 — Wrong timezone midnight
**Given:** UTC date changes while New York trading session day does not.
**When:** Scheduler/accounting ticks.
**Then:** Use released session calendar/timezone for boundaries; no artificial loss reset.

### LOS-005 — Daylight saving
**Given:** Clock changes across DST boundary.
**When:** Compute next deadlines.
**Then:** Correct UTC mapping without duplicate/missing scheduled actions.

### LOS-006 — Profits not free risk
**Given:** Open profitable trade has stop above entry but material giveback/gap exposure.
**When:** Aggregate risk.
**Then:** Separate original-risk, current-giveback and stress; do not mark portfolio risk zero.

### LOS-007 — Currency P&L
**Given:** Position currency gains but account-currency FX moves adversely.
**When:** Mark equity/loss.
**Then:** Qualified account-currency conversion affects risk and performance, with timestamps/costs.

### LOS-008 — Withdrawal not loss
**Given:** Account equity falls solely from external withdrawal.
**When:** Compute trading performance.
**Then:** Cashflow-adjusted P&L excludes withdrawal while available capital declines.

### LOS-009 — Late fee correction
**Given:** Broker posts fee after economic position close.
**When:** Reconcile accounting.
**Then:** Amend lifecycle net result and relevant evidence/model dataset by version; not new trade.

## UI — API, browser, evidence and policy configuration

### UI-001 — Sizing drilldown
**Given:** Candidate size constrained by risk, cash and source limit.
**When:** Open signal/position details.
**Then:** Show all inputs/caps/as-of times and exact selected size; UI shares backend calculation.

### UI-002 — Unknown versus zero
**Given:** Broker risk value unavailable.
**When:** Render dashboard.
**Then:** Display UNKNOWN/stale, not zero remaining risk or fake safe account.

### UI-003 — Desired stop UI
**Given:** Pending stop replace would tighten floor.
**When:** Open protection panel.
**Then:** Show both desired and confirmed levels/quantities, pending family and deadline.

### UI-004 — Source XSS
**Given:** Signal symbol/body contains HTML/script payload.
**When:** Render every relevant UI surface.
**Then:** Text-only safe rendering, no script execution; CSP defense not sole sanitizer.

### UI-005 — CSV formula injection
**Given:** Source-controlled export value begins with spreadsheet formula prefix.
**When:** Export report.
**Then:** Safe text encoding/escaping policy; no unintended formula execution in consumer file.

### UI-006 — API direct bypass
**Given:** UI disables risky control but caller submits direct API request.
**When:** Backend validation.
**Then:** Same effective permission/hard-cap/version checks reject action; no UI-only enforcement.

### UI-007 — Stale config edit
**Given:** Two browser tabs edit budgets with old version.
**When:** Save second tab.
**Then:** Conflict/revalidation, no lost update or unnoticed raised limit.

### UI-008 — Simulation toggle live
**Given:** User/client mutates historical/paper mode field.
**When:** Call execution endpoint.
**Then:** Server authorization/environment isolation prevents live route escalation.

### UI-009 — Policy publish versus activate
**Given:** New Kelly/runner candidate is saved.
**When:** GUI refresh/deployment.
**Then:** Remains candidate/shadow until scoped release; save is not live activation.

### UI-010 — Source disable journey
**Given:** User disables provider while positions remain.
**When:** Browser action then exit event.
**Then:** New entries disabled, existing management retained, reason and scope visible.

### UI-011 — Account drain journey
**Given:** User chooses drain versus disconnect.
**When:** Pending/open lifecycles progress.
**Then:** Drain accepts no new exposure but retains readback/protection/exits until final safe detach.

### UI-012 — Mobile desktop parity
**Given:** Same authenticated role uses phone and desktop.
**When:** Execute all registered journeys.
**Then:** No missing confirmations/scope/details due to viewport; backend results identical.

### UI-013 — Evidence truthful status
**Given:** Only helper/simulator tests passed, no broker paper/live test.
**When:** Render release/report.
**Then:** Exact tested tier and NOT_RUN external scope; no claim actual account certified.

### UI-014 — Candidate route trace
**Given:** Three accounts considered and one selected.
**When:** View decision.
**Then:** Exact included/excluded identities and reasons; alternate candidates not shown as executed orders.

## TST — Verification integrity, mutations and release boundaries

### TST-001 — Unbound contract harness
**Given:** No callable application adapter configured.
**When:** Run contract test runner.
**Then:** Nonzero error with NOT_BOUND, no fake passes or skipped-green result.

### TST-002 — Missing actual result
**Given:** Bound adapter omits quantity/selection decision required by case.
**When:** Run case.
**Then:** Test fails, not default expected value from catalog.

### TST-003 — Dedup guard mutation
**Given:** Remove durable source claim in disposable code.
**When:** Run duplicate/cross-post/concurrency tests.
**Then:** At least one mandatory test fails; mutant killed.

### TST-004 — Reservation guard mutation
**Given:** Remove owner-wide atomic reservation.
**When:** Run concurrent multi-account cases.
**Then:** Oversubscription detected by independent ledger/invariant; mutant killed.

### TST-005 — Close scope mutation
**Given:** Replace owned close with ticker-wide close.
**When:** Run overlap/manual cases.
**Then:** Detect wrong quantities/ownership and fail, even if final account flat.

### TST-006 — Protection truth mutation
**Given:** Mark desired stop as confirmed immediately.
**When:** Run rejected/pending replace fixtures.
**Then:** Fail risk-release/UI assertions; cannot pass from local config alone.

### TST-007 — Boundary mutation
**Given:** Change inclusive loss threshold or floor rounding.
**When:** Run minus/equal/plus exact-unit vectors.
**Then:** Boundary mismatch fails reliably.

### TST-008 — Partial history claim
**Given:** Source/backtest dataset has known gaps.
**When:** Produce learning/release evidence.
**Then:** Missing coverage explicit; no exhaustive-history or proven-return claim.

### TST-009 — Paper-to-live auto-promotion
**Given:** All paper tests pass.
**When:** Release controller runs.
**Then:** No new live authorization, funds transfer, order, cancellation or activated account.

### TST-010 — Unsupported extension
**Given:** Core equity suite passes while crypto derivative adapter absent.
**When:** Scope report.
**Then:** Equity evidence scoped; extension remains NOT_IMPLEMENTED/BLOCKED, not overall complete.

### TST-011 — All named scenarios required
**Given:** Several applicable named cases lack executable mappings.
**When:** Release-evidence validation.
**Then:** Nonzero incomplete gate; untested cases remain NOT_RUN, never waived by headline test count.

### TST-012 — Competing fault sequence
**Given:** Freeze input domains then enumerate every defined critical event ordering.
**When:** Test generation and application execution.
**Then:** Count complete declared vectors, check invariants after every event, retain rejected/unreachable proof and actual evidence.

