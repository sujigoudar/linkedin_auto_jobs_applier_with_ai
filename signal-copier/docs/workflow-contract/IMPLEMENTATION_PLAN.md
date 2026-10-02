# Workflow-contract implementation plan (WC packages)

Controlling documents, in authority order: `WORKFLOW_SPECIFICATION.md` (this
directory), `SCENARIO_CATALOG.json` (292 named requirements), `generated/`
(9,228 vectors), then `../design/REMEDIATION_PLAN.md` (R0–R8, in flight).
Everything here is non-live: no live limits change, no validation trades, no
new accounts or paid services. Package status vocabulary (spec §21):
NOT_IMPLEMENTED, IMPLEMENTED_NOT_WIRED, WIRED_NOT_TESTED, TESTED_SIMULATOR,
TESTED_BROKER_PAPER, TESTED_OWNER_LIVE, BLOCKED, NOT_APPLICABLE_WITH_REASON.

Rules for every WC package are the same as `REMEDIATION_PLAN.md` "Rules for
every work package" (read them), plus:

- Work only in your own worktree; never the main checkout.
- New modules live under `app/workflow/` (create `app/workflow/__init__.py`
  once; the first package to need it creates it). Each module has a module
  docstring that cites the spec sections it implements.
- Money is exact: integer cents (or `Decimal`) in the new modules; never float
  arithmetic on money. Quantities use the instrument's step and never round up.
- Every decision returns a reason code from spec §2 (`AUTH_REJECTED`,
  `UNKNOWN_PROVIDER`, `PARSE_AMBIGUOUS`, `NONACTIONABLE`, `DUPLICATE`,
  `INSTRUMENT_UNRESOLVED`, `WAIT_TRIGGER`, `EXPIRED`, `UNROUTABLE`,
  `POLICY_DISABLED`, `CAPITAL_LIMITED`, `RISK_LIMITED`, `MARGIN_UNKNOWN`,
  `LIQUIDITY_REJECTED`, `SUBMISSION_UNKNOWN`, `PARTIAL_UNPROTECTED`,
  `STOP_BREACHED`, `OWNERSHIP_CONFLICT`, `EXIT_ORPHAN`,
  `RECONCILIATION_REQUIRED`, `CLOSED_PENDING_ACCOUNTING`, `CLOSED`), defined
  once in `app/workflow/reasons.py` (WC-01 creates it).
- Tests: `tests/test_wcNN_<slug>.py`, real `SignalStore` on `tmp_path`, real
  modules; boundary tests at threshold−1/threshold/threshold+1 for every
  numeric rule the package introduces; invariant checks (spec §1.1 I01–I24
  that apply) after every event in the test, not only at the end.
- Verification as in the remediation plan (ruff, CI mypy with
  `--cache-dir=/dev/null`, the package tests, listed "also run" files).
  Add every new module to the CI mypy file list in
  `.github/workflows/signal-copier-ci.yml` (repo root) and the Makefile
  `typecheck` target.

## Shared interfaces (define once, build to them in parallel)

```python
# app/workflow/reasons.py
class Reason(str, enum.Enum): ...  # the §2 codes above, plus the admission
# blocking reasons used by the generated admission suite, in THIS order:
ADMISSION_BLOCKING_ORDER = [
    "SOURCE_AUTHORITY", "NOT_CURRENT_ACTIONABLE_ENTRY", "NO_ELIGIBLE_ROUTE",
    "BUDGET_NOT_ADMISSIBLE", "REGIME_UNKNOWN", "HALTED", "UNCERTAIN_EFFECT",
]

# app/workflow/money.py
Cents = int                      # exact integer cents in the account currency
def to_cents(value: Decimal | str | int) -> Cents: ...   # never float

# app/workflow/identity.py (WC-02)
@dataclass(frozen=True) class PhysicalAccount: physical_account_id: str; broker: str;
    broker_account_id: str; environment: str  # paper|live|sandbox|unknown
    base_currency: str; margin_type: str  # cash|margin|retirement|unknown
    restriction_state: str  # none|pdt_restricted|closing_only|unknown
@dataclass(frozen=True) class AccountBinding: binding_id: str; physical_account_id: str;
    config_account_id: str; version: int; revoked: bool
@dataclass(frozen=True) class CapabilityProfile: physical_account_id: str;
    instrument_family: str; session: str; operation: str; order_recipe: str;
    evidence_tier: str  # unknown|declared|simulator|paper|live
class IdentityRegistry:  # backed by SignalStore tables
    def physical_for_config_account(self, config_account_id) -> PhysicalAccount | None
    def collapse_bindings(self, config_account_ids: list[str]) -> list[PhysicalAccount]  # I02
    def capability(self, physical_account_id, instrument_family, session, operation) -> CapabilityProfile | None  # None == unknown == unsupported

# app/workflow/budget.py (WC-03)
@dataclass(frozen=True) class ResourceVector:  # all Cents or int; None == unknown
    cash: Cents; buying_power: Cents | None; initial_margin: Cents; maintenance: Cents | None;
    notional: Cents; planned_risk: Cents; stress_risk: Cents | None; close_quantity: int; slots: int
class ReservationState(str, enum.Enum): DRAFT, HELD, COMMITTED_TO_PENDING_ORDER, PART_FILLED,
    FILLED_EXPOSURE, RELEASE_PENDING, RELEASED, UNKNOWN_HELD
@dataclass class BudgetScope: owner: str; physical_account_id: str; portfolio_id: str | None;
    sleeve_id: str | None; provider: str; analyst: str | None; underlying: str; cluster: str | None
class HierarchicalBudget:
    def check_and_reserve(self, opportunity_id: str, scope: BudgetScope, need: ResourceVector,
                          snapshot: BrokerSnapshot | None) -> ReservationResult  # ONE BEGIN IMMEDIATE txn
    def transition(self, reservation_id, new_state, *, evidence: dict) -> None
    def remaining(self, scope) -> dict[str, Cents]  # per level: owner/account/portfolio/sleeve/provider/underlying
@dataclass class ReservationResult: ok: bool; reservation_id: str | None; reason: Reason | None;
    binding_level: str | None; remaining: dict[str, Cents]
@dataclass class BrokerSnapshot: buying_power: Cents | None; equity: Cents | None;
    maintenance: Cents | None; reflected_intent_ids: frozenset[str] | None; as_of: datetime
# Three risk measures (spec §6.1), pure functions on lot lists:
def original_planned_loss(lots) -> Cents; def mark_to_protection_loss(lots, mark) -> Cents;
def stress_loss(lots, scenario) -> Cents

# app/workflow/sizing.py (WC-04)
@dataclass(frozen=True) class SizingResult: quantity_units: int; planned_risk_cents: Cents;
    notional_cents: Cents; binding_constraint: str; reason: Reason | None
def size_linear_long(*, risk_budget_cents, unit_risk_cents, cash_capacity_cents,
                     entry_price_cents, source_max_units, lot_step: int = 1) -> SizingResult
def size_long_option(*, risk_budget_cents, premium_cents, multiplier, costs_per_contract_cents, ...) -> SizingResult
def size_linear_future(...); def size_fx(...); def size_spot_crypto(...)

# app/workflow/admission.py (WC-05)
@dataclass(frozen=True) class AdmissionInputs: authorization: str; interpretation: str;
    eligible_physical_accounts: list[str]; budget_state: str; margin_regime: str; halt: str;
    uncertain_effect: bool
@dataclass(frozen=True) class AdmissionDecision: admit_new_entry: bool;
    selected_physical_account_count: int; blocking_reasons: list[str]; broker_call_count: int
def evaluate_admission(inputs: AdmissionInputs) -> AdmissionDecision   # pure, no I/O
# app/workflow/selection.py (WC-05)
def select_account(candidates: list[Candidate], policy: RankingPolicy) -> Selection  # feasibility first, deterministic ranking, DecisionTrace rows

# app/workflow/intents.py (WC-06)
class IntentState: DRAFT, OUTBOXED, DISPATCHING, SUBMITTED, ACKNOWLEDGED, UNKNOWN, FILLED, REJECTED, EXPIRED
@dataclass(frozen=True) class OrderIntent: intent_id; opportunity_id; physical_account_id; binding_id;
    client_correlation_id; policy_hash; quantity; price_constraints; protection_recipe; reservation_id
class Outbox: def enqueue(intent) ; def claim_next(worker_lease) ; def record_response(intent_id, response)
```

Packages that depend on these interfaces import them; if an interface is not
yet on your branch, create the minimal stub with the exact signature above in
your own module and note it in your report. The integrator reconciles.

## Packages

### WC-01 — Inventory, reasons, traceability skeleton (gate G0)
Read-only inventory of the ACTUAL application: every source adapter and
parser, schema table, asset class, account field, route (HTTP + console),
entry/exit path, sizing mode, budget/risk field, order type/state, adapter
operation, scheduled job, release gate. Write
`docs/workflow-contract/INVENTORY.md` (tables with file:line) and
`docs/workflow-contract/EFFECTIVE_POLICY.md` (effective configuration
precedence with provenance: env → yaml → DB → provider override; note that
v3.4 numbers are reference only). Create `app/workflow/__init__.py`,
`app/workflow/reasons.py`, `app/workflow/money.py`. Create
`docs/workflow-contract/build_traceability.py` that reads
`SCENARIO_CATALOG.json` and writes `requirements_traceability.json` with every
scenario's status from a mapping file `traceability_map.yaml` (initially all
NOT_IMPLEMENTED or IMPLEMENTED_NOT_WIRED where the inventory shows existing
code, with `implementation_paths` pointing at real files). Tests:
`tests/test_wc01_traceability.py` asserts every scenario id appears, statuses
are from the allowed set, every referenced path exists.

### WC-02 — Canonical identities (gate G1)
`app/workflow/identity.py` + tables `physical_accounts`, `account_bindings`,
`capability_profiles` (SCHEMA + `_COLUMN_MIGRATIONS` where applicable + alembic
revision). Derive a PhysicalAccount for every existing `config_accounts` row
at startup (broker + broker-reported account id when the adapter exposes one,
else `config_account_id` with `evidence_tier="declared"`), bindings for each
row, collapse duplicates (two config rows with the same broker account id are
one physical account). Owner API: `GET /physical-accounts`,
`POST /physical-accounts/{id}/capabilities` (owner declares a capability with
evidence tier; unknown stays unsupported). Tests: two bindings → one physical
account (I02); unknown capability is not supported (I21); paper and live with
the same display name are different accounts.

### WC-03 — Hierarchical budgets, resource vector, three risk measures (gate G2)
`app/workflow/budget.py` + tables `portfolios`, `portfolio_backings`,
`strategy_sleeves`, `budget_reservations` (state machine above), `owner_limits`.
`check_and_reserve` runs ONE `BEGIN IMMEDIATE` transaction that checks owner,
account, portfolio, sleeve, provider, underlying and cluster remaining
together (I08) and claims the opportunity id (unique index) so two workers
cannot both admit. Broker snapshot normalisation: subtract only local
commitments not in `reflected_intent_ids`; `None` membership with a stale
snapshot → blocked (`MARGIN_UNKNOWN`/`CAPITAL_LIMITED` with reason detail).
The $10/$10/$15 case: second reservation is blocked or sized down per policy,
never both admitted. Reuse the existing `capital_reservations`/strategy
budgets where they exist by wrapping them, do not duplicate state. Tests:
simultaneous reservations from two threads (sqlite3 connections) → exactly
one admitted; every level boundary ±1 cent; reservation state transitions
valid/invalid; UNKNOWN submission keeps HELD; three risk measures on the
spec §13.3 example (7.50 / 22.50 / 60.00).

### WC-04 — Product-correct sizing (gate G2)
`app/workflow/sizing.py` with the functions above. `size_linear_long` must
reproduce `generate_cases.sizing_oracle` exactly for all 4,500 vectors
(min of floor(risk/unit_risk), floor(cash/price), source_max; zero when any
bound is zero; never round up). Options: full premium at risk;
`$2 × 100 = $200` so a $15 budget → 0 contracts. Futures: point value;
FX: lot→units and quote→account conversion with an explicit rate argument
(no rate → reason `INSTRUMENT_UNRESOLVED`); crypto: step and dust
conservation. Stop == entry is not zero risk (adverse cost allowance binds).
Tests: the 4,500 vectors run against the function directly (fast), plus
the spec §7.5 example (14 shares, $700, $14.70), plus options/futures/fx.

### WC-05 — Admission gate and single-destination selection (gate G3)
`app/workflow/admission.py` (pure `evaluate_admission` reproducing the
restricted oracle for all 4,608 vectors, with blocking reasons in
`ADMISSION_BLOCKING_ORDER`) and `app/workflow/selection.py` (feasibility
before ranking; deterministic ranking: released preference → full recipe
qualification → lower incremental concentration/stress → lower cost → fresher
evidence → stable account id; `DecisionTrace` rows persisted in a new
`decision_traces` table with inclusion/exclusion reason per candidate).
Tests: all 4,608 vectors; three feasible accounts → one selected, the other
two recorded as alternatives (I01); an account that cannot buy one unit is
excluded before ranking; duplicate bindings collapse.

### WC-06 — Durable intents, outbox and submission-unknown recovery (gate G4)
`app/workflow/intents.py` + tables `order_intents`, `outbox`. Transaction
order per spec §6.3: claim opportunity → validate versions → write selection
→ reserve → write intent + outbox → commit; dispatcher marks DISPATCHING in a
second short transaction, calls the adapter outside any transaction, persists
the response. Integrate with the existing command ledger (`app/command_ledger.py`)
and writer lease: a worker without the lease cannot dispatch. UNKNOWN keeps
the reservation and blocks conflicting new exposure on that account/underlying
(I05). Crash tests at every boundary (kill between each step, restart,
idempotent recovery; follow `tests/test_alloc07_crash_boundaries.py`).

### WC-07 — Kelly profiles and evidence states (gate G7, non-live)
`app/workflow/kelly.py`: profile key (provider × analyst × strategy × asset
family × horizon × exit policy), evidence states NO_HISTORY / INCONCLUSIVE /
NEGATIVE_EDGE / ELIGIBLE, robust candidate from a full net-outcome
distribution (log-growth over outcomes with block bootstrap; lower-confidence
criterion), fractional multiplier λ∈(0,1], the §7.4 hierarchical minimum,
only downward modifiers, no stacking beyond the hard ceiling. Shadow-only:
the engine never consumes a Kelly budget unless `sizing_mode ==
"kelly_shadow"` logs the would-be size without changing the order. Tests:
§7.5 example (0.228571 full, 0.057143 quarter, ceiling 0.25 % wins → $15);
no history → no number; negative edge → zero; correlated providers are not
summed.

### WC-08 — Staged entries, pyramiding admission, runner state (gate G7, non-live)
`app/workflow/scaling.py`: four mechanisms as distinct types; add admission
requires released add policy, confirmed protection, profitable trigger, fresh
data, time remaining, no active exit, resources after the add; whole-lifecycle
recomputation preserving seed R (I16); runner state with high-water, giveback,
deadline, monotonic floor (I11); persisted and restored across restart (I19).
Tests: §13.3 example; add passes original-risk and fails giveback → rejected;
restart restores high-water and floor.

### WC-09 — Margin regimes and settlement (gate G2/G5)
`app/workflow/margin.py`: per-physical-account regime
(`legacy_pdt_verified` | `new_intraday_verified` | `unknown`) with evidence
and date; `unknown` blocks affected new exposure (never a permissive default);
owner limit ∧ broker limit (intersection); cash-account rule; "margin is not
equity" (I09); T+1 settled/unsettled cash fields on the snapshot; overnight
extension recheck. Tests: every row of spec §9 table.

### WC-10 — Contract adapter binding the generated suites
`tests/workflow_adapter.py` exposing `run_case(request) -> {actual,
implementation_paths, evidence}` for suites `admission` and `linear_sizing`.
It must invoke the REAL application path: for admission, build a disposable
`SignalStore` + engine fixture, create the accounts/routes/budget state the
inputs describe, submit a signal through `engine.handle_signal` with the
paper broker wrapped in a counting proxy, and observe `admit_new_entry`
(an order intent/reservation was created), `selected_physical_account_count`,
`broker_call_count` (must be 0: planning mode flag on the engine that stops
before dispatch), `blocking_reasons` (from the decision trace, normalised to
`ADMISSION_BLOCKING_ORDER`). For sizing, call the engine's sizing path with a
stub adapter reporting the cash capacity. Never recompute the oracle.
Evidence: per case, the decision-trace row id and the sqlite file hash.
Run: `python docs/workflow-contract/run_contracts.py --adapter tests.workflow_adapter:run_case --isolated-non-live`
must exit 0 and write `APPLICATION_RESULTS.json` (commit it under
`docs/workflow-contract/evidence/`). Depends on WC-03/04/05/06 being wired
into the engine (WC-20); until then the adapter may bind the pure functions
and MUST label evidence `scope: "module_not_engine"` so nobody mistakes it.

### WC-11 — Independent reference model and the 120 race vectors
`tests/workflow_reference_model.py`: a small independent ledger/state model
(positions, reservations, intents, protection) with the invariants I01–I24
as predicates. `tests/test_wc11_race_vectors.py` runs all 120 permutations
through the REAL lifecycle manager/engine with a scripted paper broker
emitting the observations in the given order, checks every required
invariant after every event, and classifies each vector as PASS,
REJECTED_IMPOSSIBLE_ORDERING (with the causal edge that makes it impossible)
or FAIL. No vector may be skipped.

### WC-12 — Mutation harness for the §19.3 inventory
`tests/mutation/` with one mutant per inventory item implemented as a
monkeypatch that weakens the guard (e.g. make `collapse_bindings` return the
input unchanged; make `check_and_reserve` skip the owner level; round up in
`size_linear_long`; treat desired stop as confirmed) and a test proving a
mandatory test fails under the mutant. Report killed/surviving with the
reason for any documented equivalent.

### WC-20 — Engine integration (after WC-02..06)
Wire `_handle_signal` entries through: identity collapse → admission →
feasibility sizing per candidate → selection → `check_and_reserve` →
`OrderIntent` + outbox → dispatch → fills → protection, replacing the
ALLOC-01 claim/bind logic with the new reservation (keep its tests green by
mapping them onto the new path). Exits inherit the owning account (no
router). Adds are child intents of the lifecycle. Planning mode (`dry_run`)
stops before dispatch and records the trace (used by WC-10 and the TR-11
simulator).

### WC-21 — Console traces (gate G6)
Decision traces visible per signal (TR-03/TR-04): candidates, exclusion
reasons, selected account, reservation vector and state, intent state,
protection state; readiness and operations center extended with reservation
and intent health. Playwright tests.

### WC-22 — Evidence manifest and release gate report
Fill `requirements_traceability.json` from executed tests only; run
`validate_evidence.py`; produce `docs/workflow-contract/RELEASE_EVIDENCE.md`
with executed/pass/fail/not-run/blocked counts, code/config/data hashes,
killed/surviving mutants, finite-domain and event-sequence coverage, UI
journeys, remaining financial risks and external blockers; software
correctness separate from profitability evidence. Nothing is activated.
