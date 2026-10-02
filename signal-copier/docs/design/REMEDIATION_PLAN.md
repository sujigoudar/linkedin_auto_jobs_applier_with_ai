# Remediation plan: work packages for the gap audit

Source: `docs/audit/SOLUTION_GAP_ANALYSIS.md` (sections 3–5, 8) and the raw
audits under `docs/audit/raw/`. Every finding ID below refers to those files;
each raw entry carries `file:line` evidence, the scenario and the minimal fix.

This plan turns the dependency gates R0–R7 into small work packages (WP). Each
WP is written so that a worker with no prior context can execute it: it names
the files, the exact behaviour, the tests that prove it, and the commands that
must pass. Workers do not commit; the integrator verifies, commits and pushes
after each package.

Status vocabulary (from the audit): Designed / Implemented / Integrated /
Isolated-tested / Externally qualified / Deployed inactive / Live released.
Nothing in this plan reaches "Externally qualified" or beyond: no live venue,
no sandbox credentials and no live account is available or authorized here.

## Rules for every work package

1. Read the raw audit entry for each finding ID first (`docs/audit/raw/*.md`).
   Then read the code it cites. Do not guess.
2. Change only the files the package names unless a test or type check forces
   a one-line follow-up elsewhere. Report every extra file touched.
3. Never weaken, skip or delete an existing test to make a change pass. When
   an existing test pins the wrong behaviour named in the finding, rewrite
   that test to assert the corrected behaviour and say so in the report.
4. Fail closed: when a risk input is missing, reject with a message that says
   what is missing. Never default a quantity, price or balance.
5. Add the regression tests the package lists in a new file
   `tests/test_wpNN_<slug>.py`. Tests run the real engine, the real
   `SignalStore` on a `tmp_path` database and the paper broker; mock only a
   broker method when the package says to.
6. Verification before reporting done, run from `signal-copier/`:
   - `ruff check .`
   - the CI mypy command in `.github/workflows/signal-copier-ci.yml` (copy it
     verbatim)
   - `python -m pytest tests/test_wpNN_*.py -q` plus every existing test file
     the package lists under "also run"
7. Report: files changed, tests added (names), the exact pytest summary line,
   and anything left undone with the reason.

Shared code facts (verified 2026-10-02):

- `app/models.py`: `Side` is `BUY|SELL|CLOSE`. `Signal` is a dataclass with
  `quantity: float|None`, `price`, `stop_loss`, `take_profit`, `targets`,
  `entry_order_type`, option/future/fx specs, `channel_id`, `message_id`,
  `revision_id`, `original_message_id`. `OrderResult` has `status`
  (`PENDING|FILLED|REJECTED|ERROR`), `broker_order_id`, `filled_quantity`,
  `filled_price`, `fee`, `executed_at`. `DestinationAccount` has
  `multiplier`, `fixed_quantity`, `managed_lifecycle`, `max_notional_exposure`,
  `risk_percent_of_equity`, `exclusive_writer_qualified`,
  `daily_loss_limit_percent`, `min_equity_threshold`.
- `app/risk.py`: `size_for_account(signal, account)` defaults quantity to 1.0.
- `app/engine.py`: `_handle_signal` (489–1370) routes; the plain path sizes at
  ~1018; the managed path is `_handle_managed_entry` (2622) /
  `_handle_managed_close` (3019); `_resolve_close` (2177) builds the opposing
  order for a plain CLOSE; `close_position` (3268) is the dashboard exit.
- `app/reconciliation.py`: `_reconcile_broker_positions` computes
  `deficit = confirmed_owned_quantity - broker_owned` (~320); `_correct_position`
  applies terminal statuses (~470–500).
- `app/lifecycle/manager.py`: `request_exit` (1161), `resolve_pending_exit`
  (1332), `_apply_exit_fill` (1448), `on_stop_filled` (991),
  `_place_stop_locked` (1732), `retry_unprotected_positions` (335).
- `app/main.py`: `AccountRequest` (2411), `POST /accounts` (2459),
  `_account_has_exposure` (2518), `RoutingRuleRequest` (2524).
- `app/db.py`: `SCHEMA` plus `_COLUMN_MIGRATIONS` bootstrap new columns;
  alembic head is `0037`. New columns need a `_COLUMN_MIGRATIONS` entry AND
  a new alembic revision (copy the shape of `alembic/versions/0037_*.py`).
- Tests: `tests/conftest.py`; the API-test fixture pattern is in
  `tests/test_alloc11_journal_and_ledger_fixes.py` (`client` fixture, fresh
  `PaperBroker` via `monkeypatch.setitem(main_module.brokers, "paper", ...)`).

---

## Gate R0 — stop the bleeding

### WP-01 — B-02: no quantity means reject, never 1.0

Findings: B-02 (raw B), A-08.
Files: `app/risk.py`, `app/engine.py`, `app/backtest/simulator.py` (only if it
calls `size_for_account`), tests.

Steps:
1. In `app/risk.py` add `class UnsizedEntryError(ValueError)`. In
   `size_for_account`, when `account.fixed_quantity is None` and
   `signal.quantity is None`, raise
   `UnsizedEntryError("entry has no quantity and account '<id>' has no fixed_quantity (refusing to default to 1.0)")`.
   Remove the `1.0` default.
2. In `app/engine.py`, at every call of `size_for_account` for an ENTRY (plain
   path ~1018 and `_handle_managed_entry` ~2643), catch `UnsizedEntryError` and
   return/record a `REJECTED` `OrderResult` with that message, saved through
   `save_order_result` with the same `purpose`/`family_id` the branch already
   uses, and export the routing outcome the same way neighbouring rejections
   do. The managed path returns `_ManagedOrderOutcome(result, None, None)`.
3. Grep for other callers of `size_for_account` (backtest, previews) and make
   them surface the error as a rejection/`None`, never a crash.

Tests (`tests/test_wp01_unsized_entry_rejected.py`):
- plain account, `Signal(quantity=None)` BUY → status `rejected`, message
  contains "no quantity"; broker received nothing (`PaperBroker.fills == []`).
- managed account, same → `rejected`, no lifecycle created.
- `fixed_quantity=2` → fills 2.
- `quantity=None` CLOSE on a plain account holding 5 → still closes 5 (CLOSE is
  not sized by `size_for_account`).
Also run: `tests/test_engine.py`, `tests/test_risk*.py`, `tests/test_backtest*.py`.

### WP-02 — C-02: a plain CLOSE never carries SL/TP/targets

Findings: C-02 (raw C).
Files: `app/engine.py` (`_resolve_close`), tests.

Steps: in `_resolve_close` build the resolved `Signal` with
`stop_loss=None, take_profit=None, targets=[]` (keep `price`). Update the
docstring: a close is an exit; protective legs belong to entries.

Tests (`tests/test_wp02_close_strips_protection.py`): subclass `PaperBroker`
to capture the `signal` passed to `place_order`; plain account long 10;
`Signal(side=CLOSE, stop_loss=1, take_profit=2)` → captured signal has
`stop_loss is None`, `take_profit is None`, `targets == []`, side SELL, qty 10.
Also run: `tests/test_engine.py`.

### WP-03 — D-03: sign the broker readback by plan side

Findings: D-03 (raw D).
Files: `app/reconciliation.py`, tests.

Steps: in `_reconcile_broker_positions`, after reading `broker_owned`, convert
it to the lifecycle's own orientation:
`broker_owned_abs = -broker_owned if lifecycle.plan.side == Side.SELL else broker_owned`.
Use `broker_owned_abs` for the lost-response adoption branch (`> 0`) and the
deficit computation. If `broker_owned_abs < 0` (venue holds the OPPOSITE side),
log a warning with account/symbol and `continue` (never fabricate a fill).

Tests (`tests/test_wp03_short_readback_sign.py`): managed account, entry
`Side.SELL` 10 fills on paper (paper positions go to −10); run the reconciler
once → lifecycle still open, `confirmed_owned_quantity == 10`, no exit order
row, `store.get_position == -10`. Second test: readback −6 → `on_stop_filled`
applied with 4, owned 6. Third: readback +10 for a short plan → warning, no
change. Mock `broker.get_broker_position` for the second and third.
Also run: `tests/test_ops01_ops02_ops03_worker_health_and_reconciliation.py`.

### WP-04 — C-06/D-04: FILLED without a quantity falls back to requested

Findings: C-06, D-04 (raw C, raw D).
Files: `app/reconciliation.py` (`_correct_position`), tests.

Steps: on `new_status == FILLED` with `confirmed_quantity is None`, use the
row's `requested_quantity` (fall back to `optimistic_quantity` only when
`requested_quantity` is NULL). Log at warning that the adapter reported FILLED
without a size.

Tests (`tests/test_wp04_filled_without_quantity.py`): save a PENDING order row
with `requested_quantity=10`, `filled_quantity=None`; mock
`get_order_status` → `OrderResult(status=FILLED, filled_quantity=None)`; run
reconciliation once → position 10, row `filled_quantity == 10`.
Also run: `tests/test_reconciliation*.py`.

### WP-05 — D-07/F-01: exit path from the lifecycle, flip refused under exposure

Findings: D-07, F-01 (raw D, raw F).
Files: `app/engine.py` (`close_position`, `_handle_signal` managed/plain
branch), `app/main.py` (`POST /accounts`, provider/analyst override endpoints
that write `managed_lifecycle`), tests.

Steps:
1. `close_position`: choose the managed path when
   `self.lifecycle_manager.get_lifecycle(account_id, symbol)` is not None,
   else the plain path. Never consult `account.managed_lifecycle` for an exit.
2. `_handle_signal`: for a CLOSE, same rule: an open lifecycle → managed close;
   no lifecycle → plain close (even if the effective flag says managed).
3. `POST /accounts`: if the stored `managed_lifecycle` differs from the request
   and `_account_has_exposure(account_id)` → 409 with a message naming the
   account and "managed_lifecycle".
4. The provider/analyst settings-override endpoints (grep `managed_lifecycle`
   in `app/main.py` and `app/providers.py`): when the override would change
   the effective `managed_lifecycle` for an account with exposure → 409.
   Deleting an override is also a change.

Tests (`tests/test_wp05_exit_path_and_flip_guard.py`): (a) managed entry
fills and stop rests; set `account.managed_lifecycle=False` on the account
object; `close_position` still goes through `request_exit` and the paper stop
is cancelled (`broker._stop_orders` empty after). (b) plain long, flag flipped
True → `close_position` still flattens. (c) API: account with open position,
`POST /accounts` flipping `managed_lifecycle` → 409. (d) override endpoint → 409.
Also run: `tests/test_engine.py`, `tests/test_protection_transfer.py`,
`tests/test_alloc08_managed_and_properties.py`.

### WP-06 — F-03: partial account updates must not clobber safety fields

Findings: F-03 (raw F).
Files: `app/main.py`, `app/db.py` (if a merge helper is needed),
`app/static/views/tr08.js` and the "Pause new entries" caller (grep
`"/accounts"` in `app/static`), tests.

Steps:
1. Add `PATCH /accounts/{account_id}` taking `AccountPatchRequest` where every
   field is optional (`None` = unchanged). Load the stored row, merge, apply
   the same exposure guards as `POST` (broker change, managed_lifecycle change
   from WP-05), then `upsert_config_account` with the merged values.
2. Change the console's pause/unpause and TR-08 partial saves to use PATCH
   with only the fields they edit.
3. `POST /accounts` keeps full-replace semantics; document it in the docstring.

Tests (`tests/test_wp06_account_patch.py`): create an account with
`exclusive_writer_qualified=True`, `risk_percent_of_equity=0.5`,
`qualification_level="paper"`; `PATCH {"enabled": false}` → GET shows the
three fields unchanged and `enabled` false. PATCH with an unknown account → 404.
Playwright test is optional; if written, follow `test_alloc06_*`'s pattern.
Also run: `tests/test_alloc04_api_end_to_end.py`.

### WP-07 — G-07: most-specific routing rule wins

Findings: G-07 (self-audit, section 5 of the report).
Files: `app/routing.py` (`evaluate`), `app/static/views/tr11.js` (precedence
note only), tests.

Steps: in `evaluate`, iterate rules in precedence order: rules whose
`symbol_filter` names the symbol first (in insertion order), then rules with
no filter (insertion order). The trace keeps one entry per rule and gains
`"precedence": int` (0-based evaluation position). Dedup/paused logic
unchanged.

Tests (`tests/test_wp07_rule_precedence.py`): catch-all rule inserted first
→ account A; symbol-filtered rule inserted second → account B; signal for the
filtered symbol routes to B (single mode selects B); other symbols route to A.
Rewrite the assertion in `tests/test_tr11_routing_simulator.py` that pins
insertion order; say so in the report.
Also run: `tests/test_routing*.py`, `tests/test_alloc01_single_destination.py`.

---

## Gate R1 — explicit intent model

### WP-08 — Intent on the signal, `allow_short` on the account

Findings: A-01, B-14, A-10 (model part only).
Files: `app/models.py`, `app/sources/text_parser.py`, `app/sources/webhook.py`,
`app/db.py` (+ `_COLUMN_MIGRATIONS`), new alembic revision `0038`,
`app/main.py` (`AccountRequest`, `AccountPatchRequest`), `app/routing.py`
loaders, `config/routing.example.yaml`, tests.

Steps:
1. `app/models.py`: add
   `class Intent(str, enum.Enum): ENTRY_LONG="entry_long"; ENTRY_SHORT="entry_short"; SELL="sell"; EXIT="exit"; REDUCE="reduce"; STOP_UPDATE="stop_update"; TARGET_UPDATE="target_update"; CANCEL="cancel"; ADD="add"`.
   Add `Signal.intent: Optional[Intent] = None` and `Signal.reduce_fraction: Optional[float] = None`.
   In `Signal.__post_init__` (create one if absent) derive when `intent is None`:
   BUY→ENTRY_LONG, SELL→SELL (ambiguous; resolved by the engine in WP-09),
   CLOSE→EXIT. Document each value in one line.
2. `DestinationAccount.allow_short: bool = False`. Persist it:
   `config_accounts.allow_short INTEGER NOT NULL DEFAULT 0` via SCHEMA +
   `_COLUMN_MIGRATIONS` + alembic `0038`; `upsert_config_account`/
   `list_config_accounts`/the routing loaders carry it; API request models
   accept it; `tests/test_e01_alembic_migration_stamping.py` head → 0038.
3. Parser: word map `short`→ side SELL with intent ENTRY_SHORT; `sell`→ SELL
   with intent SELL; `close`/`exit`/`flat`→ CLOSE/EXIT; `trim`/`reduce`/
   `take profit on`→ CLOSE with intent REDUCE; `half`/`all`/`N%` after a
   reduce verb → `reduce_fraction` (0.5 / 1.0 / N/100). Keep the quantity
   regex as is.
4. Webhook: optional `intent` field validated against `Intent`; optional
   `reduce_fraction` (0 < x ≤ 1).

Tests (`tests/test_wp08_intent_model.py`): derivations; parser cases
("SHORT AAPL 10" → ENTRY_SHORT, "SELL AAPL 10" → SELL, "close half AAPL" →
REDUCE 0.5, "trim 25% AAPL" → REDUCE 0.25); webhook accepts/rejects intent;
account round-trip of `allow_short` through POST/GET.
Also run: `tests/test_text_parser*.py`, `tests/test_webhook*.py`,
`tests/test_e01_alembic_migration_stamping.py`.

### WP-09 — A-01: resolve SELL against the account's book

Findings: A-01, B-14.
Files: `app/engine.py`, tests.

Steps: in `_handle_signal`, before the plain/managed branch, resolve an
incoming `Intent.SELL` per account:
- the account holds a same-symbol LONG (plain: `store.get_position > 0`;
  managed: open lifecycle with `plan.side == BUY`) → treat as an EXIT: set
  `order_purpose="close"`, route through the existing close path; when the
  signal carries a quantity, pass it as a reduce quantity (WP-10 honours it).
- no long and `account.allow_short` → `Intent.ENTRY_SHORT` entry.
- no long and not `allow_short` → REJECTED
  "sell on account '<id>' with no long position and allow_short=false".
`Intent.ENTRY_SHORT` explicit on an account with `allow_short=False` → REJECTED.
A SELL that resolves to an exit is exempt from the entry-only gates
(qualification, loss limit, margin, min-equity, asset-class, bracket
embedding) exactly as CLOSE is.

Tests (`tests/test_wp09_sell_resolution.py`): plain long 5, SELL 10 → sells 5,
flat, no short; plain flat + allow_short False → rejected; plain flat +
allow_short True → short 10; managed long with stop, SELL (no qty) → exit via
`request_exit`, stop cancelled; explicit ENTRY_SHORT on allow_short False →
rejected. Rewrite any existing test asserting that "sell" opens a short on a
default account; list them in the report.
Also run: `tests/test_engine.py`, `tests/test_alloc01_single_destination.py`,
`tests/test_track18*.py` (provider ownership).

### WP-10 — D-05/D-11: partial exits honour the quantity

Findings: D-05, D-11.
Files: `app/engine.py` (`_resolve_close`, `_resolve_and_submit_plain_close`,
`_handle_managed_close`), `app/lifecycle/manager.py` (`on_price_update` target
sizing), tests.

Steps:
1. `_resolve_close`: `requested = signal.quantity` or
   `signal.reduce_fraction * abs(position)`; quantity = `min(requested, abs(position))`
   when requested, else `abs(position)`. Reject (return a REJECTED result from
   the caller) when `requested <= 0`.
2. Managed close: compute the same requested quantity against
   `lifecycle.confirmed_owned_quantity` (fraction of OWNED, not planned) and
   pass it to `request_exit`.
3. Targets: in `on_price_update`, size a target as
   `reduce_fraction * confirmed_owned_quantity` at fire time; a target with
   `reduce_fraction None` is sized by WP-13's default split (until then treat
   None as "reject at plan validation": `validate_plan` returns an error for a
   SELL target without `reduce_fraction`).

Tests (`tests/test_wp10_partial_exit.py`): plain long 10, CLOSE qty 4 → sells
4, position 6; CLOSE reduce_fraction 0.5 → 3; CLOSE qty 50 → sells 6 (capped);
managed long 10 with stop: CLOSE qty 4 → `request_exit` 4, stop resized to 6
(`broker._stop_orders` quantity 6); target 50 % on a position filled 6 of 10
planned → sells 3.
Also run: `tests/test_protection_transfer.py`, `tests/test_engine.py`.

### WP-11 — A-02/A-11: edits amend, deletions cancel, never a second entry

Findings: A-02, A-11, A-03.
Files: the edit/revision handling path (grep `revision_id`,
`original_message_id`, `SourceEventKind.EDITED`/`DELETED` in `app/engine.py`,
`app/sources/*.py`, `app/db.py`), `app/lifecycle/manager.py` (public
`update_stop_price(account, symbol, price)` wrapper over `_replace_stop_price`
if none exists), tests.

Steps:
1. When a signal arrives with `original_message_id` (an edit): look up the
   orders already produced by the original signal (`signals` by
   `channel_id`+`message_id`). If an ENTRY was already FILLED/PENDING for an
   account: never place a new entry. If the edit changes `stop_loss` on a
   managed lifecycle → replace the stop price; if it changes `take_profit`/
   `targets` → update the lifecycle targets; record an `orders` row with
   `status=rejected`/message "edit applied: stop 95→90" is NOT acceptable —
   instead record the applied revision in the existing stop/target event log
   and return an `OrderResult(status=FILLED? no)`. Return
   `OrderResult(status=REJECTED, message="edit applied to existing position; no new entry")`
   only when nothing was amendable; otherwise return the stop-replace result.
2. If the original entry is still PENDING at the broker (resting limit) and
   the edit changes price/quantity → cancel and re-place through the ledger
   (reuse `_ledgered_cancel_order`); if the adapter has no cancel capability →
   REJECTED "cannot amend resting entry on this adapter".
3. A DELETED/cancelled source event for a message whose entry is still
   PENDING → cancel it; if FILLED → no action, log.

Tests (`tests/test_wp11_edit_and_delete.py`): managed entry fills with stop
95; edit with stop 90 → exactly one entry order row, paper stop price 90; edit
with a new price only → no new order; delete event for a pending entry (mock
`place_order` to return PENDING) → `cancel_order` called.
Also run: `tests/test_track41*.py`, `tests/test_signal_correlation*.py`.

### WP-12 — A-04/A-05/A-13: validate the instrument before routing

Findings: A-04, A-05, A-13, A-08.
Files: `app/sources/text_parser.py`, tests.

Steps:
1. Symbol token validation: reject (MISSING_DATA/NOT_A_SIGNAL disposition,
   never a Signal) a symbol that is purely numeric, or in a stop-word set
   {`TO`, `HALF`, `ALL`, `AT`, `THE`, `A`, `AND`, `OPEN`, `CLOSE`, `NOW`}.
   Consume "to open"/"to close" verbs: "buy to open" → entry, "sell to close"
   → exit intent, "buy to close" → exit (cover).
2. Option alerts: a token matching `\d+(\.\d+)?[CP]\b` or `\d+\s*(call|put)`
   plus an expiry (`M/D`, `M/D/YY`, ISO) → `asset_class=OPTION` with a full
   `OptionContractSpec`; if strike or expiry is missing → MISSING_DATA with
   reason "option contract incomplete", never an equity Signal.
3. Quantity tokens `%`, `$`, ranges (`150-152`) are never quantities:
   `N%` → reduce_fraction (for reduce verbs) or ignored; `$N` → ignored with a
   note; `A-B` after the symbol → `price_low/price_high`.
4. Asset class: never infer FUTURE/CRYPTO from symbol shape alone; keep the
   source-declared class; when none, leave the parser's existing default but
   set `raw["asset_class_inferred"]=True` so the engine's gate message can say
   so.

Tests (`tests/test_wp12_parser_instrument_validation.py`): the probe cases
from raw A ("SELL half AAPL", "BUY TO OPEN AAPL 150C 1/17 @ 2.50",
"BUY 10 AAPL", "BUY AAPL 150-152", "BUY AAPL 150C" without expiry).
Also run: `tests/test_text_parser*.py`, `tests/test_e02*.py`.

### WP-13 — A-10/D-06: stop/target updates and default target sizing

Findings: A-10, D-06, A-16.
Files: `app/engine.py` (`_handle_managed_entry` targets; new intent branch),
`app/lifecycle/manager.py` (`validate_plan`, public `update_targets`), tests.

Steps:
1. Target sizing default: when a signal's `targets` carry no `fraction`, size
   them equal-split with the last level taking the remainder
   (3 levels → 1/3, 1/3, rest). A single `take_profit` → one target at 1.0.
   `validate_plan` rejects a SELL target with `reduce_fraction` None or ≤ 0.
2. Intent `STOP_UPDATE` (signal with `stop_loss`, no quantity): for every
   destination with an open lifecycle → replace the stop price (ledgered);
   plain accounts → REJECTED "stop update needs a managed lifecycle".
   `TARGET_UPDATE` → replace the lifecycle's unfired targets.
3. Parser wiring: the existing classifier output for stop/target updates
   (see raw A-10 for where it is dropped) now sets `intent` instead of being
   discarded.

Tests (`tests/test_wp13_targets_and_updates.py`): "BUY AAPL 100 SL 95 TP1 105
TP2 110" managed → targets 0.5/0.5; price 105 → sells 50; price 110 → sells
50; STOP_UPDATE to 97 → paper stop at 97; plain account → rejected.
Also run: `tests/test_protection_transfer.py`, `tests/test_text_parser*.py`.

### WP-14 — C-03/C-04: adapters receive the exit intent

Findings: C-03, C-04.
Files: `app/brokers/tastytrade.py`, `app/brokers/ninjatrader.py`,
`app/brokers/mt4_mt5.py`, tests (mock HTTP only; no venue).

Steps: `signal.intent` is now on every Signal the engine submits (the engine
sets `Intent.EXIT` on the resolved close signal in `_resolve_close` and the
lifecycle's `_submit_exit_order` — add that one line in each). Adapters:
Tastytrade emits "Sell to Close"/"Buy to Close" when `intent == EXIT`;
NinjaTrader emits `sentiment: flat` for an exit; MT5/MetaApi close by position
ticket when one is known (persist the ticket from the entry fill in
`orders.broker_order_id`; the adapter takes it from
`signal.raw.get("position_ticket")` which the engine fills from the entry row
of the same family when present) else send the opposing market order with a
warning in the result message.

Tests (`tests/test_wp14_adapter_exit_intent.py`): capture the outbound payload
per adapter with a stub HTTP client; assert the fields above.
Also run: `tests/test_tastytrade*.py`, `tests/test_ninjatrader*.py`,
`tests/test_mt*.py`.

---

## Gate R2 — sizing model

### WP-15 — B-03: contract multipliers in every notional

Findings: B-03, B-10 (multiplier part).
Files: `app/risk.py` (new `contract_multiplier(signal) -> float`),
`app/engine.py` (`_check_risk_basis`, `_check_buying_power`,
`_try_reserve_capital`), `app/capital_allocator.py` (notional helpers), tests.

Steps: multiplier = option.multiplier / future.multiplier / fx unit size
(`standard_lot_100000`→100000, `mini_lot_10000`→10000, `micro_lot_1000`→1000,
`units`→1) / 1.0 otherwise. Every `notional = |qty| * |price|` becomes
`|qty| * |price| * multiplier`. An OPTION or FUTURE entry with no spec →
REJECTED "contract spec missing". FOREX with no spec → multiplier 1 and the
result message notes "fx unit assumed: units".

Tests (`tests/test_wp15_contract_multiplier.py`): option 10 @ 2.50 ×100 gates
on 2500 (set `max_notional_exposure=2000` → rejected); future with spec; fx
lots; option without spec → rejected.
Also run: `tests/test_e03_capital_exposure_gate.py`, `tests/test_alloc03_strategy_budget.py`.

### WP-16 — B-01: risk-fraction sizing

Findings: B-01.
Files: `app/models.py` (`DestinationAccount.sizing_mode: str = "multiplier"`,
`risk_fraction: float|None`), `app/db.py` (+migration `0039`), `app/main.py`
request models, `app/risk.py`, `app/engine.py`, tests.

Steps: `sizing_mode` ∈ {`multiplier`, `fixed`, `risk_fraction`}. For
`risk_fraction`: the engine reads `broker.get_account_balance(account)`; needs
`equity`, `signal.price`, `signal.stop_loss`; qty =
`floor(equity * risk_fraction / (|price - stop| * contract_multiplier))`; any
missing input → REJECTED naming it; qty 0 → REJECTED. Keep
`risk_percent_of_equity` as the ceiling check it already is.

Tests (`tests/test_wp16_risk_fraction_sizing.py`): paper equity 100 000,
risk 0.01, price 50, stop 45 → 200 shares; no stop → rejected; equity None
(mock) → rejected.
Also run: `tests/test_engine.py`, `tests/test_e01_alembic_migration_stamping.py`.

### WP-17 — B-04: venue quantization

Findings: B-04, D-11 (quantization part).
Files: `app/brokers/base.py` (`normalize_quantity(self, account, symbol, quantity) -> float|None`,
default: return `quantity`), `app/brokers/paper.py` (step 1e-8),
`app/brokers/alpaca.py` (equities: whole shares unless
`fractional=True` in account `symbol_map`? no — read raw B-04 and use the
simplest rule it names), `app/brokers/ccxt_broker.py` (market precision /
limits when markets are loaded, else `None` = unknown), `app/engine.py`
(after sizing: `q = broker.normalize_quantity(...)`; `None` → REJECTED
"quantity step unknown"; `0` → REJECTED "below venue minimum"),
`app/lifecycle/manager.py` (quantize exit and stop quantities the same way),
tests.

Tests (`tests/test_wp17_quantization.py`): stub adapter with step 1 → 3.7 →
3; step unknown → rejected; exit of 3.5 on a step-1 venue → 3.
Also run: `tests/test_engine.py`, `tests/test_protection_transfer.py`.

---

## Gate R3 — order-family tracking

### WP-18 — D-01: bracket child legs are tracked orders

Findings: D-01, C-07.
Files: `app/models.py` (`OrderResult.child_order_ids: dict[str, str]` default
empty, keys `stop`/`take_profit`), `app/brokers/alpaca.py` (populate from
response `legs`), `app/brokers/paper.py` (populate when it registers its
simulated stop), `app/engine.py` (after a plain-account bracket entry, save
one `orders` row per child with `purpose='stop_exit'`/`'target_exit'`,
`status=PENDING`, `family_id=entry signal id`, `broker_order_id=child id`),
`app/reconciliation.py` (child rows are polled like any PENDING row; a FILLED
child applies the exit to `positions` and cancels the sibling if the adapter
reports it still open), tests.

Tests (`tests/test_wp18_bracket_children.py`): paper bracket entry → three
rows (entry FILLED, stop PENDING, tp PENDING) sharing `family_id`; simulate
the stop fill → stop row FILLED, position 0, tp row REJECTED/cancelled.
Also run: `tests/test_reconciliation*.py`, `tests/test_tr06*.py`.

### WP-19 — D-02/D-10: poll the stop before attributing a deficit

Findings: D-02, D-10.
Files: `app/reconciliation.py`, `app/lifecycle/manager.py`
(`on_stop_filled(..., filled_price=...)` already exists; add
`resize_stop_to_owned(account, symbol, owned)`), tests.

Steps: in `_reconcile_broker_positions`, for a lifecycle with
`stop.broker_order_id`, call `get_order_status` first: FILLED → 
`on_stop_filled(filled_quantity, filled_price)` with the venue's figures;
otherwise, on a deficit, resize/cancel the resting stop to the new owned
quantity BEFORE applying the correction; a full deficit with a live stop →
cancel the stop, then close the lifecycle. Never delete lifecycle state while
`stop.broker_order_id` is live and uncancelled (log and keep it).

Tests (`tests/test_wp19_stop_polling.py`): managed long 100 with paper stop;
mock readback 60 and stop open → stop resized to 60, owned 60; mock stop
FILLED @ 44 → exit journaled with price 44, lifecycle closed; readback 0 with
stop still open → stop cancelled first.
Also run: `tests/test_ops01_ops02_ops03_worker_health_and_reconciliation.py`.

### WP-20 — D-08/D-09: ambiguous exits and stop placements

Findings: D-08, D-09.
Files: `app/lifecycle/manager.py`, tests.

Steps: (1) in `request_exit`, an `OrderResult.status == ERROR` from
`_submit_exit_order` is handled exactly like the raised-exception branch
(open a `PendingExit(broker_order_id=None)`, do not restore full stop
coverage). (2) in `_place_stop_locked`/`retry_unprotected_positions`, before
placing a new stop, read the last `stop_change` ledger row for this
(account, symbol); if it has a `remote_identifiers.broker_order_id`, poll it
with `get_order_status`; if it is open → adopt it (set `stop.broker_order_id`,
status CONFIRMED) instead of placing another. (3) at startup
(`restore_from_store`), run the same adoption for every unresolved
`stop_change` row.

Tests (`tests/test_wp20_ambiguous_exit_and_stop.py`): exit returns ERROR →
pending exit with no id, stop not re-armed; readback 0 → resolved. Stop
placement returns ERROR but the paper broker actually registered the stop
→ retry adopts it; `len(broker._stop_orders) == 1`.
Also run: `tests/test_protection_transfer.py`, `tests/test_alloc07_crash_boundaries.py`.

### WP-21 — B-05/C-11/C-12/C-20: definite rejection vs ambiguity

Findings: B-05, C-11, C-12, C-20.
Files: new `app/brokers/classification.py`
(`classify_http_failure(status_code: int|None, exc: Exception|None) -> OrderStatus`),
`app/brokers/alpaca.py`, `app/brokers/ccxt_broker.py`, `app/brokers/tradovate.py`,
`app/brokers/schwab.py`, `app/brokers/robinhood.py`, `app/brokers/mt4_mt5.py`,
tests.

Rule: 4xx except 408/429 → REJECTED (definite); 408/429/5xx/timeouts/
connection errors → ERROR (ambiguous). ccxt: `InvalidOrder`,
`InsufficientFunds`, `BadSymbol` → REJECTED; `NetworkError`/`RequestTimeout`
→ ERROR. MT5 requote/timeout → ERROR, not REJECTED.

Tests (`tests/test_wp21_failure_classification.py`): table test of the
helper; per adapter one stubbed 400 and one stubbed 503.
Also run: `tests/test_alpaca_broker.py`, `tests/test_ccxt*.py`,
`tests/test_alloc05_unknown_and_exit_scope.py`.

### WP-22 — client order ids and ledger-driven resolution

Findings: C-19, D-13 (reconciler pass), section 6 "no adapter sends a client
order id".
Files: `app/models.py` (`Signal.client_order_id: Optional[str]`),
`app/engine.py` (set it to the command-ledger idempotency key before
`place_order`), `app/brokers/alpaca.py` (`client_order_id` on submit;
`find_order_by_client_id` via `GET /v2/orders:by_client_order_id`),
`app/brokers/ccxt_broker.py` (`clientOrderId` param; lookup via
`fetch_open_orders`/`fetch_order` where the exchange supports it),
`app/brokers/base.py` (optional `find_order_by_client_id`),
`app/reconciliation.py` (a pass over `UNKNOWN_AMBIGUOUS` ledger rows: when
the adapter can look up by client id → resolve as placed (adopt the order id)
or not placed (release reservation via the existing
`engine.resolve_unknown_submission` path, with evidence
"venue lookup returned 404")), tests.

Tests (`tests/test_wp22_client_order_id.py`): engine passes the key; stub
adapter lookup returns an order → ledger row resolved placed, orders row
updated; lookup returns none → reservation released.
Also run: `tests/test_alloc05_unknown_and_exit_scope.py`, `tests/test_alloc11_journal_and_ledger_fixes.py`.

### WP-23 — D-14/D-16/D-17: recovery edges

Findings: D-14, D-16, D-17, D-15.
Files: `app/reconciliation.py`, `app/lifecycle/manager.py`,
`app/brokers/base.py` + `app/brokers/ccxt_broker.py` (symbol on
`cancel_order`/`replace_stop_quantity` as an optional kwarg), `app/main.py`
(owner endpoint `POST /lifecycles/{account_id}/{symbol}/unregister` allowed
only when `confirmed_owned_quantity == 0` and no live stop id), tests.

Steps: (1) a pending entry with no order id, `broker_owned == 0` and age >
`config.LOST_ENTRY_GRACE_SECONDS` (default 300) → `resolve_pending_entry(0,
remainder_cancelled=True)`. (2) at startup, for each restored lifecycle, read
the venue position: venue > tracked → adopt the difference as owned and
re-protect (log + WP-34 alert). (3) ccxt passes the symbol it knows from
`plan.symbol` (manager passes `symbol=` on every cancel/replace).
(4) `retry_unprotected_positions` also retries when `uncovered_quantity > 0`.

Tests (`tests/test_wp23_recovery_edges.py`): one per step with paper/stub.
Also run: `tests/test_protection_transfer.py`, `tests/test_alloc07_crash_boundaries.py`.

---

## Gate R4 — journal correctness

### WP-24 — E-02: replay on quantity, not status

Findings: E-02.
Files: `app/economics.py`, `app/db.py` (`list_filled_orders_chronological`
and any replay query filtering `status='filled'`), `app/reconciliation.py`
(`_correct_position` exports the partial), tests.

Tests (`tests/test_wp24_partial_then_cancel.py`): PENDING 100, poll →
REJECTED with `filled_quantity=30` → position 30, economics open qty 30, a
later sell 30 replays as a close (no short), one export envelope for 30.
Also run: `tests/test_account_economics*.py`, `tests/test_reconciliation*.py`.

### WP-25 — E-03/E-05/E-16: exit prices are real or unknown

Findings: E-03, E-05, E-16.
Files: `app/lifecycle/manager.py` (`resolve_pending_exit(..., filled_price)`
→ `_apply_exit_fill(exit_price=...)`), `app/reconciliation.py` (deficit-
inferred exits persist `filled_price=None`), `app/brokers/paper.py` (exits
fill at `last simulated price` when known else `filled_price=None`, never
0.0), `app/economics.py` (`filled_price` None or ≤ 0 → unresolved), tests.

Tests (`tests/test_wp25_exit_prices.py`): async target exit resolved with
price 61 → row price 61; deficit-inferred stop → price NULL and the symbol
listed in `incomplete_symbols` with reason; paper exit without a simulated
price → None.
Also run: `tests/test_account_economics*.py`, `tests/test_protection_transfer.py`.

### WP-26 — E-06: promotion needs coverage

Findings: E-06.
Files: `app/provider_value.py` (and the endpoint in `app/main.py` that
returns `promote`), `app/db.py` (plain-account closes get a `family_id` by
FIFO match to the entry order of the same account/symbol/source when saving
a close), tests.

Rule: `promote` only when `unknown_outcome_episodes == 0` and
`open_episodes / total <= 0.2` (constant `PROMOTION_MAX_OPEN_RATIO`); the
response carries `coverage = {decided, open, unknown, total}`.

Tests (`tests/test_wp26_promotion_coverage.py`): 10 wins + 25 unknown-price
exits → not promotable, coverage shows 25 unknown; plain-account entry+close
→ one episode with a family.
Also run: `tests/test_provider_value*.py`, `tests/test_tr09*.py`.

### WP-27 — E-07/E-08: real fill timestamps

Findings: E-07, E-08.
Files: `app/brokers/alpaca.py` (`executed_at` from `filled_at`),
`app/brokers/ccxt_broker.py` (from `timestamp`), `app/db.py`
(`orders.confirmed_at` column + migration `0040`; `_update_order_status_locked`
keeps `executed_at` unless the result carries a real fill time),
`app/execution_quality.py` (exclude `purpose in ('stop_exit','target_exit',
'time_exit')` and `signals.source='lifecycle_manager'` from latency), tests.

Tests (`tests/test_wp27_fill_timestamps.py`).
Also run: `tests/test_execution_quality*.py`, `tests/test_e01_alembic_migration_stamping.py`.

### WP-28 — E-11/B-10: currency on accounts and fills

Findings: E-11, B-10.
Files: `app/models.py` (`DestinationAccount.currency: str = "USD"`,
`AccountBalance.currency`), `app/db.py` (`config_accounts.currency`,
`orders.price_currency`, migration `0041`), adapters that know the currency
(paper USD, Alpaca USD, ccxt quote currency, OANDA account currency),
`app/economics.py` (`realized_pnl_by_currency`; `realized_pnl` only when one
currency), `app/capital_allocator.py` (refuse to sum reservations across
currencies: `unresolved_reason="mixed currencies"`), `app/export_events.py`
(use the row's currency, never a guessed USD), tests.

Tests (`tests/test_wp28_currency.py`).
Also run: `tests/test_account_economics*.py`, `tests/test_export*.py`.

### WP-29 — E-15/G-C-14: fees reach the book

Findings: E-15, G-C-14, E-09.
Files: `app/brokers/ccxt_broker.py` (fee from fill), `app/brokers/alpaca.py`
(leave None unless the API reports it; document), `app/export_events.py`
(pass `fee` as `Money` when known; emit a correlated FEE event from
`save_order_result` when `fee` is known), `app/economics.py`
(`net_realized` only when every contributing fill has a non-NULL fee, else
`None` with `fee_coverage`), tests.

Tests (`tests/test_wp29_fees.py`).
Also run: `tests/test_export*.py`, `tests/test_account_economics*.py`.

---

## Gate R5 — risk controls that exist

### WP-30 — B-08/F-02: loss limits end to end

Findings: B-08, F-02.
Files: `app/db.py` (load/save `daily_loss_limit_percent`,
`min_equity_threshold` on `config_accounts` + migration `0042`; new table
`risk_halts(account_id, reason, triggered_at, cleared_at, cleared_by)`),
`app/routing.py` loaders, `app/main.py` (request models; `GET /risk-halts`,
`POST /risk-halts/{account_id}/clear`), `app/daily_loss_limiter.py`
(constructor takes `brokers`; daily P&L = realized today from
`compute_account_economics` + mark-to-market from the latest equity snapshot
when available; signed, only losses count; on breach insert a `risk_halts`
row), `app/engine.py` (an uncleared halt row rejects entries even after a
restart), tests.

Tests (`tests/test_wp30_loss_limits.py`): limit 5 %, equity 100 000, realized
−6 000 today → entry rejected, halt row exists; restart (new engine, same
store) → still rejected; clear via API → admitted.
Also run: `tests/test_alloc09_loss_limit_fails_closed.py`, `tests/test_daily_loss*.py`.

### WP-31 — B-09: margin detector fed from balances

Findings: B-09.
Files: `app/engine.py` (pass `balance.equity`/`maintenance_margin`/
`excess` from `get_account_balance` into `check_and_persist_margin_call`;
block entries while an unresolved `margin_call_alerts` row exists),
`app/margin_call_detector.py`, tests.

Tests (`tests/test_wp31_margin_detector.py`).
Also run: `tests/test_margin*.py`.

### WP-32 — B-07/B-11: buying power fails closed; leverage cap

Findings: B-07, B-11, B-16.
Files: `app/engine.py` (`_check_buying_power`: no figure and no configured
ceiling → REJECTED; include this process's pending reservations),
`app/models.py` (`DestinationAccount.max_gross_leverage: float = 1.0`),
`app/db.py` (+migration `0043`), `app/main.py`, `app/brokers/paper.py`
(cash never below 0 on buys; shorts require margin = notional, equity =
cash + Σ position × last price), tests.

Rule: `(confirmed + pending + new_notional) <= max_gross_leverage * (equity - maintenance_margin)`.

Tests (`tests/test_wp32_buying_power_and_leverage.py`).
Also run: `tests/test_e03_capital_exposure_gate.py`, `tests/test_paper*.py`.

### WP-33 — B-13/C-17: qualification keyed by venue environment

Findings: B-13, C-17.
Files: `app/qualification.py`, `app/db.py` (route key gains `environment`),
`app/brokers/base.py` (`venue_environment(self, account) -> str` default
`"unknown"`; Alpaca returns `paper`/`live` from its base URL; ccxt
`sandbox`/`live`), tests.

Rule: a route is release-approved only for the environment it was qualified
in; `revoked` is a terminal state; `is_route_release_approved` checks both.

Tests (`tests/test_wp33_qualification_environment.py`).
Also run: `tests/test_track34*.py`, `tests/test_qualification*.py`.

### WP-34 — F-06: a human is notified

Findings: F-06, F-10 (sweep part).
Files: new `app/alerts.py` (`AlertSink` with `record(kind, account_id,
message, payload)` → `alerts` table + optional outbound webhook
`config.ALERT_WEBHOOK_URL`, posted with the existing http client, failures
logged never raised), `app/db.py` (`alerts` table + migration `0044`),
`app/main.py` (`GET /alerts?unacknowledged=1`, `POST /alerts/{id}/ack`;
startup sweep that marks `claimed`/`selected` allocation intents older than
`ALLOCATION_INTENT_STALE_SECONDS` as `skipped` with reason "stale after
restart" and raises an alert), call sites: protection deficit
(`_restore_stop_coverage`), loss halt (WP-30), unknown submission (engine
ambiguous branch), skipped allocation (`skip_allocation_intent`),
venue>tracked adoption (WP-23), tests.

Tests (`tests/test_wp34_alerts.py`): each call site produces one row; webhook
stub receives JSON; ack works; stale intent swept.
Also run: `tests/test_alloc07_crash_boundaries.py`.

### WP-35 — F-07/F-04/F-08: rule deletion, schema check, disk

Findings: F-07, F-04, F-08.
Files: `app/main.py` (rule delete/narrow → 409 when a provider routed by
that rule has open exposure on a removed destination, unless
`?force=true`, which records an alert), the TR-16 deployment check
(`alembic current` vs. bootstrap: report "bootstrapped, stamped at head" as
OK), `/health` (disk free below `config.MIN_FREE_DISK_MB` → not ready),
tests.

Tests (`tests/test_wp35_ops_guards.py`).
Also run: `tests/test_tr16*.py`, `tests/test_health*.py`.

---

## Gate R6 — adapter honesty

### WP-36 — C-01/C-05/C-10: declare only what works

Findings: C-01, C-05, C-10.
Files: `app/brokers/base.py` (`supported_entry_order_types: frozenset =
{MARKET}`; `can_route_entries: bool = True`), `app/brokers/tastytrade.py`
and `app/brokers/tradestation.py` (remove OPTION from
`supported_asset_classes`), `app/brokers/ninjatrader.py`, `rithmic.py`,
`signalstack.py`, `mt4_mt5.py` (`can_route_entries=False` with a docstring
line each saying why), `app/brokers/alpaca.py` + `ccxt_broker.py` (implement
LIMIT entries: `limit_price=signal.price`; add `LIMIT` to the set),
`app/engine.py` (entry with `entry_order_type` not in the adapter's set →
REJECTED "adapter does not support limit/stop entries"), `README.md`
adapter table, tests.

Tests (`tests/test_wp36_adapter_declarations.py`).
Also run: `tests/test_alpaca_broker.py`, `tests/test_broker_capability_gate.py`.

### WP-37 — sandbox-gated venue tests (code only; cannot run here)

Files: `tests/venue/test_alpaca_paper_venue.py`,
`tests/venue/test_ccxt_sandbox_venue.py`, `docs/testing/VENUE_QUALIFICATION.md`.

Each file `pytest.skip`s unless the documented env vars are set; covers
submit/status/cancel/position/balance/client-id lookup with tiny quantities.
Status stays "Isolated-tested" until someone runs them with credentials.

---

## Gate R7 — commercial seam

### WP-38 — copier side of the seam

Findings: G-C-24 (paper ids), G-C-25 (per-account routing outcome),
G-C-13 (evidence class per account).
Files: `app/brokers/paper.py` (order ids = uuid4, unique across restarts),
`app/export_events.py` + `signal_platform_contracts` (routing outcome carries
`account_id`; add `held_stale`, `conflicting_source_data`, `not_selected`
outcomes; envelope carries `evidence_class` per account: paper/live/
simulated from the adapter's `venue_environment`), tests.

Tests (`tests/test_wp38_copier_seam.py`).
Also run: `tests/test_export*.py`, contract tests under
`../signal_platform_contracts`.

### WP-39 — commercial side of the seam

Findings: G-C-02, G-C-12, G-C-26, G-C-10.
Files: under `../signal-portfolio-commercial/` — `PublicationIntent.side`
(both adapters use it; CLOSE maps to the channel's close action), PLATFORM
ledger entries keyed by `account_id`, cursor advance on unknown non-economic
kinds (log + skip, never stall), customer product selection reachable from
self-signup (minimal: list published products + select one), tests in that
package's own suite.

---

## Gate close-out

### WP-40 — documentation and traceability

Files: `CHANGELOG.md` (Unreleased), `README.md` (routing precedence, intent
words, sizing modes, alerts, adapter table), `docs/adr/0013-intent-model.md`,
`docs/adr/0014-sizing-and-quantization.md`, `docs/design/CAPITAL_ALLOCATION.md`,
`docs/testing/ALLOCATION_TRACEABILITY.yaml/.md` (one row per WP test),
`docs/state/PROGRESS.md`, `docs/audit/SOLUTION_GAP_ANALYSIS.md` (status
column per finding: Implemented / Isolated-tested / not externally qualified).
