# Trading Signal Copier

Ingests trade signals from multiple sources and copies them, sized and
symbol-mapped per account, to one or more execution destinations.

This is a scaffold, not a finished product: the core pipeline (ingestion →
routing → risk sizing → execution → persistence) is fully working and
tested. Every source/broker integration is now working code — either
built directly (Telegram, Discord, Slack, SMS, Twitter, Alpaca, IBKR,
same-host MT5, SignalStack), or by wiring up an established open-source
project instead of reimplementing a platform bridge from scratch:
MT4/MT5 via [MetaApi](https://github.com/metaapi/metaapi-python-sdk),
Rithmic via [async_rithmic](https://github.com/rundef/async_rithmic), and
NinjaTrader execution via
[TradeRouter](https://github.com/roydufek/traderouter)'s NinjaScript
strategy. One gap remains genuinely open — NinjaTrader as a signal
*source* — because no existing open-source project reads trade events back
out of NinjaTrader (everything found is one-way, TradingView-in only); see
"What's real vs. stubbed" below.

## Architecture

```
Source adapter --(Signal)--> SignalCopierEngine --(per destination account)--> Broker adapter
                                     |
                                     v
                              SignalStore (SQLite)
```

- **`app/models.py`** — the one contract everything shares: `Signal` (a
  normalized trade instruction) and `OrderResult`.
- **`app/sources/`** — one adapter per signal source. Each adapter's only
  job is to turn whatever that platform sends into a `Signal` and call
  `on_signal(signal)`.
- **`app/brokers/`** — one adapter per execution destination. Each
  implements `place_order(signal, account, quantity, symbol)`.
- **`app/engine.py`** — `SignalCopierEngine`: for every incoming signal,
  looks up which destination accounts should receive it (`app/routing.py`),
  sizes and symbol-maps it per account (`app/risk.py`), and calls the
  right broker.
- **`app/db.py`** — SQLite log of every signal received, every order
  result, and this service's own tracked net position per
  (account, symbol) — see "Close signals" below.
- **`app/main.py`** — FastAPI app. Push-based sources (webhooks) get an
  HTTP route; pull-based sources (bots, pollers) would be started as
  background tasks in the `lifespan` handler.

Routing and account config are plain YAML (`config/*.yaml`, gitignored —
copy from the `.example.yaml` files). **Credentials are never stored in
YAML** — they're read from environment variables per account
(`CCXT_{ACCOUNT_ID}_API_KEY`, etc.), so the config files stay safe to
commit.

## Providers and analysts

Multiple named sources — or several traders posting into one shared
channel — often need to be treated differently even when they route to
the same destination account: a provider you trust more at full size, an
analyst inside a channel you want muted, a provider whose signals should
always go through the managed-lifecycle protect-first path regardless of
what the account's own default is. `app/providers.py`'s `ProviderRegistry`
handles this as a settings-inheritance layer, entirely optional:

    account (accounts.yaml) -> provider (Signal.source) -> analyst (Signal.analyst)

Any field left unset at a level inherits from the next broader one;
`enabled: false` at any level (account, provider, or analyst) skips that
destination for that signal without touching the others. Configure it in
`config/providers.yaml` (copy from `config/providers.example.yaml` — see
that file for the exact shape); no file at all means every signal uses
its destination account's own settings, completely unchanged from before
this existed. `Signal.analyst` is optional and only set by a source
parser that can actually identify who within that source posted a
signal — most currently can't, and provider-level (not analyst-level)
overrides still apply either way.

`GET /providers` returns every configured provider/analyst override
alongside the effective settings it resolves to for each destination
account, so "what does this analyst's signal actually do here" is
answerable without doing the account->provider->analyst merge by hand.

## Close signals

A `close` signal doesn't carry a size — closing means flattening whatever
is currently open, not scaling a new trade. `SignalCopierEngine` resolves
this per destination account before calling any broker:

1. Look up this service's own tracked net position for that account +
   mapped symbol (`app/db.py`'s `positions` table — its own record of what
   it has sent, not a live read of the broker's actual book).
2. Flat (zero)? Report `REJECTED` — "no open position to close" — without
   calling the broker.
3. Otherwise resolve to the opposing `buy`/`sell` at the full open
   quantity and call the broker with that. Brokers never see `Side.CLOSE`
   from the engine; each broker's own close handling (where present) is
   only a defensive fallback for direct/standalone use.

Position tracking updates from `OrderResult.filled_quantity` on `FILLED`,
or optimistically from the requested quantity on `PENDING` (SignalStack,
Alpaca, IBKR, NinjaTrader, and Rithmic all confirm fills asynchronously,
outside the `place_order` call). Paper, ccxt, and MT5 report real fills
synchronously, so their tracked positions are accurate immediately.

For the PENDING brokers, `app/reconciliation.py`'s `OrderReconciler`
background task periodically re-checks each PENDING order via the
broker's optional `get_order_status()` and corrects the tracked position:
reverses it if the order was actually rejected, or trues it up if the
confirmed fill quantity differs from the optimistic guess. Only brokers
that implement `get_order_status()` are covered this way — currently
**Alpaca** (a REST GET on the order) and **IBKR** (reads the locally
cached `Trade` object, which ib_insync keeps live-updated via its own
event stream). SignalStack, NinjaTrader, and Rithmic have no confirmed
order-status-read API wired up yet, so their PENDING orders stay
optimistic until that's added. The reconciler's poll interval is
`RECONCILE_INTERVAL_SECONDS` (default 30s).

## Stop-loss / take-profit

A `Signal`'s `stop_loss`/`take_profit` are sent as native exit orders on
every broker where that's a confirmed, safe API to use (verified against
each library/API's real source or docs, not guessed):

- **MT5** — `sl`/`tp` request fields, sent with the entry order.
- **MetaApi** — `stop_loss`/`take_profit` params on
  `create_market_buy_order`/`create_market_sell_order`.
- **Alpaca** — a bracket (`order_class: "bracket"`, both legs) or
  one-triggers-other (`order_class: "oto"`, a single leg) order; Alpaca
  manages the exit once the parent fills.
- **ccxt** — the unified `stopLossPrice`/`takeProfitPrice` order params
  (verified against ccxt's source: used by 90+ of its exchange
  implementations, including Binance and Bybit). If the configured
  exchange doesn't support it, ccxt raises `NotSupported`, reported as an
  ERROR rather than silently placing the entry without its exit.
- **IBKR** — a market parent order plus one or two child exit orders
  linked via `parentId`, only the last `transmit=True` so the whole group
  submits together — the same parent/child/transmit pattern
  `IB.bracketOrder()` uses, just with a market rather than limit parent
  (verified against ib_insync's source).

Still not forwarded: **Rithmic** (its `submit_order` takes stop/target
distance in *ticks*, not the prices a `Signal` carries — converting
needs the instrument's tick size, which isn't wired up yet) and
**NinjaTrader**/**SignalStack** (their bridge payload schemas, as
documented by TradeRouter and SignalStack respectively, don't have
confirmed SL/TP fields — inventing one risks a silently wrong or ignored
exit order on real money). If you need SL/TP on one of those, say which
and I'll research and verify it properly rather than guess.

### The entry is refused, not silently unprotected

The four brokers above that can't forward `stop_loss`/`take_profit`
(`paper`, `signalstack`, `ninjatrader`, `rithmic`) used to just ignore
those fields on a plain (non-`managed_lifecycle`) account — the entry
went through, the position opened, and nothing protecting it was ever
submitted, with no error anywhere. That's now a release-blocking defect
class this project treats as fixed, not documented-and-left: if a signal
carries `stop_loss`/`take_profit` and the destination account isn't
`managed_lifecycle`, `SignalCopierEngine.handle_signal` checks
`broker.supports_native_bracket` **before** calling `place_order` — if
it's `False`, the entry is `REJECTED` outright ("refusing to submit an
entry that would silently open unprotected") instead of being sent. The
same check exists on the `managed_lifecycle` path too: `PositionLifecycleManager.validate_plan`
refuses an entry whenever the target broker has neither a native bracket
nor a *verified* `place_protective_stop` implementation
(`BrokerAdapter.can_protect_a_managed_position()`), rather than admitting
the plan and letting `on_entry_fill` discover the gap after the fact and
just log a warning.

Both checks are computed from whether the broker subclass actually
overrides the relevant base-class no-op (`has_protective_stop_capability`,
`has_cancel_capability`, `has_replace_stop_capability`,
`has_position_readback_capability`, `has_order_status_capability` — see
`app/brokers/base.py`'s "Capability introspection" section) — not from a
separately maintained boolean that could silently drift out of sync with
the code. `GET /brokers` exposes this same matrix for every registered
broker, so "is this route actually safe to trade unattended" is a fact
you can query, not something to infer from a broker's name or an imported
SDK.

A signal with **no** `stop_loss`/`take_profit` at all is unaffected —
this only blocks the specific case of requesting protection a broker
would silently drop.

## Managed lifecycle (protect-first position management)

For a broker/exchange that can't submit entry + stop-loss + take-profit as
one atomic bracket/OCO order, embedding stop_loss/take_profit straight
into the entry (as the "Stop-loss / take-profit" section above describes)
means firing independent orders that a naive implementation could let
both fill — an oversell. Any `DestinationAccount` can opt into a
different path instead by setting `managed_lifecycle: true` in
`config/accounts.yaml`:

    ENTRY -> actual fill observed -> protect the filled quantity FIRST
    -> manage targets/trailing as logical (app-side) instructions
    -> coordinate every exit through one CloseArbiter

- **`app/lifecycle/models.py`** — `PositionPlan`, `Target`,
  `TrailingPolicy`: the account-agnostic description of an entry, its
  stop, and its profit-taking/trailing behavior.
- **`app/lifecycle/close_arbiter.py`** — `CloseArbiter`: the single
  serialization point per (account, symbol) enforcing `available_to_sell
  = confirmed_owned_quantity - reserved_quantity`. Every exit — a target
  firing, a trailing ratchet, the stop itself filling, a provider CLOSE
  signal — reserves its quantity here before touching the broker, so two
  exits can never both claim the same shares. A violation of that
  invariant self-halts the position (further exits are rejected) rather
  than risk a silent oversell.
- **`app/lifecycle/manager.py`** — `PositionLifecycleManager`: submits the
  entry, places the protective stop on the *actual* confirmed fill
  (design's core rule), evaluates logical targets/trailing on price
  updates, and performs the stop-resize transition (cancel the existing
  stop -> confirm the cancel -> submit the exit -> observe what actually
  filled -> resize a replacement stop to the true remainder) so a partial
  fill on a target never leaves the stop covering more than what's left.
- **`app/engine.py`**'s `handle_signal` routes a `managed_lifecycle`
  account's BUY/SELL signals into a `PositionPlan` (`stop_loss` becomes
  `initial_stop`; a `take_profit` becomes one logical SELL target for the
  full planned quantity) and its CLOSE signals into
  `PositionLifecycleManager.request_exit()` against that manager's own
  tracked owned quantity — not `SignalStore`'s plain position table, which
  the non-managed path uses.
- **An entry with no resolved stop-loss is refused outright** ("no
  stop-loss resolved for this entry ... refusing to enter unprotected"),
  never sent to the broker unprotected.

### Partial-fill-during-a-transfer correctness (protection transfers)

Reducing a stop to free shares for an exit creates a real, non-instantaneous
protection gap for those shares — and if that exit only *partially* fills
(the normal case on any broker that confirms fills asynchronously, e.g.
Alpaca), the stop must not be restored against what was *requested*, only
against what's *actually confirmed done*. `app/lifecycle/models.py`'s
`PendingExit` and `TransferPhase` track this explicitly, and
`PositionLifecycleManager` enforces it:

- If `broker.place_order()` for an exit reports `PENDING` (not a
  synchronous final fill), the manager does **not** resize/restore the
  stop yet. It records a `PendingExit` (requested quantity, broker order
  id) and leaves the freed shares genuinely uncovered — `covered_quantity`
  / `uncovered_quantity` on the lifecycle reflect that honestly, not a
  single `protected=true/false` flag.
- **No second exit is accepted while one is unresolved** — `request_exit`
  refuses with "hasn't resolved yet" rather than submitting another
  broker write on top of an unknown outcome.
- **No trailing/tighten update touches the stop while unresolved** either
  (`_replace_stop_price` skips and logs) — re-arming a stop sized off
  current owned quantity would re-cover shares that might still leave via
  the pending order, recreating the same oversell risk.
- Once the outcome is final — `PositionLifecycleManager.resolve_pending_exit()`,
  called by `app/reconciliation.py` polling `get_order_status()` the same
  way it already does for pending entries — the stop is resized to
  `confirmed_owned_quantity - confirmed_filled_quantity`, using whatever
  actually filled. A 15-share target that only fills 8 before its
  remainder is confirmed cancelled restores the stop to 54 (62 − 8), not
  47 (62 − 15); if 3 more fill while that cancellation was in flight, the
  restore target is 51, not 54. See `tests/test_protection_transfer.py`,
  which reproduces this exact sequence.

### Crash-resumable persistence

`PositionLifecycleManager` accepts an optional `store=` (the same
`SignalStore`) and, when given one, persists every lifecycle transition
(entry fill, stop resize, pending-exit open/resolve) to a `lifecycle_state`
table as one JSON blob per (account, symbol) — including the `CloseArbiter`
ledger's `owned`/`reserved`/`halted` snapshot. `app/main.py` calls
`restore_from_store()` once at startup, before any signal is handled, to
rebuild in-memory lifecycles and arbiter ledgers from that table — a
process restart no longer loses what a managed-lifecycle position was
mid-transfer doing (`tests/test_protection_transfer.py`'s
`test_persisted_lifecycle_resumes_after_restart_with_deficit_intact`
exercises this, including resuming an in-flight `PendingExit`). The
position closes the row is deleted (`store.delete_lifecycle_state`), so
the table only ever holds what's actually still open.

### What's still open, not implemented

Being explicit about what this round did *not* close, rather than
implying broader coverage than exists:

- **A live price feed driving `on_price_update()` in production.**
  Nothing calls it outside tests — wiring a real feed per broker (or per
  exchange) is future work, and profitability claims for a trailing/target
  strategy shouldn't be trusted until they account for real feed latency
  and the cancel/replace delays above, not an idealized instant-fill
  assumption.
- **Fill confirmation for an entry that reports `PENDING`.** The position
  is opened but left unprotected by this manager until a fill-confirmation
  path feeds the entry's actual fill back in (the pending-*exit* half of
  this problem is now handled via `resolve_pending_exit`/reconciliation
  above; the pending-*entry* half is not).
- **Per-order-family accounting for genuinely independent broker orders**
  (as opposed to this manager's own cancel-then-exit sequence). Everything
  routed through `PositionLifecycleManager` goes through the single
  `CloseArbiter` lock, so it can't itself submit two competing sells —
  but this doesn't model a broker's own OCO/bracket group's execution
  guarantees (or lack of them: Alpaca's own OCO docs note both legs can
  fill before a cancellation lands in a fast market), doesn't distinguish
  a venue-enforced quantity cap from independent orders that merely look
  related, and doesn't track cancel-on-disconnect / dead-man-switch
  settings that could cancel a protective stop out from under a held
  position. Treat `supports_native_bracket = True` as "this broker/route
  accepts one atomic order for entry+stop+target," not as a guarantee
  about what the venue's matching engine can still do to two already-
  accepted orders afterward.
- **A GUI.** This project has no UI; the position-level detail described
  above ("47 covered, 7 pending-exit-uncovered, restoring once resolved")
  is exposed as data via `GET /positions`' `managed_lifecycles` field (see
  "Monitoring" below), for a caller to render however it needs to.
- **Broker/route capability certification as one combined check**
  (entry type + attached protection + amendment method + trigger basis +
  session + account mode, all together, not evaluated independently) and
  **operation-specific trading-window handling** (create/amend/cancel/
  trigger/execute don't all share one "market is open" window on every
  broker). Neither is modeled here; `supports_native_bracket` and the
  optional capability methods on `BrokerAdapter` are per-operation, not a
  certified combination.

See `app/lifecycle/manager.py`'s module docstring for the full design
rationale, and `tests/test_close_arbiter.py` / `tests/test_lifecycle_manager.py`
/ `tests/test_protection_transfer.py` for the oversell-prevention,
partial-fill-arithmetic, and pending-exit-transfer proofs.

## Signal Backtester

```
POST /backtest
```

Replays historical signals this service has already received (from
`SignalStore`) against locally supplied OHLC bars, asking "what would
have happened to each signal's own resolved stop/target." This is
deliberately scoped, not a full trading-platform backtester — read
`app/backtest/replay.py`'s module docstring before trusting a report from
it, but the short version:

- **No market-data vendor is connected.** There's no API key, no MCP
  tool, no bundled dataset for historical prices — `app/backtest/models.py`'s
  `PriceHistoryProvider` is the seam a real one plugs into, and
  `CsvPriceHistoryProvider` (the only implementation shipped) reads bars
  *you* supply from a local CSV (`timestamp,open,high,low,close[,volume]`).
  Backtesting a symbol you haven't sourced data for doesn't work, and
  nothing here invents prices to make it look like it does.
- **Path-dependent ambiguity is preserved, not guessed.** If a bar's
  `[low, high]` range contains both the stop and the target, which one
  the price actually touched first isn't recoverable from OHLC data
  alone — that trade is reported as `AMBIGUOUS`, exactly the case
  worked out with concrete numbers (entry 100, stop 95, target 105, a
  bar with high 106 / low 94) in the design this follows.
- **Each signal is replayed as one independent round-trip trade**, sized
  at its own `quantity`, entering at its own recorded `price`. There's no
  shared-account-capital portfolio simulation across overlapping
  signals yet — a real follow-up, not implemented here.
- **No slippage or fee modeling** — a resolved trade fills at the exact
  stop/target price.
- Only signals **saved after this feature shipped** carry `stop_loss`/
  `take_profit`/`analyst` (new columns on the `signals` table, added via
  an additive migration — see `app/db.py`'s `_COLUMN_MIGRATIONS`); older
  rows replay as `NO_EXIT_LEVELS`, not silently skipped.
- `profit_factor` is `None` with an explanatory note (not `float('inf')`)
  when there are no losing trades to divide by — an "unexplained
  infinite score" is exactly the wrong answer this avoids.

```bash
curl -X POST http://localhost:8000/backtest \
  -H 'Content-Type: application/json' \
  -d '{
        "source": "tradingview",
        "symbol": "AAPL",
        "start": "2024-01-01T00:00:00+00:00",
        "end": "2024-06-01T00:00:00+00:00",
        "csv_paths": {"AAPL": "/path/to/AAPL_daily_bars.csv"}
      }'
```

Returns `{"summary": {...}, "trades": [...]}` — every replayed signal
with its outcome (`win`/`loss`/`ambiguous`/`still_open`/`no_price_data`/
`no_exit_levels`/`not_replayed`), not just the ones that resolved
cleanly. There's no persisted "Signal Backtests workspace" or GUI for
this yet — the dashboard stays strictly read-only (see below), so a
run's own request/response is the interface for now.

See `tests/test_backtest_simulator.py`, `tests/test_backtest_replay.py`,
`tests/test_backtest_csv_provider.py`, and `tests/test_backtest_api.py`
for the ambiguity-handling and end-to-end proofs.

## Dashboard

```
GET /   # a minimal, read-only web dashboard
```

One static HTML page (`app/static/dashboard.html`, served directly — no
build step, no frontend framework, no new dependency) with vanilla JS
that polls the JSON endpoints below and renders them as tables: broker
capabilities, open positions, managed-lifecycle coverage/deficit detail,
provider/analyst overrides, recent signals, and recent orders. It
auto-refreshes every 10 seconds and has a manual refresh button. It is
**read-only** — there is no button anywhere on it that submits, cancels,
or modifies anything; every mutation still only happens through a
source's own push (a webhook, SMS) or a pull-based source's background
task, exactly as before this page existed.

## Monitoring

```
GET /positions              # every non-flat tracked position, across all accounts
GET /signals?limit=50       # most recently received signals, newest first
GET /orders?limit=50&account_id=...   # most recent order results, optionally filtered to one account
GET /brokers                # every registered broker's actual, code-verified capability matrix
GET /providers              # configured provider/analyst overrides and their effective settings per account
```

The dashboard above is built entirely on these — nothing it shows isn't
already available as JSON here too. `/positions`, `/signals`, and
`/orders` read from `SignalStore` (`app/db.py`) — this service's own
record, not a live broker read; `/brokers` and `/providers` are computed
directly from the registered adapters and `config/providers.yaml`
in-process, nothing to do with `SignalStore`. Positions self-correct in the background
for Alpaca/IBKR via `OrderReconciler` (see "Close signals" above); on
brokers without a wired-up order-status read, a PENDING order stays
optimistic until you check that broker's own account state directly.

`GET /positions` also returns a `managed_lifecycles` array — one entry per
open `managed_lifecycle` position, with `owned_quantity`,
`covered_quantity` (behind a broker-confirmed stop right now),
`uncovered_quantity`, `stop_status`/`stop_price`, `halted`/`halt_reason`,
and `pending_exit` (non-null while an exit's remainder hasn't resolved —
see "Managed lifecycle" above). This is the quantity-by-quantity picture
a naive `protected: true/false` flag can't give you.

## What's real vs. stubbed

| Component | Status |
|---|---|
| Core engine, routing, risk sizing, SQLite log | ✅ Working, tested |
| Generic JSON / TradingView webhook source | ✅ Working, tested |
| Paper (mock) broker | ✅ Working, tested |
| ccxt broker (Binance/Bybit/etc crypto exchanges) | ✅ Working, tested (needs `pip install ccxt` + API keys). Native stop-loss/take-profit via unified `stopLossPrice`/`takeProfitPrice` params. |
| SignalStack broker (relays to IBKR, Schwab, Alpaca, Tradier, TradeStation, Bybit, Coinbase Pro, Oanda, etc. via signalstack.com) | ✅ Working, tested (needs a SignalStack account + a webhook URL per connected broker) |
| Alpaca broker (plain REST, no SDK) | ✅ Working, tested (needs API key/secret; defaults to the paper-trading endpoint). Native stop-loss/take-profit via bracket/OTO orders. |
| Telegram, Discord, Slack sources | ✅ Working (needs `pip install python-telegram-bot` / `discord.py` / `slack-bolt` + a bot token; only starts if its env vars are set) |
| SMS source (Twilio) | ✅ Working (needs a public URL + `TWILIO_AUTH_TOKEN`/`TWILIO_WEBHOOK_URL` for signature validation; route is always mounted at `/sms/twilio`) |
| Twitter/X source | ✅ Working, but needs X API v2 filtered-stream access (a paid tier as of X's current pricing — verify current terms) and is the least reliable parser of the bunch since tweets are free text |
| IBKR broker | ✅ Working, tested (needs `pip install ib_insync` + a running IB Gateway/TWS with the API enabled; reports PENDING, not a confirmed fill, since IBKR confirms asynchronously — but see "Monitoring" for how PENDING gets reconciled). Native stop-loss/take-profit via bracket orders. |
| MT5 broker (same-host only) | ✅ Working (needs `pip install MetaTrader5`, Windows, and the service running on the same host as a logged-in MT5 terminal — one terminal process per account). Native stop-loss/take-profit via `sl`/`tp` request fields. |
| MT4/MT5 source & broker, via [MetaApi](https://github.com/metaapi/metaapi-python-sdk) | ✅ Working (needs `pip install metaapi-cloud-sdk` + a MetaApi account — free tier covers 1 MT4/MT5 account; no local terminal needed at all). Preferred over the same-host MT5 broker above unless you specifically want to avoid the cloud dependency. The source polls deal history on an interval rather than a real-time push callback — see its docstring for why. Native stop-loss/take-profit via `stop_loss`/`take_profit` params. |
| Rithmic source & broker, via [async_rithmic](https://github.com/rundef/async_rithmic) | ✅ Working (needs `pip install async_rithmic` + licensed Rithmic credentials from your broker — there's no self-serve signup, this is a paid/licensed service regardless of which library talks to it) |
| NinjaTrader broker, via [TradeRouter](https://github.com/roydufek/traderouter)'s `WebhookOrderStrategy.cs` | ✅ Working (needs TradeRouter's NinjaScript strategy file installed and compiled inside NinjaTrader itself — this service just POSTs to its local HTTP listener; see the broker's docstring) |
| NinjaTrader signal *source* | 🚧 Stub — every open-source NinjaTrader bridge found (TradeRouter, ninja-webhook, tv-ninjatrader-bridge) is one-way (external signal → NinjaTrader order); none reads trade/fill events back out. Doing that needs a custom NinjaScript AddOn this project can't write and verify without the actual platform. See the file's docstring. |

The Telegram/Discord/Slack/SMS/Twitter parsers all share one generic
free-text parser (`app/sources/text_parser.py`) that handles the common
`BUY BTCUSDT @ 65000 SL 63000 TP 70000` family of formats. If a specific
channel's format doesn't fit, override that source's `parse()`.

Note: the direct `alpaca` and `ibkr` brokers are only needed if you want
this service talking to those brokers itself. If you already have (or set
up) a SignalStack account, the `signalstack` broker reaches IBKR, Alpaca,
and several others through one already-working adapter — no need to also
configure the direct integration for those specific brokers.

## Quickstart

```bash
cd signal-copier
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # uncomment optional deps you need

cp .env.example .env                     # fill in what you have
cp config/routing.example.yaml config/routing.yaml
cp config/accounts.example.yaml config/accounts.yaml

uvicorn app.main:app --reload
```

Send a test signal:

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H 'Content-Type: application/json' \
  -d '{"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "price": 65000}'
```

With the example config this fills the `paper_main` account instantly (no
real order placed) and attempts `binance_sub1` via ccxt (will error until
you install `ccxt` and set its API key env vars).

## Adding a new source or broker

1. Subclass `SourceAdapter` (`app/sources/base.py`) or `BrokerAdapter`
   (`app/brokers/base.py`).
2. Implement the one required method (`start()`/`parse()` for a source,
   `place_order()` for a broker).
3. Register it in `app/main.py` (sources) or the `brokers` dict there
   (brokers), and reference its `name` in `config/routing.yaml` /
   `config/accounts.yaml`.

Nothing else needs to change — the engine, risk sizing, and persistence
layer are adapter-agnostic.

## Running tests

```bash
pytest -q
```

CI runs this automatically on every push/PR that touches `signal-copier/**`
(`.github/workflows/signal-copier-ci.yml`, scoped separately from the
parent repo's own CI so it doesn't run against unrelated changes).

## Running with Docker

```bash
cp .env.example .env
cp config/routing.example.yaml config/routing.yaml
cp config/accounts.example.yaml config/accounts.yaml

docker compose up --build
```

The database lives in a named volume (`signal_copier_db`) so it survives
container recreation; `config/` is bind-mounted so routing/account changes
don't need a rebuild. To bake in an optional adapter's dependency (e.g.
`ccxt`), uncomment and edit the `build.args.EXTRAS` line in
`docker-compose.yml`, or `docker build --build-arg EXTRAS="ccxt tweepy" .`
directly.

Verified: the image builds and runs, `/health` responds, and a webhook
signal correctly routes through to the paper broker and updates
`/positions` inside the running container.

## Security notes for when this goes live

- Set `WEBHOOK_SHARED_SECRET` before exposing `/webhook/*` publicly —
  otherwise anyone who finds the URL can inject fake signals.
- Never commit `.env` or real `config/routing.yaml` /
  `config/accounts.yaml` if they end up containing anything
  account-identifying (they're gitignored by default).
- Every broker adapter should fail loudly (as the ccxt one does) rather
  than silently skip an order when credentials are missing.
