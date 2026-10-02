"""Build the acceptance catalog. This does NOT execute or test the trading application."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent
GROUPS = []
def group(prefix, title, text):
    GROUPS.append((prefix, title, text.strip()))

group('ING','Intake, authority, identity and durability', '''
Valid authorized webhook || An approved channel and sender map to one known provider; valid schema and active replay window. || Receive one live-format message in an isolated paper fixture. || Persist envelope before acknowledgment; create one interpretation job; retain original event ID and mode; no broker call at intake.
Invalid transport signature || Payload contains a plausible trade but its signature fails. || Receive the payload. || Reject before parsing/dispatch; zero financial intent/reservation/broker calls; retain sanitized security evidence.
Authorized transport unauthorized sender || A valid transport signature is present but sender/channel is not allowed. || Receive BUY AAPL. || Reject source authorization; do not inherit permissions from transport credential.
Unknown provider || Channel is connected but has no registered provider identity. || Receive a syntactically valid entry. || Persist UNKNOWN_PROVIDER and onboarding draft; zero entry effect in live; no assumed analyst edge.
Known provider unknown analyst || Provider is approved; source author is not mapped. || Receive a trade-shaped message. || Quarantine analyst-dependent action; preserve provider/origin identity and do not copy another analyst's profile.
Disabled provider enabled child || Provider entry flag is false and analyst preference is true. || Receive entry then valid exit for existing owned lifecycle. || Block entry; keep matching scoped exit/protection active; child cannot override parent disable.
Forged forwarded origin || Authorized relay forwards content claiming a different analyst with no verified lineage. || Receive a forwarded alert. || Record transport and claimed origin separately; no inferred trading permission from display-name text.
Oversized malformed body || Body exceeds limits or is not expected object/type. || Receive payload. || Bounded sanitized rejection; no 500 from type assumptions; no queue/resource exhaustion or financial effects.
Persistence unavailable || The durable envelope insert cannot commit. || Receive an otherwise valid source message. || Do not acknowledge durable acceptance or dispatch; emit operational fault and permit documented upstream recovery.
Source replay after crash || Envelope already committed; process crashed before intake acknowledgment. || Source retries same ID/revision. || Join existing job/result; exactly one canonical observation and no duplicate trade.
Cross-post same origin || Two channels deliver same original provider event with established lineage. || Both arrive concurrently. || Collapse to one canonical opportunity while retaining both observations; one selected account maximum.
Similar but distinct signals || Two real lifecycle IDs share ticker, side and text. || Receive both. || Retain both distinct opportunities; apply concurrent aggregate caps rather than ticker-only deduplication.
Historical backfill || Import job reads last month's approved channel history. || A message looks fresh after normalization or UI mode edit. || Preserve historical mode; no live dispatch; source event time/provenance cannot be rewritten to authorize trading.
Cursor gap reconnect || Transport disconnects while several source revisions occur. || Reconnect with saved cursor. || Recover permitted gap with durable pagination and revision lineage; do not label history complete until established; stale entries stay non-live.
Prompt injection || Message instructs agent to ignore limits, run code or expose secrets. || Parser/GUI/research consumes message. || Treat text as data; no executable instructions, config changes, leaked secrets or trading authority.
''')

group('PAR','Full-message interpretation and compound instructions', '''
Negated buy || Source text says DO NOT BUY AAPL 10. || Parse full message. || NONACTIONABLE/negated result; zero entry, no substring-based BUY.
Historical recap || Source says Yesterday we bought AAPL and closed it. || Parse current receipt. || Historical commentary, not a fresh entry or exit.
Unmet conditional || Source says Buy only above 100 with a confirmed crossing. || Price remains 99. || Durable bounded WAIT_TRIGGER; no immediate order; exact trigger semantics recorded.
Option-flow observation || Source reports unusual call volume without recommending a trade. || Parse flow alert. || OBSERVATION only; no BUY_TO_OPEN inference.
Buy put direction || Exact long-put contract and opening intent are given. || Normalize action. || BUY_TO_OPEN option with bearish underlying exposure; not stock short or sell order.
Ambiguous sell option || Source says sell puts without stating owned closure versus opening sale. || Normalize action. || PARSE_AMBIGUOUS; no short-option exposure or guessed close.
Independent mixed-assets || Message contains valid stock and spot-crypto instructions with separate fields. || Parse and route. || Two child instructions with exact asset identities and independent outcomes; each has at most one eligible destination and shared owner caps.
Valid and invalid independent child || Two independent entries exist; one lacks exact option expiry. || Parse and validate. || Block unresolved child; valid child may proceed subject to all caps; parent report lists both outcomes.
Ambiguous shared stop || Message lists several instruments and one unlabeled stop. || Parse field ownership. || Block affected instructions; never apply the stop arbitrarily to every instrument.
Atomic vertical spread || Complete two-leg same-expiry vertical is supplied. || Normalize and execute in paper fixture. || One combo group and payoff/risk plan; one qualified combo intent, not two unrelated orders.
Unsupported atomic combo || Complete spread is given but adapter only supports separate legs. || Capability check. || Unsupported recipe; no speculative leg-in or naked interim exposure.
Either-or triggers || Two alternative entries in one exclusive group trigger simultaneously. || Competing workers claim group. || One exclusive opportunity token wins; other terminates/holds per policy; never open both.
Roll dependent actions || Old option remains open and new roll leg is specified. || Process roll. || Validate linked group and temporary capital; unconfirmed old close does not authorize an unsafe new naked position.
Reversal || Account owns a long; signal explicitly requests reverse to short under released policy. || Process instruction. || Close/reconcile old side first, then independently authorize new side; unknown close blocks reversal entry.
Multileg missing ratio || Butterfly text omits one leg ratio. || Resolve combo. || Reject incomplete group; no assumed 1:2:1 from a strategy label alone.
Provider quantity versus copier size || Analyst reports bought 500 shares. || Produce trade plan. || Retain analyst quantity separately; user's quantity derives from released risk/capital policy and explicit source ceilings only.
Half-size ambiguous basis || Source says add half or sell half without a registered denominator. || Parse update. || Review/incident as appropriate; no invented 50% of current or original inventory.
Price-unit conflict || Structured stop is option premium and text describes underlying stop. || Resolve protection. || Domain conflict blocks plan; no mixing dollar values across instruments.
Attachment-only alert || Image/audio contains trade details with no qualified parsing path. || Receive attachment. || Unsupported-media/observation state; no hallucinated contract or quantity.
Locale/time ambiguity || Date 03/04 and decimal comma are not disambiguated by provider grammar. || Parse actionable fields. || Reject ambiguous date/number; do not choose locale from server defaults.
''')

group('REV','Revisions, lifecycle matching and temporal order', '''
Edit before dispatch || Entry is interpreted but no effect can have occurred. || Provider changes valid entry price. || Version interpretation, invalidate old plan/reservation as safe, revalidate once; not two entries.
Edit after acceptance || Entry has possible or confirmed broker acceptance. || Provider changes entry quantity. || Same-lifecycle amendment with revalidation; no new independent order or hidden limit increase.
Delete source message || Entry is open and source message is deleted. || Receive tombstone. || Record deletion; retain management; no invented exit unless protocol explicitly defines it.
Exit before entry || Exit references lifecycle L before its OPEN arrives. || Receive late OPEN L. || L remains terminal; no new buy; reconcile any pending effect.
Old exit new same ticker || L1 is closed and L2 owns same symbol. || Delayed EXIT L1 arrives. || No sale of L2; record already-terminal/orphan match with exact lifecycle evidence.
Duplicate partial exit || Sell half instruction with fixed event denominator already filled. || Replay same source revision. || No second halving or order; return existing result.
Delayed exit beyond entry age || Owned position remains open; valid exit is older than entry freshness limit. || Receive exit. || Resolve current owned remainder and exit policy; do not reject solely using entry TTL.
Exit without fill || Entry was definitively rejected with zero fills. || Matching exit arrives. || Zero sell; terminal/audit update only, unless pending entry uncertainty still requires reconciliation.
Ambiguous all-out || Two same-provider lifecycles exist without source reference. || Receive all out lacking protocol scope. || Urgent semantic incident; keep protection; no guessed ticker-wide close.
Scoped close-all || Provider protocol explicitly means its strategy book only. || Receive close all. || Close only matching owned allocations under coordinated writer; manual and other-provider holdings unchanged.
Reply-parent missing || Exit/update relies on unavailable parent context. || Receive reply. || No invented instrument/lifecycle; collect missing authorized context or hold with incident and existing protection.
Future timestamp || Source time is beyond permitted clock-skew tolerance. || Validate entry age. || Flag clock/source anomaly; no accidental negative-age bypass.
''')

group('INS','Instrument metadata, units and product identity', '''
Exact stock resolution || Stock ticker matches a unique tradable instrument for venue/account. || Resolve metadata. || Persist exact broker ID, currency, lot and tick; no default CRYPTO class.
Ambiguous symbol || Same display code matches multiple exchanges/products. || Resolve entry. || INSTRUMENT_UNRESOLVED; no first-match execution.
Option missing expiry || Option text has underlying, strike and call but no expiry. || Resolve contract. || Reject; no nearest-Friday/nearest-liquid substitution.
Adjusted option deliverable || Contract has nonstandard multiplier/deliverable. || Size and protect. || Use authoritative actual units/deliverable; all cash/risk calculations reflect adjustment.
Expired option || Exact contract expiry has passed its tradable deadline. || Receive entry. || Reject stale/expired instrument; no automatic roll.
Continuous futures ticker || Source names a continuous root without dated contract semantics. || Route order. || Observation/unresolved until exact authorized contract chosen; no guessed front month.
Futures negative price || Instrument profile explicitly supports signed prices and exact tick value. || Validate negative trade price. || Product-correct signed price accepted only within valid profile; quantities remain nonnegative with separate side; stock profile would reject.
FX lot conversion || Source quantity is 0.1 standard lot with declared 100000-unit lot. || Calculate exposure. || 10000 base units, quote/account currency conversion and swap; not 0.1 shares.
Stale FX conversion || Non-USD risk needs account-currency conversion that is stale. || Size entry. || Block mandatory conversion-dependent exposure; not assume parity or last-known unlimited freshness.
Crypto fee in base || Buy 0.01 units and fee removes base inventory. || Apply fill and protection. || Owned quantity is net delivered base units; no stop for more than actual inventory.
Crypto minimum/dust || Permitted risk yields quantity below lot/minimum notional. || Finalize order. || Skip new entry; existing untradeable dust remains explicit, not falsely closed or rounded up.
Inverse derivative || Inverse contract has coin collateral and reciprocal payout formula. || Size/stress. || Dedicated inverse model; reject linear-share fallback.
Leveraged inverse ETF || Source signal references an inverse leveraged fund. || Aggregate underlying exposure. || Use actual economic direction/leverage and scenarios, not a positive one-to-one ticker exposure.
Metadata change during planning || Tick/multiplier or instrument trading status version changes. || Dispatch revalidation. || Invalidate stale plan and recompute or reject before effect.
''')

group('ROU','Account identity, selection, permissions and binding', '''
Three eligible accounts || Same source matches three approved account alternatives. || Allocate one canonical entry. || Select one physical account; exactly one entry intent; persist reasons for the other two not selected.
Duplicate bindings || Direct and relay connections expose same immutable physical account. || Inventory/rank accounts. || Count equity once; one candidate identity and approved binding; no duplicate trade.
No eligible account || Product or policy unsupported by every approved destination. || Allocate. || UNROUTABLE/shadow; no auto-activation of a new account or asset substitution.
Better funded alternate || Preferred candidate cannot meet minimum lot; another approved account can. || Compute feasibility/rank. || Exclude infeasible account, choose eligible alternative only if released preference permits; one selection.
Wrong gateway default || Multi-account gateway default differs from selected account. || Submit paper fixture. || Exact selected broker account ID appears on every leg/operation and readback; default cannot route it elsewhere.
Paper/live same alias || Paper and live accounts share display name. || Route paper instruction. || Resolve immutable environment-specific identity; zero live credential or endpoint access.
Account liquidation-only || Account reports close-only restriction. || Receive entry and owned exit. || Block entry; allow supported scoped risk reduction; explain restriction.
Capability unknown || Method exists but exact session/stop recipe has no evidence. || Route. || UNVERIFIED blocks new exposure; no method-presence capability inference.
Account changes mid-plan || Binding/account config version changes after sizing. || Attempt reserve/dispatch. || CAS/version check invalidates plan; no use of stale account identity.
Exit after route change || Lifecycle entered on A; current preference now favors B. || Receive exit. || Exit A's owned allocation; zero order to B.
Timeout then alternate available || A's order may be accepted; B is healthy and funded. || Router attempts recovery. || Keep A intent UNKNOWN and reservation; no B fallback or duplicate economic position.
Account removal with exposure || Account has open allocation, pending order or UNKNOWN effect. || User attempts delete/disconnect. || Block destructive action; offer pause/drain; preserve credentials needed for management where permitted.
Credential rotation same account || New binding credentials verify same immutable account. || Replace connection. || Preserve lifecycle account identity and single writer; reconcile before resumed dispatch.
No alternate live authority || Initial approved live route unavailable; another broker login exists. || Attempt route fallback. || Do not activate it merely because connected; scoped blocker, independent paper work may continue.
''')

group('CAP','Hierarchical budgets, shared backing and reservations', '''
Shared final dollars || Two entry workers each need $10 risk, only $15 remains. || Reserve concurrently. || Total commitments never exceed $15; one or smaller feasible quantities win by policy; losing transaction recomputes.
Owner cap across accounts || Each account locally has capacity but owner aggregate would breach. || Concurrent entries on separate accounts. || Global owner reservation blocks/reduces aggregate; account workers cannot bypass owner cap.
Portfolio backing double count || Two portfolios claim same $6000 account equity in full. || Validate budgets. || Reject overallocated backing; display one real $6000 resource, not $12000.
Sleeve hard reservation || Sleeve A capacity is hard reserved and unused. || Sleeve B requests it. || Deny borrowing absent released sharing policy; no automatic theft of reserved capital.
Soft reservation expiry || Forecast signal reserve is soft, bounded and expired with no order effect. || Allocator ticks. || Release unused capacity with evidence; do not reserve indefinitely.
Unknown order reservation TTL || Order may have been accepted; reservation wall-clock TTL expires. || Cleanup runs. || Do not release committed resources; reconcile until authoritative outcome.
Reflected broker commitment || Broker buying power already deducts order X's reserved amount. || Normalize snapshot and local ledger. || Deduct X only once using explicit reflected membership; avoid false double subtraction.
Unreflected local intent || Local intent reserved but broker snapshot precedes it. || Compute available capacity. || Subtract local unreflected commitment; no overspend from stale broker BP.
Partial fill accounting || 100-share intent reserved, 40 filled and 60 working. || Update ledger. || Transfer 40 into owned exposure, keep 60 pending commitments, no resource creation/release for whole order.
Cancel request || Working unfilled order reserves risk/cash. || Receive HTTP cancellation acceptance only. || Retain resources until final cancellation and late-fill reconciliation.
Final reject no fill || Authoritative rejected state plus complete no-fill evidence. || Reconcile. || Release proven-unused commitments once; no duplicate release on retry.
Withdrawal cash flow || Owner withdraws funded capital intraday. || Revalue budgets. || Reduce backing/availability, preserve trading P&L and halt latches, block excess new exposure.
Deposit loss halt || Daily halt is latched, deposit raises equity. || Process deposit. || No automatic unhalt or erased trading loss; adjust external flow only.
Manual collateral || Manual positions encumber account margin. || Compute automation capacity. || Reserve their actual whole-account effects; no free collateral assumption or unauthorized sale.
Same-underlying multiple assets || Shares, calls and correlated futures reference same underlying. || Admit another trade. || Enforce consolidated underlying and stress limits across asset labels.
Credit proceeds liability || Short-credit combo receives premium. || Update cash/risk. || Liability/collateral remain; credit does not become unlimited free risk budget.
Minimum lot cannot fit || Budget permits 0.7 whole-share equivalent. || Final sizing. || Zero-unit skip; never floor to one or raise limit to create activity.
Multiple binding constraints || Risk and cash yield same limiting size. || Explain allocation. || Report both tied binding caps and exact quantity, not arbitrary one-only explanation.
''')

group('SIZ','Financial validation and exact sizing', '''
Reference stock sizing || Synthetic equity=6000, risk cap=15, entry=50, stop=49, per-share costs=.05; other caps slack. || Size whole shares. || 14 shares, notional 700, planned risk 14.70; exact Decimal result.
Budget below first share || Same fixture risk budget=.50. || Size. || Zero shares and RISK_LIMITED, no minimum-one override.
Invalid numeric quantity || Quantity is bool, NaN, Infinity, negative or unsupported zero. || Validate entry. || Reject before reservation/broker effect; product-aware strict type handling.
Wrong-side long stop || Long entry=50 and initial stop=51 without compatible trigger semantics. || Validate new plan. || Invalid/crossed protection rejection; no risk calculated as negative profit.
Missing versus invalid stop || Source omits stop in case A; supplies malformed stop in B. || Resolve fallback. || Only A may use compatible released fallback; B rejected/incident, not silently repaired as missing.
Stop tick rounding || Required long floor falls between supported ticks. || Normalize stop. || Select only permitted rounding preserving floor/risk; if unplaceable reject/reduce, never loosen covertly.
Tight stop huge position || Stop distance near zero and very large Kelly bound. || Size. || Notional, costs, liquidity, stress and margin still cap size; no unbounded exposure.
Option full debit || Risk budget=15, option premium=2, multiplier=100, before fees. || Size long contracts. || Zero contracts because one risks at least 200 debit; no stock-like calculation or stop-only bypass.
Nonlinear margin tier || Size crossing broker concentration tier raises margin per unit. || Search maximum feasible q. || Recompute full post-trade resource function at each candidate boundary; independent-ratio bound alone insufficient.
Fee currency || Costs charged in different currency than risk equity. || Calculate q. || Convert with valid rate and conservative rounding; no omission or unit addition.
Negative cost input || Config or feed provides a negative fee/slippage allowance without explicit rebate semantics. || Validate plan. || Reject malformed economics; qualified rebates separately bounded, not negative loss budget.
Zero-risk misleading break-even || Stop equals entry. || Recalculate resources. || Costs, mark giveback and stress remain; no infinite quantity or zero whole-account risk.
Cap decreases || Identical state, lower one hard monetary cap by one cent. || Recompute. || Permitted quantity cannot increase; independent reference/property test catches nonmonotonic behavior.
Worse partial fill || Actual fill creates larger risk than pretrade allowed. || Apply fill. || Record actual price; cancel remainder safely, protect and follow authorized reduction, never enlarge cap after the fact.
''')

group('KEL','Kelly, profile evidence and adaptive allocation', '''
Binary Kelly arithmetic || Synthetic p=.55 and b=1.4 with outcomes +bR/-1R only. || Calculate full Kelly. || f=.228571428571... in risk-fraction convention; arithmetic/unit test independent of application.
Fractional Kelly hard cap || Quarter Kelly from prior fixture exceeds released .0025 lifecycle fraction. || Calculate final risk budget. || .0025 ceiling binds; $6000 fixture gives at most $15 risk, no live config change.
Variable outcomes || Empirical outcomes include -3R, -.5R, 0, +1R and +5R. || Fit sizing. || Optimize full cost-adjusted log distribution; average win/loss shortcut not claimed exact.
Insolvent scenario || Candidate f makes 1+fX nonpositive for stress X. || Evaluate objective. || Candidate infeasible, not log clipped to attractive finite value.
No history || New analyst/strategy/asset profile has zero observations. || Request Kelly. || INSUFFICIENT_EVIDENCE; no fabricated p; new profile shadow/paper, existing released baseline treated separately.
Thin child profile || Five highly correlated trades in narrow cell, broader compatible parent available. || Fit profile. || Conservative shrinkage/uncertainty and effective sample, no high-confidence independent cell estimate.
Negative after-cost edge || Gross outcomes positive, costs make conservative net edge nonpositive. || Allocate under Kelly mode. || No new exposure from that policy; costs cannot be omitted to force a trade.
Parse confidence misuse || Parser emits 99% semantic confidence but no trading history. || Size. || No conversion to 99% win probability or elevated risk.
Several providers same thesis || Multiple alerts share origin/underlying and overlap. || Combine profiles. || Dependence/correlation handling; do not sum independently optimal Kelly fractions as independent bets.
Partial exits double count || One lifecycle has three profitable trims and one final loss. || Build training set. || One combined lifecycle outcome including every cost, plus separately labeled leg attribution; not three wins versus omitted loss.
New exit policy || Runner policy changes outcome distribution from provider exit. || Update profile. || New version/qualified pooled inference; old performance not silently claimed for new policy.
Lookahead leakage || Regime feature uses next day's realized return or future revised source. || Train/replay. || Dataset/test rejects unavailable information; no evaluation contamination.
Winning streak || Several recent wins occur under unchanged live release. || Online worker recalculates. || No unapproved risk increase; future changes require scheduled/versioned evidence/release within caps.
Losing streak || Losses hit pre-released drawdown reduction/halt threshold. || Next signal arrives. || Reduce/stop new risk as policy dictates; no doubling to recover losses.
Holdout retuning || Candidate fails frozen test period. || Research tries to retune on same holdout. || Retain failed result and trial registry; new test design required, no reused holdout called untouched.
Portfolio horizon mismatch || Scalp and 30-day option R samples offered as one-period portfolio returns. || Joint optimization. || Reject incoherent horizon alignment; use common-time cashflow/scenario replay or conservative budgets.
''')

group('MAR','Margin, settlement and restriction regimes', '''
Cash no borrowing || Account is cash or released borrow limit zero. || Signal exceeds eligible funded cash. || Reduce quantity/skip; no margin use despite broker-advertised BP.
Margin account cash sufficient || Risk-approved size fits eligible cash. || Fund trade. || No incremental loan solely to raise utilization; risk size unchanged.
Approved debit || Same-risk trade requires borrowing; all permissions, cost and stress buffers pass. || Allocate. || Borrow only required permitted amount; record projected interest and remaining headroom.
Unknown maintenance || Broker read fails for maintenance or house requirement. || New leveraged candidate arrives. || MARGIN_UNKNOWN blocks added exposure; not assume zero requirement.
Intraday versus overnight || Position allowed intraday would exceed overnight collateral. || Approach mandatory conversion/close time. || Prevent overnight extension; perform released timed reduction/exit and report failure if blocked.
House margin increase || Broker raises requirement on held concentrated security. || Reconcile margin. || Recompute headroom, halt new risk and apply authorized reduction; no static old-margin assumption.
Legacy PDT account || Dated account evidence says legacy regime during transition. || Evaluate same-day stock entry/exit plan. || Enforce that account's current restrictions; no universal new-rule bypass.
New intraday account || Dated broker evidence confirms new regime. || Evaluate entries. || Use account's live intraday margin requirements; no universal legacy $25000 veto.
Unknown regime || No reliable evidence of which restriction regime applies. || Admit affected entry. || Block/precise configuration incident; valid owned protective exits continue.
Unsettled cash || Recent securities sale proceeds not yet usable under account rules. || Cash-account entry requests those funds. || Respect broker eligibility/settlement calendar; not infer availability from displayed cash alone.
Derivative margin not risk || Futures initial margin is much less than stress loss. || Size. || Apply independent loss/stress/variation-margin bounds; never risk-budget=margin-deposit.
Manual portfolio liquidation risk || Bot sleeve looks safe but whole account's manual holdings breach stress headroom. || New candidate. || Block new exposure on whole-account constraint; do not treat sleeve as isolated broker account.
Funding destroys edge || Long hold requires loan/funding costs larger than conservative opportunity edge. || Cost-aware allocation. || Skip or choose a separately feasible lower-cost authorized plan; no forced leverage.
Crypto collateral parity || Stablecoin quoted at .92 but collateral module assumes 1.00. || Stress/admission. || Revalue at qualified account-currency prices and block stale assumption.
''')

group('ENT','Entry conditions, pricing, sessions and deadlines', '''
Explicit limit preserved || Provider Max Buy=50.00 and current ask=50.10. || Plan order. || No buy above 50.00; wait only within valid policy/TTL or skip, no chase override.
Trigger versus limit || Source says buy breakout at 100 but never above 100.20. || Market crosses trigger at 100.30. || Trigger does not permit price-constraint breach; no buy at 100.30.
Stale quote || Executable quote age exceeds released threshold by one clock unit. || Dispatch revalidation. || Reject/wait for fresh data within TTL; no stale price used to size marketable order.
Wide spread || Spread exceeds released bound. || Entry candidate. || LIQUIDITY_REJECTED or bounded wait; source signal strength cannot bypass hard limit.
Target already passed || Source target=105 and delayed entry reference now exceeds 105. || Revalidate entry economics. || Expire/reject incompatible stale opportunity; no automatic chase toward an obsolete target.
Session closed || Regular-only equity signal arrives after session close. || Route/submit. || No queued next-day unintended order; bounded future-session policy only if explicitly released.
Early close || Exchange calendar closes earlier than normal. || Schedule entry TTL and exit. || Respect actual early close and deadlines; no hardcoded 16:00 assumption.
TTL before cancel finality || Unfilled entry TTL expires while cancel is only requested. || Timer runs. || Mark expiration intent, keep reservation/late-fill responsibility until resolved; no resource release on timer alone.
Price moves after sizing || Quote/account version changes before submit. || Dispatch. || Recheck and resize/expire atomically; no stale fixed plan bypass.
Partial fill remainder expires || 40/100 filled, deadline reached. || Cancel remainder. || Keep/manage/protect 40; reconcile cancellation and any late extra fills, not mark whole trade canceled.
No valid entry reference || Source has no valid price/reference and baseline requires one. || Resolve plan. || Reject no-guess; do not substitute arbitrary old last trade.
Trigger repeated ticks || A once-only breakout condition remains true across many ticks. || Feed updates. || One lifecycle trigger claim; no repeated entry or add without separate policy.
''')

group('ORD','Broker order states, effects, deduplication and corrections', '''
Accepted acknowledgment || Durable selected-account intent exists and broker returns accepted with order ID. || Consume response. || Persist ID/raw state and keep exposure commitment; accepted is not filled/protected.
HTTP success business rejection || Broker returns 200 with rejected order status. || Map response. || REJECTED not WORKING; release only proven-unused resources after no-fill reconciliation.
Timeout possible acceptance || Network times out after possible submit. || Handle error. || SUBMISSION_UNKNOWN, retained account/reservation/client ID; no resend or failover.
Known failure before send || Qualified transport proves no request was dispatched. || Handle failure. || Mark unsent/retryable under same intent and policy; no fabricated broker order; distinction evidenced.
Duplicate acknowledgment || Same order acknowledgment delivered twice. || Apply responses. || One broker order linkage; no repeated accounting or allocations.
Duplicate fill || Same authoritative execution ID appears through stream and polling. || Apply events. || Quantity/cash/P&L change once; retain observation lineage.
Fill before acknowledgment || Execution arrives before original HTTP response. || Correlate by qualified IDs. || Apply fill once to durable intent, protect owned quantity and merge later acknowledgment.
Fill after cancel request || Cancel pending while 20 additional shares fill. || Apply fill. || Own/protect 20, update remainder/reservations; cancel request not zero-position proof.
Not-found eventual consistency || Lookup momentarily returns no order after possible acceptance. || Recovery polls. || Preserve UNKNOWN; no resubmit based on one absent lookup.
Pagination hides order || Relevant order/fill is on later page. || Reconcile. || Follow complete pagination/cursors; no empty/first-page false-finality.
Unknown broker enum || API introduces a status not in adapter mapping. || Consume event. || Unknown state with conservative commitments and incident; not default canceled/success.
Replacement family || Old order replaced by new external ID; old may have late fills. || Reconcile family. || Retain all IDs and total cumulative fills; no duplicate exposure or lost old executions.
Order correction bust || Broker cancels/corrects a prior execution. || Apply correction. || Compensating ledger event, recalculated owned quantity/cash/protection and performance; no destructive history rewrite.
Duplicate client key different payload || Retry attempts same key with changed quantity or price. || Validate dispatch. || Reject immutable-intent conflict; no reuse of idempotency key for a different trade.
Generic retry decorator || Simulated timeout would trigger normal HTTP retry middleware. || Submit intent. || Effect operation bypasses blind retry; safe read retries remain independent.
Done for session || Broker says done_for_day but GTC could reactivate. || Resource cleanup. || Preserve future effect responsibility per actual semantics; not terminal canceled by assumption.
''')

group('PRO','Protection, partial fills and replacement safety', '''
Partial fill before bracket activation || Parent partially filled and native bracket exits inactive until full fill. || Protection check. || Explicit uncovered quantity and deadline; qualified supplemental recipe or cancel/reduce response, never assume protected.
Stop confirmed || Actual 40-share fill and broker confirms working 40-share stop. || Update protection. || Confirmed quantity/floor=40 at exact level; not requested 100-share size.
Stop rejected || Newly filled quantity cannot obtain its protective order. || Incident handler. || Halt further entries, safely cancel remainder, reconcile and attempt authorized scoped reduction; retain unresolved exposure if unsuccessful.
Desired versus confirmed floor || Desired long floor=51, old confirmed=50, replacement pending. || Render/risk check. || Use confirmed protection for risk release; show desired 51 separately, no false guaranteed coverage.
Replace rejection || Old working stop remains valid; tighter replacement rejected. || Handle response. || Retain old stop, pending discrepancy/deadline and incident as needed; do not mark new floor confirmed.
Crossed long floor || Committed floor=50.50 and qualified trigger price drops to50.20. || Trail update. || STOP_BREACHED/exit path; never lower floor to50.20 to submit an acceptable order.
Unlinked full exits || One 100-share allocation has fixed stop100 and proposed independent trail100. || Reserve close capacity. || Reject unsafe duplicate executable close exposure or use qualified exclusive recipe.
Cancel-create gap || Stop cancellation final before new stop submission fails. || Fault injection in simulator. || Detect uncovered quantity immediately; apply recovery, not assume zero-gap replacement.
Partial exit resizes stop || 100 owned, 30 confirmed exit fill. || Reconcile stop quantities. || Remaining protection and close capacity reflect70 plus any still-live family semantics; no stop100 oversell.
Late incremental entry fill || Partial position already protected; additional entry shares fill during stop resize. || Apply event. || Additional shares receive explicit coverage/recovery; not block lifecycle and leave them unmanaged.
Native trigger mismatch || Custom policy uses bid but native stop uses consolidated trade. || Qualify recipe. || Do not label semantics identical; use separately released compatible recipe or block feature.
GTC disappeared || Broker cancels protective order for corporate action/session/venue reason. || Reconciler sees no working stop. || Mark protection missing, restore qualified cover/reduce and report; no trust in old database row.
Feed outage with native stop || Quote feed fails but broker confirms native stop. || Worker cycle. || No invented trail observation/new entry; retain native protection and degraded status.
Protection deadline exceeded || Fill unprotected beyond released critical threshold. || Durable timer fires/restarts. || Critical incident and exact remediation status; no resetting deadline on restart.
Short ceiling monotonicity || Qualified short lifecycle has protective ceiling above market. || Favorable/adverse price updates. || Ceiling may tighten downward but never loosen upward; mirrored trigger/risk semantics correct.
''')

group('EXT','Exits, targets and close-quantity arbitration', '''
Provider full exit || Owned matching lifecycle has 50 remaining shares. || Receive valid full exit. || Close at most50 via its original account/writer; other lifecycles unchanged.
Stop and exit simultaneous || Both stop-fill observation and provider exit arrive for same shares. || Explore every permitted ordering. || One net economic close; no oversell, proper cancel/reconcile family behavior.
Timer and owner close || Mandatory time exit coincides with authenticated scoped manual close. || Both handlers run. || Join serialized close intent or reconcile existing close, not duplicate full sell.
Partial percentage denominator || Registered protocol means50% of original filled100, current remainder70. || First partial event arrives. || Sell50 (subject to current owned/close-capacity), not silently35; save denominator and event id.
Insufficient remaining for reduction || Source asks reduce50 but only20 legitimately remain. || Apply released partial policy. || Reduce at most20 only if capped-reduction semantics are authorized, otherwise reject; never sell other owners' shares.
Fractional contract trim || One option contract owned and source asks half. || Size partial close. || No fractional option or automatic full close; explicit no-feasible-partial or released alternative with explanation.
Multiple target allocations || Targets distribute integer quantities across three levels. || Build target plan for7 shares. || Deterministic quantities sum to at most7 and residual rule explicit; rounding cannot oversell.
Exit rejected by broker || Scoped close receives authoritative rejection. || Handle result. || Position remains owned/open with available protection and incident; never report flat.
Exit during entry remainder || Some entry fills exist and remainder still working. || Provider exits all. || Cancel/reconcile remaining entry, close actual owned fills safely, catch late entry fills; lifecycle not closed while reopening possible.
Closing hedge increases risk || Combo has short and protective long option legs. || Request close long protection only. || Evaluate resulting portfolio; block unsafe new uncovered liability despite SELL label.
Orphan exit unknown order || No known fill but associated submission is UNKNOWN. || Receive exit. || Do not send guessed sell; preserve terminal marker and reconcile possible fill/entry before scoped action.
Pause new entries || Account/source paused but position remains open. || Exit/stop/timeout occurs. || Manage valid exit/protection; pause flag cannot starve reductions.
''')

group('ADD','Staged entry and pyramiding into winners', '''
Pyramiding not released || Position profitable; code supports adds but live release does not. || Add trigger arrives. || No live add; optional shadow evaluation only, no new authority from feature presence.
Valid profitable add || Released add trigger, all caps/headroom/coverage pass and no pending exit. || Evaluate incremental child. || Add child intent to same lifecycle with full post-add risk plan and coverage; originalR unchanged.
Averaging down disguised || Existing position below declared profit reference; provider says add. || Evaluate winner policy. || Reject under winner-only policy; no martingale or fresh lifecycle to bypass prohibition.
Profitable stop negative risk || Old lot locked apparent profit and proposed large add. || Compute aggregate risk. || Old risk contribution floored at0; no negative-risk credit funding unlimited new exposure; giveback/stress still bind.
Synthetic add measures || 10 shares at50, confirmed stop50.50, bid52; propose5 at52. || Compute before costs. || Original add risk7.50, current mark-to-stop giveback22.50, gap-to48 loss60; three separate metrics.
Add cap across children || Seed+two adds have exhausted whole-lifecycle budget. || Third add signal. || Reject/reduce within remaining total; no risk reset or new trade ID loophole.
Desired stop frees budget || Add would fit only assuming unconfirmed tighter stop. || Evaluate add. || Use confirmed floor; reject until confirmed if all other policy requirements then pass.
Add and exit same tick || Add profit condition and valid provider exit both true. || Arbitrate. || Exit wins; no new exposure; cancel/reconcile any pending add safely.
Add partial fill protection || New add fills incrementally while seed remains protected. || Consume fill/replace events. || Preserve old coverage, cover new quantities, conserve close capacity and reservations.
Add margin stress fail || Add fits planned stop budget but fails stressed maintenance. || Size. || Block add; retain seed management unchanged.
Add near time cutoff || Profitable signal arrives too near mandatory lifecycle exit. || Evaluate holding-time gate. || Reject uneconomic/ineligible add; do not extend deadline to justify it.
Staged entry allocation || Original approved total risk split50/30/20 over planned tranches. || First tranche fills and next trigger never occurs. || Use only filled planned exposure; unused reservation expiry explicit; no forced later tranche.
Account compounding intraday || Mark gains lift current equity but released risk-equity rule caps at session start. || Size new trade. || Do not automatically expand risk on unrealized intraday gains.
''')

group('RUN','Runners, trailing policies and time/expiry limits', '''
Runner disabled || Provider exit arrived and no released derived runner strategy exists. || Manage remaining profitable position. || Close matching remainder; do not ignore exit to pursue more profit.
Target plus runner || Released plan assigns partial target and protected residual. || Target fills. || Only confirmed remaining quantity becomes runner with its own durable protection/deadline, no quantity creation.
No fixed target trend mode || Released trend recipe uses trail and time backstop only. || Position rises without target event. || Keep valid remainder protected; no arbitrary forced profit target or removal of stop.
Widening ATR || ATR increases after long floor has locked higher. || Recompute trail. || New desired floor=max(previous floor,candidate); never loosen to new lower ATR value.
Runner overnight unauthorized || Position remains profitable at session boundary. || Time exit fires. || Exit/reduce under deadline; no overnight extension because trade winning.
Runner option near expiry || Long option profitable but exit/exercise cutoff is near. || Runner gate. || Apply exact expiry/collateral/liquidity rule; do not rely only on underlying trend.
Missing ATR input || Runner requires ATR but current feature unavailable. || Update. || Frozen compatible baseline fallback or no nonurgent update, preserving confirmed stop; no invented volatility.
Bad tick high-water || Feed gives objectively invalid high tick under predeclared validation. || Trail calculation. || Reject anomalous observation with provenance; do not ratchet on bad data or discard real adverse ticks opportunistically.
Restart high-water || Runner persistsH, floor and activation before crash. || Restart/reconcile. || Restore values and policy version; never reset protection or activation to current price.
Derived runner attribution || Owner separately releases holding past provider exit. || Provider exits but derived runner remains. || Attribute to derived strategy/profile, not faithful-copy P&L; all unchanged hard caps and deadlines apply.
Tiny remainder economics || Remainder too small for meaningful trim or fee/minimum constraints. || Runner policy chooses action. || Explicit dust/whole-unit decision, no impossible fractional order or hidden extra purchase.
Giveback threshold || Current equity-to-stop potential loss exceeds released runner giveback cap. || Reprice. || Tighten/reduce/exit per rule with confirmed effects; do not call locked entry profit zero risk.
''')

group('OWN','Overlapping ownership and manual intervention', '''
Two analysts same direction || A owns100 and B owns50 same stock in one account. || B exits. || At most50 sold forB; A's100 basis/stop/deadline retained.
Same-symbol new entry overwrite || A open; B new entry arrives with new stop. || Create allocation. || B gets distinct lifecycle and protection; no overwrite ofA owner or plan.
Opposing netted positions || A long100; B proposes short50 in same net account without released netting policy. || Route. || Reject conflict; sell50 cannot be labeled independent hedge while consumingA holdings.
Hedging-mode ticket close || Qualified account has distinct long/short tickets. || Close one allocation. || Exact ticket/account/side selected; gross and net risk both conserved.
Manual shares reserved || Account contains80 manual shares and20 bot-owned. || Bot full exit. || At most20 bot shares closed; no account-wide close100.
Manual sell consumes inventory || Owner manually sells shares overlapping bot attribution. || Reconcile broker quantity. || Freeze conflicting commands, repair using declared ownership procedure, resize obsolete exits; no automatic buyback.
Manual stop cancel || Owner cancels a bot protective order outside app. || Reconciler detects it. || Apply owner-intervention/protection policy with visible incident; do not silently ignore or endlessly fight user.
Manual unknown new position || Broker reveals same-symbol quantity with no bot fills. || Reconcile. || Classify unmanaged/unattributed; do not auto-adopt into latest analyst lifecycle.
Tax lot distinction || Internal allocation attributes shares to analystB. || Generate report/close. || Do not claim that virtual allocation selected broker tax lot; actual tax-lot instruction separate.
Fractional split difference || Corporate action creates fractional shares/cash-in-lieu. || Reconcile allocation sums. || Apply explicit transformation and cash events; no false unexplained trade or deletion of remainder.
''')

group('EVT','Corporate actions, derivatives and market exceptions', '''
Gap through stop || Price gaps well below sell-stop trigger. || Stop executes. || Record actual worse fill and realized loss; no backtest fill at stop or guaranteed cap claim.
Halted market exit || Position held when exchange halts and exit deadline occurs. || Attempt qualified reduction. || Retain available protection, explicit unable-to-exit incident, no fabricated flatten; manage reopen.
Stock split || 2-for-1 split occurs with working orders. || Corporate-action reconciliation. || Quantities double and economically corresponding basis/floors adjust; verify broker order changes without phantom P&L.
Reverse split dust || Reverse split leaves fractional economic interest. || Apply transformation. || Track whole quantity and cash-in-lieu separately; no rounding to create shares.
Merger symbol successor || Old symbol changes into cash plus successor securities. || Instrument lifecycle event. || Exact deliverable/cash allocation mapping; freeze ambiguous orders and no ticker-only substitution.
Early short-option assignment || Short leg assigned before expected expiry. || Broker event. || Post option closure and underlying/cash obligation, reprice actual portfolio, protect/reduce authorized exposure.
Long option automatic exercise || ITM long remains at cutoff without approved funding plan. || Expiration workflow. || Preempt by timely authorized policy or handle actual exercised underlying; never assume expiration removes all obligations.
Calendar spread broken state || Near leg assigned while farther hedge remains. || Risk update. || Dedicated resultant-risk/collateral handling; not intact-vertical max-loss formula.
Futures delivery deadline || Contract nears first notice/last trade under no-delivery policy. || Durable cutoff. || Block entry and execute allowed close/roll plan; failure remains critical, not silent delivery exposure.
Borrow recall || Broker recalls stock borrow. || Receive recall/restriction. || Halt new shorts, preserve authorized cover workflow/cost attribution; no indefinite assumption borrow remains.
Derivative funding debit || Funding/variation margin reduces collateral materially. || Post event. || Adjust cash/headroom and halt/reduce under released limits; no omission from P&L/risk.
Broker forced liquidation || Broker liquidates part/all positions. || Reconcile fill. || Update ownership, cancel/reconcile conflicting bot orders and latch incident; do not reopen to restore old target.
''')

group('OPS','Failure, restart, coordination and operational health', '''
Crash after reserve before send || Intent/outbox committed, dispatch not begun. || Restart. || Recover same intent, verify no possible effect, revalidate TTL before safe dispatch; no duplicate reservation.
Crash after possible send || Broker may accept before response persistence. || Restart. || UNKNOWN reconciliation on original identity/client key; no fresh create or other-account fallback.
Crash after fill before protection || Fill exists externally but local protection commit absent. || Restart. || Reconcile fill, restore ownership, start/continue overdue protection incident and qualified repair.
Crash after exit fill || Position flat externally but local exit state stale. || Restart. || Deduplicate/readback fill, release only final resources, cancel/reconcile remaining entry/protection families.
Dual workers || Two processes try same physical-account effect stream. || Compete for lease and dispatch. || Only fenced owner writes; stale worker cannot send after lease loss.
SQLite busy || Another short transaction holds write lock during global reserve. || Reserve attempt. || Bounded retry of entire safe transaction or explicit failure, no stale read-then-write overspend.
Disk full || Ledger cannot persist intent/response. || Event arrives. || No new effect without durable intent; preserve unresolved state/incident and recover real broker facts after service restored.
Partial broker snapshot || Positions endpoint errors halfway through pagination. || Reconciler runs. || Snapshot incomplete/UNKNOWN; never delete missing allocations or label account flat.
Feed outage || All quote reads fail but worker loop still completes. || Health update. || Quote readiness false with age/error evidence; HTTP liveness may remain true; no green protected-trading claim.
Email outage || Core ledger, broker and native protection remain healthy. || Critical email send fails. || Record notification failure and alternate existing incident evidence; do not disable valid exit logic.
Optional research failure || Kelly training dependency errors. || Existing lifecycle needs exit. || Frozen released baseline and exit manager continue; optional analytics cannot block risk reduction.
Rate-limit pressure || Entry burst threatens order/lookup quotas. || Scheduling. || Reserve exit/protection/reconciliation capacity, backpressure new entries; no infinite retry storm.
Expired credential || Broker token expires with managed exposure. || Refresh/readback. || Qualified refresh to same account or degraded incident/protection; no switch to first accessible account.
Restore older backup || Broker effects happened after backup snapshot. || Recover server. || Reconcile all external post-backup effects and fencing before new entry; never replay old outbox blindly.
Failed migration || New schema migration aborts mid-upgrade. || Deployment. || Compatible rollback/blocked startup; no mixed-version writer corrupting ownership/reservations.
UI unavailable || Browser/server UI crashes while worker and state healthy. || Timer/stop event. || Financial management continues independently; incident status later visible.
Lost source connection || No new provider alerts received, open positions remain. || Source health degrades. || Block source-dependent new entries; stops/time exits/reconciliation continue.
Overdue deadline restart || App restarts after mandatory exit time. || Recovery gate completes. || Process overdue exit immediately as feasible, not reschedule next day; preserve inability-to-exit incident.
''')

group('LOS','Loss controls, P&L and calendar integrity', '''
Daily threshold exact || Net realized/unrealized/cost loss equals released daily halt threshold. || Revalue account. || Latch policy-defined breach at exact inclusive boundary; cancel safe unfilled entries and preserve exits.
Weekly halt restart || Weekly halt previously latched. || Restart or calendar day changes. || Halt persists until released reset procedure; no accidental daily reset clears weekly control.
Fees cross threshold || Price P&L alone under loss cap, fees/interest push it over. || Accounting update. || Include costs and latch halt; no win-rate-only bypass.
Wrong timezone midnight || UTC date changes while New York trading session day does not. || Scheduler/accounting ticks. || Use released session calendar/timezone for boundaries; no artificial loss reset.
Daylight saving || Clock changes across DST boundary. || Compute next deadlines. || Correct UTC mapping without duplicate/missing scheduled actions.
Profits not free risk || Open profitable trade has stop above entry but material giveback/gap exposure. || Aggregate risk. || Separate original-risk, current-giveback and stress; do not mark portfolio risk zero.
Currency P&L || Position currency gains but account-currency FX moves adversely. || Mark equity/loss. || Qualified account-currency conversion affects risk and performance, with timestamps/costs.
Withdrawal not loss || Account equity falls solely from external withdrawal. || Compute trading performance. || Cashflow-adjusted P&L excludes withdrawal while available capital declines.
Late fee correction || Broker posts fee after economic position close. || Reconcile accounting. || Amend lifecycle net result and relevant evidence/model dataset by version; not new trade.
''')

group('UI','API, browser, evidence and policy configuration', '''
Sizing drilldown || Candidate size constrained by risk, cash and source limit. || Open signal/position details. || Show all inputs/caps/as-of times and exact selected size; UI shares backend calculation.
Unknown versus zero || Broker risk value unavailable. || Render dashboard. || Display UNKNOWN/stale, not zero remaining risk or fake safe account.
Desired stop UI || Pending stop replace would tighten floor. || Open protection panel. || Show both desired and confirmed levels/quantities, pending family and deadline.
Source XSS || Signal symbol/body contains HTML/script payload. || Render every relevant UI surface. || Text-only safe rendering, no script execution; CSP defense not sole sanitizer.
CSV formula injection || Source-controlled export value begins with spreadsheet formula prefix. || Export report. || Safe text encoding/escaping policy; no unintended formula execution in consumer file.
API direct bypass || UI disables risky control but caller submits direct API request. || Backend validation. || Same effective permission/hard-cap/version checks reject action; no UI-only enforcement.
Stale config edit || Two browser tabs edit budgets with old version. || Save second tab. || Conflict/revalidation, no lost update or unnoticed raised limit.
Simulation toggle live || User/client mutates historical/paper mode field. || Call execution endpoint. || Server authorization/environment isolation prevents live route escalation.
Policy publish versus activate || New Kelly/runner candidate is saved. || GUI refresh/deployment. || Remains candidate/shadow until scoped release; save is not live activation.
Source disable journey || User disables provider while positions remain. || Browser action then exit event. || New entries disabled, existing management retained, reason and scope visible.
Account drain journey || User chooses drain versus disconnect. || Pending/open lifecycles progress. || Drain accepts no new exposure but retains readback/protection/exits until final safe detach.
Mobile desktop parity || Same authenticated role uses phone and desktop. || Execute all registered journeys. || No missing confirmations/scope/details due to viewport; backend results identical.
Evidence truthful status || Only helper/simulator tests passed, no broker paper/live test. || Render release/report. || Exact tested tier and NOT_RUN external scope; no claim actual account certified.
Candidate route trace || Three accounts considered and one selected. || View decision. || Exact included/excluded identities and reasons; alternate candidates not shown as executed orders.
''')

group('TST','Verification integrity, mutations and release boundaries', '''
Unbound contract harness || No callable application adapter configured. || Run contract test runner. || Nonzero error with NOT_BOUND, no fake passes or skipped-green result.
Missing actual result || Bound adapter omits quantity/selection decision required by case. || Run case. || Test fails, not default expected value from catalog.
Dedup guard mutation || Remove durable source claim in disposable code. || Run duplicate/cross-post/concurrency tests. || At least one mandatory test fails; mutant killed.
Reservation guard mutation || Remove owner-wide atomic reservation. || Run concurrent multi-account cases. || Oversubscription detected by independent ledger/invariant; mutant killed.
Close scope mutation || Replace owned close with ticker-wide close. || Run overlap/manual cases. || Detect wrong quantities/ownership and fail, even if final account flat.
Protection truth mutation || Mark desired stop as confirmed immediately. || Run rejected/pending replace fixtures. || Fail risk-release/UI assertions; cannot pass from local config alone.
Boundary mutation || Change inclusive loss threshold or floor rounding. || Run minus/equal/plus exact-unit vectors. || Boundary mismatch fails reliably.
Partial history claim || Source/backtest dataset has known gaps. || Produce learning/release evidence. || Missing coverage explicit; no exhaustive-history or proven-return claim.
Paper-to-live auto-promotion || All paper tests pass. || Release controller runs. || No new live authorization, funds transfer, order, cancellation or activated account.
Unsupported extension || Core equity suite passes while crypto derivative adapter absent. || Scope report. || Equity evidence scoped; extension remains NOT_IMPLEMENTED/BLOCKED, not overall complete.
All named scenarios required || Several applicable named cases lack executable mappings. || Release-evidence validation. || Nonzero incomplete gate; untested cases remain NOT_RUN, never waived by headline test count.
Competing fault sequence || Freeze input domains then enumerate every defined critical event ordering. || Test generation and application execution. || Count complete declared vectors, check invariants after every event, retain rejected/unreachable proof and actual evidence.
''')

scenarios=[]
for prefix,title,text in GROUPS:
    for n,line in enumerate(text.splitlines(),1):
        if not line.strip(): continue
        fields=[x.strip() for x in line.split(' || ')]
        if len(fields)!=4: raise ValueError((prefix,n,line))
        case_title,given,when,then=fields
        scenarios.append({
            'id':f'{prefix}-{n:03d}', 'category':title, 'title':case_title,
            'given':given,'when':when,'then':then,
            'status':'NOT_RUN','implementation_paths':[], 'test_paths':[], 'evidence_paths':[],
            'required_observations':['decision_and_reason','outgoing_broker_effects_or_proven_zero','persistent_state','quantity_cash_risk_deltas','deadline_and_recovery_state'],
            'required_variants':['declared_numeric_time_boundaries','applicable_supported_routes_and_modes','restart_at_relevant_effect_boundary'],
            'execution_authority':'ISOLATED_NON_LIVE_ONLY'
        })
ids=[s['id'] for s in scenarios]
assert len(ids)==len(set(ids))
(ROOT/'SCENARIO_CATALOG.json').write_text(json.dumps({'schema_version':'1.0','status':'ACCEPTANCE_REQUIREMENTS_NOT_EXECUTED_TESTS','scenarios':scenarios},indent=2)+'\n')
lines=['# Named acceptance scenarios','',f'{len(scenarios)} named Given/When/Then requirements across {len(GROUPS)} categories. **All are NOT_RUN against the application in this package.**',
       '', 'Each scenario must additionally record exact adapter calls (or zero), persisted state, quantities/cash/risk deltas, recovery/deadlines and evidence. Run every applicable declared variant; do not substitute screenshots or mocked helper results for actual wired application behavior.','']
for prefix,title,text in GROUPS:
    lines.extend([f'## {prefix} — {title}',''])
    for s in [x for x in scenarios if x['id'].startswith(prefix+'-')]:
        lines += [f"### {s['id']} — {s['title']}",f"**Given:** {s['given']}",f"**When:** {s['when']}",f"**Then:** {s['then']}",'']
(ROOT/'SCENARIO_CATALOG.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'named_scenarios':len(scenarios),'categories':len(GROUPS)},indent=2))
