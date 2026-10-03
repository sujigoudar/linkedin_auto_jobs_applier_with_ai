# Audit E — Accounting, analytics, and the commercial export

Scope: `signal-copier/app/{economics,account_economics_v2,provider_value,trade_episode,statistics,equity_history,execution_quality,export_events,relay_worker,relay_scheduler,provider_scout,shadow_mode,certification}.py`, `app/backtest/*`, the serving endpoints in `app/main.py`, `signal_platform_contracts`, and the consumer side of `signal-portfolio-commercial/app/services/integration_inbox.py` / `platform_performance.py` / `source_coverage.py`.
Method: static read of every path that writes or reads `orders`, plus one throwaway script (`scratchpad/probe_accounting.py`, temp DBs only, no pytest) that drove the real engine/reconciler/lifecycle manager through five scenarios. Findings marked **[empirical]** were reproduced by that script; everything else is by code reading. UNVERIFIED is used where a claim depends on a real external broker's behaviour I could not exercise.

Paths below are relative to `/home/user/linkedin_auto_jobs_applier_with_ai/signal-copier/` unless prefixed.

Severity key: P0 = can cause a wrong trading decision or hidden loss; P1 = silently wrong number; P2 = missing capability; P3 = doc/test.

---

## E-01 — A managed-lifecycle provider CLOSE is journaled with `side='close'`, so every downstream replay drops the exit (P&L, episodes, provider score, capital gate)
- **Severity:** P0
- **Classification:** semantic-miss (and test-encoded as intended)
- **Evidence:**
  - `app/engine.py:965-981` — managed branch persists the fill with `side=signal.side` (line 969), which for a CLOSE signal is literally `Side.CLOSE`; the resolved `exit_side` is computed at 933-937 but used only for the export envelope.
  - `app/reconciliation.py:211-217` — comment admits "a managed CLOSE's own `orders` row stores `signal.side` (literally `Side.CLOSE`)".
  - `app/lifecycle/manager.py:1446` — `_SELF_PERSISTED_EXIT_KINDS = {"stop","target","time_exit"}`: `provider_exit` is deliberately NOT given a second row, so the `side='close'` row is the only record of that fill.
  - `app/economics.py:189-194,205-217` — `_signed_quantity('close')` returns `None` → the fill is skipped and the symbol is appended to `incomplete_symbols`.
  - `app/trade_episode.py:268-272` — `side not in ("buy","sell")` → `continue` → episode never closes.
  - `app/provider_value.py:202-211` — same skip in the FIFO replay.
  - `app/capital_allocator.py:178-200` + `app/engine.py:2084-2090` — `incomplete_symbols` → `has_unresolved` → every new entry for the account is REJECTED while a ceiling is configured.
  - `tests/test_trk23_managed_lifecycle_export.py:315-319` and `tests/test_e06_account_economics.py:114-123` encode the CLOSE-side row / its exclusion as intended behaviour; no test joins the two facts.
- **Scenario [empirical]:** managed account, BUY 10 AAPL @50, provider CLOSE fills. Result: `orders` rows `(side='close', status='filled', purpose='close')`; `compute_account_economics` → `realized_pnl=0.0`, `open_quantity={'AAPL': 10.0}`, `incomplete_symbols=['AAPL']` while `positions.net_quantity=0.0`; `compute_trade_episodes` → `outcome='open'`; `compute_provider_value_from_episodes` → `closed_episodes=0, win_rate=None`; `confirmed_open_notional` → `notional=500, unresolved=['AAPL']`; the next BUY on that account with `max_notional_exposure` set was **REJECTED** ("open exposure for symbol(s) ['AAPL'] this replay could not resolve"). The commercial export for the same fill carried `side='sell'` — so the local journal and the commercial PLATFORM book disagree on whether the position is open.
- **Consequence for scorecards:** for a provider whose exits are its own CLOSE signals (the normal case), wins and losses taken via CLOSE are invisible; only stop-outs (which DO get a BUY/SELL row) are counted → the episode scorecard and `/providers/value` can show a good provider as `not_promising`/`cancel_candidate`, and `insufficient_data` for one that is never stopped out.
- **Fix:** in `_handle_signal`'s managed branch persist `side=export_side` (the lifecycle's `exit_side`), exactly as `close_position` already does at `app/engine.py:3320,3386`; add a regression test that runs a managed entry+CLOSE through the engine and asserts `compute_account_economics(...).incomplete_symbols == []` and episode `outcome != 'open'`; backfill existing rows from `signals`+`lifecycle_state`/family.

## E-02 — A partially-filled order that is then cancelled is applied to `positions` but never enters P&L, episodes, or the commercial export
- **Severity:** P0
- **Classification:** semantic-miss
- **Evidence:**
  - `app/reconciliation.py:468-480` — REJECTED branch applies `actual_quantity - optimistic_quantity` to `positions` and stores `confirmed_cumulative_fill=actual_quantity`, but the row's `status` becomes `'rejected'` (`:503-512`).
  - `app/db.py:8896-8905`, `:4867-4887`, `:8961-8973`, `:8944-8947` — every analytics query is `WHERE status = 'filled'`; a `'rejected'` row with `filled_quantity=30` is invisible, not even flagged incomplete.
  - `app/reconciliation.py:452-466` — `_export_reconciled_fill` only runs for `new_status == FILLED`.
- **Scenario [empirical]:** plain account, BUY 100 → PENDING; broker later reports canceled with `filled_quantity=30, filled_price=10`. After `reconcile_once`: `positions.net_quantity=30.0`, `compute_account_economics(...).per_symbol == {}`, `incomplete_symbols == []`, zero `EXECUTION_APPLIED` exports. The 30 shares are owned, will later be sold (that sell WILL be journaled), and the replay then treats the sell as opening a short at the sell price: realized P&L, cost basis, win rate, and the commercial book are all wrong, with no disclosure.
- **Fix:** make the replay queries select on `filled_quantity > 0` (or `applied_execution_delta != 0`) regardless of terminal status, or split the cancelled-with-partial case into a `'filled'` row for the executed part plus a `'rejected'` remainder; export the partial fill in `_correct_position` whenever `confirmed_quantity > optimistic_quantity`.

## E-03 — Asynchronously resolved managed exits (target/time exits that reported PENDING) are journaled with `filled_price=NULL` even though the broker reported a price; the symbol then poisons P&L and the capital gate, and the fill is never exported
- **Severity:** P0
- **Classification:** disconnected
- **Evidence:**
  - `app/reconciliation.py:402,418,422` — `_reconcile_pending_exits` receives `result` (with `filled_price`, e.g. Alpaca `filled_avg_price`, `app/brokers/alpaca.py:210-217`) but calls `resolve_pending_exit(account, symbol, filled, remainder_cancelled=...)` with no price.
  - `app/lifecycle/manager.py:1332-1334,1374-1386,1420-1429` — `resolve_pending_exit` has no price parameter; `_apply_exit_fill` is called without `exit_price`, the docstring calls this "honestly None".
  - `app/lifecycle/manager.py:1557-1564,1566-1579` — the row is saved with `filled_price=None`; `build_execution_applied_envelope` (`app/export_events.py:123`) returns `None` → no export.
  - `app/economics.py:207-217` — `price is None` → `incomplete_symbols`; `app/trade_episode.py:405-411` → `realized_pnl=None`, `outcome='unknown'`; `app/capital_allocator.py:200` + `app/engine.py:2084-2090` → gate rejects.
- **Scenario [empirical]:** managed entry BUY 10 @50; `request_exit(source="target")` returns PENDING; broker later reports FILLED @60; `reconcile_once`. Result: `target_exit` row with `filled_price=None`; economics `realized=0.0`, `open=10`, `incomplete=['AAPL']`; episode `outcome='unknown'`; only the entry was exported; `confirmed_open_notional` → `unresolved=['AAPL']` (new entries blocked). A +100 win is recorded as "unknown" — and for a provider, unknown episodes are excluded from the win-rate denominator (see E-06).
- **Fix:** thread `result.filled_price` through `resolve_pending_exit(..., filled_price=...)` → `_apply_exit_fill(exit_price=...)`; for the partial path use the broker's cumulative average price. Same for `_reconcile_broker_positions:323` where a stop is inferred from a position deficit (see E-05).

## E-04 — The per-source filter added to `compute_account_economics` cannot see any lifecycle-initiated exit (stop/target/time) or manual close, so strategy exposure never decreases
- **Severity:** P0
- **Classification:** semantic-miss
- **Evidence:**
  - `app/db.py:8896-8911` — `source` filter is `JOIN signals s ... AND s.source = ?`.
  - `app/lifecycle/manager.py:1549-1556` and `:1601-1607` — every self-initiated exit and every `request_exit` submission creates `Signal(source="lifecycle_manager", ...)`; `app/engine.py:3302` — manual flatten creates `Signal(source=reason)` (`"manual_exit"`).
  - `app/capital_allocator.py:203-218` (`confirmed_strategy_notional`) and `app/engine.py:2138-2147` — ALLOC-03 strategy ceiling consumes the filtered replay.
- **Scenario [empirical]:** managed entry BUY 10 @50 from `tradingview`, stop fills @44. Unfiltered economics: `realized=-60, open=0`. `source='tradingview'` economics: `realized=0.0, open=10`. `confirmed_strategy_notional('tradingview')` = **500** although the account is flat. Every stop-out permanently inflates the strategy's "open notional"; with a strategy ceiling configured the strategy is eventually locked out of new entries after enough stop-outs; the inverse (a flip after a stop) can under-count.
- **Fix:** attribute by `family_id`/entry signal's source (join the exit row to its family's entry signal: `s_entry.source` where `s_entry.id = o.family_id`), or persist the originating provider on the synthetic exit signal (`raw["provider"]`) and filter on that; add a test with a stop exit under the source filter.

## E-05 — Stop fills inferred from a broker-position deficit (or any `on_stop_filled` without a price) are journaled with a proxy price (last tick or resting stop level) labelled as `filled_price`
- **Severity:** P1
- **Classification:** semantic-miss (mislabeled quantity)
- **Evidence:** `app/lifecycle/manager.py:1052-1056` — fallback chain `filled_price → last_observed_price → stop.broker_confirmed_price`; the comment calls these "a genuine, already-known value". `app/reconciliation.py:320-323` — OPS-03 readback calls `on_stop_filled(..., filled_quantity=deficit)` with no price at all, so the fallback is always taken on that path. The row then flows into `economics.py` as a real price, into `trade_episode.py` as a known-price execution (`outcome` win/loss), and into `EXECUTION_APPLIED` (`manager.py:1566`) as `filled_price`.
- **Scenario:** gap-through on a stop (stop at 45, actual fill 41). Journal says the loss was 5/share, reality 9/share. Provider loss understated, commercial PLATFORM book carries a fabricated fill price, `has_unknown_price_execution` stays False so nothing discloses it.
- **Fix:** keep `exit_price=None` when the caller has no broker-reported price (disclose as unknown) or fetch the stop order's real fill via `get_order_status(stop_broker_order_id)` before persisting; never export a non-fill price as `filled_price`.

## E-06 — Provider win rate / promotion rest on a survivorship-biased denominator: open episodes, unknown-price episodes and all plain-account episodes are excluded
- **Severity:** P0
- **Classification:** semantic-miss
- **Evidence:**
  - `app/provider_value.py:444-447,508-516` — `win_rate = wins / (wins + losses)`; open and `unknown` episodes are skipped.
  - `app/provider_scout.py:105-113` — `decided = wins + losses`; `promote` when `win_rate >= 0.4` and `profit_factor is None or >= 1.0`; `profit_factor is None` means *no losing episode at all* (`provider_value.py:450-453`), which is exactly what E-01/E-03 produce (losses via CLOSE or async stop become open/unknown).
  - `app/trade_episode.py:259-263` + `app/engine.py:2589` (`family_id=None` for every plain-account close) — a plain account's entries form episodes that can never close; `compute_trade_episodes` counts them as `open` forever, so the scout and `/providers/value/episodes` score providers on managed accounts only.
  - `app/provider_scout.py:93-96` — a `promote` recommendation is reached with ≥10 decided episodes regardless of how many open/unknown/plain episodes exist for the same provider.
- **Scenario:** provider with 10 small wins closed by targets that filled synchronously and 25 losses that exited via async stops/targets (E-03) or via its own CLOSE signals (E-01) → `decided=10, win_rate=1.0, profit_factor=None` → `promote`. Conversely a provider traded only on plain accounts is permanently `insufficient_data`.
- **Fix:** gate promotion on `unknown_outcome_episodes == 0 and open_episodes/total < threshold` (or report coverage: decided/total) and refuse `promote` while any excluded class is non-zero; give plain-account closes a family (match the entry by FIFO when saving the close) so plain accounts contribute episodes.

## E-07 — Reconciliation-confirmed fills overwrite `executed_at` with the poll time; latency, chronological replay order and the exported `event_time` are all based on it
- **Severity:** P1
- **Classification:** semantic-miss
- **Evidence:** `app/brokers/alpaca.py:211-219` builds `OrderResult` without `executed_at` → default `datetime.now()` (`app/models.py:357`) at poll time; `app/db.py:3391-3398` (`_update_order_status_locked`) unconditionally sets `executed_at = result.executed_at`; `app/execution_quality.py:224` computes latency from it; `app/economics.py:201` / `db.py:8905` order the replay by it; `app/export_events.py:159-160` uses it as `event_time`/`effective_time`.
- **Scenario [empirical]:** plain BUY → PENDING at 18:44:57.440; reconciled FILLED 1.24 s later → `executed_at` became 18:44:58.68 and the timing row reports that. In production with `RECONCILE_INTERVAL_SECONDS` of tens of seconds, "signal-to-fill" latency is inflated by up to one interval, and a late-reconciled entry can sort AFTER its own stop exit, flipping the cost-basis replay (sell treated as a new short). UNVERIFIED for IBKR/other adapters (not read), but the default-factory behaviour is shared.
- **Fix:** adapters should populate `executed_at` from the broker's fill timestamp (`filled_at` for Alpaca); `_update_order_status_locked` should keep the original `executed_at` unless the result carries a real fill time; add a `confirmed_at` column for the poll instant.

## E-08 — Execution-quality latency mixes synthetic `lifecycle_manager`/manual exit signals (received ≈ executed) into per-symbol signal-to-fill latency
- **Severity:** P1
- **Classification:** semantic-miss
- **Evidence:** `app/execution_quality.py:213-230` uses `list_filled_orders_with_signal_timing` (`db.py:8944-8947`), which joins every filled order to its own signal's `received_at`; for stop/target/time exits the "signal" is created microseconds before the `OrderResult` (`manager.py:1549-1564`), and for `request_exit` submissions at `manager.py:1601-1617`. Those rows contribute ~0 s samples under the same `per_symbol[symbol]` as real provider signals.
- **Scenario:** a symbol with 5 provider entries at 2 s latency and 5 stop exits at 0.001 s reports mean ≈1 s. The dashboard latency chart (TR-14) and `account_economics_v2.latency_seconds_mean` (`account_economics_v2.py:309-313`) understate provider-signal latency by up to 2x.
- **Fix:** exclude `purpose in ('stop_exit','target_exit','time_exit')` and `signals.source = 'lifecycle_manager'` from latency (or report them as a separate "internal exit latency" series).

## E-09 — `fee` is hard-coded to `None` in every `EXECUTION_APPLIED` export even when the adapter reported one; no FEE event is ever emitted
- **Severity:** P2
- **Classification:** disconnected
- **Evidence:** `app/export_events.py:143` `fee=None` while `OrderResult.fee/fee_currency/slippage` exist (`app/models.py:358-367`) and are persisted (`app/db.py:2869-2871`); PaperBroker sets them (`app/brokers/paper.py:121-123`); no `EventType.FEE` builder exists anywhere in `app/` (grep: none). Commercial side handles this honestly — `platform_performance.py:329-339` keeps `net_pnl=None` while `unknown_fee_entry_count>0` — so commercial "net" is never computable from this producer.
- **Scenario:** the commercial platform can never move past gross for any tenant fed by signal-copier, even for venues whose fee is known.
- **Fix:** pass `fee=str(result.fee)` when `result.fee is not None` (as `Money`), or emit a correlated FEE event from `save_order_result` when `fee` is known.

## E-10 — Gross is presented as the account P&L; "net" exists only on the dashboard and only for paper, computed from a capped order sample
- **Severity:** P2
- **Classification:** silently-unsupported / mislabeled
- **Evidence:** `app/economics.py:154` note "Gross of fees (not yet tracked)" — but `orders.fee` IS tracked (`db.py:244-247`) and populated by paper; `economics.py` never reads it. `app/static/views/tr14.js:139` `ORDERS_FEE_SAMPLE_LIMIT = 500`, `:1127-1133` — `Net = gross_realized(all fills) - fee_per_fill × (filled rows among the most recent 500)`; label says "Net". `app/db.py:2830-2834` claims fee tracking "feeds into daily_pnl aggregation" — nothing writes `daily_pnl` (table at `db.py:1829`, only reader `daily_loss_limiter.py:38` via a non-existent `get_daily_pnl`).
- **Scenario:** paper account with 1,200 fills: "Net" subtracts 500 fees from gross over 1,200 fills.
- **Fix:** sum `orders.fee` in the replay (per currency, see E-11) and report `net_realized` only when every contributing fill has a non-NULL fee; drop the sampled dashboard math; fix the `save_order_result` docstring.

## E-11 — No account/base currency anywhere; realized P&L sums quote-currency units across symbols; export currency is guessed from symbol syntax
- **Severity:** P1
- **Classification:** silently-unsupported
- **Evidence:** `config_accounts` has no currency column (`db.py:295-304`); `AccountBalance` has none (`models.py:370-398`); `economics.py:237-238` adds per-symbol realized into one `realized_pnl`; `account_economics_v2.py:293-299` puts broker `equity` (base currency) beside `gross_realized` (mixed units) with no unit field; `export_events.py:84-87` `_resolve_currency`: any symbol without "/" → `"USD"` (so `USDJPY`, `GBPJPY`, a LSE equity, an EUR future all export as USD; `app/sources/text_parser.py:143-170` explicitly parses slash-less FX pairs, so such symbols are expected). Disclosed in `capital_allocator.py:124-131` for the gate, but not on `/accounts/{id}/economics`, `/providers/value`, or the export.
- **Scenario:** account trading BTC/USDT (+1,500 USDT) and USDJPY (-150,000 JPY) shows `realized_pnl = -148,500`; the commercial ledger books the JPY fill as USD.
- **Fix:** add `currency` to `config_accounts`/`AccountBalance`, store `price_currency` per order (from adapter/instrument), report `realized_pnl_by_currency`, and refuse to sum across currencies without a rate.

## E-12 — Unrealized/`cumulative_pnl` folds a 0.0 for unpriced open symbols into the series that drives drawdown/Sharpe-equivalent/correlation; mark-availability changes masquerade as P&L moves
- **Severity:** P1
- **Classification:** semantic-miss
- **Evidence:** `app/equity_history.py:149-160` — unpriced symbol contributes `0.0` to `unrealized_pnl`, and `cumulative_pnl = realized + unrealized`; `app/statistics.py:116-126,226-245` consumes `cumulative_pnl` deltas and never reads `unpriced_open_symbols`; plain (non-managed) accounts and brokers without `has_last_price_capability` are *always* unpriced, so their curve is pure realized P&L with step changes at each close, while a managed account's curve jumps when the first `PriceMonitor` tick arrives. The honest `unpriced_open_symbols` disclosure exists on each snapshot row but not on `/accounts/{id}/statistics` or `/accounts/correlation`.
- **Scenario:** position opened at 100, no tick for 2 h, first tick at 90: the series shows a -10/share "delta" at the tick instant → spurious volatility/drawdown; cross-account correlation pairs a realized-only series with a marked one.
- **Fix:** compute statistics on `realized_pnl` by default and only include unrealized when `unpriced_open_symbols == []` for every snapshot in the window; surface `unpriced_snapshot_count` in `RollingStats`.

## E-13 — `/accounts/{id}/economics/extended` takes unrealized marks from *any* account's open lifecycle for the same symbol, contradicting its own provenance claim
- **Severity:** P3
- **Classification:** semantic-miss (minor)
- **Evidence:** `app/account_economics_v2.py:136-143` iterates `lifecycle_manager.list_open_lifecycles()` with no `account_id` filter; the module docstring (24-31) says a non-managed account "contributes None". `equity_history.py:132-147` does filter by account.
- **Scenario:** plain account A holds AAPL; managed account B has an AAPL lifecycle on a different broker/symbol map → A's "unrealized" uses B's mark and `mark_age_seconds` from B.
- **Fix:** filter `lifecycle.plan.account_id == account_id`.

## E-14 — Commercial `routing_outcome` is a single last-writer-wins column; fan-out and allocation produce misleading values, and held-out signals never get one
- **Severity:** P1
- **Classification:** semantic-miss (consumer) + silently-unsupported (producer)
- **Evidence:**
  - `signal-portfolio-commercial/app/services/integration_inbox.py:527-535` — `source_row.routing_outcome = payload.outcome`, one column per receipt; each account's outcome has a distinct `event_id` (`export_events.py:327`) so all are applied in sequence order.
  - `app/engine.py:654-657, 898-902, 1082-1089` — accounts that were eligible but not selected (single-destination) emit **no** outcome and no order row (good: never "rejected"); but in `replicate` mode or when a `single` alternative was tried and rejected before the winner filled, the receipt's final `routing_outcome` is whichever event applied last.
  - `app/engine.py:593-595` — stale-signal hold-out returns after the SOURCE_RECEIPT export (`:562`) with no `_export_routing_outcome`; `:531-533` — cross-transport conflict returns **before** the receipt export, so the commercial side never sees that signal at all. `_KNOWN_ROUTING_OUTCOMES` (`signal_platform_contracts/payloads.py:290-297`) has no `held`/`not_selected`/`skipped` value.
- **Scenario:** replicate accounts A (filled) and B (rejected by capital) → commercial shows `rejected` for a trade that is live on A. Stale signals show `routing_outcome=None` forever, indistinguishable from "outcome not yet delivered" (`source_coverage.py:42-45`).
- **Fix:** make the commercial projection per-account (list) or aggregate with precedence (any `admitted_*` wins); add `held_stale`/`conflicting_source_data`/`not_selected` outcomes to the contract and emit them at `engine.py:595`/`533`/`657`.

## E-15 — Export coverage of fills: four classes never (or wrongly) reach the commercial book
- **Severity:** P1
- **Classification:** disconnected
- **Evidence (per class):**
  1. Cancelled-with-partial fills — never exported (E-02; `reconciliation.py:452-466` FILLED-only).
  2. Async-resolved target/time/stop exits with no price — never exported (E-03; `export_events.py:123` refuses without `filled_price`), while the same fill IS applied to `positions`.
  3. Deficit-inferred stop fills — exported with a proxy price (E-05).
  4. Partial progress on a still-PENDING order (`engine.py:1287-1293` applies `result.filled_quantity` to positions and saves `status='pending'`) — not exported until terminal, and not in economics either (`db.py:8904` `status='filled'`); reconciliation deliberately leaves non-terminal PENDING rows untouched (`reconciliation.py:161-172`).
  Plain sync fills, managed sync entries/closes, reconciled plain FILLED, lifecycle-owned reconciled FILLED, and priced self-initiated exits are exported (verified at `engine.py:1296`, `:938`, `reconciliation.py:466`, `:218-225`, `manager.py:1566`).
- **Scenario:** commercial PLATFORM book understates executed quantity by every partial-then-cancelled order and every async-resolved exit; `platform_performance` realized P&L diverges from the local journal in both directions.
- **Fix:** export at the moment `positions` is mutated (one envelope per confirmed delta, keyed by `broker_order_id`+cumulative qty) rather than per terminal status.

## E-16 — Paper broker fills managed exits at `0.0` and that price is journaled and exported as real
- **Severity:** P1 (paper-evaluated numbers only)
- **Classification:** semantic-miss
- **Evidence:** `app/brokers/paper.py:93` `price = signal.price or 0.0`; the exit signal built at `manager.py:1601-1607` carries no price; `close_position` saves it with the resolved side (`engine.py:3382-3395`) so it DOES enter economics; export at `engine.py:3363-3379`/`manager.py:1566`.
- **Scenario [empirical]:** managed entry @50 then provider CLOSE: row `filled_price=0.0`, `EXECUTION_APPLIED` payload `filled_price='0.0'`. A manual flatten on paper books realized P&L of `-(entry) × qty`. Certification's automated `PAPER_EXECUTION` check (`app/certification.py:75-78`) counts such rows as evidence.
- **Fix:** PaperBroker should fill exits at `last_observed_price`/`simulate_price` level or return `filled_price=None` (disclosed unknown), never `0.0`.

## E-17 — The deprecated closing-fill provider scorecard is what both dashboards render, with a now-inverted disclosure
- **Severity:** P1
- **Classification:** semantic-miss + doc/test drift
- **Evidence:** `app/static/dashboard.html:1238` and `app/static/views/tr09.js:149-158, 571, 659, 902` read `GET /providers/value` (`compute_provider_value_report`, FIFO replay) — the function `provider_value.py:10-43` itself calls deprecated and says "nothing new should read `ProviderValue.win_rate`". tr09's on-screen note (`:659`) still says "a managed-lifecycle stop/target exit never creates an order row" — stop rows now exist (`db.py:4873-4877`), while the actual exclusion is E-01 (CLOSE rows) and E-03 (priceless exits). `provider_value.py:229-235` counts `closing_fills` once per LOT consumed, so one fill closing three lots counts three "trades"; `_verdict` (`:306`) sample-gates on that count. `dashboard.html:1114,1123` labels `completed_trade_win_rate` (the per-fill deprecated alias, `economics.py:89-95`) as "Completed-trade win rate".
- **Scenario:** owner reads the Provider Value table and the TR-09 scorecard, sees a provider with 12 winning closing fills and `keep`, while the provider's CLOSE-exited losses are absent (E-01).
- **Fix:** point both UIs at `/providers/value/episodes`, render `open_episodes`/`unknown_outcome_episodes` beside win rate, and relabel the economics tile `closing_fill_win_rate`.

## E-18 — Backtest replays raw signal quantity at the signal price with no routing, so it cannot reconcile to live sizing; "research-only" labelling is partial
- **Severity:** P2
- **Classification:** disconnected (drift risk)
- **Evidence:** `app/backtest/replay.py:365-429` — entry at `row["price"]` (no check that the first bar even traded through it), quantity `row.get("quantity")` (never `size_for_account`, never routing rules/symbol_map), exits at the exact stop/target level (`simulator.py:40-43`); `run_with_capital_contention` (`:288-300,323-329`) sizes notional from the same raw quantity, so the "same `CapitalAllocator.admit()` as live" claim is checked against notionals live never uses. Cost stress is opt-in (`main.py:5632`). `shadow_mode.py` is the one path that reuses `size_for_account`/routing — but it produces intents, not P&L. The dashboard labels the panel "Research report" (`tr15.js:79,490`) and notes zero-cost fills (`:340`); the `/backtest` endpoint and `backtest_runs` persistence carry no research-only marker; `fit_simulator.py:156-168` presents `simulated_pnl_at_your_size` to prospects from the same raw-quantity engine.
- **Scenario:** account with `multiplier=0.1`: live P&L is a tenth of the backtest; capital contention rejects signals live would admit (or vice versa).
- **Fix:** size each replayed signal through `size_for_account`/`symbol_for_account` for the named account, require the first bar's range to contain the entry price, default a non-zero slippage, and stamp every persisted run/summary with `evidence_class="research_replay"`.

## E-19 — `/capital-allocation` `deployed_notional` and `unresolved_symbols` inherit E-01/E-02/E-03 and the endpoint presents them as "real open exposure"
- **Severity:** P1
- **Classification:** semantic-miss (inherited)
- **Evidence:** `app/main.py:1666-1690,1699-1725` describes `deployed_notional` as "this account's real open notional exposure"; it is `confirmed_open_notional` (`capital_allocator.py:178-200`) over the journal that misses the classes above. `available_notional` becomes `null` and the live gate rejects (`engine.py:2084-2090`) once any managed CLOSE or async exit has happened on the account.
- **Scenario [empirical]:** after one managed CLOSE, `deployed_notional=500` for a flat account and `unresolved_symbols=['AAPL']`; dashboard TR-14 capital bar shows 500 "Deployed".
- **Fix:** fix E-01/E-02/E-03; until then surface `incomplete_symbols` reasons (which row ids) so an operator can see why the gate is closed.

## E-20 — Equity snapshots: writer, source, and cash-flow exposure
- **Severity:** P2
- **Classification:** ok (with inherited errors)
- **Evidence:** `app/equity_history.py:170-188` — `EquitySnapshotter.snapshot_once` (started in `main.py:284-288`, interval `EQUITY_SNAPSHOT_INTERVAL_SECONDS`) writes `account_equity_snapshots` from local economics (`compute_account_economics`) + lifecycle marks, never from broker balance. Because the series is P&L-based (not balance-based) deposits/withdrawals do **not** corrupt it; the honest `twr=None` (`account_economics_v2.py:199-204`) is correct. However the series inherits E-01/E-02/E-03/E-12, is write-only from one process (a restart gap simply leaves a hole), and `/accounts/{id}/balance` (broker equity) is never persisted, so no reconciliation between the two exists.
- **Fix:** persist broker `equity`/`cash` alongside each snapshot when the adapter reports it, and store a `deposits_withdrawals` ledger if TWR is ever to be computed.

## E-21 — `account_economics_v2` slippage/implementation-shortfall silently excludes the fills most likely to slip
- **Severity:** P2
- **Classification:** silently-unsupported
- **Evidence:** `app/account_economics_v2.py:104-121` — samples only where the originating signal carried `price` and `side in (buy, sell)`; stop/target/time exits (synthetic signal, no price) and managed CLOSE rows (`side='close'`, E-01) contribute nothing; `sample_count` is reported but the excluded classes are not named.
- **Scenario:** slippage reported as "mean 0.01" from 20 limit entries while 20 stop-outs each slipped 0.30.
- **Fix:** report `excluded_fill_count` by class; for stop exits compare against `stop.desired_price`.

## E-22 — Relay "exact bytes" contract is re-serialisation, not byte forwarding
- **Severity:** P3
- **Classification:** doc
- **Evidence:** `app/db.py:2931-2958` parses `envelope_json` back into `EventEnvelope`; `app/relay_worker.py:218` calls `model_dump_json()` again. `payload_hash` is over the payload dict (`export_events.py:165`), so integrity survives, but the docstring claim ("never reconstructs") is false and a pydantic serializer change would alter bytes.
- **Fix:** forward the stored `envelope_json` string verbatim.

## E-23 — Placeholder gates and false docstrings that read as real (Q11 sweep)
- **Severity:** P3 (one P2)
- **Classification:** mixed
- **Honest capability-states (fine):** `tr01.js` (`:915,954,1029,1115,...`), `tr03.js` (`:476-881`), `tr14.js:1121,1140` render `not_tracked` with reasons; `statistics.py`/`account_economics_v2.py` return `None` with notes; `CapitalContentionReport.not_tracked` (`replay.py:222-224`); `fit_simulator`/`replay` outcomes `EXIT_UNSCORABLE`/`AMBIGUOUS`.
- **Silent fabrications / stale claims:**
  - `app/engine.py:782-791` — `# TODO: integrate broker.get_margin_state()`: the E04 margin gate is called with `current_equity=None, maintenance_requirement=None, excess_margin=None` on every entry and passes through; it is presented in comments as a "fail-closed gate" (P2, silently-unsupported).
  - `app/db.py:2830-2834` — fee "feeds into daily_pnl aggregation": no writer exists (P3).
  - `app/provider_value.py:73-86` module docstring and `tr09.js:659` — stop exits "never create an order row": inverted since TR-EPISODE-01 (P3).
  - `app/economics.py:34-36,154` — "orders has no fee column yet": the column exists and is populated for paper (P3).
  - `app/execution_quality.py:41-46` documents the managed-CLOSE `submitted_at=NULL` gap honestly (ok), but E-07/E-08 are not disclosed.
  - `app/account_economics_v2.py:24-31` provenance claim contradicted by E-13.

---

## Direct answers to the eleven questions

1. **Fill classes in P&L:** reconciliation-confirmed FILLED rows are included (status flips to `filled`, `reconciliation.py:199,503`) but with poll-time `executed_at` (E-07). Managed stop/target/time exits ARE `orders` rows since TR-EPISODE-01 (`manager.py:1521-1589`) — but priceless when resolved async (E-03) or proxy-priced when inferred (E-05). Managed provider CLOSE rows are `side='close'` and excluded (E-01). Partial fills on still-PENDING orders are applied to `positions` but excluded (E-15.4); partial-then-cancelled is excluded forever (E-02). Win rate is wrong in both directions accordingly.
2. **Unrealized price:** `PositionLifecycle.last_observed_price` = entry fill or last `PriceMonitor` tick (`lifecycle/models.py:316-344`); never a fresh quote at read time. Labelling is honest in v2 (`None` + `unavailable_marks` + `mark_age_seconds`) and on each snapshot row, but `cumulative_pnl`-derived statistics silently treat 0.0 as real (E-12). Plain accounts are always unpriced.
3. **Fees/slippage:** populated only by `PaperBroker.place_order` (`paper.py:121-123`); no real adapter sets them (grep); persisted but unread by economics; export hard-codes `fee=None` (E-09); dashboard "Net" is sampled paper math (E-10).
4. **Currency:** none recorded anywhere; sums are unit-less; export guesses (E-11).
5. **Episodes/provider value:** episode = `orders.family_id` group; a bad trade is excluded if it exits via managed CLOSE (E-01), via an async/priceless exit (E-03), or sits open; plain-account trades never close (E-06). Denominator = decided only (survivorship). Scout promotes on ≥10 decided with `profit_factor None` allowed → yes, a provider can be promoted on 10 real closed wins while its losses are in the excluded classes.
6. **Strategy vs account view:** only per-account views exist; the per-source filter is the only strategy-level replay and it is inconsistent with the account view because lifecycle/manual exits carry `source='lifecycle_manager'`/reason (E-04) — the two do not reconcile today (empirical: account says flat, strategy says 500 open).
7. **Latency:** real timestamps for plain sync (`submitted_at` at `engine.py:1178`) and managed entries; managed CLOSE leaves `submitted_at` NULL (documented, `execution_quality.py:41-46`) so that stage is dropped; reconciled fills use poll-time `executed_at` (E-07); synthetic exit signals dilute the headline latency (E-08).
8. **Equity history:** `EquitySnapshotter` from local economics; deposits don't corrupt (P&L series), but it inherits every journal gap (E-20).
9. **Backtest:** entry at signal price, exits at exact level, no slippage by default, raw quantity, no routing — a separate engine from live sizing (E-18); labelled "Research report" in the UI only.
10. **Export:** see E-15 for the four classes missing/wrong; non-selected allocation candidates emit nothing (no false `rejected`), but fan-out and early-rejected alternatives can leave the receipt showing `rejected`, and held-out signals never get an outcome (E-14).
11. **Dashboards:** E-23 lists the honest states vs the silent ones; the most consequential renderings are the deprecated provider scorecard (E-17), the "Completed-trade win rate" mislabel (E-17), the paper-only sampled "Net" (E-10), and the capital bar showing phantom deployed notional (E-19).
