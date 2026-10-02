# Audit B — Sizing and Risk domain (signal-copier)

Read-only audit of `/home/user/linkedin_auto_jobs_applier_with_ai/signal-copier` (HEAD working tree, 2026-10-02). No repo files were edited; pytest was not run. All line numbers refer to the files as read during this audit.

Severity: P0 = can create unintended exposure/loss; P1 = silently wrong financial behavior; P2 = missing capability the owner would expect; P3 = doc/test.
Classification: semantic-miss | disconnected | mock-only-tested | silently-unsupported | missing | ok.

Quick answers to the eleven questions are at the end (section "Answers by question"); the findings list is the primary output.

---

## B-01 — Order quantity is a copied count scaled by a flat multiplier; nothing sizes from risk or equity, and `risk_percent_of_equity` only rejects
- **Severity:** P1  **Classification:** semantic-miss
- **Evidence:**
  - `app/risk.py:16-19`
    ```python
    if account.fixed_quantity is not None:
        return account.fixed_quantity
    base_quantity = signal.quantity if signal.quantity is not None else 1.0
    return base_quantity * account.multiplier
    ```
  - Plain path `app/engine.py:1014` `order_signal, quantity = signal, size_for_account(signal, account)`; managed path `app/engine.py:2639` `quantity = size_for_account(signal, account)` — identical, no stop/equity input.
  - `app/engine.py:1863-1872` — risk basis is a comparison only: `risk_notional = abs(price - stop_loss) * abs(quantity)` ... `if risk_notional > risk_ceiling: return False, self._reject(...)`. Quantity is never recomputed.
  - `docs/design/CAPITAL_ALLOCATION.md:20` labels this "**Risk-basis sizing**"; `README.md:277-282` admits "position sizing (`app/risk.py`) still uses a flat `multiplier`/`fixed_quantity`/provider-override, never a live balance".
  - Provider overrides (`app/engine.py:460-467`, `675-682`) only narrow `multiplier`/`fixed_quantity`; there is no provider-equity or follower-equity concept anywhere (grep `equity` in `app/risk.py`, `app/providers.py`: none).
- **Scenario:** Provider trades a $1,000,000 book and posts "BUY AAPL 100 @ 200 SL 190". Follower has a $10,000 Alpaca margin account (`multiplier: 1.0`, no opt-in gates). Quantity = 100 × 1.0 = 100 shares = $20,000 notional = 200% of equity. The only unconditional gate, `_check_buying_power`, compares $20,000 to Alpaca's reported `buying_power` (2×–4× equity on a margin account, so $20,000–$40,000) and passes. With `risk_percent_of_equity: 0.01` configured the entry is simply REJECTED (risk $1,000 > ceiling $100); the trader gets zero shares instead of the 10 shares a risk-sized copier would send.
- **Fix:** Add an account sizing mode (`sizing: risk_fraction`) that computes `qty = floor(equity × risk_fraction / (|price − stop| × contract_multiplier))` (needs B-03's unit normalization) and keep the current reject check as the ceiling.

## B-02 — A signal with no quantity silently becomes 1.0 unit of whatever the instrument is
- **Severity:** P0  **Classification:** semantic-miss
- **Evidence:**
  - `app/risk.py:18` `base_quantity = signal.quantity if signal.quantity is not None else 1.0`
  - Text grammar makes quantity optional: `app/sources/text_parser.py:61` `(?:\s+(?P<quantity>...)\s*(?:lots?|units?|shares?)?)?`; webhook: `app/sources/webhook.py:13` `"quantity": 0.01, # optional`.
  - With no price the unconditional buying-power gate skips: `app/engine.py:1928-1934` `if order_signal.price is None ...: return True, None`; with no opt-in gate `_try_reserve_capital` admits: `app/engine.py:2046-2047` `if not has_gate: return True, 0.0, None`.
  - Every adapter sends a market order for that quantity (`alpaca.py:133-135`, `ccxt_broker.py:178-184`, `ibkr.py:122`, `oanda.py:117-126`, `mt4_mt5.py:113`).
- **Scenario:** Telegram message "buy BTCUSD" (no size, no price) routed to a ccxt/Binance account with no gates configured: quantity 1.0 BTC market buy (≈$60k+ at current prices) or, on an underfunded account, an exchange `InsufficientFunds` error that is then held as an ambiguous reservation (B-06). On an MT5 account the same message is 1.0 lot = 100,000 units. On a NinjaTrader/Tradovate futures account it is 1 ES contract (~$250k notional).
- **Fix:** Reject an ENTRY whose `signal.quantity is None` unless the account has `fixed_quantity` set (fail closed), and remove the `1.0` default from `size_for_account`.

## B-03 — Quantity units are never normalized: lots vs units vs shares vs contracts, and contract multipliers are ignored by every notional/risk computation
- **Severity:** P0  **Classification:** semantic-miss
- **Evidence:**
  - Parser discards the unit word (`text_parser.py:61` `(?:lots?|units?|shares?)?` is non-capturing) and `Signal.fx.unit`, `Signal.option.multiplier`, `Signal.future.multiplier` exist (`app/models.py:72, 83, 95`) but are never read by sizing or gating: grep for `signal.fx`, `option.multiplier`, `future.multiplier` in `app/` hits only `signal_correlation.py:110-116` and `export_events.py`.
  - Same float is sent as different units: MT5 `app/brokers/mt4_mt5.py:113` `"volume": quantity` (lots); OANDA `app/brokers/oanda.py:117,126` `units = quantity ...; "units": str(units)` (base-currency units); Alpaca `alpaca.py:133` `"qty": str(quantity)` (shares); SignalStack `signalstack.py:57` `"quantity": quantity` (whatever the downstream broker assumes).
  - Notional and risk ignore multipliers: `app/engine.py:2077` `notional = abs(quantity) * abs(order_signal.price)`; `app/engine.py:1943` same for buying power; `app/engine.py:1863` `risk_notional = abs(price - stop_loss) * abs(quantity)`; confirmed exposure `app/capital_allocator.py:199` `abs(open_quantity) * average_cost`.
- **Scenario (FX):** "SELL EURUSD 0.50 lots SL 1.0950" (README example format) → `quantity=0.5`. On an MT5 account that is 0.5 lots = 50,000 EUR; on an OANDA account the adapter sends `units=-0.5` (≈ €0.50; OANDA's whole-unit precision rule for majors is UNVERIFIED here, so it may instead be rejected). The owner-wide ceiling sees 0.5 × 1.0950 = $0.55 of "notional" for a 50,000-EUR short.
  **Scenario (options):** "BUY AAPL 200C x10 @ 2.50 SL 1.50" on a Tastytrade/TradeStation account: real premium notional $2,500 and risk-to-stop $1,000, but the gates compute notional = 10 × 2.50 = $25 and risk = $10, so `risk_percent_of_equity: 0.01` on a $10,000 account (ceiling $100) admits a trade that actually risks 10% of equity.
- **Fix:** Normalize `quantity` to venue units per `asset_class`/contract spec before `place_order` (lot size for MT5, units for OANDA, contracts for futures/options) and multiply notional/risk by the contract multiplier; refuse FX/option/future signals whose spec is missing.

## B-04 — Whole-share / lot-step / min-notional rounding is enforced by only two adapters; everything else forwards raw floats and the paper broker fills anything
- **Severity:** P1  **Classification:** silently-unsupported (mock-only-tested for paper)
- **Evidence:**
  - Only Rithmic and Tradovate check: `app/brokers/rithmic.py:105-108` `if not float(quantity).is_integer(): ... "requires a whole-number contract quantity; refusing to silently truncate"`; `app/brokers/tradovate.py:178-181` same.
  - Raw pass-through: `alpaca.py:133` `"qty": str(quantity)`; `ibkr.py:122` `MarketOrder(action, quantity)`; `ccxt_broker.py:178-184` `amount=quantity` with no `amount_to_precision`/`markets[symbol]['limits']` check; `oanda.py:126`; `tradestation.py:161` `"Quantity": str(quantity)`; `tastytrade.py:174`; `schwab.py:174`; `signalstack.py:57`; `mt4_mt5.py:113` (no `volume_step` check).
  - Paper broker accepts any float: `app/brokers/paper.py:95-100` `book[symbol] = current + quantity` — no rounding, no minimum, no cash check, so every engine/lifecycle test passes with fractional or sub-minimum sizes.
  - Decision ledger requires "Minimum trade size: downsize only if valid; never round up; zero shares = skip" (`docs/design/ALLOCATION_DECISION_LEDGER.md:74`, DL-44) — no implementation.
- **Scenario:** Account `multiplier: 0.25`, provider sends "BUY MSFT 1": quantity 0.25 shares → IBKR `MarketOrder("BUY", 0.25)` is rejected by TWS unless fractional trading is enabled → adapter returns ERROR (`ibkr.py:125-131`) → see B-06. Same provider on ccxt/Kraken: 0.25 × multiplier of a 0.0001-BTC signal = 0.000025 BTC, below the market minimum → `InvalidOrder` → ERROR → reservation held. A bracket order with a fractional qty on Alpaca is rejected with HTTP 422 (fractional orders cannot carry `order_class`) → same.
- **Fix:** Add a per-adapter `normalize_quantity(symbol, quantity) -> float | None` (lot step, min qty, min notional, whole-share flag) called by the engine after sizing; reject (never round up) when the result is zero, per DL-44.

## B-05 — A definite broker validation rejection (HTTP 4xx, ccxt `InvalidOrder`/`InsufficientFunds`) is classified as an *ambiguous* submission, so the capital reservation is held indefinitely
- **Severity:** P1  **Classification:** silently wrong (semantic-miss)
- **Evidence:**
  - Alpaca converts any `httpx.HTTPError` (including `HTTPStatusError` 422/403 from `raise_for_status()`) into ERROR: `app/brokers/alpaca.py:151-164`. ccxt converts every exception into ERROR: `app/brokers/ccxt_broker.py:185-191`. IBKR: `ibkr.py:125-131`. OANDA transport errors: `oanda.py:132-136`.
  - `app/command_ledger.py:146-150` — ERROR → `UncertaintyState.UNKNOWN_AMBIGUOUS`.
  - `app/engine.py:1236-1249` `elif ambiguous_submission and notional: ... "holding reservation of %.2f until resolved"` — released only by a human via `resolve_unknown_submission` (`engine.py:1954-1982`).
  - Reservations are durable and reloaded at restart: `app/capital_allocator.py:277-278`.
- **Scenario:** `max_notional_exposure: 5000`. Entry for $4,000 of a fractional bracket order is rejected by Alpaca with 422 → ERROR → $4,000 stays reserved. Every later entry on that account is rejected with "notional exposure ceiling (5000) would be exceeded ... pending=4000.00" until an operator finds and resolves the ledger row. With a strategy ceiling (`db.py:3273-3282` sums unresolved `capital_reservations`) the leak blocks every account trading that strategy.
- **Fix:** Have adapters return REJECTED (not ERROR) for definite client-side rejections (4xx other than 408/429, ccxt `InvalidOrder`/`InsufficientFunds`/`BadSymbol`), and keep ERROR for transport/timeout ambiguity only.

## B-06 — Gating prices come from the message, never a live quote; every adapter sends a MARKET order; no max-chase/slippage policy; `entry_order_type` is ignored
- **Severity:** P1  **Classification:** semantic-miss / missing
- **Evidence:**
  - `app/engine.py:2077` `notional = abs(quantity) * abs(order_signal.price)`; `1943` (buying power) and `1843/1863` (risk-to-stop) all use `order_signal.price`. The docstring at `2014-2016` admits "this build has no independent current-market-price source to fall back to".
  - `BrokerAdapter.get_last_price` exists for Alpaca (`alpaca.py:370`), ccxt (`ccxt_broker.py:281`), IBKR (`ibkr.py:219`) but the engine never calls it before entry (grep `get_last_price` in `app/engine.py`: none; only `app/pricing.py:132` for post-fill monitoring).
  - Market orders everywhere: `alpaca.py:135` `"type": "market"`, `ccxt_broker.py:180` `type="market"`, `ibkr.py:122` `MarketOrder`, `oanda.py:124` `"type": "MARKET"`, `mt4_mt5.py:111,116` `TRADE_ACTION_DEAL` with `"deviation": 20`.
  - `Signal.entry_order_type` (LIMIT/STOP) is read only by `app/sources/webhook.py:132` and `app/export_events.py:240`; no broker adapter reads it. `price_low`/`price_high` likewise unused in gating.
  - Freshness gating applies only to a provider registered in the catalog with a freshness config (`app/engine.py:1389-1399`; comment at `585-592`: unregistered sources "always return ok=True"). No chase/slippage knob (grep `max_chase|slippage_bps` in `app/`: only backtest `cost_stress.py`). Ledger DL-41 (`ALLOCATION_DECISION_LEDGER.md:71`): "chase <= 0.20%; ... quote age <= 1 s ... REPO: no signal-age or unfilled-lifetime config found".
- **Scenario:** A relayed Telegram alert "BUY NVDA @120 SL 110 x100" is processed 20 minutes late while NVDA trades at 128. Gates evaluate notional $12,000 and risk-to-stop $1,000; the market order fills ≈$12,800 with real risk-to-stop $1,800 (80% more than the figure the `risk_percent_of_equity` gate approved). A provider alert that says `entry_order_type: limit` at 120 is executed as a market buy at 128 — a 6.7% chase with no limit.
- **Fix:** Before admission, read `get_last_price` where the adapter has it, gate on `max(signal.price, live)`, reject when `|live − signal| / signal > max_chase_pct` (default 0.2%), and send LIMIT orders when `entry_order_type == LIMIT` (or when a chase bound is configured).

## B-07 — `_check_buying_power` fails open; only Alpaca and the paper broker ever report buying power; the paper figure is cash only and rises on a short sale
- **Severity:** P1  **Classification:** silently-unsupported / disconnected
- **Evidence:**
  - Fail-open branches: `app/engine.py:1921-1927` (no `has_balance_capability` → admit), `1928-1934` (no price → admit), `1936-1942` (`buying_power is None` → admit); docstring `1896-1915` calls this deliberate.
  - `get_account_balance` is overridden only in `app/brokers/paper.py:128` and `app/brokers/alpaca.py:395` (grep `def get_account_balance` across `app/brokers`); every other adapter inherits the base `return None` (`app/brokers/base.py:136-151`). So IBKR, ccxt (documented, `ccxt_broker.py:46-57`), OANDA, MT4/MT5, Tradovate, Rithmic, NinjaTrader, SignalStack, Schwab, TradeStation, Tastytrade, Robinhood have no buying-power gate at all.
  - Paper: `paper.py:139-140` `cash = self._cash_for(...); return AccountBalance(..., cash=cash, buying_power=cash)` — `equity` and `maintenance_margin` None; `_apply_fill_to_cash` `paper.py:81-86` credits cash on SELL and never rejects a negative balance; pending orders/reservations are not deducted.
  - The check runs before the admission lock (`engine.py:2033` vs lock at `2083`), so two concurrent entries both compare against the same broker figure.
- **Scenario:** A $5,000 OANDA practice account receives "BUY EURUSD 500000" — no buying-power gate, no opt-in ceiling → sent; only OANDA's own margin check can stop it. On paper: "SELL AAPL 1000 @ 200" on a flat account adds $200,000 to simulated cash, so the next "BUY TSLA 1000 @ 250" ($250,000) passes the buying-power check on a $100,000 starting balance.
- **Fix:** Fail closed for accounts whose adapter cannot report buying power unless an opt-in ceiling (`max_notional_exposure`/`risk_percent_of_equity`) is configured; include this process's pending reservations in the comparison; make the paper broker model cash ≥ 0 and margin for shorts.

## B-08 — Daily-loss limit and min-equity circuit breakers are disconnected on three levels (store methods don't exist, fields are never loaded, the P&L table is never written) and the one global default would halt every account
- **Severity:** P1  **Classification:** disconnected + mock-only-tested
- **Evidence:**
  - Store methods do not exist: `app/daily_loss_limiter.py:38-44` `getattr(self.store, "get_daily_pnl", None)` ... "SignalStore.get_daily_pnl does not exist"; `:52-56` `self.store._broker_adapters`. grep `def get_daily_pnl|_broker_adapters` in `app/db.py`: no hits (confirmed).
  - Fields unreachable in production: `DestinationAccount.daily_loss_limit_percent`/`min_equity_threshold` (`app/models.py:712,716`) are not read by `load_routing_config` (`app/routing.py:165-187`), `load_routing_config_from_store` (`routing.py:211-227`), `SignalStore.list_config_accounts` (`db.py:4293-4323`), `upsert_config_account` (`db.py:4325-4375`) or the API `AccountRequest` (`main.py:2402-2420`), although the columns exist (`db.py:318-319`). `config.DEFAULT_MIN_EQUITY_THRESHOLD` (`config.py:421`) is never read by the engine (grep: only `config.py`).
  - `daily_pnl` table is created (`db.py:1829-1842`, `alembic/versions/0036`) and never inserted into (grep `daily_pnl` in `app/`: only the schema and a docstring at `db.py:2833`).
  - Global default halts everything: `app/engine.py:749-750` `daily_loss_limit_percent = account.daily_loss_limit_percent or config.DEFAULT_DAILY_LOSS_LIMIT_PERCENT` → `daily_loss_limiter.py:44` returns "Daily loss limit check failed: no daily P&L source is implemented in this build (failing closed)" for every ENTRY on every account. `tests/test_alloc09_loss_limit_fails_closed.py:24-40` asserts exactly this rejection; `tests/test_e09_account_liquidation.py:42` injects `mock_store._broker_adapters` to make the check pass — mock-only.
  - Even if wired, two semantic bugs: `daily_loss_limiter.py:62` `daily_loss_percent = abs(daily_pnl) / balance.equity * 100` — a +5% GAIN day trips a 5% LOSS limit; `:119` checks current equity, not the post-trade "would fall below" the field docstring promises (`models.py:713-714`). `halt_trading_if_limit_exceeded` (`:74`) has no callers.
  - No loss latch across restart/midnight, no weekly halt, no kill switch: grep `kill_switch|trading_halt|weekly|circuit_breaker|loss_latch` in `app/`: only the config comment `config.py:413` pointing at `docs/risk/CIRCUIT_BREAKER.md`, which does not exist (`ls docs/risk/`: no such directory).
- **Scenario:** Owner sets `DEFAULT_DAILY_LOSS_LIMIT_PERCENT=5` believing it protects live accounts; every entry on every account (paper included) is rejected until the env var is removed. Owner instead writes `daily_loss_limit_percent: 5` in `accounts.yaml`: it is silently ignored, and an account can lose 40% in a day with no halt.
- **Fix:** Either delete the limiter and the dead fields, or wire them end-to-end: load the fields in `routing.py`/`db.py`/`main.py`, pass `self.brokers` into `DailyLossLimiter`, compute daily P&L from `compute_account_economics` + `equity_history` snapshots (realized + mark-to-market), use signed P&L (`daily_pnl < 0`), and persist a per-account halt row that survives restart until an operator clears it.

## B-09 — Margin-call detector is a production no-op: the engine always passes `None, None, None`
- **Severity:** P2  **Classification:** disconnected + mock-only-tested
- **Evidence:**
  - `app/engine.py:782-791`
    ```python
    # TODO: integrate broker.get_margin_state() calls ...
    margin_error = self.margin_call_detector.check_and_persist_margin_call(
        account=account, current_equity=None, maintenance_requirement=None, excess_margin=None, broker=account.broker)
    ```
  - `app/margin_call_detector.py:54-55` `if current_equity is None and maintenance_requirement is None and excess_margin is None: return None`.
  - Alpaca's `maintenance_margin` and `equity` are fetched (`alpaca.py:423-425`) but used only for display (`account_economics_v2.py:298`); no adapter has `get_margin_state`.
  - `tests/test_e04_margin_call_detection.py:33-75` passes numbers directly into the detector; no test drives it through the engine with a real broker figure.
- **Scenario:** Alpaca account with equity $9,000 and maintenance requirement $9,500 (already in a margin call) receives a new entry: the detector returns None, `_check_buying_power` sees Alpaca's `buying_power` (which can still be > 0 on a day-trading-BP basis) and the entry is admitted.
- **Fix:** Feed `balance.equity` and `balance.maintenance_margin` from `get_account_balance` into the detector (fail closed when a margin account reports equity but no maintenance figure) and block entries while an unresolved `margin_call_alerts` row exists.

## B-10 — Per-account, owner-wide and strategy ceilings sum raw numbers across currencies and instruments; no basis currency; the FX source that exists is reference-only and unwired
- **Severity:** P1  **Classification:** silently wrong (documented but still wrong for any mixed book)
- **Evidence:**
  - `app/capital_allocator.py:121-131` ("Multi-currency / basis-currency conversion ... unimplemented ... summed as if their numbers were directly comparable"); `:242-248` `total += report.notional + allocator.pending_reservation(account.account_id)`.
  - Strategy ceiling: `app/db.py:3273-3282` `confirmed + pending + notional > ceiling` over `capital_reservations.notional` with no currency column; `confirmed_strategy_notional` `capital_allocator.py:203-218` same.
  - `app/engine.py:2077` notional in whatever unit `price` is quoted in (USDT, USD, quote currency per lot, option premium...).
  - FX exists only as context: `app/context/fx.py:1-8` "reference rate ... Use this for reporting/context only"; `main.py:6045-6057` `GET /context/fx/{base}/{quote}`; no caller in the allocator/engine. No `basis_currency` config anywhere (grep `basis_currency|base_currency` in `app/`: only `models.py:93` `FxContractSpec.base_currency`, unused).
- **Scenario:** `MAX_OWNER_NOTIONAL_EXPOSURE=100000`. Open book: 1 BTC on ccxt at 60,000 (USDT) → 60,000; 100 AAPL at 200 → 20,000; 1.0 lot EURUSD on MT5 at 1.08 → 1.08. Sum 80,001.08 < 100,000, so a new $19,000 entry is admitted while real gross exposure is ≈ $80,000 + €100,000 ≈ $188,000.
- **Fix:** Declare a basis currency per deployment and per account, store a `notional_basis` per reservation/fill (`quantity × contract_size × price × fx_to_basis`), and refuse to sum an account whose basis conversion is unavailable (same fail-closed posture as `unresolved_symbols`).

## B-11 — No leverage cap, no maintenance-margin headroom, no "borrowed funds excluded" rule; the only margin-aware gate (broker buying power) implicitly permits 2–4× leverage
- **Severity:** P1  **Classification:** missing
- **Evidence:**
  - grep `leverage|max_leverage|gross_leverage` in `app/`: no hits. `AccountBalance.maintenance_margin` (`models.py:398`) is never used by any gate (grep: display only, `account_economics_v2.py:298`).
  - `app/engine.py:1943-1944` `notional = abs(quantity) * abs(order_signal.price); if notional > balance.buying_power:` — for an Alpaca margin account `buying_power` is 2× (overnight) or 4× (day-trading) equity (`alpaca.py:424` passes the raw field).
  - Ledger DL-38 (`docs/design/ALLOCATION_DECISION_LEDGER.md:68`): "canary gross equity leverage 1.00; standard 1.25 ...; buying-power reserve >= 0.25; borrowed funds excluded from risk-equity denominator ... REPO: no leverage-cap config found; only `margin_call_detector.py`"; `:124` "No new leverage logic is added"; `:131` risk defaults "are **not** enforced by the code".
  - `_check_risk_basis` uses gross `balance.equity` as the denominator (`engine.py:1862-1864`), which on a margin account includes borrowed-funded positions.
- **Scenario:** $10,000 Alpaca margin account, `buying_power` $40,000. Four sequential signals of $9,500 each all pass `_check_buying_power` (each < $40,000 at the time, and Alpaca's BP only shrinks as fills settle) → $38,000 of positions on $10,000 equity (3.8× leverage) with no copier-side objection; a 26% adverse move wipes the account.
- **Fix:** Add `max_gross_leverage` (default 1.0 live / per-ledger 1.25 on release) enforced as `(confirmed + pending + new_notional) ≤ max_gross_leverage × (equity − maintenance_margin)`, and use `cash`-based equity (not borrowed) as the risk denominator.

## B-12 — No same-underlying or correlated-exposure aggregation; `symbol_map` is a flat per-account string map, so one instrument can be two positions
- **Severity:** P2  **Classification:** missing (admitted in docstring)
- **Evidence:**
  - `app/risk.py:25` `return account.symbol_map.get(signal.symbol, signal.symbol)` — the mapped string is the position key everywhere (`db.py:3561-3573` positions keyed `(account_id, symbol)`; `economics.py:202,219` keyed `order["symbol"]`).
  - `app/capital_allocator.py:132-136` "Two signals from different analysts ... both entering the same underlying symbol are not netted, correlated, or otherwise treated as a shared/overlapping risk bucket"; `:143-144` no cross-account netting.
  - `Signal.option.underlying` (`models.py:68`) is read only by `signal_correlation.py:113` (dedup fingerprint), never by sizing/gating; grep `underlying` in `app/engine.py`, `app/capital_allocator.py`: docstring only.
  - Managed accounts reject a second lifecycle for the same `(account, symbol)` (`app/lifecycle/manager.py:727-729`, EXE-09) but that is keyed on the same string, not the underlying; plain accounts just stack fills.
- **Scenario:** Provider A (TradingView) sends `NASDAQ:AAPL` and provider B (Telegram) sends `AAPL`; the account's `symbol_map` has only `NASDAQ:AAPL: AAPL`? Then both land on `AAPL` and a later CLOSE from A flattens B's shares too. Without that map entry they are two keys, two positions, and provider B's "buy AAPL 200C x10" (OCC symbol) is a third; a $5,000 `max_notional_exposure` is evaluated per key sum (fine) but the risk gate never sees that all three are the same name, so a 10% AAPL gap hits ~3× the risk the operator believed each gate bounded. Across two accounts the owner-wide sum is gross (`capital_allocator.py:233-234`), a long on one and a short on the other both count.
- **Fix:** Derive an `underlying` key per position (option/future spec → underlying; equity symbol normalization strips venue prefixes) and expose a per-underlying exposure total that the per-account and owner-wide ceilings (and an optional per-underlying cap) use.

## B-13 — Qualification gate is keyed on strings the signal/parser controls (`asset_class` default CRYPTO) and omits the venue environment; a route qualified against a paper endpoint stays "release_approved" after the endpoint is switched to live
- **Severity:** P1  **Classification:** semantic-miss
- **Evidence:**
  - `app/engine.py:1745-1754`
    ```python
    if isinstance(broker, PaperBroker): return True, None
    route_key = account.account_id
    asset_class = signal.asset_class.value
    approved = self.store.is_route_release_approved(adapter_type=account.broker, route_key=route_key, asset_class=asset_class, product_type=_UNDECLARED_ROUTE_PRODUCT_TYPE)
    ```
  - Paper vs live is decided only by the adapter class (`isinstance`), never by endpoint: Alpaca's endpoint is an env var defaulting to paper (`alpaca.py:102` `os.getenv(f"{prefix}_BASE_URL", "https://paper-api.alpaca.markets")`); OANDA `oanda.py:90,96` `_ENV` practice/live; IBKR port and ccxt `sandbox` are instance attributes (`main.py:2195-2202` infers "paper"/"live" from them for display only). None of these enters the qualification key; `is_route_release_approved` (`db.py:5325-5349`) checks only that the `release_approved` rung was ever recorded; the ladder (`qualification.py:91-99`) has no `revoked`/`expired` state.
  - `signal.asset_class` defaults to CRYPTO (`models.py:115`; `text_parser.py:396` `asset_class: AssetClass = AssetClass.CRYPTO`) or is inferred from the symbol (`text_parser.py:156`) — a label the message controls, not a venue fact. `account.qualification_level`/`management_recipe` (`models.py:677-686`) are not consulted by the gate.
  - Not gated (by design): CLOSE (`engine.py:720`), protective-stop placement (`lifecycle/manager.py:1798`), reconciler actions.
  - Misconfiguration direction: an account with `broker: paper` always routes to the in-memory `PaperBroker` (`main.py:149-150`), so no real-money bypass exists via the paper exemption; the gap is the environment keying above. The ledger's "SignalStack is a retail relay" (`ALLOCATION_DECISION_LEDGER.md:66`) is consistent: SignalStack can never reach `account_entitled` (`base.py:193-221`).
- **Scenario:** Operator records the full ladder through `release_approved` for `(alpaca, acct1, equity, default)` while `ALPACA_ACCT1_BASE_URL` is unset (paper-api). Later they set it to `https://api.alpaca.markets` to go live: `_check_route_qualified` still returns approved; the first live order routes with no re-qualification, no venue test on the live endpoint. Separately, qualifying `(ibkr, acct2, crypto, default)` because a text source defaulted everything to CRYPTO admits any symbol that source emits, because the IBKR adapter declares EQUITY-only (`ibkr.py:63`) only after the qualification check (`engine.py:849`).
- **Fix:** Include the resolved venue environment (`base_url`/`env`/`port`/`sandbox`) in the route tuple (re-qualify on change), add a `revoked` terminal state checked by `is_route_release_approved`, and derive `asset_class` for the key from the account's declared product, not from the parsed signal.

## B-14 — Nothing prevents a provider "sell"/"short" from opening a short; no `allow_short`/long-only setting; paper shorts increase buying power
- **Severity:** P1  **Classification:** missing (ledger DL-45 requires "no short equities")
- **Evidence:**
  - `app/sources/text_parser.py:36` `"short": Side.SELL`; `:59` grammar `buy|sell|long|short|close|exit`.
  - Entry path never reads the tracked position: `get_position` is called only on close paths (`engine.py:2187`, `2521`); a SELL entry goes straight to `size_for_account` (`engine.py:1014`) → `place_order`. Adapters send it as a plain sell: `alpaca.py:134` `"side": signal.side.value`; `ibkr.py:107` `action = "SELL"`; `oanda.py:117` negative units (documented as a SHORT, `oanda.py:27-32`); ccxt `side="sell"`.
  - Managed path builds `PositionPlan(side=SELL)` (`engine.py:2673-2676`; `lifecycle/models.py:120` "SELL for short").
  - Paper: `paper.py:85-86` SELL adds cash. No `allow_short|long_only|no_short` config (grep: none). Ledger `ALLOCATION_DECISION_LEDGER.md:75` DL-45 "no short equities ... REPO: no enforcement found".
- **Scenario:** Provider posts "SELL TSLA" meaning "exit my earlier long". The follower's earlier BUY was rejected (e.g. by B-05's held reservation), so the account is flat. Quantity = `signal.quantity or 1.0` × multiplier → Alpaca margin account opens a short; on a cash account Alpaca rejects (ERROR → ambiguous reservation, B-05). If the provider's exit was sized "100" while the follower's long was 50 (multiplier 0.5 applied at entry but the exit message carried the provider's full size and a different multiplier override), the account flips from +50 to −50.
- **Fix:** Add `allow_short: false` per account (default false for equity/cash accounts); for a SELL entry when short is disallowed, treat it as reduce-only against the tracked (and broker-read-back) long and reject the remainder.

## B-15 — Fees and slippage are never captured from a real adapter and never enter any gate or P&L
- **Severity:** P2  **Classification:** silently-unsupported
- **Evidence:**
  - Only the paper broker sets the E10 fields: `app/brokers/paper.py:121-123` `fee=self.fee_per_fill, fee_currency="USD", slippage=0.0` (and `FEE_PER_FILL = 0.0`, `paper.py:51`). grep `fee=|fee_currency=|slippage=` in `app/brokers`: no other hits (Alpaca activities, ccxt `order["fee"]`, OANDA `commission`/`financing`, MT5 commission are all ignored).
  - `save_order_result` persists them (`db.py:2844`, `2869-2871`) — so the columns exist but hold NULL for every real fill.
  - Gates ignore fees: `engine.py:1943` notional vs buying power with no fee buffer; economics is "Gross of fees (not yet tracked)" (`economics.py:34-36`, `:154`); `daily_pnl.fees` never written (B-08).
- **Scenario:** ccxt/Binance taker fee 0.1%: 100 round trips on a $100k account cost ≈ $200 that the realized-P&L, win-rate, and (if ever wired) daily-loss figures never see; an entry that uses 100% of buying power is rejected by the exchange for the fee shortfall → ERROR → held reservation (B-05).
- **Fix:** Populate `fee`/`fee_currency` from each adapter's fill payload (ccxt `fee`, Alpaca `/v2/account/activities`, OANDA fill transaction), subtract fees in `compute_account_economics`, and add a configurable fee/slippage buffer to the buying-power check.

## B-16 — Paper fills at price 0.0 are counted as valid fills with zero cost, making that exposure invisible to every notional ceiling
- **Severity:** P2  **Classification:** mock-only-tested / silently wrong
- **Evidence:**
  - `app/brokers/paper.py:93` `price = signal.price or 0.0`; `:120` `filled_price=price`.
  - `app/economics.py:207-217` flags a fill as incomplete only when `price is None`, non-finite, or `signed_qty == 0` — `0.0` is accepted, so `average_cost` becomes 0 (`:227`) and `confirmed_open_notional` adds `abs(qty) * 0` (`capital_allocator.py:199`).
  - For real adapters a FILLED result without `filled_price` is None → `incomplete_symbols` → every gated admission is rejected (correct fail-closed path).
- **Scenario:** Certification/paper runs: "buy BTCUSD 2" with no price fills 2 BTC at 0.0; `max_notional_exposure: 50000` still shows 0 deployed; the next "buy BTCUSD 2 @ 60000" passes (requested 120,000 > 50,000? no — rejected), but "buy ETHUSD 10 @ 3000" passes at 30,000 while the book already holds 2 BTC. Realized P&L on closing the 2 BTC later equals the full sale proceeds.
- **Fix:** Treat `filled_price <= 0` as unresolved in `compute_account_economics`, and make `PaperBroker.place_order` reject a priceless signal (or fill at the last `simulate_price`) instead of 0.0.

## B-17 — `risk_percent_of_equity` is a 0–1 fraction while `daily_loss_limit_percent` is a 0–100 percent; the YAML/DB loaders do not validate the fraction
- **Severity:** P2  **Classification:** semantic-miss (config units)
- **Evidence:** `models.py:656-660` "percentage (0-1)"; `models.py:707-709` "e.g., 5 for 5%"; API validates `le=1` (`main.py:2411`) but `routing.py:174` `risk_percent_of_equity=spec.get("risk_percent_of_equity")` and `routing.py:221` (DB) accept any float; `engine.py:1864` `risk_ceiling = equity * risk_percent_of_equity`.
- **Scenario:** `accounts.yaml` `risk_percent_of_equity: 2` (operator means 2%) → ceiling = 2 × equity → the gate can never trip; the operator believes risk is capped at 2% per trade.
- **Fix:** Validate `0 < risk_percent_of_equity <= 1` in both loaders (reject at startup) and rename to `risk_fraction_of_equity`, or unify both fields on one unit.

## B-18 — `risk_percent_of_equity` cannot work on the paper broker (equity is None), so it is exercised only through a test-only subclass
- **Severity:** P3  **Classification:** mock-only-tested
- **Evidence:** `paper.py:139-140` returns `cash`/`buying_power` only (docstring `:132-138` explains equity stays None); `engine.py:1854-1861` rejects when `balance.equity is None`; `tests/test_e03_capital_exposure_gate.py:470-481` defines `_EquityBroker(PaperBroker)` overriding `get_account_balance` to supply equity. In production the gate is usable only with Alpaca (`alpaca.py:423`).
- **Scenario:** Operator rehearses `risk_percent_of_equity: 0.02` on a paper account before going live: every entry is rejected with "could not report a current equity figure", so the rehearsal proves nothing about live behavior.
- **Fix:** Make `PaperBroker.get_account_balance` report `equity = cash + Σ(position × last fill or simulated price)` and add an engine-level test that goes through `_try_reserve_capital` with a real broker figure.

## B-19 — Documentation and comments overstate/misdescribe the risk layer
- **Severity:** P3  **Classification:** doc
- **Evidence:**
  - `README.md:1279` "sizing (`app/risk.py`) still doesn't read live buying power before submitting an order" — stale: `_check_buying_power` runs on every entry (`engine.py:2033`), but fails open (B-07).
  - `README.md:276-282` "read-only observability, not enforcement" vs the same unconditional check.
  - `docs/design/CAPITAL_ALLOCATION.md:20` "Risk-basis sizing" for a reject-only gate (B-01).
  - `models.py:707-711` promises daily-loss enforcement "(realized + unrealized)" and `config.py:413` cites `docs/risk/CIRCUIT_BREAKER.md`; neither the implementation nor the file exists (B-08).
  - `engine.py:321-329` comments describe the loss limiter and margin detector as live "circuit breakers ... fail-closed".
- **Fix:** Rewrite these passages to state exactly which gates are unconditional, which are opt-in, which fail open, and which are unimplemented.

---

## Answers by question

1. **Position sizing.** Plain and managed accounts use the same `size_for_account` (`risk.py:16-19`): `fixed_quantity` if set, else `(signal.quantity or 1.0) × multiplier`, with provider/analyst overrides narrowing those two numbers only (`engine.py:460-467, 675-682`). Quantity is never derived from stop distance × risk %; `risk_percent_of_equity` is a reject-only ceiling (`engine.py:1863-1872`). "buy AAPL" with no quantity → 1 share (or 1 BTC / 1 lot / 1 contract, B-02). quantity=100 on a 1/10th-size follower → 100 shares, limited only by broker buying power where reported (B-01, B-07). There is no account-equity-proportional sizing anywhere.
2. **Rounding/minimums.** Only Rithmic and Tradovate reject non-integers (`rithmic.py:105-108`, `tradovate.py:178-181`); nobody enforces lot steps, crypto precision, option/future multipliers or venue min-notional; the paper broker fills any float (B-04). A fractional quantity sent to a rejecting broker comes back as ERROR, which the ledger treats as ambiguous and holds the capital reservation (B-05).
3. **Price used for gating.** `order_signal.price` from the message, in `_try_reserve_capital` (`engine.py:2077`), `_check_buying_power` (`1943`) and `_check_risk_basis` (`1843,1863`); no live quote is read before entry (`get_last_price` only in `pricing.py`); no max-chase/slippage policy; all adapters send market orders and ignore `entry_order_type` (B-06). Freshness gating exists only for catalog-registered providers (`engine.py:1389-1399`).
4. **Buying power.** Fails OPEN when the adapter has no override, returns None, or the signal has no price (`engine.py:1921-1942`). Real numbers: Alpaca (`alpaca.py:420-426`) and Paper (`paper.py:140`, cash only — no margin, no pending-order deduction, shorts add cash). None: IBKR, ccxt, OANDA, MT4/MT5 (both), Tradovate, Rithmic, NinjaTrader, SignalStack, Schwab, TradeStation, Tastytrade, Robinhood (B-07).
5. **Daily loss / min equity / margin.** Confirmed: `SignalStore.get_daily_pnl` and `SignalStore._broker_adapters` do not exist in `app/db.py`. `daily_pnl` is created (`db.py:1829`) and never written. The account fields are never loaded from YAML/DB/API. No persistent loss latch, no weekly halt, no kill switch. `MarginCallDetector` receives `None, None, None` from `engine.py:785-791` and returns None at `margin_call_detector.py:54-55` — a production no-op (B-08, B-09).
6. **Owner-wide / strategy ceilings.** Plain sums of raw `quantity × price` and reservation notionals (`capital_allocator.py:242-248`, `db.py:3273-3282`); no basis currency; `app/context/fx.py` is reference-only and unwired (B-10).
7. **Leverage/margin.** No leverage cap, no maintenance-margin headroom, no borrowed-funds exclusion; `maintenance_margin` is display-only; the ledger's canary 1.00 / standard 1.25 values are "preserved in the ledger only" (`ALLOCATION_DECISION_LEDGER.md:131`) (B-11).
8. **Same-underlying exposure.** None; positions and ceilings are keyed by the mapped symbol string, so `symbol_map` can make one instrument two positions (or merge two), and options are separate keys (B-12).
9. **Qualification gate.** Blocks a live ENTRY unless `release_approved` was recorded for `(account.broker, account.account_id, signal.asset_class, "default")`; paper vs live is `isinstance(broker, PaperBroker)` only; the venue endpoint (paper-api vs live env var/port/sandbox) is not part of the key and there is no revocation (B-13). A `broker: paper` misconfiguration routes to the in-memory broker, so it cannot reach real money.
10. **Short selling.** Nothing; "short" → SELL, entry path never checks the position, no `allow_short` setting; OANDA/IBKR/Alpaca-margin open a short; paper shorts raise buying power (B-14).
11. **Fees/slippage.** Never subtracted from buying power or notional; E10 columns populated only by the paper broker with 0.0 (B-15).

## UNVERIFIED items
- OANDA's rejection of fractional `units` for majors (`UNITS_PRECISION_EXCEEDED`) and Alpaca's exact 422 behavior for fractional bracket orders are vendor behaviors not re-verified here; the adapters' lack of any precision handling is verified.
- IBKR fractional-share acceptance depends on account settings; not verified.
