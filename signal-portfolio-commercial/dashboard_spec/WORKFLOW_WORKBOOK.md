# Workflow execution and acceptance workbook

All cases are NOT_RUN against the actual application. Steps below are design requirements and actual test-driver obligations.

## J-PUBLIC_CATALOG: CP-091

PU-01 → PU-02 → PU-04

Prerequisites: Fresh database; anonymous visitor; one private Product draft; then publish a synthetic licensed test projection in isolated test DB

1. Read catalog empty and exclusions.
2. create draft via authorized staff service.
3. verify still absent publicly.
4. release isolated fixture.
5. filter and compare permitted products.

Expected: Only released audience-safe versions visible;  empty/missing/error distinct;  compare tray performs no financial or billing command

## J-PUBLIC_DETAIL: CP-092

PU-02 → PU-03 → PU-06

Prerequisites: Released test projection with actual/model datasets, a missing fee interval and two approved legal versions

1. Open version.
2. switch period and origin.
3. inspect drawdown and table.
4. request export.
5. follow methodology.

Expected: Origin, fee basis, coverage and version survive each view;  no fabricated net curve or raw provider contract exposure

## J-SIGNUP: CP-093

ID-01 → ID-02 → ID-03 → ID-04

Prerequisites: Controlled identity issuer with email verification,expired callbacks and replayable fixture token;no production secret

1. Signup.
2. verify email.
3. consume callback once.
4. reauthenticate.
5. recover with expired and valid challenge.
6. sign out.

Expected: Real session/tenant row created only from verified identity; no trade,invoice or entitlement solely from login

## J-ELIGIBILITY: CP-094

ID-04 → PU-02 → CU-09

Prerequisites: Approved jurisdiction/service matrix;allowed,restricted and review-pending residences

1. Submit facts.
2. inspect independent service verdicts.
3. revise facts.
4. acknowledge current document.
5. attempt stale-approval reuse.

Expected: Backend enforces current eligibility; customer cannot write approved=true or infer eligibility from IP alone

## J-CHECKOUT: CP-095

PU-05 → CU-11 → CU-01

Prerequisites: Approved test price,customer and Stripe protocol simulator with signed canonical events

1. Preview selected price.
2. open hosted test checkout.
3. return without webhook.
4. deliver duplicate and reordered events.
5. refresh.

Expected: Return URL alone grants nothing; verified canonical processor state updates allowed entitlement once; no trading mandate

## J-SUBSCRIPTIONS: CP-096

CU-11 → CU-02 → CU-10

Prerequisites: Customer with three portfolio selections,one active mandate and paid test subscription

1. Preview downgrade.
2. choose retained future scope.
3. cancel at period end.
4. simulate past_due/dispute.
5. inspect existing open obligation.

Expected: No new selection beyond entitlement; billing transitions never blindly flatten or remove existing protection

## J-PORTFOLIO_SELECT: CP-097

PU-02 → CU-02 → CU-03

Prerequisites: Eligible customer with paid entitlement and no selections;one licensed published version

1. Select version.
2. save.
3. reload.
4. duplicate same operation.
5. attempt restricted version.
6. inspect limits.

Expected: Selection persists once; no current historical position copied by mere selection

## J-ALERTS: CP-098

CU-04 → CU-05

Prerequisites: TenantA/TenantB alerts with originals,revisions,cancel,tombstone and delayed delivery

1. Page/scroll.
2. change filters during incoming event.
3. open revision.
4. reconnect.
5. inspect outcomes.

Expected: No cross-tenant rows or duplicates; delivery and actual execution statuses remain distinct

## J-DELIVERY_PREFS: CP-099

CU-12 → CU-13 → CU-16

Prerequisites: Verified and unverified delivery targets;quota and quiet-hours policy

1. Change preference.
2. verify endpoint.
3. send labeled test.
4. mute marketing.
5. attempt to suppress mandatory notice.

Expected: No investment order sent; secret masked; mandatory safety channel policy preserved and explained

## J-PLATFORM_CONNECT: CP-100

CU-07 → CU-08 → CU-07

Prerequisites: Configured provider redirect allowlist and controlled OAuth/protocol endpoints;no live keys

1. Begin connection.
2. reject tampered callback.
3. verify exact account/environment.
4. save.
5. reauthorize.
6. inspect unavailable capability.

Expected: Connection identity proven; no trade/membership purchase; external scope never inferred from display broker name

## J-COPY_ACTIVATE: CP-101

CU-03 → CU-09 → CU-07

Prerequisites: Eligible customer + paid selection + connected demo account + source rights + candidate mandate

1. Draft new-only setup.
2. preview account,size,version.
3. expire preview.
4. refresh.
5. confirm same key twice.
6. read operation.

Expected: Exactly one authorized activation operation; no existing-position sync unless separately requested and validated

## J-CUSTOMER_PERFORMANCE: CP-102

CU-01 → CU-06 → CU-05

Prerequisites: Follower actual data plus model report,deposit,partial trims,unresolved fee and stale mark

1. Switch origin/account/period while responses reverse.
2. open episode.
3. export.
4. inspect unavailable metrics.

Expected: No deposit as profit; no model return in actual card; trade episodes distinct from closing fills; all labels preserved

## J-TRADE_DETAIL: CP-103

CU-04 → CU-05 → TR-03

Prerequisites: Customer-scoped and separate private-owner fixtures of100 request,partial fill30,target10 pending,remaining entry fills30

1. Open order family.
2. inspect actual50 or60 according to confirmed exit.
3. inspect commitments and protection.
4. attempt cross-scope access.

Expected: No full requested quantity invented; views follow backend execution ledger; customer cannot enter private console

## J-SAFETY_EXIT: CP-104

CU-03 → CU-10 → CU-05

Prerequisites: Active mandate with pending order and open position;payment past_due and later mandate revocation

1. Pause new-only.
2. preview termination choices.
3. retain unresolved obligations.
4. request permitted handoff.
5. inspect progress.

Expected: No unqualified automatic flatten; revocation does not extend authority beyond contract; existing obligations remain visible

## J-SUPPORT: CP-105

CU-14 → AD-11 → AD-21

Prerequisites: Customer support case plus scoped support_readonly membership;redacted attachment

1. Create case.
2. upload bounded document.
3. request operator help.
4. attempt account impersonation and secrets export.
5. resolve with evidence.

Expected: Support sees only permitted scope and cannot publish/release or reveal private credentials

## J-LAB_UNIVERSE: CP-106

AD-02 → AD-03 → AD-04

Prerequisites: Authorized histories and rights grants including expired/model-prohibited sources

1. Create sleeve with parser/policy/version lineage.
2. inspect full coverage.
3. include source in universe.
4. check grants.

Expected: Every chosen sleeve retains exact source and data lineage; ineligible component excluded with reason,not silently guessed

## J-LAB_RUN: CP-107

AD-04 → AD-05

Prerequisites: Frozen universe with finite candidate set,compute budget,point-in-time dataset and controlled job runner

1. Save run.
2. validate full candidate denominator.
3. start.
4. interrupt shard.
5. resume.
6. cancel separate job.

Expected: Each candidate succeeds/fails/blocks explicitly; resume no duplicates; no live policy or external publication effect

## J-LAB_COMPARE: CP-108

AD-05 → AD-06 → AD-07

Prerequisites: Candidate results with different common windows,holdouts,fee stress and forward sample sizes

1. Compare exact versions.
2. inspect rejected candidates.
3. select one.
4. create private version draft.

Expected: Retrospective composite labeled hypothetical; no hidden cherry-picking,auto-approval or public result creation

## J-RELEASE_REVIEW: CP-109

AD-07 → AD-08 → AD-09

Prerequisites: Draft Product/PortfolioVersion with rights/data/platform/merchant/review evidence;one expired dependency

1. Preview diff.
2. deny stale evidence.
3. request changes.
4. refresh evidence.
5. separate reviewer approves released scope.

Expected: Release approval not automatic live activation; record reviewer independence or explicit permitted owner self-review limitation

## J-PUBLISHER: CP-110

AD-09 → AD-10 → AD-21

Prerequisites: Qualified protocol simulator with acknowledgment,response loss,external children and fair cohort version

1. Inspect destination.
2. publish only in isolated authorized mode.
3. lose response.
4. reconcile.
5. prepare scoped correction.

Expected: One publication identity; unknown never blind replay; future subscribers cannot join a previously frozen new-entry cohort

## J-RIGHTS: CP-111

AD-02 → AD-08 → AD-21

Prerequisites: Grant geography/channel/asset windows,revocation and still-open managed exposure

1. Create draft evidence.
2. review.
3. evaluate every sleeve intersection.
4. expire grant before confirm.
5. manage disclosed obligations.

Expected: Payment or reviewer role cannot grant nonexistent rights; new publication blocked at exact effect boundary

## J-BUSINESS: CP-112

AD-12 → AD-13 → AD-18

Prerequisites: Subscription invoice/refund/cost/royalty events with distinct currencies and restatements

1. Filter book/period.
2. reconcile totals.
3. inspect invoice.
4. export frozen report.

Expected: Investment P&L not business revenue; unknown charges excluded only with explicit incomplete totals; no payout from chart

## J-MANAGED_ACCOUNT: CP-113

AD-14 → AD-15 → CU-15

Prerequisites: Broker-managed test program,precommitted allocation,NAV,cutoff,open trades and cashflow ambiguity

1. Preview dealing request.
2. review units/fees.
3. reject unsupported cashflow fee convention.
4. inspect correction.
5. show investor statement.

Expected: No SaaS investment custody; allocation fixed before outcome; no guessed fee or live broker operation without approved program

## J-RECOVERY: CP-114

AD-22 → AD-21 → TR-16

Prerequisites: Inactive standby,restored generation,expired session,unknown publication and unreachable old writer

1. Inspect health.
2. prepare restore evidence.
3. deny unfenced promotion.
4. verify current rights.
5. restore customer views scoped.

Expected: Web reachability not financial readiness; no second writer,restored active flag or public secrets; real external recovery qualification remains separate

## JT-01: No accounts -> inactive account -> confirmed partial inventory

TR-01 → TR-02 → TR-03

Prerequisites: Live credentials absent;temporary SQLite and independent venue book

1. Create inactive account.
2. inspect clean empty dashboard.
3. load controlled30-share fill.
4. open details.
5. verify coverage and UNKNOWN flags.

Expected: No optimistic holdings;status panel explains each independent readiness dimension

## JT-02: Message review and duplicate suppression

TR-04 → TR-05 → TR-06

Prerequisites: Negated message,revision,duplicate external event and normal entry

1. Classify each.
2. inspect evidence.
3. replay duplicate.
4. verify exact broker command count.

Expected: Negated history cannot enter;one intended destination one effect

## JT-03: Exact account capability setup

TR-07 → TR-08 → TR-01

Prerequisites: Two accounts at same adapter;one missing stop amendment capability

1. Save independently.
2. verify immutable identity.
3. preview qualification.
4. try copied readiness state.

Expected: Capability badges bound to actual account/product/version;one account cannot certify other

## JT-04: Provider history and parser version validation

TR-09 → TR-10 → TR-05

Prerequisites: Authorized100-message test corpus including all defined classes and revisions

1. Import all pages.
2. interrupt/resume.
3. classify every record.
4. compare versions.
5. reject unknown grammar.

Expected: Coverage denominator preserved;missing data remains gap;no live replay

## JT-05: Routing rule preview and scoped release

TR-11 → TR-05 → TR-06

Prerequisites: Two overlapping rules;manual existing holding;entry pause

1. Save rule draft.
2. preview destinations.
3. deduplicate.
4. release approved config.
5. receive exit after pause.

Expected: One destination effect;existing-position exit follows original ownership;manual holding not swept

## JT-06: Policy inheritance and missing stop handling

TR-12 → TR-05 → TR-03

Prerequisites: Provider no-stop case and released fallback;hard account risk ceiling

1. Reset override to inherit.
2. preview source basis.
3. attempt wider lower-level limit.
4. submit config review.

Expected: Inherited constraints respected;missing fallback blocks entry;no arbitrary new default stop

## JT-07: Partial profit protection transfer

TR-02 → TR-03 → TR-06

Prerequisites: 62 owned,stop62,target15;8 then3 fills while cancel unresolved

1. Preview trim.
2. confirm once.
3. inspect each transfer state.
4. reconcile final result.

Expected: 54 remains after8;pending7 counted;51 after3 more;no excess full stop or duplicate sell

## JT-08: Lost response and restart

TR-06 → TR-13 → TR-03

Prerequisites: Durably recorded command accepted by controlled venue with response lost

1. Trigger unknown.
2. restart application.
3. open same operation.
4. readback resolves ID.

Expected: No second command;UI never announces successful cancellation/filled based on timeout

## JT-09: Financial metrics and partial exits

TR-14 → TR-03

Prerequisites: Buy100@10 fee1;sell40@11 fee.4;mark60@12;currencyUSD

1. Open gross/net report.
2. inspect actual fills and fee allocations.
3. export.

Expected: Realized39.20,unrealized119.40,combined158.60 under declared linear convention;episode not3wins

## JT-10: Historical run with ambiguous candle

TR-15 → TR-14

Prerequisites: Long100 stop95 target105;bar high106 low94;original event times

1. Run full fixture.
2. inspect both path-consistent bounds.
3. pause/resume job.

Expected: No favorable fill order invented;backtest is model,not actual record

## JT-11: Truthful health and recovery

TR-13 → TR-16

Prerequisites: All required broker reads fail while HTTP stays responsive

1. Inspect process/task/route/protection individually.
2. restore feed.
3. acknowledge incident.

Expected: Loop-completed not usable-data;acknowledge not resolve;repair requires current observations

## JT-12: Inactive site and origin isolation

TR-16 → TR-01

Prerequisites: Inactive role,old writer unproven,read-only owner session

1. Log into inspection.
2. attempt financial POST.
3. review promotion.
4. inject stale active config.

Expected: Read inspection possible but financial authority disabled;no automatic writer on missed heartbeat
