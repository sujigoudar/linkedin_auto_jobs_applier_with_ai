# Audit D — Position lifecycle, protection, exits, reconciliation

Repo: `signal-copier` (read-only audit; no repo files touched, no pytest run).
All paths relative to `signal-copier/`. Line numbers are from the working tree as read on 2026-10-02.

Severity: P0 = can create unintended exposure/loss/oversell · P1 = silently wrong · P2 = missing capability · P3 = doc/test.
Classification: semantic-miss | disconnected | mock-only-tested | silently-unsupported | missing | ok.

Legend for the two products (ADR-0008): **plain** = `managed_lifecycle=False`, stop must be a native bracket or the entry is rejected (`app/engine.py:1016-1053`); **managed** = `PositionLifecycleManager` (protect-first, logical targets, `CloseArbiter`).

---

## Findings

### D-01 — Plain accounts: the native bracket's stop/TP legs are tracked by nobody; local `positions` never learns the position went flat
- **Severity:** P0
- **Classification:** semantic-miss
- **Evidence:**
  - Plain entry records the fill once and never again: `app/engine.py:1284-1293` (`record_fill` on FILLED/partial), `:1305-1322` (`save_order_result` of the parent only).
  - Reconciler polls only rows `WHERE o.status = 'pending' AND o.broker_order_id IS NOT NULL` — `app/db.py:3328-3332`; `app/reconciliation.py:147-199`. Bracket child legs never get an `orders` row.
  - Alpaca embeds the legs and returns only the parent id: `app/brokers/alpaca.py:138-149`, `:167-173`. IBKR builds parent+children but caches/returns only the parent: `app/brokers/ibkr.py:111-146`. MT4/MT5 send `sl`/`tp` on the entry (`app/brokers/mt4_mt5.py:122-125`, `:262-265`) and implement neither `get_order_status` nor `get_broker_position`.
  - CLOSE gate: `app/engine.py:2367-2393` (`_reconcile_before_plain_close`). With readback (Alpaca, ccxt-perp, paper) a mismatch → REJECTED and **nothing ever corrects `positions`**. Without readback (IBKR, MT5) and `exclusive_writer_qualified=True` (`app/models.py:706`) the close proceeds and `_resolve_close` sells `abs(position)` — `app/engine.py:2191-2192`.
  - Stale `positions` keeps counting as exposure: `app/capital_allocator.py:178-200` replays confirmed fills from `orders`, so gated accounts stay blocked.
- **Scenario:** Plain IBKR/MT5 account, `exclusive_writer_qualified=True`. BUY 100 with bracket → stop fills at the venue → local still 100 → provider CLOSE → SELL 100 → account is now short 100 (MT5 netting / IBKR). On Alpaca the same CLOSE is rejected forever ("does not match this service's own tracked position"), dashboard shows an open position that does not exist, and new entries stay blocked by the notional gate.
- **Fix:** On a bracket entry, persist the child-leg order ids (Alpaca `legs[]`, IBKR child `orderId`s) as `orders` rows and poll them; or run a periodic `get_broker_position` pass for plain accounts and zero `positions` when the venue is flat. Never let an `exclusive_writer_qualified` close run on a bracket-entered position whose leg status is unknown.

### D-02 — Managed: any broker-position deficit is attributed to the stop; the resting stop at the venue is never cancelled or resized
- **Severity:** P0
- **Classification:** disconnected
- **Evidence:** `app/reconciliation.py:320-324` (`deficit = confirmed_owned - broker_owned` → `on_stop_filled(deficit)`), no `get_order_status(stop.broker_order_id)` anywhere (grep). `app/lifecycle/manager.py:1002-1036` (`on_stop_filled`) only edits local `protected_quantity`/`broker_order_id`; a full deficit sets `closed=True` and deletes `lifecycle_state` (`:1021-1024`, `:1505-1506`) while the venue stop keeps resting.
- **Scenario:** Managed Alpaca long 100 with a 100-share GTC stop. Operator sells 40 by hand at the broker (or a corporate action). Next pass: lifecycle owned 60, "protected 60", but the venue stop is still 100 → stop triggers → sells 100 → short 40. With a full manual sale the lifecycle closes itself and the 100-share stop is orphaned → short 100 on trigger.
- **Fix:** On deficit, first `get_order_status(stop.broker_order_id)`; if the stop is not filled, `replace_stop_quantity`/`cancel_order` it to the new owned quantity before applying the correction. Never delete lifecycle state while a stop order id is live and un-cancelled.

### D-03 — Managed SHORT positions are destroyed by the position-readback pass (sign bug), with a fabricated BUY fill exported
- **Severity:** P0
- **Classification:** silently-unsupported (untested)
- **Evidence:** Readback is signed (`app/brokers/base.py:119-123` "negative = short"; Alpaca `qty` passthrough `app/brokers/alpaca.py:361-368`; ccxt `-contracts` `app/brokers/ccxt_broker.py:278`). `confirmed_owned_quantity` is always positive (`app/lifecycle/manager.py:796-797`). `app/reconciliation.py:320-323` computes `deficit = owned - broker_owned` with no side handling → for a short of 10: `10 - (-10) = 20` → `on_stop_filled(20)` → `tx.settle(0, 20)` leaves `owned = -10` (no clamp by design, `app/lifecycle/close_arbiter.py:150-161`) → `_check_invariant` halts (`:163-174`) → `closed=True` (`manager.py:1016-1024`), lifecycle_state deleted, local stop record cleared while the real BUY stop still rests → `_apply_exit_fill` records a BUY of 20 into `positions` (`:1500-1504`), writes a synthetic `orders` row and an EXECUTION_APPLIED envelope carrying the stop's `broker_order_id` (`:1557-1589`). The lost-response entry branch (`reconciliation.py:308-313`) also requires `broker_owned > 0`, so a short is never adopted.
  - Tests: `tests/test_ops01_ops02_ops03_worker_health_and_reconciliation.py` has no `Side.SELL`/short case (grep).
- **Scenario:** Managed Alpaca SELL 10 (short) fills, BUY stop placed. First reconcile pass (~30 s, `app/config.py:267`): position halted, lifecycle closed, `positions` flips to +10, a 20-share "stop_exit" BUY is exported to the commercial platform, and the real short remains open at the broker with its stop orphaned.
- **Fix:** `broker_owned_signed = broker_owned if plan.side == Side.BUY else -broker_owned`; compare against that in all three branches; add a short-side regression test.

### D-04 — Reconciler applies ZERO quantity for a FILLED order whose `get_order_status` omits `filled_quantity`; five adapters never report it
- **Severity:** P0
- **Classification:** disconnected (engine and reconciler disagree on the FILLED-without-quantity contract; untested)
- **Evidence:** `app/reconciliation.py:448` (`optimistic_quantity = order["filled_quantity"] or 0.0`), `:481-485` (`actual_quantity = confirmed_quantity if not None else optimistic_quantity` → delta 0). Engine's own rule for the same case is "use the requested quantity": `app/engine.py:1284-1286`, `:2302-2304`. Adapters returning FILLED with no `filled_quantity`: `app/brokers/tradestation.py:225-231`, `tradovate.py:261-267`, `tastytrade.py:234-240`, `robinhood.py:313-319`, `schwab.py:234-240`; all of them return PENDING from `place_order` (`tradestation.py:188-194`, `tradovate.py:225-231`, `tastytrade.py:200-205`, `robinhood.py:280-285`, `schwab.py:200-206`). `tests/test_pending_fill_reconciliation_integration.py:106,130` only script numeric quantities.
- **Scenario:** Plain TradeStation BUY 100 → PENDING → venue fills → reconciler marks the row FILLED with `confirmed_cumulative_fill=0.0`, `positions` stays 0 → a later CLOSE: "no open position to close" (`app/engine.py:2522-2534`). The 100 shares live at the broker, unprotected (these brokers lack native brackets so the entry had no stop) and invisible to the dashboard, flatten, and risk gates.
- **Fix:** In `_correct_position`'s FILLED branch fall back to `order["requested_quantity"]` (mirror the engine), and/or make those adapters read the venue's filled quantity.

### D-05 — A CLOSE signal's explicit quantity is ignored: every CLOSE is a full flatten
- **Severity:** P0
- **Classification:** semantic-miss
- **Evidence:** `app/engine.py:2186-2206` (`_resolve_close`: `quantity = abs(position)`; `signal.quantity` is dropped), managed `app/engine.py:3125`, `:3205` (`request_exit(..., available)`). `Side` has no reduce/partial value (`app/models.py:16-19`). The text grammar parses a quantity for every side including close (`app/sources/text_parser.py:37-38`, `:60-64`); the webhook accepts `quantity` with `side: close`.
- **Scenario:** Holding 10 BTC; provider posts "CLOSE BTC 5" → engine sells 10. "close BTC half" → full close as well (non-numeric token ignored).
- **Fix:** Honor `signal.quantity` on CLOSE (cap at owned/available, route through the arbiter), or reject a CLOSE that carries a quantity until partial closes are supported — never silently widen it.

### D-06 — Multi-target signals become zero-quantity targets: no take-profit ever executes (managed); TP2+ silently dropped (plain)
- **Severity:** P0
- **Classification:** semantic-miss
- **Evidence:** `app/engine.py:2651-2671`: when `signal.targets` is non-empty every level becomes `Target(reduce_fraction=level.fraction)` and `take_profit` is ignored. Text sources never set `fraction` (`app/sources/text_parser.py:356-364`; `take_profit` = TP1 at `:269-271`). `app/lifecycle/manager.py:1112` → `planned_quantity * (None or 0.0) = 0` → `request_exit(0)` → `:1206-1209` REJECTED "no shares available to sell" → `fired` never set (`:1117-1118`) → re-evaluated every 15 s tick forever. The engine comment at `:2652-2663` calls this a "known, disclosed gap" but nothing rejects, warns, or sizes it. Plain path embeds only TP1 at full size (`app/engine.py:1016`, `alpaca.py:138-146`).
- **Scenario:** "BUY AAPL 100 SL 95 TP1 105 TP2 110" on a managed account → price reaches 110, nothing sells → price returns to 95 → stop-out. The trader's plan had two profit exits; the system executed none.
- **Fix:** Default per-level sizing (equal split, last level = remainder) or reject a managed entry whose targets carry no sizing; on the plain path reject/warn when more than one target cannot be embedded.

### D-07 — Managed vs plain is chosen from different flags on different paths, and `managed_lifecycle` can be flipped with open exposure
- **Severity:** P0
- **Classification:** disconnected
- **Evidence:** Signals use the provider/analyst-effective flag: `app/engine.py:675-682`, `:880`; overrides at `app/providers.py:40`, `:94-96`; live-editable via `app/main.py:2926-2945`, `:2959-2979`. Dashboard close/flatten use the raw account flag: `app/engine.py:3310` (`close_position`), called from `app/main.py:2011-2015` and `:2061-2068`. `POST /accounts` guards only `broker` changes while exposure exists (`app/main.py:2465-2473`) and writes the new `managed_lifecycle` at `:2481`.
- **Scenario:** Account is raw-plain; provider override makes its entries managed. Entry fills, stop rests at the venue, fill recorded in `positions` (`manager.py:939-941`). Operator clicks "Exit now" → plain path → readback matches (the position is real) → SELL 100 via `_submit_order`; the managed stop is never consulted (arbiter bypassed) → stop later triggers → short 100. The reverse flip (managed→plain) makes "Exit now" answer "no open position to close" and the position cannot be flattened from the dashboard.
- **Fix:** Select the exit path from the existence of an open lifecycle for `(account, symbol)`, not from a config flag; refuse `managed_lifecycle` changes (account, provider, analyst) while exposure exists, exactly as EXE-10 does for `broker`.

### D-08 — Managed exit returning ERROR (not raised) is treated as "0 filled" and the full-size stop is re-armed
- **Severity:** P0
- **Classification:** disconnected
- **Evidence:** `app/lifecycle/manager.py:1288-1318`: only PENDING opens a `PendingExit`; ERROR falls through to `actual_filled = 0.0`, `tx.settle(requested, 0)`, then `_restore_stop_coverage(remaining = owned)`. The raised-exception branch (`:1264-1286`) does the right thing (pending exit, no broker_order_id). Adapters return ERROR on transport errors/timeouts instead of raising: `app/brokers/alpaca.py:158-164`, `app/brokers/ccxt_broker.py:185-191`. The ledger classifies that same result UNKNOWN_AMBIGUOUS (`app/command_ledger.py:146-150`, written at `manager.py:1677-1681`) but `request_exit` ignores it.
- **Scenario:** Full close of 100 times out after the venue filled it → a fresh 100-share stop is placed on a flat position → trigger → short 100 (futures/perps/MT5 fill it; UNVERIFIED whether Alpaca rejects a side-flipping stop).
- **Fix:** Treat ERROR exactly like the exception branch: open `PendingExit(broker_order_id=None)` and let the position readback resolve it.

### D-09 — Ambiguous stop placement is retried blind → duplicate resting stops (also across a crash)
- **Severity:** P1 (P0 outcome once two stops fill)
- **Classification:** disconnected
- **Evidence:** `app/lifecycle/manager.py:1812-1834` treats ERROR as "nothing resting" (`protected_quantity=0`, `broker_order_id` untouched/None); `retry_unprotected_positions` (`:335-369`) → `_replace_stop_price` (`:2077`, `:2150`) → `_place_stop_locked` submits a NEW stop. Alpaca/ccxt return ERROR on a timeout after the POST may have been accepted (`alpaca.py:243-244`, `ccxt_broker.py:240-241`). Ledger keys carry a per-call nonce (`manager.py:1772`), so dedupe is impossible by design, and there is no open-orders readback capability (`app/brokers/base.py` has none). Crash variant: `on_price_update` persists before taking any lock (`:1083-1084`), so `STOP_PENDING`/stale id can be persisted mid-placement; after restart `restore_from_store` (`:572-602`) + retry places a second stop.
- **Scenario:** Two 100-share stops rest; both fill on the move → short 100.
- **Fix:** Before (re)placing, check the last attempted id from the `stop_change` ledger row (`remote_identifiers`) via `get_order_status`, and add a `list_open_orders(symbol)` capability; consume unresolved `stop_change` ledger rows at startup.

### D-10 — Stop fills are detected only via position readback; the stop order id is never polled; ccxt spot has no readback at all
- **Severity:** P1
- **Classification:** missing
- **Evidence:** `app/reconciliation.py:265-279` (skip when `not has_position_readback_capability` or readback `None`); `app/brokers/ccxt_broker.py:265-270` (`fetch_positions` raises on spot → `None`). No `get_order_status(stop.broker_order_id)` call exists. Detection latency for brokers with readback is one reconcile interval (30 s, `app/config.py:267`).
- **Scenario:** Managed ccxt-spot long; stop fills; lifecycle still "owns" it; target/CLOSE → `_ledgered_cancel_order` fails (order gone) → `request_exit` ERROR "could not confirm cancellation" (`manager.py:1246-1257`) forever; dashboard shows an open position, flatten impossible, no `orders` row for the stop fill, trailing/tighten skipped (`:2143-2147`).
- **Fix:** Poll `stop.broker_order_id` with `get_order_status` in the reconciler (Alpaca/ccxt both implement it) and on any cancel failure; route a confirmed fill to `on_stop_filled` with the real price.

### D-11 — `reduce_fraction` is a fraction of PLANNED, not owned, quantity; no lot/step quantization anywhere on the exit/stop path
- **Severity:** P1
- **Classification:** semantic-miss
- **Evidence:** Defined as fraction of originally planned quantity: `app/lifecycle/models.py:91-97`, `app/models.py:54-56`. Computed at `app/lifecycle/manager.py:1112`; `_compute_reduction_plan` silently clamps to `tx.available` (`:133`). No `round`/floor/lot-size in `engine.py`, `risk.py`, `lifecycle/manager.py`, `alpaca.py`, `ccxt_broker.py` (grep). Alpaca sends `qty: str(quantity)` raw (`alpaca.py:133`, replace `:306`).
- **Scenario:** Planned 100, filled 60 (remainder cancelled): TP1 50 % sells 50 (83 % of the position), TP2 50 % sells 10. `fixed_quantity=7`, TP 50 % → 3.5 shares sent; the replacement stop for 3.5 is rejected by the venue → `PROTECTION_FAILED`, position partly unprotected.
- **Fix:** Size targets against confirmed owned at fire time (or make the plan-based rule explicit in the UI) and quantize every exit/stop quantity to the venue's step.

### D-12 — Trailing stops and time exits are unreachable from any signal/config; targets are process-bound logical orders fed by an unchecked REST price poll
- **Severity:** P1
- **Classification:** silently-unsupported
- **Evidence:** `Signal` has no trailing/time-exit fields (`app/models.py`, grep); `_handle_managed_entry` never builds `TrailingPolicy`/`time_exit` (`app/engine.py:2673-2687`); `app/lifecycle/models.py:49-57` states it. Targets fire only inside `on_price_update` (`manager.py:1101-1132`) driven by `PriceMonitor` (`app/pricing.py:106-148`, 15 s, `app/config.py:272`) from last-trade/ticker reads with no timestamp, staleness, RTH or outlier check (`alpaca.py:385-393`, `ccxt_broker.py:281-290`). A full-size TP (`reduce_fraction=1.0`) cannot amend in place (`manager.py:137-142`), so the stop is cancelled BEFORE the exit is submitted (`:1241-1259`); if the exit comes back PENDING (Alpaca DAY market order submitted after hours, `alpaca.py:135-136`) the position sits with no stop until it fills.
- **Scenario:** Process down overnight: the native stop protects, but every logical TP is missed. Process up: an extended-hours print above TP cancels the stop, queues a market sell for the open, and leaves the position unprotected through the gap.
- **Fix:** Surface "logical, process-bound" clearly; add price-timestamp/RTH sanity checks; for full-size exits submit the exit as OCO/attached where supported, or submit the exit before cancelling the stop and reconcile.

### D-13 — No production path ever reads unresolved command-ledger rows or resolves an ambiguous plain entry
- **Severity:** P1
- **Classification:** disconnected
- **Evidence:** `SignalStore.list_unresolved_command_ledger_entries` (`app/db.py:4089`) and `SignalCopierEngine.resolve_unknown_submission` (`app/engine.py:1954`) have no callers outside `tests/` (grep); no endpoint/CLI exposes them. Plain entry exception → `status=ERROR` saved (`app/engine.py:1184-1199`, `:1305`) → excluded from `list_pending_orders` (`app/db.py:3332`) → never polled. ALLOC-05 holds the capital reservation "until `resolve_unknown_submission`" (`app/engine.py:1236-1249`). Nothing reads `get_broker_position` for plain accounts except at CLOSE time, where it only rejects (`:2367-2385`). CapitalAllocator restores unresolved reservations on restart (`app/capital_allocator.py:269-278`), so the hold survives restarts.
- **Scenario (Q2):** Plain entry times out after the venue accepted it → position real, `positions` 0, reservation held forever → every later entry on a gated account rejected; no operator action can clear it short of editing the DB.
- **Fix:** Startup + periodic pass over unresolved ledger rows: poll `remote_identifiers.broker_order_id` where present, else position readback; expose `resolve_unknown_submission` on an owner endpoint.

### D-14 — Lost-response managed entry that never reached the venue is never resolved and blocks the symbol permanently
- **Severity:** P1
- **Classification:** missing
- **Evidence:** `app/reconciliation.py:308-318` resolves only when `broker_owned > 0`; otherwise deficit is 0 → `continue`. `validate_plan` rejects every later entry while the lifecycle exists (`manager.py:727-743`); it is restored on restart (`:572-602`); `unregister_plan` is reachable only from a REJECTED entry (`app/engine.py:2871`) or a zero-fill terminal poll (`manager.py:985-986`), never from an operator endpoint.
- **Scenario:** Broker returns ERROR (credentials, 5xx) for a managed BUY → pending entry with no order id → venue flat → symbol blocked for that account forever ("an active managed lifecycle already exists").
- **Fix:** After a bounded grace with `broker_owned == 0` (and no open orders), call `resolve_pending_entry(0, remainder_cancelled=True)`; add an owner endpoint to unregister a never-filled plan.

### D-15 — Partial coverage deficit after a failed amend is never retried
- **Severity:** P1
- **Classification:** disconnected
- **Evidence:** `app/lifecycle/manager.py:1704-1727` (`_restore_stop_coverage` warns and leaves `protected_quantity` short while `status` stays `STOP_CONFIRMED`); `retry_unprotected_positions` skips any `STOP_CONFIRMED` lifecycle (`:352`) regardless of `uncovered_quantity` (`app/lifecycle/models.py:414-416`).
- **Scenario:** 62 owned, TP 15 amended the stop to 47, only 8 fill, restore-to-54 PATCH fails → 7 shares uncovered indefinitely; dashboard deficit only.
- **Fix:** Retry whenever `uncovered_quantity > 0`, not only when status is not confirmed.

### D-16 — Crash between entry fill and stop persist orphans the position from its lifecycle; restart recovery ignores "venue > tracked"
- **Severity:** P1
- **Classification:** disconnected
- **Evidence:** Engine records the fill (`app/engine.py:2901`) then `on_entry_fill` sets owned/places the stop and persists only at the end (`manager.py:772-805`, persist at `:804`); `start_plan` persisted owned 0 (`:761`). After a crash in that window: `retry_unprotected_positions` skips owned ≤ 0 (`:350`), `_reconcile_broker_positions` ignores venue > tracked (`app/reconciliation.py:321-322`), managed CLOSE answers "no shares available to sell" (`app/engine.py:3125-3136`), flatten fails, `validate_plan` refuses re-entry, `positions` says 100. IBKR `get_order_status` is in-memory only (`app/brokers/ibkr.py:178-180`), so any PENDING IBKR order from before a restart is never resolved either.
- **Fix:** At startup reconcile each restored lifecycle against `get_broker_position` and the entry ledger row's `terminal_evidence`; treat venue > tracked as adopt-and-protect (or alert), never ignore.

### D-17 — ccxt `cancel_order` needs the symbol but keeps it only in process memory → after a restart every managed exit/trail on symbol-requiring exchanges is refused
- **Severity:** P1
- **Classification:** disconnected
- **Evidence:** `app/brokers/ccxt_broker.py:98-108`, `:253-263`; `BrokerAdapter.cancel_order` has no symbol parameter (`app/brokers/base.py:101-105`) although the manager knows `plan.symbol`. Refusal paths: `manager.py:1243-1257` (exit), `:2143-2147` (replace/trail).
- **Scenario:** Restart on Binance → provider EXIT/dashboard flatten/trailing all fail with "could not confirm cancellation" until the stop fills by itself.
- **Fix:** Add `symbol` to `cancel_order`/`replace_stop_quantity`, or persist `_order_symbols`.

### D-18 — ccxt stop capability is asserted by method override only; exchanges that reject the stop get an admitted, unprotected entry with no containment
- **Severity:** P2
- **Classification:** mock-only-tested
- **Evidence:** `app/brokers/base.py:165-166`, `:240` (`can_protect_a_managed_position` = method is overridden); `ccxt_broker.py:228-251` has no `exchange.has` check (unlike `place_order`'s ADP-02 check at `:160-171`). Failure path: `manager.py:1812-1834` then endless 30 s retries (`:335-369`); `tests/test_ccxt_lifecycle_capabilities.py` exercises stubs only.
- **Fix:** Verify `exchange.has[...]` per exchange at qualification/validate time; add a containment policy (alert + optional auto-flatten after N failed protections).

### D-19 — Close-arbiter coverage (Q8)
- **Severity:** P2
- **Classification:** ok (with the bypasses in D-02/D-07)
- **Evidence:** Provider CLOSE, manual close, flatten, time exit, targets, trailing all funnel through `request_exit` under `CloseArbiter.transition` (`manager.py:1173`); stop fills and readback corrections take the same lock (`:1002`, `:1361`, `:2072`). Plain accounts use `_plain_close_locks` + DB `claim_close` (`app/engine.py:2511-2519`). Not under the arbiter: `_correct_position` writes `positions` directly for an older plain order on a now-managed account (`app/reconciliation.py:187-197`); `on_stop_filled` settles without `reserve()` by design (invariant halts if a pending exit is in flight, `close_arbiter.py:175-179`); D-07's plain path on a managed position.

### D-20 — Writer-lease coverage (Q11)
- **Severity:** P2
- **Classification:** ok (gaps are DB/export-side, not broker-side)
- **Evidence:** `require_active()` at `app/engine.py:498`, `:3292`; `manager.py:347`, `:787`, `:883`, `:1095` (after the price observation, by design), `:1168`, `:1359`; `check_time_exits` relies on `request_exit`. Not checked: `on_stop_filled` (`manager.py:991-1067`, writes `positions`, a synthetic `orders` row and an EXECUTION_APPLIED export), `_correct_position` (`app/reconciliation.py:427-513`, DB writes + export + capital release), `resolve_unknown_submission` (`app/engine.py:1954`), `POST /reconciliation/run-now` (`app/main.py:2083-2094`, fails only when the pass reaches `retry_unprotected_positions`). A fenced-out process can still mutate `positions`/`orders` and emit exports.
- **Fix:** Gate `reconcile_once` and `on_stop_filled` on `require_active()` too.

### D-21 — Readback-detected stop fills are persisted and exported with a proxy price
- **Severity:** P2
- **Classification:** semantic-miss (doc says "never fabricated")
- **Evidence:** `manager.py:1052-1056` falls back to `last_observed_price` then `broker_confirmed_price`; `_persist_self_initiated_exit` (`:1557-1589`) writes an `orders` row and an EXECUTION_APPLIED envelope with that price under the stop's `broker_order_id`; `app/reconciliation.py:323` passes no price. P&L/provider-value consumers read it as a real fill price.
- **Fix:** Fetch the real `filled_avg_price` via `get_order_status` (D-10) or mark the price as estimated in the payload.

### D-22 — A target whose PENDING exit later resolves to zero fill stays `fired=True`; time-exit counter reports triggers it did not place
- **Severity:** P2
- **Classification:** missing
- **Evidence:** `manager.py:1117-1118` sets `fired` on PENDING; `resolve_pending_exit` (`:1332-1431`) never resets it. `_consume_expired_time_exit` returns True even when `request_exit` was REJECTED/ERROR (`:387-409`).
- **Fix:** Reset `fired` when a pending exit resolves with 0 filled; count only FILLED/PENDING results.

### D-23 — Duplicate-exit guard scope (Q5)
- **Severity:** P2
- **Classification:** ok as documented
- **Evidence:** TRK-27 covers only "already flat" (`manager.py:284-333`, `:1175-1185`, mirrored at `app/engine.py:3098-3113`); SIG-01 covers the same `signal.id` (`app/engine.py:545-559`); in-memory only, 900 s window (`app/config.py:459`). Partial trims can only come from the entry's own targets (single `fired` flag), provider trims do not exist (see D-05), so "same trim twice" cannot occur today; a duplicate CLOSE across a restart is harmless while flat and a documented non-goal after re-entry (ADR-0010).

### D-24 — Lifecycle deletion paths (Q7, EXE-07)
- **Severity:** P3
- **Classification:** ok
- **Evidence:** `has_unresolved_entry` (`app/lifecycle/models.py:419-428`) guards `closed` at `manager.py:1021`, `:1316`, `:1409`. `lifecycle_state` is deleted only by `_persist` when closed (`:608-609`), `unregister_plan` (`:764-770`; called from a REJECTED entry `app/engine.py:2871` and a zero-fill terminal `manager.py:985-986`), and `_apply_exit_fill` (`:1505-1506`). `DELETE /accounts/{id}` refuses with exposure (`app/main.py:2499-2512`, includes open lifecycles). A second same-symbol entry is rejected by `validate_plan` (`manager.py:727-743`) and that rejection IS recorded (`app/engine.py:965-981`) and exported as `rejected` (`:983-989`). Residual: `POST /accounts` can change `managed_lifecycle` under exposure (D-07).

### D-25 — Reconciliation correctness (Q9)
- **Severity:** P2
- **Classification:** ok (with D-03/D-04 caveats)
- **Evidence:** Broker wins: `app/reconciliation.py:468-485`. Reconciliation never adds positions for plain accounts (only known `orders` rows, `app/db.py:3328-3332`); for managed lifecycles the lost-response branch adopts the WHOLE venue position as this entry's fill (`:308-318`), including any manually held quantity (P2). Reservation release is single-shot: status flips to terminal in the same transaction as the position correction (`:503-513`, `app/db.py:3410-3465`) then `_release_reservation_if_any` (`:579-589`); managed reservations release once in `resolve_pending_entry` (`manager.py:972-975`, guarded by `:887`); `CapitalAllocator.release` clamps at zero (`app/capital_allocator.py:390-393`).

### D-26 — Exit-side resolution for shorts (Q12)
- **Severity:** P2
- **Classification:** ok logically; venue validation missing
- **Evidence:** Plain: `_resolve_close` picks BUY for a negative position (`app/engine.py:2191`); readback comparison is signed (`:2370`). Managed: `exit_side` (`app/lifecycle/models.py:396-398`), side-aware triggers/trailing (`manager.py:1593-1596`, `:211-218`), stop placed with `exit_side` (`:1798`). Crypto spot: a SELL "entry" is a spot sale; `place_protective_stop(exit_side=BUY)` sends `type=market` + `stopLossPrice` (`ccxt_broker.py:228-251`) whose support is exchange-specific and unchecked; no readback on spot (D-10). Managed shorts on any venue WITH readback are broken by D-03.

---

## Answers by question

| Q | Answer (findings) |
|---|---|
| 1 | Nobody tracks bracket legs; only the parent `orders` row (status pending + broker_order_id) is polled; `positions` never goes flat; CLOSE either rejected forever (readback) or re-sold → short (`exclusive_writer_qualified`, IBKR/MT5). **D-01**, D-04. |
| 2 | Confirmed: nothing adopts a lost-response plain fill; ERROR rows are never polled; `resolve_unknown_submission` has no caller; reservation held forever. **D-13**. |
| 3 | Partial fill → stop resized in place or cancel/resubmit (ok, `manager.py:909-956`, `:2077-2119`); failure persisted as UNPROTECTED + `PROTECTION_FAILED` and retried every pass and on next fill (ok). Restart: rehydrated from `lifecycle_state` incl. pending entry; **but** crash windows orphan (D-16) or double the stop (D-09); partial deficits never retried (D-15). |
| 4 | Stop fills are learned only by `get_broker_position` deficit every 30 s (no stop-id polling; none on ccxt spot); deficit is assumed to be the stop and the venue stop is never resized → oversell/short (**D-02**); short positions mis-signed (**D-03**); `_reconcile_before_plain_close` only rejects, never corrects (D-01). |
| 5 | Fraction is of PLANNED quantity, not remaining; no rounding/quantization; a CLOSE with quantity is a full flatten (**D-05**); multi-TP → zero-size targets (**D-06**); duplicate guard scoped to "already flat" only (D-23); D-11. |
| 6 | Targets/trailing/time exits are logical and process-bound; trailing/time exits are not reachable from signals at all; feed = 15 s REST last-trade/ticker with no staleness/RTH check; full-size TP cancels the stop before the exit is submitted. **D-12**. |
| 7 | EXE-07 guard holds (D-24). Other overwrite paths: `POST /accounts` flipping `managed_lifecycle` under exposure and provider overrides (**D-07**); rejected second entry is recorded and exported (ok). |
| 8 | Arbiter covers all managed exit kinds; bypasses: plain path on a managed position via flag divergence (**D-07**), venue stop left untouched on corrections (**D-02**), `_correct_position` direct writes (D-19). |
| 9 | Broker wins; never adds positions for plain accounts; managed lost-response adoption takes the whole venue position; releases are single-shot (D-25). FILLED without quantity applies nothing (**D-04**). |
| 10 | Rehydrated: capital reservations, open lifecycles + arbiter ledgers. Not acted on: unresolved command-ledger rows (nobody calls the list), IBKR in-memory trades. Orphaned stop: D-02/D-16; doubled stop: **D-09**. |
| 11 | Lease checked on every broker-write path; not on `on_stop_filled`, `_correct_position`, `resolve_unknown_submission`, `run-now` (DB/export writes only). D-20. |
| 12 | Short exits resolve correctly (plain BUY-to-close, managed `exit_side`); crypto spot "short" is a spot sale with an unchecked buy-stop and no readback; managed shorts with readback are destroyed by **D-03**. D-26. |

UNVERIFIED items: whether Alpaca rejects a stop that would flip a flat/long account short (affects the realized outcome of D-02/D-08/D-09 on Alpaca only — futures, crypto perps and MT5 netting accounts flip); exact ccxt per-exchange support for `stopLossPrice` on spot (D-18/D-26).
