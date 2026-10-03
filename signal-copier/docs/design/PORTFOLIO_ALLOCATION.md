# Portfolio allocation: one intended trade, one selected account

Status: **in progress** (see "Release-state report"). Source of truth for
behavior is the code: `app/routing.py` (`pool_for`), `app/engine.py`
(`_handle_signal`, `_try_reserve_capital`), `app/capital_allocator.py`,
and the `allocation_intents` / `strategy_budgets` / `capital_reservations`
tables in `app/db.py`. Decisions and their provenance are in
[ALLOCATION_DECISION_LEDGER.md](ALLOCATION_DECISION_LEDGER.md);
scenario-to-test evidence is in
[../testing/ALLOCATION_TRACEABILITY.md](../testing/ALLOCATION_TRACEABILITY.md).
ADR: [0012](../adr/0012-single-destination-allocation.md).

## 1. The defect and its root cause

**Defect.** With N accounts listed for a source, one signal produced N
broker orders (1 signal x 3 accounts = 3 executions). Exposure multiplied
with the number of connected accounts.

**Evidence-based root cause** (not a guess):

| Layer | Evidence at commit 26306af |
|---|---|
| Requirement was written as fan-out | `README.md` "If I have two accounts of the same type…": *"every account listed as a destination for a matching rule receives the signal; there is no automatic 'pick the best one' logic … that's fan-out (deliberate copying to multiple accounts)."* |
| Code implemented that statement | `app/engine.py` `_handle_signal`: `for raw_account in destinations:` placed one order per matching account. `RoutingConfig.destinations_for` returned every matching account. |
| Test encoded the same semantic | `tests/test_engine.py::test_signal_routes_and_sizes_to_multiple_accounts` (added in the first scaffold commit `ab8c878`) asserts three FILLED orders from one signal. |
| Duplicate protections were account-scoped | SIG-01 (`2e05b43`) stopped the *same account* being routed twice. The command-ledger key is `entry:{account}:{symbol}:{signal}`; capital reservations, ownership and replay guards were all per account or per signal id. Three individually idempotent account orders were therefore all "correct". |
| Allocation concept was absent | No entity existed between "signal" and "account order". `CAPITAL_ALLOCATION.md` explicitly scoped itself to a per-account gate. |
| Integration tests never asserted order count per intent | Multi-account tests asserted each account's sizing, not that exactly one account trades. |

So the cause was an **ambiguous requirement (replication vs alternatives)
encoded consistently in docs, code and a test**, plus account-scoped
idempotency. It was not missing requirement ingestion and not a
disconnected allocator: there was no allocator.

Two further defects of the same family were found while building the
regressions: the duplicate-command replay exported an invalid routing outcome
(raised on every replay), and the daily-loss limiter reads store members that do
not exist because its tests mock them. Both are fixed or fail closed now.

**Regression that would have prevented it:**
`tests/test_alloc01_single_destination.py::test_one_signal_three_eligible_accounts_produces_one_order`
and the through-the-webhook variant
`tests/test_alloc04_api_end_to_end.py::test_one_webhook_three_eligible_accounts_one_order`.

## 2. Invariants and where they are enforced

| # | Invariant | Enforcement | Test |
|---|---|---|---|
| 1 | One logical allocation decision before account-specific execution | `allocation_intents` row, `claim_allocation_intent`, keyed by (strategy, signal) not account | alloc01, alloc02 |
| 2 | Single destination by default | `RoutingRule.delivery_mode="single"`; engine binds one account | alloc01 `…one_order`, alloc04 |
| 3 | Selection is not failover | Walk is pre-submission only; after a submission attempt the intent is `committed` and bound; ledger idempotency unchanged | alloc01 `…never_rerouted…`, `…restart…` |
| 4 | Exposure bounded before destination choice | Sizing happens per candidate from the same signal; no candidate can enlarge the trade. (Quantity is per-account multiplier/fixed — see Known limits.) | alloc01 |
| 5 | One physical funding pool | **Not enforced**: account identity is `account_id`; two ids for one broker account would double-count | gap, see §4 |
| 6 | Joint admission | Strategy ceiling + account gates + owner ceiling; strategy check and reservation insert are one `BEGIN IMMEDIATE` transaction | alloc03 incl. cross-process |
| 7 | Separate financial quantities | Notional ceilings and risk-percent gates stay distinct; no combined "available capital" figure is introduced | existing + alloc03 |
| 8 | Reservations track obligations | Ambiguous submissions keep their reservation (ALLOC-05); release only on independent evidence | alloc05 |
| 9 | Ownership follows executions | Existing: derived per (account, symbol, source) from confirmed order deltas (Track 18) | alloc05 SNOW tests |
| 10 | Closing requires resolved obligations | Existing `has_unresolved_entry` (EXE-07 fix `36ba5ec`) | existing tests |
| 11 | Protection is independent work | Existing `retry_unprotected_positions` each reconcile (PRO-04 partial, `5de4a52`); persisted deficit record is a gap | existing; gap |
| 12 | One financial command path | Existing arbiter/ledger; allocation adds no second path | existing |
| 13 | Policy changes do not rewrite history | Intent persists `candidates` and the bound account; a config edit that removes the bound account does **not** re-select | alloc01 (restart) |
| 14 | Simulated/paper/live isolation | Unchanged; route qualification gate (Track 1b) still runs per candidate | existing |

## 3. Resolution of the eight "blocking questions"

These were resolved from existing documents, not re-asked (ledger rows
DL-43 to DL-46, DL-08, DL-40, DL-54, DL-23).

1. **Minimum trade size** — existing sizing engine; never round up; zero
   permissible quantity is an explained skip (`allocation_intents.state =
   'skipped'` with each candidate's reason).
2. **Deferred signals** — durable processing is infrastructure; waiting to
   enter is a trading behavior. Capacity-constrained entries are rejected
   (skipped) by default. No deferred-entry policy exists in code yet.
3. **Assignment/exercise** — attribute to the physical account and
   originating lifecycle; never liquidate unrelated holdings. Options remain
   an unreleased route; event handling for assignment is **not implemented**.
4. **Netting** — three cases kept distinct: same-direction same-instrument
   (design: separate allocations; **code currently rejects a second managed
   lifecycle**, EXE-09), opposing openings blocked, related instruments get
   exposure checks (not implemented beyond account/owner/strategy ceilings).
5. **Margin lending** — no synthetic lending; strategy ceilings are internal
   budgets, counted once.
6. **Config UI** — extend the existing console; see §6.
7. **Dedicated vs shared** — expressed by which rules list an account;
   explicit per-account bindings/purpose are not implemented.
8. **Capital limits** — unset strategy budgets block nothing (opt-in, like the
   existing gates); no approved dollar value is invented.

## 4. Entity reconciliation against the real code

| Required concept | Reality | Action |
|---|---|---|
| PhysicalAccount (one pool per broker account) | **Partial.** `DestinationAccount`/`config_accounts` keyed by `account_id`. No canonical broker-account reference, so duplicate connections or aliases are not detected. | Absent: design only. |
| CapabilityProfile | **Implemented under other names, connected.** `BrokerAdapter.can_trade_asset_class`, `supports_native_bracket`, `route_qualifications` ladder, buying-power check. Per-account purpose/role: absent. | Reused; no duplicate model created. |
| AccountBinding (strategy ↔ account) | **Partial.** `RoutingRule` (source, symbol_filter, destinations) *is* the approved pool; now carries `delivery_mode`. Asset-class/strategy-family bindings: absent. | Extended `RoutingRule`; no second model. |
| Portfolio / owner ceiling | **Implemented, connected.** `MAX_OWNER_NOTIONAL_EXPOSURE`, `owner_wide_exposure`. | Reused. |
| StrategySleeve / Budget | **New (partial).** `strategy_budgets`: global notional ceiling per strategy (signal `source`), counted once across accounts. Protected-vs-shared allocation, planned-risk budgets: absent. | Implemented slice. |
| Reservation | **Implemented, connected.** `capital_reservations` (durable, P0-4), now strategy-tagged; release matched by signal id. | Extended. |
| AllocationIntent | **New, integrated.** `allocation_intents`. | Implemented. |
| PositionAllocation / ownership | **Partial.** Derived per (account, symbol, source) from the order journal; managed lifecycle keyed (account, symbol). No immutable allocation id, so two providers cannot hold the same managed instrument concurrently. | Gap (ledger DL-09/DL-13). |

## 5. Dependency-based work breakdown

No calendar is asserted. Gates are acceptance criteria with evidence.

| Gate | Depends on | Acceptance | State |
|---|---|---|---|
| G0 contracts: decision ledger, invariants, root cause | — | Ledger recovered from history, absent artifacts recorded | done |
| G1 schema + store: intents, bind/release, budgets, migrations | G0 | Alembic 0037; separate-process claim race test | done |
| G2 engine integration: pool resolution, select-before-submit, resume | G1 | One signal → one order through engine and through webhook API | done |
| G3 joint admission: strategy ceiling, durable reservations, ID-matched release | G1, G2 | Cross-process atomic admission test | done |
| G4 unknown-outcome hold + operator resolution | G3 | Reservation held for ambiguous submit; released once on evidence; crash at each boundary never resubmits | done (plain entry path) |
| G5 UI: delivery mode, budgets, intent audit in existing console | G2, G3 | Rendered, driven in a real browser | partial: delivery mode + simulator done and browser-tested; strategy budgets and intent audit have API only |
| G6 immutable allocation identity under managed lifecycles | G2 | Two providers hold same instrument; scoped protection/exit | **not started — largest remaining gap** |
| G7 account identity / bindings / purpose | G2 | Duplicate-credential detection; per-account purpose | not started |
| G8 deferred-entry policy, split mode, assignment handling | G6 | per ledger | not started |

## 6. UI

Extend the existing private console only. Implemented: API for routing
`delivery_mode`, strategy budgets and the allocation-intent audit view.
The TR-11 routing editor now shows and preserves `delivery_mode` and the
simulator shows which account would be selected; both were driven in a real
browser. Strategy budgets and the intent audit view have API endpoints but no
console screen yet.

## 7. Migration and rollout

- Alembic `0037` adds `allocation_intents`, `strategy_budgets`,
  `capital_reservations.strategy_key`, and
  `config_routing_rules.delivery_mode` (default `'single'`).
- **Behavior change on upgrade:** every existing multi-destination rule now
  selects ONE account. A deployment that relied on fan-out must set
  `delivery_mode: replicate` on that rule before upgrading. Single-destination
  rules are unaffected.
- No ownership is guessed from ticker matches; existing positions are not
  adopted or liquidated by this change. Positions created by earlier
  broadcast behavior remain on whichever accounts they are on.
- Rollback: the new tables/columns are additive; old code ignores them. A
  rollback would restore fan-out behavior for multi-destination rules.

## 8. Release-state report

States: **Designed → Implemented → Integrated → Isolated-tested →
Externally qualified → Deployed inactive → Live released.** Nothing here is
externally qualified, deployed or live-released.

| Capability | Highest state reached | Notes |
|---|---|---|
| Single-destination selection (plain accounts) | Integrated, isolated-tested | Real engine + real webhook API, paper broker only |
| Single-destination selection (managed-lifecycle accounts) | Integrated | Bind/release wiring present; **no dedicated test yet** |
| Intent bound-account resume after restart | Isolated-tested | simulated by pre-binding; not a kill-at-boundary test |
| Cross-process claim/admission atomicity | Isolated-tested | SQLite, `spawn` processes, one host |
| Strategy ceiling counted once across accounts | Integrated, isolated-tested | notional only |
| Unknown-submission reservation hold | Integrated, isolated-tested | `resolve_unknown_submission` is engine-level only; no API/UI to resolve yet |
| Crash at each durable boundary (plain entry) | Isolated-tested | in-process `BaseException`, not a process kill |
| Property + mutation tests for the allocator | Isolated-tested | detects a bypassed allocator |
| Same-direction multi-provider allocations | Designed | code rejects second managed lifecycle |
| Account identity / bindings / purpose | Designed | |
| Deferred entry, split, assignment/exercise | Designed | |
| Risk defaults (0.25/0.75/0.50/25%, halts) | Not implemented | ledger only; need explicit release |
| TR-11 delivery-mode editor + simulator selection | Integrated, isolated-tested | real browser (Playwright), paper |
| Strategy budget / intent audit screens | Designed | API exists, no console screen |
| Daily-loss / min-equity gates | Not functional | fail closed; no daily-P&L source (see traceability) |

## 9. Known limits

- Quantity is still per-account (`multiplier`/`fixed_quantity`), so selecting
  a different account can change size; the intended-trade bound (invariant 4)
  is not yet enforced as an explicit intent-level maximum.
- The account-level capital gate still uses in-process `asyncio` locks plus a
  durable reservation table; only the **strategy** gate is atomic across
  processes. The single-writer lease (ADR-0002) is what prevents two live
  writers.
- Strategy attribution key is the signal `source`, not (source, analyst).
- Strategy confirmed notional is a replay of confirmed fills; multi-currency
  conversion, correlation and stress exposure are not modelled.
