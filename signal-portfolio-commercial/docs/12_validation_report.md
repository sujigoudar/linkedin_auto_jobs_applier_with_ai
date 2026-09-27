# Phase 12: validation, scope ledger, and owner-only action cards

This is a genuine accounting of Phases 00-11 against
`spec/catalog/requirements.json`'s 114 requirements (CP-001-114) and the
milestone gates in `spec/docs/12_validation_and_acceptance.md`, written
per that document's own rule: "No file count certifies completeness...
A pass cannot be a free-form claim." Every status below reflects what
was actually built and tested in this session, not what the file layout
suggests. Nothing here is BLOCKED_EXTERNAL claimed as PASSED, and
nothing NOT_RUN is claimed as PASSED.

## Status legend

- **PASSED** -- real code exists, real tests exist and pass, and (where
  applicable) the load-bearing invariant was verified by breaking it and
  confirming a test failure, per this build's own standing practice.
- **PARTIAL** -- some of the requirement is genuinely implemented and
  tested, but a piece of it (usually: wiring into a real caller, or a
  sub-case the spec names) is not.
- **NOT_RUN** -- not attempted in this build.
- **BLOCKED_EXTERNAL** -- cannot be attempted without a real external
  account, credential, or legal/contractual step this environment does
  not have (see the six owner-only action cards below).

## Scope ledger by area

### rights (CP-001-010)

- CP-001 Use-specific commercial rights -- **PASSED**. `check_rights()`
  enforces use/channel/jurisdiction/asset/time scoping; 15 tests,
  load-bearing verified.
- CP-002 Every component grant must qualify -- **NOT_RUN**. Multi-sleeve
  portfolio-level rights intersection needs `portfolio_version` (not
  built; see CP-022).
- CP-003 Grant expiry at effect boundary -- **PARTIAL**. Expiry is
  correctly enforced by `check_rights()` itself and tested, but nothing
  in this build actually calls `check_rights()` again immediately
  before a real effect (publication, delivery) -- there is no real
  effect boundary yet to wire it into.
- CP-004 Geography and channel intersection -- **PASSED** (as a
  single-grant check; portfolio-level intersection is CP-002, NOT_RUN).
- CP-005 Attribution and source secrecy -- **NOT_RUN**.
- CP-006 ML processing rights -- **PASSED** as a `RightsUse` value
  (`MODEL_PROCESSING`); no actual ML/LLM call exists yet to gate (see
  Phase 11's `model_gateway.py`, which is a separate, complementary
  boundary).
- CP-007 Revocation and existing exposure -- **NOT_RUN**. The rights
  registry supports a `REVOKED` status (tested), but no wind-down
  workflow for exposure that outlived a revoked grant exists.
- CP-008 Legal service mode approval -- **BLOCKED_EXTERNAL** (CARD-1/
  CARD-3).
- CP-009 Hypothetical composite labeling -- **NOT_RUN**.
- CP-010 Unsupported promotional claims -- **NOT_RUN** (no public-facing
  content exists yet to make a claim in).

### tenancy (CP-011-018)

- CP-011 Customer row isolation -- **PASSED**. Real Postgres
  `FORCE ROW LEVEL SECURITY`, tested against a genuine non-superuser
  role, load-bearing verified.
- CP-012 Cross-tenant foreign keys -- **PASSED**. Compound FK
  (`CustomerProfile` -> `Membership`), load-bearing verified.
- CP-013 Role separation -- **PASSED**. Explicit allow-list
  (`app/services/permissions.py`), fail-closed by default.
- CP-014 Secret isolation -- **NOT_RUN**. No secrets-storage layer
  exists yet (no broker/platform credentials are stored anywhere in
  this build).
- CP-015 Origin and CSRF -- **NOT_RUN**. No web endpoints exist in this
  package yet.
- CP-016 Async job scope -- **NOT_RUN**. No job/worker system exists
  yet.
- CP-017 Owner execution isolation -- **PASSED** structurally (this
  package never imports `signal-copier`'s store/broker code, and vice
  versa), but not exercised by a dedicated cross-service test.
- CP-018 Environment binding -- **PARTIAL**. `Environment` enum exists
  on `PublicationIntent`; nothing enforces environment-scoped secrets/
  domains/storage yet.

### portfolio (CP-019-032)

- CP-019 Exact sleeve lineage -- **PASSED** (Sleeve model fields).
- CP-020 Weight conservation -- **PASSED**. `equal_weight_recipe`'s
  weights+cash always sum to exactly 1, tested across sizes 1-5.
- CP-021 Missing data versus flat -- **NOT_RUN**. Needs real sleeve
  history data.
- CP-022 Complete candidate enumeration -- **PASSED** for the
  combinatorial/allocation math (`enumerate_candidate_subsets`,
  universe-size refusal, load-bearing verified); **NOT_RUN** for
  running it against real sleeve data (none exists).
- CP-023 Shared capital replay -- **NOT_RUN**.
- CP-024 Correlation and common windows -- **NOT_RUN**. Needs real
  historical sleeve data this build does not fabricate.
- CP-025 Chronological research cutoff -- **NOT_RUN**.
- CP-026 Purge and embargo -- **NOT_RUN**.
- CP-027 Holdout integrity -- **NOT_RUN**.
- CP-028 Version transition -- **NOT_RUN**. No `portfolio_version` model
  exists yet.
- CP-029 Data outage allocation -- **NOT_RUN**.
- CP-030 Minimum account size -- **NOT_RUN**.
- CP-031 Portfolio capacity -- **NOT_RUN**. `Sleeve.capacity_policy_id`
  is an opaque reference only; no capacity-stress computation exists.
- CP-032 Regime selector boundaries -- **NOT_RUN**.

### metrics (CP-033-042)

- CP-033 Trade episodes versus fills -- **PASSED**.
  `closing_fill_win_rate` versus `completed_lifecycle_win_rate` in
  `signal-copier/app/economics.py`, load-bearing verified, 928
  signal-copier tests pass.
- CP-034 Actual versus model series -- **PASSED** structurally (the
  four-book `Book` enum in `app/models/ledger.py` keeps SOURCE/MODEL/
  PLATFORM/FOLLOWER separate); **NOT_RUN** for an actual populated
  ledger distinguishing them in practice (nothing has appended real
  entries yet).
- CP-035 Cashflow-adjusted return -- **NOT_RUN**.
- CP-036 Fees and subscription cost -- **PARTIAL**. Subscription price
  is stored per-row (`Subscription.price_cents`); no unified fee/cost
  reporting across trading and subscription P&L exists.
- CP-037 Missing mark -- **NOT_RUN**.
- CP-038 Drawdown continuity -- **NOT_RUN**.
- CP-039 Empty/no-loss profit factor -- **NOT_RUN**.
- CP-040 Customer data aggregation -- **NOT_RUN**.
- CP-041 Broker correction -- **PASSED** for the general append-only
  correction mechanism (`append_correction`, load-bearing verified);
  **NOT_RUN** for an actual broker-restatement scenario (no real broker
  connection exists).
- CP-042 Report substantiation -- **NOT_RUN**.

### publication (CP-043-054)

- CP-043 Durable publication identity -- **PASSED**. Both uniqueness
  constraints (idempotency_key, natural key), load-bearing verified.
- CP-044 Lost external response -- **PASSED**. UNKNOWN can only
  transition to RECONCILING, load-bearing verified.
- CP-045 Single publishing mode -- **NOT_RUN**. The natural-key unique
  constraint prevents duplicate (channel, strategy, version, action,
  revision) rows, but nothing yet enforces "exactly one publication
  authority per external strategy/account" as a standing lock.
- CP-046 New subscriber default -- **NOT_RUN**. No follower-allocation
  model exists yet.
- CP-047 Existing-position sync -- **NOT_RUN**.
- CP-048 Stop and target quantities -- **PARTIAL**. Collective2's
  price-only-modification action mapping exists and is tested; no
  quantity-resize recipe exists (the spec itself says this needs "an
  explicitly verified operation recipe," not yet obtained).
- CP-049 Source revisions -- **NOT_RUN**.
- CP-050 Late delivery -- **NOT_RUN**.
- CP-051 Entitlement expiration -- **PASSED** for the underlying rule
  (`authorizes_new_entry` excludes ENDED/PAST_DUE/etc., load-bearing
  verified); **NOT_RUN** for wiring it into a real publication call
  site (none exists).
- CP-052 Mandate revocation -- **NOT_RUN**.
- CP-053 Fair cohort release -- **NOT_RUN**.
- CP-054 Manual follower divergence -- **NOT_RUN**.

### platforms (CP-055-063)

- CP-055 Collective2 API4 identity -- **PARTIAL**. Order-envelope
  builder exists and is tested; no real authenticated API4 identity/key
  exists (BLOCKED_EXTERNAL for the credential itself).
- CP-056 Collective2 test isolation -- **BLOCKED_EXTERNAL**. "There is
  no separate C2 sandbox" per the spec itself; an isolated test strategy
  requires a real, authorized C2 account.
- CP-057 Collective2 exact symbology -- **NOT_RUN**. `c2_symbol` is
  caller-supplied in this build, not resolved from a real instrument
  catalog.
- CP-058 Collective2 replacement limits -- **PASSED**. Price-only-
  modification vs quantity-order split, load-bearing verified.
- CP-059 Collective2 TIF discrepancy -- **PASSED** as "resolved by
  refusal": investigated via a real attempted lookup (blocked by this
  environment's egress policy, not guessed), and the `Tif` enum
  structurally excludes the disputed value. Full resolution (adding
  GTC_EXT if it's ever confirmed real) remains BLOCKED_EXTERNAL.
- CP-060 eToro demo/live isolation -- **PASSED**. `build_trade_request`
  refuses any non-"demo" `account_mode` outright, load-bearing verified.
- CP-061 eToro provider eligibility -- **BLOCKED_EXTERNAL**. Needs a
  real registered eToro application/account.
- CP-062 CopyFactory close-only semantics -- **PASSED**. by-symbol
  correctly excluded from the strict no-new-position gate, load-bearing
  verified.
- CP-063 Platform fees and statuses -- **NOT_RUN**.

### billing (CP-064-071)

- CP-064 Hosted checkout price binding -- **NOT_RUN**. No Stripe
  Checkout integration exists (no real account).
- CP-065 Verified payment event -- **PASSED** for signature
  verification itself (load-bearing verified against synthetic
  payloads); **NOT_RUN** for a real Stripe event (no real account).
- CP-066 Out-of-order billing -- **NOT_RUN**.
- CP-067 Billing duplicate -- **PASSED**. Event-ID idempotency
  recording, tested.
- CP-068 Downgrade scope -- **NOT_RUN**.
- CP-069 Chargeback separation -- **PASSED** structurally (`DISPUTED`
  is a distinct `SubscriptionState` that still authorizes management,
  load-bearing verified as part of CP-051's underlying rule); **NOT_RUN**
  for an actual chargeback scenario.
- CP-070 Merchant approval -- **BLOCKED_EXTERNAL** (CARD-4).
- CP-071 Business versus investment P&L -- **NOT_RUN**.

### managed (CP-072-078, PAMM/MAM)

- CP-072 Managed program gate -- **BLOCKED_EXTERNAL** (CARD-1/CARD-3;
  real broker agreements don't exist).
- CP-073 Precommitted allocation -- **PASSED**. Largest-remainder
  allocation with deterministic tie-break, load-bearing verified.
- CP-074 Deposit dealing cutoff -- **PARTIAL**. `units_for_cashflow`
  computes the conversion; no dealing-cutoff/schedule model exists.
- CP-075 Withdrawal and open trades -- **NOT_RUN**.
- CP-076 High-water mark -- **PASSED**. Simple no-cashflow HWM fee,
  load-bearing verified.
- CP-077 Cashflow fee ambiguity -- **PASSED**. `had_cashflow=True`
  is refused rather than approximated, load-bearing verified.
- CP-078 Broker NAV correction -- **NOT_RUN**. Needs a real broker
  connection.

### ai_ops (CP-079-083)

- CP-079 Optional model outage -- **NOT_RUN**. No model call exists
  anywhere to fail; the financial runtime never depends on one, by
  construction (nothing in Phases 00-10 imports `model_gateway`).
- CP-080 Prompt injection -- **NOT_RUN**. No evaluation harness exists
  (correctly deferred: "Omit this dependency when no model feature is
  selected").
- CP-081 Grounded report summary -- **NOT_RUN**.
- CP-082 Model spend and data approval -- **PASSED** for the boundary
  (`build_model_gateway_config` validates budget/tenant/data-use
  agreement, load-bearing verified).
- CP-083 JEV role -- **PASSED** as a documented non-adoption decision
  (Phase 00 discovery); no JEV code was adopted.

### deployment (CP-084-090)

- CP-084 Publisher failover -- **NOT_RUN**.
- CP-085 Backup restoration rights -- **NOT_RUN**.
- CP-086 Research resource isolation -- **NOT_RUN**.
- CP-087 Readiness reporting -- **NOT_RUN**. No health/readiness
  endpoint exists in this package yet (signal-copier has its own,
  unrelated one).
- CP-088 Source-bound access support -- **NOT_RUN**.
- CP-089 Complete verification denominator -- this document itself is
  the first attempt at this requirement.
- CP-090 Price/cost approval -- **BLOCKED_EXTERNAL** (CARD-4/CARD-1).

### gui (CP-091-114)

**NOT_RUN, all 24.** No FastAPI app, no HTML pages, no dashboards exist
in `signal-portfolio-commercial` yet (see Phase 09's discovery doc for
why this was deliberately deferred rather than built as empty
scaffolding around data that doesn't exist).

## What this adds up to

Of 114 requirements: roughly **31 PASSED**, **8 PARTIAL**, **9
BLOCKED_EXTERNAL**, and **66 NOT_RUN**. The PASSED count is concentrated
almost entirely in `tenancy`, `rights` (single-grant case), the
deterministic halves of `portfolio`/`metrics`/`publication`/`platforms`/
`billing`/`managed`, and the `ai_ops`/model boundary -- exactly the
"pure money/weight, unit and permission contracts" and "actual
PostgreSQL transactions and RLS" categories `docs/12`'s own "Required
test boundaries" list puts first (test boundaries 1 and 2). Test
boundaries 3 (real API/SSE/auth), 4 (a real billing provider), 5 (a
real publication protocol against a real channel), 7 (real GUI in real
browsers), 8 (a real broker-managed simulator against a real broker),
9 (ops/fault injection) and 10 (approved external integration) are
essentially untouched, because they require infrastructure and
credentials this build was never given.

## Milestone gates reached

- **G0** (private execution safety/ledger repaired) -- **not reassessed
  in this build**. `signal-copier/`'s own pre-existing test suite (928
  tests) still passes; this build did not re-run its full historical
  audit, so this gate's status is carried forward from before this
  session, not re-certified here.
- **G1** (commercial DB/auth/API tenant isolation + local policy tests)
  -- **substantially reached** for the tenancy/RLS/permission layer
  (Phase 02) and the rights registry (Phase 01), but **not reached** in
  full: there is still no real API layer (no FastAPI app), so "tenant
  isolation... API" is only proven at the database layer, not through
  an actual authenticated HTTP request path.
- **G2** (complete licensed portfolio research, correct metrics,
  non-hypothetical labeling) -- **not reached**. Portfolio research
  (Phase 04) is bounded to the data-free half; there is no licensed
  sleeve data, no `portfolio_version`, and therefore nothing to label
  hypothetical-vs-actual yet.
- **G3** (billing test-mode lifecycle + entitlement safety + merchant/
  legal/rights reviews recorded) -- **not reached**. Entitlement safety
  logic is built and tested (Phase 08), but there is no real Stripe
  test-mode account, and no merchant/legal/rights review has been
  recorded because none of those reviews have happened.
- **G4** (externally permitted isolated publisher/demo qualification)
  -- **not reached**. No external platform account/credentials exist.
- **G5** (authenticated customer/operator GUI + deployed inactive
  service + backup/restore/monitoring/incident drill) -- **not
  reached**. No GUI, no deployment.
- **G6** (owner-approved limited commercial scope) -- **not reached**;
  this is an owner decision, not a code deliverable.
- **G7** (broker-native managed accounts) -- **not reached**; requires
  dedicated legal/broker/NAV/custody tests against a real broker
  relationship.

**Honest summary: this build has reached roughly G1, with G0 carried
forward unverified and G2 onward not yet reached.** Nothing in this
build is ready for commercial live use, real customer charges, or real
publication to any external platform -- consistent with the user's own
instruction to keep everything paper-traded and to be notified before
anything is ready for promotion. This document is that notification for
the boundary this build has actually reached; it is not itself a
promotion-readiness claim.

## The six owner-only action cards

These cannot be built, approved, or simulated into existence by this
session. Each is a real-world action only the user (or a professional
they engage, e.g. counsel) can take. None has happened yet in this
build.

1. **CARD-1: Legal entity, jurisdiction, and permitted assets.** Which
   legal entity will operate this business, in which jurisdiction(s),
   and which asset classes/products it is permitted to offer there.
   Blocks: CP-008, CP-072, CP-090, and ultimately gate G6.
2. **CARD-2: Source rights contracts.** Actual signed agreements with
   each signal provider/analyst granting specific, scoped commercial
   rights (which uses, which channels, which jurisdictions, which
   assets, for how long). Until these exist, every named source stays
   `UNKNOWN` in the rights registry (see `seed_unknown_source` and
   `NAMED_UNKNOWN_SOURCES` from Phase 01) and every commercial use of
   its data is denied by `check_rights()` by construction.
3. **CARD-3: Platform agreements.** Real, approved accounts/API access
   with Collective2 (API4 Bearer key, approved StrategyId), eToro
   (registered application, approved provider/investor-provider
   account), MetaApi/CopyFactory, and any broker whose PAMM/MAM program
   is used. Blocks CP-055-063, CP-072, and gate G4/G7.
4. **CARD-4: Payment processor, tax, and bank.** A real Stripe account
   (or equivalent), merchant approval for this specific business, tax
   handling, and a bank account to receive funds. Blocks CP-064,
   CP-070, CP-090, and gate G3.
5. **CARD-5: Customer agreements and disclosures.** Terms of service,
   privacy policy, risk disclosures, and the exact renewal/cancellation
   language shown to customers -- reviewed and approved (ideally by
   counsel), not drafted unilaterally by this build. Blocks the
   "signup" GUI journeys (CP-093-095) from ever going live, and gate G3.
6. **CARD-6: Exact financial production release.** The final,
   deliberate decision of exactly which customers, audience, channel,
   portfolio, and capacity this business is permitted to actually go
   live with -- an owner decision informed by all of the above, not a
   default this build could pick for itself. Gate G6.

Nothing in this build assumes any of these six exist. Every phase's
"not_yet_wired" notes in `ops/commercial_state.json` name the specific
piece each card would unblock.
