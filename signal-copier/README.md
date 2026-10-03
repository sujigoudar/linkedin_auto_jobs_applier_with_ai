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
strategy. NinjaTrader as a signal *source* is also real, working code now
(`app/sources/ninjatrader.py` plus a fresh-written NinjaScript indicator,
`ninjascript/SignalCopierAutoJournal.cs`, since no existing open-source
project reads trade events back out of NinjaTrader — everything found is
one-way, TradingView-in only). The remaining gap is narrower than "genuinely
open": the Python side (parsing, the `/ninjatrader/webhook` route) is real
and tested; the NinjaScript `.cs` side has been reviewed against two
verified real reference implementations but never compiled or run against
a real or Sim101 NinjaTrader 8 install, since there is no NinjaTrader or
Windows environment available here — see "What's real vs. stubbed" below.

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
  (account, symbol) — see "Close signals" below. Still plain stdlib
  `sqlite3` (Core is enough; an ORM rewrite isn't needed for the volume
  here), but schema changes are now versioned Alembic migrations
  (`alembic/versions/`, C03) instead of appending to `_COLUMN_MIGRATIONS`
  — that list is frozen as of the migration to Alembic; the next schema
  change should be `alembic revision -m "..."`, not another tuple there.
  Every database (fresh or an existing pre-Alembic one) gets stamped at
  the current head on open (`SignalStore._stamp_alembic_head_if_needed`)
  without re-running schema creation against it.
- **`app/main.py`** — FastAPI app. Push-based sources (webhooks) get an
  HTTP route; pull-based sources (bots, pollers) would be started as
  background tasks in the `lifespan` handler.

Accounts, routing rules, and provider/analyst overrides are **live-managed
through the dashboard/API**, backed by SQLite (`SignalStore`'s
`config_accounts`/`config_routing_rules`/`config_providers`/`config_analysts`
tables) — not static YAML you hand-edit and restart the process for. See
"Managing config through the GUI/API" below for what that actually means
and, just as importantly, what it doesn't cover. `config/*.yaml` still
exists as a one-time import path for an existing hand-edited setup
(`app/config_admin.py`'s `seed_from_yaml_if_empty`) and remains the option
for anyone who prefers file-based config in version control — but once any
account exists (from either path), the database is authoritative and the
YAML files are no longer read. **Credentials are never stored in the
database or YAML either way** — they're always read from environment
variables per account (`CCXT_{ACCOUNT_ID}_API_KEY`, etc.), so neither the
config files nor the database need to be treated as secret.

## Managing config through the GUI/API

Direct answer to "can I manage everything through the GUI": **accounts,
routing rules, and provider/analyst overrides — yes, live, no restart.
Signal sources themselves — partially; see the table below.**

The dashboard (`GET /`) has forms for all three, backed by real CRUD
endpoints:

```
GET/POST/DELETE   /accounts                          # destination accounts
GET/POST/PUT/DELETE  /routing-rules /routing-rules/{id}  # which source feeds which account(s)
GET/POST/DELETE   /providers/{provider_id}                       # provider-level overrides
POST/DELETE       /providers/{provider_id}/analysts/{analyst_id} # analyst-level overrides
```

Every write here does two things: persists to the database, AND mutates
the running engine's live `RoutingConfig`/`ProviderRegistry` objects in
place — so a signal arriving immediately after you click "Add account" in
the browser already uses it, no restart, no reload. `tests/test_config_crud.py`
proves this end-to-end (create an account + routing rule through the
API, then send a real webhook signal in the same test, with no restart in
between).

**What this does NOT cover — the genuinely honest limits:**

| What you might expect | What's actually true today |
|---|---|
| "Give it a Discord/Telegram/Slack/X and it just works" | No. You still have to create a bot/app on that platform's own developer portal yourself (Discord Developer Portal, @BotFather, api.slack.com/apps, X's API dashboard) — that step is inherent to those platforms and no amount of GUI here can skip it. |
| Adding a Telegram/Discord/Slack/X/SMS **source** through this app | Not yet live-manageable. Each pull-based source is a long-lived background task started once at process startup from env vars (`TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, etc. — see `.env.example`); starting/stopping one dynamically via an API call needs careful asyncio task lifecycle handling this project hasn't built yet. Set the env var, restart the process, and it starts. The webhook source (`POST /webhook/{name}`) is the one push-based exception — genuinely zero config needed beyond a routing rule pointing at whatever `{name}` you choose. |
| Setting broker credentials through the GUI | Deliberately not offered. `DestinationAccount`/broker credentials stay in environment variables per the project's "never store secrets in config" rule (see Security notes below) — creating an account through the dashboard registers its *routing/sizing* config, not its API keys. Weakening this to make onboarding one step shorter isn't a trade this project makes. |
| Editing which brokers are *available* (`ccxt`, `alpaca`, etc.) | Not live either — the broker registry (`app/main.py`'s `brokers` dict) is built once at startup from installed packages/env vars. `GET /brokers` shows you what's actually registered. |

So: once you've done the one-time, per-platform bot setup and set the
resulting token as an env var (and restarted once), **everything else —
which account that source's signals go to, sizing per provider/analyst,
adding new accounts, changing routing — is fully GUI/API-managed from
then on**, live.

## Providers and analysts

Multiple named sources — or several traders posting into one shared
channel — often need to be treated differently even when they route to
the same destination account: a provider you trust more at full size, an
analyst inside a channel you want muted, a provider whose signals should
always go through the managed-lifecycle protect-first path regardless of
what the account's own default is. `app/providers.py`'s `ProviderRegistry`
handles this as a settings-inheritance layer, entirely optional:

    account (GET/POST /accounts) -> provider (Signal.source) -> analyst (Signal.analyst)

Any field left unset at a level inherits from the next broader one;
`enabled: false` at any level (account, provider, or analyst) skips that
destination for that signal without touching the others. Manage it live
through the dashboard's "Providers & analysts" forms or the
`POST/DELETE /providers/{id}` and `/providers/{id}/analysts/{id}`
endpoints (see "Managing config through the GUI/API" above); no provider
configured at all means every signal uses its destination account's own
settings, completely unchanged from before this existed.
`Signal.analyst` is populated by every source that can actually identify
who posted a signal: Discord (the message author), Telegram (username or
full name), Slack (the raw user ID), Twitter/X (the numeric author ID),
SMS (the sender's phone number), and the webhook source (an explicit
`"analyst"` field in the JSON payload) — see each source's own docstring
for exactly what identifier it uses. `tests/test_source_analyst_wiring.py`
covers each one.

`GET /providers` returns every configured provider/analyst override
alongside the effective settings it resolves to for each destination
account, so "what does this analyst's signal actually do here" is
answerable without doing the account->provider->analyst merge by hand.

## Signal-provider value analysis & subscription management

Separate from the settings-inheritance layer above: `app/provider_value.py`,
`app/provider_scout.py` and a "Signal provider value & subscriptions"
dashboard panel answer "is this provider still worth what I'm paying for
it" and "is any free source I'm already trading worth adopting properly."

**Subscription cost tracking** (`provider_subscriptions`, one row per
provider — `PUT/DELETE /providers/{id}/subscription`, `GET
/providers/subscriptions`): cost per billing cycle, currency, billing
cycle (`monthly`/`annual`/`one_time`/`free`), when you started tracking it
(`subscribed_since`, the anchor for the cost-to-date estimate below — kept
across edits unless you explicitly change it), an informational next
renewal date, and a status. Purely a cost record; it never touches routing
or sizing.

**Value calculation** (`app/provider_value.py`, `GET /providers/value`,
filterable by `source`/`analyst`/`asset_class`): realized P&L, win rate
and profit factor per (provider, analyst, asset class), replayed from this
service's own confirmed execution journal — never self-reported. Two
providers can share one destination account/symbol (a second analyst
adding to a position the first one opened), so this uses **FIFO lot
attribution**, not `app/economics.py`'s per-account volume-weighted
average: each entry fill opens a lot tagged with its own signal's
provider, and a reducing fill consumes those lots oldest-first, crediting
each lot's realized P&L to ITS provider regardless of which provider's
signal (if any) triggered the close. See that module's own docstring for
the full methodology.

**Read this before acting on a "cancel this provider" number:** a
managed-lifecycle position's stop-loss, profit-target, or trailing exit
fill **never appears in this calculation at all** — those are applied
directly to the tracked position/lifecycle state (see "Managed
lifecycle" above) and never create the `orders` row this replay reads (the
same disclosed limitation `app/economics.py`'s account-level P&L already
has). A provider whose losing trades are mostly caught by a stop, rather
than an explicit CLOSE signal, will look artificially better here than it
really is. Corroborate against the dashboard's "Managed-lifecycle
coverage" panel first.

**"Still worth paying for" verdict:** each provider's totals (summed
across every analyst/asset_class) are compared against
`PROVIDER_VALUE_MIN_SAMPLE_SIZE` closing fills (default 10),
`PROVIDER_VALUE_WIN_RATE_THRESHOLD` (default 0.4) and
`PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD` (default 1.0) — a disclosed
heuristic, not a claim of statistical significance:

- `insufficient_data` — fewer closing fills than the sample-size floor.
- `cancel_candidate` — win rate AND profit factor both poor, AND
  realized P&L minus an estimated cost-to-date (`cost_amount` × billing
  cycles elapsed since `subscribed_since` — a coarse estimate, not
  proration-exact) is negative.
- `underperforming_free` — same poor performance, but `cost_amount` is 0
  (free/untracked) so there's no $ to weigh it against.
- `keep` — otherwise.

**Scheduled free-provider scouting** (`app/provider_scout.py`, a
background loop started the same way as `OrderReconciler`/`PriceMonitor`,
interval `PROVIDER_SCOUT_INTERVAL_SECONDS`, default once a day): every
(source, analyst, asset_class) with real fill data that ISN'T yet a
`provider_subscriptions` row — i.e. every free source already being
executed — gets the same win-rate/profit-factor evaluation (minus the
cost half, since there's nothing to weigh yet) and a `promote` /
`not_promising` / `insufficient_data` recommendation, persisted to
`provider_candidates` (`GET /providers/candidates`) so the dashboard shows
"last evaluated at X" without recomputing the full replay on every page
load. **Promoting a candidate** (`POST /providers/candidates/promote`)
only creates a `provider_subscriptions` row (defaulting to
`cost_amount=0`/`billing_cycle="free"`) and clears its candidate
snapshot — it deliberately does NOT create a routing rule or change any
provider/analyst setting; a source with no routing rule was never
actually being executed, and promoting it here doesn't change that.

## Multi-asset routing, account selection, and balances

Direct, honest answers to how this actually behaves today — not what a
mature multi-asset platform would ideally do:

**Does the system handle every asset class (crypto, forex, futures,
stocks, options)?** `Signal.asset_class` (`crypto`/`forex`/`equity`/
`option`/`future`) is carried through the whole pipeline — persisted,
passed to the broker, shown in the backtester — but nothing about it is
inherently "handled" beyond what a specific broker adapter actually
implements. `AlpacaBroker` and `IBKRBroker` only submit plain equity
orders (both say so in their own module docstrings — Alpaca's explicitly
does not attempt options-specific order shaping even though Alpaca
itself supports options); `CCXTBroker` is crypto-only by what ccxt is.
Nothing here implements true options-contract order construction,
futures margin/contract-roll handling beyond symbol mapping, or forex
lot-sizing beyond what MT4/5's own request fields do.

**If one provider mixes asset classes (e.g. options + stocks + crypto
alerts in one channel), does each trade reach the correct broker?**
As of this round, **yes, or it's refused — never silently misrouted.**
Every `BrokerAdapter` can declare `supported_asset_classes`
(`app/brokers/base.py`); `SignalCopierEngine` checks
`broker.can_trade_asset_class(signal.asset_class)` before submitting
anything and rejects the signal outright if it doesn't match (message:
`"broker '...' cannot trade asset_class=... — refusing to route this
signal here"`), rather than sending a malformed order or letting the
broker misinterpret it. This is opt-in per broker and only declared
where the code has a real, verified reason to restrict (Alpaca/IBKR:
equity-only; ccxt: crypto-only) — an undeclared broker (SignalStack,
MT4/5, NinjaTrader, Rithmic) is unrestricted by default, since those
genuinely can carry more than one asset class or this project hasn't
verified a hard restriction for them yet. `GET /brokers`'
`supported_asset_classes` field shows exactly which brokers are
restricted and to what. `tests/test_asset_class_gate.py` reproduces the
mixed-provider scenario directly: an options alert and a crypto alert
from the same source both correctly refused on an equity-only account,
while a stock alert from that same source still routes normally.

**Are balances and margin kept separated per broker/exchange?** Every
`DestinationAccount` maps to exactly one broker connection with its own
credentials (env vars per `account_id`) and `PositionLifecycleManager`/
`CloseArbiter` track protection and available-to-sell state per
`(account_id, symbol)` independently — nothing here ever pools or
commingles state across accounts or brokers. `get_broker_position`
(position readback) and `get_account_balance` (cash/equity/buying-power/
margin — see `app/models.py`'s `AccountBalance` and
`app/brokers/base.py`'s docstring) both exist as optional, per-broker
capabilities, real (not stubbed) for `AlpacaBroker` (`GET /v2/account`)
and exposed at `GET /accounts/{account_id}/balance`; `GET /brokers`'
`has_balance_capability` says which registered brokers actually have it.
**Deliberately still not implemented for `CCXTBroker`** (spot crypto has
no single account-wide balance/buying-power figure the way an equity
account does — see that module's own docstring) — an account on ccxt
gets `null` for every field, never an invented number. **This is
read-only observability, not enforcement:** position sizing (`app/risk.py`)
still uses a flat `multiplier`/`fixed_quantity`/provider-override, never
a live balance/margin read, and an undersized/oversized order relative to
actual available capital still isn't caught before submission — closing
that gap would mean wiring `get_account_balance` into the admission path
itself, which is a separate, larger change not attempted here.

An account can still opt into narrower gates (`app/capital_allocator.py`):
`max_notional_exposure` rejects an entry that would push this account's
confirmed open notional (quantity × price, summed at cost across every
symbol it holds) over that number; `risk_percent_of_equity` rejects an
entry whose risk-to-stop (|entry price − stop_loss| × quantity) would
exceed that percentage of the account's real, freshly-fetched equity; and
a process-wide `MAX_OWNER_NOTIONAL_EXPOSURE` setting (`app/config.py`)
rejects an entry that would push the SUM of every configured account's
confirmed + pending notional over that number (this service is
single-tenant, so "every configured account" already is "owner-wide").
All three are atomic per-account (and, for the owner-wide one,
per-process) admission gates, so two signals arriving concurrently can't
both spend the same remaining capacity. Every one is opt-in (`None` by
default, meaning no gate); a signal with no resolvable price is REJECTED
(not silently skipped) whenever any gate is configured, and an account
whose open exposure this service's fill replay couldn't fully resolve
blocks new admissions for that account until it resolves, rather than
treating the unresolved part as zero. None of this does currency
conversion, per-analyst overlap accounting, cross-account netting, or
stress-loss modeling — see `app/capital_allocator.py`'s module docstring
for the exact, current scope.

**If I have two accounts of the same type (e.g. two options-capable
accounts), where does an incoming options trade get placed?** (ALLOC-01)
A routing rule's `destinations` are **alternatives for ONE intended
trade**, listed in priority order (`delivery_mode: single`, the default).
For each entry signal the engine records one durable allocation intent
(`allocation_intents`), then walks the approved pool in order and picks the
first account that passes every pre-submission gate (route qualification,
asset-class support, daily-loss/margin/equity breakers, sizing, capital
admission). Exactly one account receives the order; the others get none.
Once a submission has been attempted on the selected account — whatever
its outcome, including an unknown/lost response — the intent is committed
and is **never rerouted** to another account. A restart or duplicate
delivery resumes the same bound account.

If no approved account can take the trade, the signal is recorded as an
explained skip (`allocation_intents.state = 'skipped'`, with each
account's rejection reason) and nothing is submitted.

Deliberate copying to several accounts is still possible, but it must be
configured explicitly with `delivery_mode: replicate` on that rule — it is
never inferred from the number of accounts listed. See
`docs/design/PORTFOLIO_ALLOCATION.md`.

## Routing precedence

Routing rules are evaluated in order of specificity (WP-07):

1. Rules whose `symbol_filter` names the signal's symbol come first
   (in insertion order).
2. Rules with no symbol filter (catch-all) come second (in insertion order).

Within each group, the first rule whose conditions match is used. The `GET
/routing-rules/simulate` endpoint shows which rule would be selected for a
given symbol and account, including its precedence rank.

Example: if you have a catch-all rule that routes to Account A, and later add
a symbol-specific rule for AAPL that routes to Account B, then AAPL signals go
to B and everything else goes to A. The order matters — a catch-all listed
first would win for all symbols, shadowing later specific rules.

## Signal intent interpretation

Every signal carries an explicit **intent** describing what it means to do
(WP-08, WP-09, WP-13):

- **`entry_long`** — open a new long position or add to an existing one.
- **`entry_short`** — open a new short position (requires `allow_short=true`
  on the account).
- **`sell`** — ambiguous; resolved by the engine based on the account's book:
  - If the account holds a same-symbol long position → **EXIT** (reduce or
    flatten the long).
  - If the account is flat and `allow_short=false` → **REJECTED**.
  - If the account is flat and `allow_short=true` → **ENTRY_SHORT**.
  - Explicit ENTRY_SHORT on an account with `allow_short=false` → **REJECTED**.
- **`exit`** — close the position (reduce by quantity/fraction if given, else
  flatten).
- **`reduce`** — close a fraction of the position (e.g., "take profit on half").
  Must carry `reduce_fraction` (0 < x ≤ 1).
- **`stop_update`** — change the stop-loss price on an open managed lifecycle
  (rejected for plain accounts).
- **`target_update`** — add or amend take-profit targets on an open managed
  lifecycle.
- **`cancel`** — cancel a pending order (order id required).

**Derivation**: when not explicit, intent is derived from `side` in
`Signal.__post_init__`:
- BUY → ENTRY_LONG
- SELL → SELL (resolved by engine per account)
- CLOSE → EXIT

**Parser inputs** (from text signals):
- "BUY AAPL 100" → ENTRY_LONG
- "SHORT AAPL 100" or "SELL SHORT AAPL 100" → ENTRY_SHORT
- "SELL AAPL 100" → SELL (resolved at engine)
- "CLOSE AAPL" or "FLATTEN AAPL" → EXIT
- "TRIM AAPL" or "TAKE PROFIT ON AAPL" → REDUCE (with `reduce_fraction`
  parsed from "half", "all", or "N%")
- "UPDATE STOP 95" → STOP_UPDATE
- Numbers like "150C" with strike and expiry → OPTION asset class with full contract spec

Webhook endpoints accept an explicit `intent` field (JSON string) validated
against this enum.

## Sizing modes

Each destination account can size entries using one of three modes (WP-16):

### Fixed quantity
`sizing_mode: "fixed"` with `fixed_quantity: N`

The account ignores the signal's quantity and always trades `N` units.

### Multiplier (default)
`sizing_mode: "multiplier"`

The engine submits the signal's quantity directly, gated by
`max_notional_exposure` (if set). Notional = `|quantity| × |price|` ×
`contract_multiplier`, where multiplier is:
- Options: 100 (US standard)
- Futures: contract-specific (ES: 50, MES: 5, crude: 1000, etc.)
- Forex: lot size (standard 100k, mini 10k, micro 1k, units 1)
- Equities/crypto: 1.0

A signal without a contract spec for options/futures is **rejected**.

### Risk-fraction sizing
`sizing_mode: "risk_fraction"` with `risk_fraction: 0.01` (example: 1%)

The engine calculates: `qty = floor(equity × risk_fraction / (|price - stop| × multiplier))`

**All inputs required:**
- `broker.get_account_balance().equity` — if unavailable → REJECTED
- `signal.price` — if missing → REJECTED
- `signal.stop_loss` — if missing → REJECTED
- Result must be > 0, else REJECTED

Never guesses or defaults these values.

## Loss limits and risk controls

### Daily loss limit (WP-30)

Set `daily_loss_limit_percent` on an account (e.g., 5%) to halt new entries
when realized losses for the day exceed that percentage of starting equity.

Daily loss = `realized P&L today` + `mark-to-market on open positions` (when
broker balance is available).

When breached, an `entries:risk_halt` alert is raised and new entries are
**REJECTED** with reason "account has an active risk halt". Halts are
persisted in the `risk_halts` table and can be cleared by an operator via
`POST /risk-halts/{account_id}/clear` with an evidence message (required,
≥3 characters, logged for audit).

### Margin call detection (WP-31)

`app/margin_call_detector.py` reads from `broker.get_account_balance()` each
reconciliation pass and compares `maintenance_margin` against available
`excess`. When exceeded, a `margin_call_alerts` row is created and entries
are rejected while unresolved.

### Buying power and leverage (WP-32)

The capital allocator gates entries by:
- **Buying power** — if the broker reports it, entries requiring more than
  available are rejected with "insufficient buying power". If the broker
  cannot report it and no ceiling is configured → entry is rejected as a
  precaution ("buying power unknown and no ceiling configured").
- **Leverage cap** — `max_gross_leverage` (default 1.0, meaning no leverage)
  multiplied by `(equity - maintenance_margin)`. The paper broker enforces
  this on its own cash account.

Both fail closed: missing data → reject.

## Alerts and operations (WP-34, WP-42)

### Alerts

New alerts are raised for:
- **Loss halt triggered** — daily loss limit exceeded
- **Margin call** — maintenance margin exceeded
- **Unknown submission** — order status returned ERROR and was never confirmed
- **Protection deficit** — a managed position's stop-loss was lost (detected
  during reconciliation)
- **Stale allocation** — an allocation intent left in `claimed`/`selected`
  after a restart, automatically marked skipped

All alerts are stored in the `alerts` table with `kind`, `account_id`,
`message`, `payload`, and `created_at`. Unacknowledged alerts can be listed
via `GET /alerts?unacknowledged=1` and acknowledged via `POST /alerts/{id}/ack`.

Alerts can optionally be forwarded to an external webhook
(`config.ALERT_WEBHOOK_URL`, posted as JSON, failures logged never raised).

### Operations center (TR-20)

Single dashboard screen with four panels, each polling every 15 seconds:

1. **Alerts** — kind/account/message/time, acknowledge button
2. **Risk halts** — account/reason/triggered_at, clear button (requires
   operator evidence ≥3 chars)
3. **Unresolved commands** — submission ledger rows with ERROR status, mark
   as "not placed" button (requires evidence)
4. **Allocation intents and strategy budgets** — recent intents with state and
   selected account; budgets editable inline

Navigation badge shows count of unacknowledged alerts + open halts +
unresolved commands (cached 15 s).

## Readiness checklist (WP-45)

Before autonomous operation, `GET /readiness` per account checks:

- [ ] Sizing configured (mode + parameters)
- [ ] Loss limit configured
- [ ] Adapter can route entries (can_route_entries=true per adapter)
- [ ] Venue environment known (paper/live/sandbox)
- [ ] Route qualification approved for this (source, asset_class)
- [ ] Alerts path configured
- [ ] Writer lease active (single-writer fencing)

When any item is blocked, TR-16 shows a "Not ready" state with a "Fix" link
to the relevant screen (account editor for sizing/limits, routing for
qualification, operations center for leases).

## Adapter capabilities

Every broker adapter declares what it can actually do. These declarations are
checked at admission time (WP-36, WP-48, WP-49):

| Adapter | Entry types | Can cancel | Can update stop | Can readback position | Can readback order status | Bracket children | Notes |
|---|---|---|---|---|---|---|---|
| **Paper** | Market | ✓ | ✓ | ✓ | ✓ | ✓ | Simulated; all orders fill; children tracked |
| **Alpaca** | Market, Limit | ✓ | ✗ | ✓ | ✓ | ✓ | US equities; bracket legs returned as child_order_ids |
| **CCXT** | Market | ✓ (varies) | ✗ | ✓ (spot only) | ✓ | ✗ | Crypto; cancel support varies by exchange |
| **IBKR** | Market | ✓ (status lag) | ~ | ✗ (in-memory) | ~ | ~ | Status lookup in-memory only (C-14); real account code needed (C-18) |
| **Tastytrade** | Market | ✓ | ✗ | ✗ | ✗ | ✗ | US options/equities/futures; status via separate polling (unreliable) |
| **Tradovate** | Market | ✓ | ✗ | ✗ | ✗ | ✗ | Futures; status from polling only |
| **TradeStation** | Market | ✓ | ✗ | ✗ | ✗ | ✗ | Equities/options/futures; options unverified |
| **Schwab** | Market | ✓ (live-only) | ✗ | ✗ | ✗ | ✗ | Live-only; paper not supported |
| **Robinhood** | Market | ✓ | ✗ | ✗ | ✗ | ✗ | Unofficial API; live-only |
| **MT5 (local)** | Market | ✓ | ✓ | ✓ | ~ | ✗ | Same-host Windows MT5; position tracking via ticket persistence |
| **MetaApi (cloud)** | Market | ✓ | ~ | ✓ | ~ | ✗ | Cloud-hosted MT5/MT4; status from polling |
| **NinjaTrader** | Market | ✗ | ✗ | ✗ | ✗ | ✗ | Execution only (relay); no readback |
| **Rithmic** | Market | ✗ | ✗ | ✗ | ✗ | ✗ | Execution only (relay) |
| **SignalStack** | Market | ✗ | ✗ | ✗ | ✗ | ✗ | Execution only (relay) |
| **OANDA** | Market | ✓ | ✗ | ✓ | ✓ | ✗ | Forex; status via API |

**Legend:**
- **Entry types**: market (all adapters support); limit, stop, bracket (where declared)
- **Can cancel**: immediately + reliably (✓), with status lag (~), or not (✗)
- **Can update stop**: amend a resting stop price on an open order (~=partial support)
- **Can readback position**: query live position via API (✓), not available (✗), in-memory cache only (~)
- **Can readback order status**: query recent fills and rejections (~=polling only, unreliable)
- **Bracket children**: native bracket orders return child leg ids for tracking
- **Notes**: adapter-specific caveats and limitations

### Adapter qualification per asset class

Some adapters don't implement every asset class despite what the code might
suggest:

- **Tastytrade, TradeStation**: OPTION declared but legs not built; options
  routes rejected
- **NinjaTrader, Rithmic, SignalStack, MT5, MetaApi**: relay only, no entry
  routing capability; routes rejected

See individual adapter docstrings (e.g., `app/brokers/alpaca.py`) for details.

## Close signals

A `close` signal doesn't carry a size — closing means flattening whatever
is currently open, not scaling a new trade. `SignalCopierEngine` resolves
this per destination account before calling any broker:

1. Look up this service's own tracked net position for that account +
   mapped symbol (`app/db.py`'s `positions` table — its own record of what
   it has sent, not a live read of the broker's actual book).
2. Flat (zero)? Report `REJECTED` — "no open position to close" — without
   calling the broker.
3. **(P0-5) Reconcile before proceeding.** A plain account's own tracked
   position is this service's best record, not the broker's real book —
   see "Exclusive-writer qualification" below for exactly what's now
   required before a close is allowed to act on it.
4. Otherwise resolve to the opposing `buy`/`sell` at the full open
   quantity and call the broker with that. Brokers never see `Side.CLOSE`
   from the engine; each broker's own close handling (where present) is
   only a defensive fallback for direct/standalone use.

On a plain (non-`managed_lifecycle`) account, steps 1–4 are serialized per
`(account_id, symbol)` (`SignalCopierEngine._resolve_and_submit_plain_close`)
— without this, two close attempts landing close together (a duplicate
dashboard click, a retried HTTP request, a provider `EXIT` signal racing a
manual "Exit now") could both read the same tracked position and both
submit a full-quantity sell, overselling. `managed_lifecycle` accounts
already get the same guarantee from `CloseArbiter` (see "Managed
lifecycle" below); this closes the equivalent gap for plain accounts.

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
cached `Trade` object, which ib_async keeps live-updated via its own
event stream). SignalStack, NinjaTrader, and Rithmic have no confirmed
order-status-read API wired up yet, so their PENDING orders stay
optimistic until that's added. The reconciler's poll interval is
`RECONCILE_INTERVAL_SECONDS` (default 30s).

### Exclusive-writer qualification (P0-5)

A plain (non-`managed_lifecycle`) account's `CLOSE` used to act purely on
`SignalCopierEngine`'s own locally tracked position, with no check against
what the broker actually holds. That tracked value can go stale after a
manual intervention, an external fill placed directly at the broker, a
corporate action, or ordinary reconciliation lag (a `PENDING` order this
service hasn't yet confirmed a terminal status for) — closing against a
stale projection risks flattening the wrong size, or flattening when
there's nothing real left to flatten.

Before a plain close is allowed to proceed, `SignalCopierEngine` now
requires **one** of:

- **A fresh broker position readback.** If the account's broker adapter has
  a real (code-verified, not just declared) `get_broker_position`
  implementation — `BrokerAdapter.has_position_readback_capability` — the
  engine reads it and compares it to the locally tracked quantity (small
  absolute+relative tolerance for floating-point noise). A match lets the
  close proceed; a mismatch is `REJECTED` with a message naming both
  quantities and the account/symbol, and the close does **not** proceed.
  This is the default, preferred path and needs no configuration —
  **Alpaca**, **ccxt**, and **paper** already implement it.

- **`DestinationAccount.exclusive_writer_qualified = True`.** For a broker
  adapter with no verified position-readback capability at all
  (SignalStack, IBKR, NinjaTrader, Rithmic, Schwab, Robinhood, Tastytrade,
  TradeStation, Tradovate, OANDA, MT4/MT5 as of this writing), the engine
  can't reconcile automatically — so it refuses to close by default. This
  flag is the operator's own explicit, narrow, per-account assertion that
  **nothing else writes to this specific broker account's position outside
  Signal Copier** — no manual trade at the broker's own dashboard/API, no
  other automated system, no corporate action changing share count without
  an offsetting fill this service sees. It is **off by default** and must
  be set deliberately; it is not a config convenience to flip just to make
  a rejection go away. Setting it on an account that ISN'T actually
  exclusively written by this service will let a real, silent divergence
  between the tracked and actual position go uncaught by this close path.

Neither condition holding is a hard, fail-closed block — the close is
`REJECTED` with a clear, actionable reason, never allowed through on an
unproven local projection. See `SignalCopierEngine._reconcile_before_plain_close`
for the exact contract and `tests/test_p0_5_close_reconciliation.py` for
the covered cases (mismatch blocks, match allows, the qualified flag allows
without a broker read).

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
  (verified against ib_async's source).

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

### Declared management recipe (P0-5)

Managed and unmanaged (plain) accounts are fundamentally different safety
products — one gets MAE/MFE tracking, protection coverage, and
transfer-on-partial-fill logic from `PositionLifecycleManager` below; the
other gets none of that. Until now that distinction was purely structural
(inferred from `managed_lifecycle`'s boolean routing switch wherever a
screen or report needed it). Every `DestinationAccount` now also carries an
explicit, **persisted** `management_recipe` (`app/models.py`'s
`ManagementRecipe`: `full_managed_lifecycle` or `plain_unmanaged`) plus a
free-form `qualification_level` string, both round-tripped through
`config_accounts` the same as every other account field, and both visible
on TR-07's Accounts screen and `GET /accounts`.

`management_recipe` defaults from `managed_lifecycle` when not given
explicitly (so every existing account gets a real, non-null value with no
migration step needed) but can be set independently — an account whose
`management_recipe` disagrees with its `managed_lifecycle` boolean is a
real misconfiguration worth surfacing/auditing, not something this field
silently resolves.

`qualification_level` is deliberately a **simple, free-form label** for
now (e.g. `"qualified"`, `"unqualified"`, `"pending_review"`), not an enum
— a related, fuller broker-capability qualification taxonomy is being
built separately for broker adapters themselves; this field is the
**account's** own declared management contract and may end up referencing
that taxonomy later, but is independent of it today.

This does **not** change what TR-07 already renders for an unmanaged
account's MAE/MFE, protection coverage, or transfer-logic
`CapabilityState` badges — those were already honest `not_tracked` badges
for a plain account before this change, and stay that way; what's new is
that the underlying managed-vs-unmanaged declaration driving them is now a
real, explicit, auditable field rather than an implicit read of
`managed_lifecycle` alone.

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
  (TRK-27) `CloseArbiter`'s own `pending_exit` guard only protects
  against a *concurrent* duplicate exit; a genuinely duplicate EXIT for
  the same real-world event, delivered through a different
  `channel_id`/`message_id`, arriving AFTER the first exit has already
  resolved, is instead recognized by
  `PositionLifecycleManager.check_duplicate_exit` (logged, REJECTED, no
  new broker order — never a fabricated FILLED replay) — see
  `docs/adr/0010-managed-exit-duplicate-episode-guard.md` for the exact
  mechanism and its explicitly bounded scope.
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
  called by `app/reconciliation.py` polling `get_order_status()` — the stop
  is resized to `confirmed_owned_quantity - confirmed_filled_quantity`,
  using whatever actually filled. A 15-share target that only fills 8
  before its remainder is confirmed cancelled restores the stop to 54
  (62 − 8), not 47 (62 − 15); if 3 more fill while that cancellation was in
  flight, the restore target is 51, not 54. See
  `tests/test_protection_transfer.py`, which reproduces this exact
  sequence.

### Pending entries: protected as soon as a fill is confirmed, not just at the end

The same async-confirmation problem exists on the way in, not just the way
out: a managed entry can report `PENDING` too, and this went through two
rounds to get right.

**Round one** (this section's original version) handled a PENDING entry by
retaining a `PendingEntry` (requested quantity, broker order id) and doing
nothing else until a terminal broker status arrived. **A follow-up review
found that this itself introduced a new bug and left one case still
unprotected**, both confirmed by reproducing them and fixed:

- **Managed full-fills were double-applied.** `handle_signal`'s
  managed-account branch always writes an `orders` row for display
  (`GET /orders`), and that row's `status='pending'` + `broker_order_id`
  made it match `app/reconciliation.py`'s *generic* pending-order query too
  — so a confirmed fill got applied once by the generic loop's
  `_correct_position` (added to `SignalStore`'s tracked position) **and**
  a second time by `_reconcile_pending_entries` (via `record_fill`),
  leaving the position at double the true quantity while the lifecycle and
  stop correctly showed the real amount. Fixed: the generic loop now skips
  position-correction (but still updates the row's display status) for any
  `(account, symbol)` with an active tracked lifecycle — that position's
  truth belongs exclusively to the lifecycle-specific reconciliation paths.
- **A working partial fill got zero protection until the whole order was
  done.** Waiting for a terminal status before calling `on_entry_fill`
  meant a genuinely-confirmed partial fill (say 30 of 100 requested) sat
  completely unprotected for however long the remaining 70 stayed open —
  and `AlpacaBroker`/`IBKRBroker`'s `get_order_status` didn't even surface
  that progress, discarding it the same as "nothing new" until the order
  reached a terminal state. Fixed on both sides: the adapters now return a
  non-terminal `PENDING` result carrying the current cumulative fill
  quantity for a still-open, partially-filled order (instead of `None`),
  and `resolve_pending_entry` protects any *increase* in confirmed fill
  immediately — placing the stop for the first confirmed amount via the
  normal `on_entry_fill` path, or **resizing the existing stop** (cancel/
  replace, never a second stop placed alongside the first) for a later,
  larger confirmed amount. A repeated observation of the same quantity is a
  no-op; `SignalStore`'s tracked position gets only the new increment since
  the last observation, applied immediately next to the persisted
  protection-state change (shrinking, though not eliminating, the crash
  window between "protection is durably recorded" and "the tracked
  position reflects it" — see "Crash-resumable persistence" below).

`app/reconciliation.py` polls unresolved pending entries the same way it
polls pending exits. Once terminal: zero confirmed fill unregisters the
plan (nothing to protect, nothing protecting it); any positive confirmed
fill — even less than requested, a partial fill whose remainder was then
cancelled — is protected, never the originally requested amount. A timeout
or lost response is **not** treated as a rejection — only a
broker-confirmed status (a terminal one, or a still-open one carrying real
fill progress) may act; anything else keeps polling unchanged.

See `tests/test_edd2b70_review_regressions.py` (the double-count and
working-partial-fill fixes, including a same-quantity-repeated-observation
no-op check and a restart-recovery check) and
`tests/test_alpaca_broker.py`/`tests/test_ibkr_broker.py`'s
`get_order_status` cases (new, partially-filled, filled, and
canceled-with-a-partial-fill, through each adapter's own
HTTP-mock/fake-trade boundary) alongside
`tests/test_pending_fill_reconciliation_integration.py`'s original
full/partial-then-cancelled cases.

### The same two bugs, for plain (non-managed) accounts

Managed accounts weren't the only place the optimistic-fill baseline
mismatch happened, and cancellation-with-a-partial-fill had its own
pre-existing bug independent of either round above.

- **Baseline mismatch** (fixed first round): a plain account's `PENDING`
  order applies an optimistic quantity to `SignalStore`'s tracked position
  (there's no `CloseArbiter`/lifecycle to defer to — see "Close signals"
  above), but the `orders` row persisted the *broker's* raw
  `filled_quantity` (`None` for a genuine `PENDING` response) instead of
  the quantity actually applied — so a confirmed fill was added a second
  time on top of what was already applied. `SignalStore.save_order_result`
  now takes an explicit `applied_quantity`, always exactly what
  `record_fill` was called with.
- **Cancellation wiped a confirmed partial fill to zero** (found in the
  follow-up review, pre-existing, independent of the above):
  `_correct_position`'s `REJECTED` branch — which also covers
  "canceled"/"expired" on adapters like Alpaca — reversed the *entire*
  optimistically-applied quantity unconditionally, ignoring
  `confirmed_quantity` even when the adapter reported a real partial fill
  before the cancellation. A 100-share buy with 30 actually filled before
  the remaining 70 was canceled left the tracked position at 0, not the
  correct 30; a 100-share close with 40 sold before the remaining 60 was
  canceled left it at 100 (nothing closed), not the correct 60. Fixed:
  `REJECTED` now reverses only the *unfilled remainder* of what was
  optimistically applied (using `confirmed_quantity` when the adapter
  reports one, same as `FILLED` already did), defaulting to "assume
  nothing filled" only when the adapter doesn't report a fill on
  rejection — preserving the original behavior for those adapters.

See `tests/test_edd2b70_review_regressions.py`'s plain-account cases,
which reproduce both worked examples above directly through
`SignalCopierEngine.handle_signal` → `OrderReconciler.reconcile_once()`.

### Round three: entry-fill arithmetic, protection-failure honesty, and one exit-execution owner

A third review (against `5e91e783f6a7fc2326d28cc7c4beab88c2c5c664`) found the
round-two fixes above were real but incomplete: they held only in isolation,
not once an entry's later fills interacted with an intervening exit, a
failed stop operation, or a crash. All ten reproduced cases are fixed; see
`tests/test_5e91e78_lifecycle_composition.py` (four preservation controls +
ten follow-on cases, all passing).

- **Cumulative entry fills are not remaining ownership.** `resolve_pending_entry`
  used to overwrite `confirmed_owned_quantity` with the entry's raw
  cumulative fill total — correct only if nothing had exited yet. 30 bought,
  10 sold, then entry cumulative reaching 60 was being sized as *60* owned
  (and a stop resized to match), when the true remaining position is 50.
  Fixed: every new confirmed increment (`newly_applied = confirmed -
  previous_confirmed`) is now added to whatever's *currently* owned
  (`tx.owned`), never substituted for it.
- **A late entry fill must not arm a fresh stop over an unresolved exit.**
  A missing `stop.broker_order_id` used to be read as "this is the first
  ever fill, place an initial stop" — but `request_exit` also clears it
  when cancelling the old stop before a target/manual close, and that
  exit's own commitment isn't done yet. Fixed: `resolve_pending_entry` skips
  stop placement/resizing entirely while `lifecycle.pending_exit` is
  unresolved, leaving that exit's own resolution (`resolve_pending_exit`) to
  protect the true remainder once it settles, rather than placing an
  independent stop that would double-cover shares the pending exit already
  claims.
- **A rejected/failed protection response is not working coverage.**
  `_place_stop_locked` treated a `REJECTED` result the same as success
  (only `ERROR` and `None` were failures); `_replace_stop_price` treated
  *any* non-`None` replacement result as a successful resize, including an
  explicit `ERROR`/`REJECTED` outcome. Either bug let a failed protection
  attempt get reported as `STOP_CONFIRMED` at the new (larger) quantity,
  even though nothing changed at the broker — see also Alpaca's own
  replace-order docs, which don't guarantee a successful-looking replace
  response means the old order is actually gone. Fixed: both now treat
  `ERROR`/`REJECTED` as failure, preserving the previously-confirmed
  coverage rather than reporting the requested quantity as newly protected.
- **One execution-application owner per order, not per symbol.**
  `OrderReconciler`'s generic loop used to skip position-correction for
  *any* order sharing an (account, symbol) with an active lifecycle — so an
  older plain order still pending when an account later became
  `managed_lifecycle` (or a second, unrelated lifecycle order) could get
  silently swallowed, never corrected by anyone. Fixed: the exclusion is
  now scoped to the exact order id the lifecycle is currently waiting on
  (its `pending_entry`/`pending_exit`'s own `broker_order_id`), not every
  order for that symbol.
- **A managed close is a commitment, not a completed sale until confirmed.**
  `_handle_managed_close` used to apply the full requested (or reported)
  quantity to `SignalStore.positions` optimistically on a `PENDING` close
  result — but nothing ever corrected that guess once the real outcome
  (partially filled, remainder canceled) came back, since
  `resolve_pending_exit` never touched the store at all. A 100-share close
  with 40 actually sold and 60 canceled left the store showing 0 (fully
  closed) forever. Fixed: `PositionLifecycleManager` is now the single
  execution-application owner for exits too (`request_exit`'s synchronous
  branch and `resolve_pending_exit`'s terminal branch both call the same
  internal `_apply_exit_fill`, applying the store delta exactly once, only
  once the real outcome is known) — the engine no longer touches the store
  for a managed close at all.
- **A missing terminal quantity is not a reversal to zero.** A terminal
  response that omits its cumulative fill (used to be treated as `0.0`)
  could unregister a lifecycle that had a real, already-confirmed fill, or
  erase an exit's known progress. Fixed: both `_reconcile_pending_entries`
  and `_reconcile_pending_exits` fall back to the last confirmed progress,
  not zero, when a terminal response's quantity is missing.
- **The fill-application decision itself needed serializing, not just the
  stop call.** `resolve_pending_entry` used to read/compare the last-applied
  checkpoint *before* acquiring any lock, so two concurrent observations of
  the same broker order (a duplicate poll, an overlapping reconciliation
  pass) could both compute the same "newly applied" delta before either
  advanced the checkpoint. Fixed: the whole read-decide-apply sequence is
  now serialized per (account, symbol) via a dedicated lock (separate from
  `CloseArbiter`'s, which is acquired/released multiple times within one
  call) — a second, identical observation arriving mid-resolution now waits
  and then correctly no-ops.
- **Crash-consistent local commits.** The previous round moved
  `SignalStore.record_fill` next to the lifecycle checkpoint update but
  still wrote them as two separate commits — an interruption between them
  left the checkpoint advanced with the position un-updated, or vice versa.
  Fixed: `record_fill` now takes an optional `lifecycle_state` and writes
  the position delta and the lifecycle checkpoint in the same local SQLite
  transaction (one commit), used for every entry/exit fill application. The
  checkpoint field itself is only advanced in memory immediately before
  that call, so anything persisted *before* it (e.g. inside a stop
  placement/resize that ran first) still reflects the OLD checkpoint —
  an interruption before the atomic commit replays the same delta on
  restart; an interruption at or after it lands with both already applied
  together, so restart sees a stale (already-seen) observation and no-ops.
  This is a short local transaction only — broker I/O always happens
  before it, never inside it, so nothing is claimed atomic with the venue.

### A known remaining gap: an ambiguous submission outcome still unregisters the plan

Not yet fixed, flagged honestly rather than silently left implicit: if
`broker.place_order()` itself raises (a network error, a timeout) during
a managed entry's *submission* — before any `broker_order_id` is even
known — `_handle_managed_entry` unregisters the plan and reports `ERROR`.
That's correct when the order genuinely never reached the broker, but
indistinguishable, with what's implemented today, from the broker having
actually accepted the order while its response was merely lost — which
would leave a real, unprotected position with no record of it at all.
Closing this needs a durable pre-submission intent (so a lost response can
be reconciled against the broker's own order history rather than assumed
away) and a policy against blind resubmission — a larger piece of work,
deliberately out of scope for this round alongside the durable
idempotency-key-to-account/action binding, `asyncio.gather`-wide task
supervision, and login-throttling work already noted as open elsewhere in
this README.

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

- **A lower-latency, per-broker live price feed.** `app/pricing.py`'s
  `PriceMonitor` does drive `on_price_update()` in production now (see
  "Continuous monitoring" below), but only via REST/RPC polling on a
  fixed interval for the brokers that have a `get_last_price`
  implementation (ccxt, Alpaca, IBKR) — a genuine websocket/tick stream
  (ccxt's "pro" support, Alpaca's market-data websocket, an MT5
  terminal's tick feed, IBKR's persistent `reqMktData` subscription) is
  future work, and profitability claims for a trailing/target strategy
  shouldn't be trusted until they account for real feed latency and the
  cancel/replace delays above, not an idealized instant-fill assumption.
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

### Continuous monitoring and trailing (`app/pricing.py`)

The lifecycle manager's target/trailing logic
(`PositionLifecycleManager.on_price_update` — tighten a stop, activate a
trail, cancel/replace when a broker can't amend one in place) was fully
built and tested, but for a long time nothing actually called it outside
tests: there was no live price feed. `app/pricing.py`'s `PriceMonitor` is
a background loop (started in `app/main.py`'s `lifespan`, same pattern as
`OrderReconciler`) that polls every open managed-lifecycle position on an
interval and feeds whatever price it gets into `on_price_update()` — this
is what makes "the system continuously monitors an open position and
moves/replaces its protective stop, including cancel-and-resubmit when
an in-place amend isn't possible" actually true in production, not just
a tested capability nothing exercises live.

Be precise about what's real here: each broker's optional
`get_last_price()` (`app/brokers/base.py`) is the only source `PriceMonitor`
uses — no separate custom price-feed infrastructure. **Three brokers have
a real implementation**: `CCXTBroker` (ccxt's own unified `fetch_ticker`
REST call), `AlpacaBroker` (`GET /v2/stocks/{symbol}/trades/latest` on
Alpaca's market-data host), and `IBKRBroker` (`reqTickersAsync`, ib_async's
one-shot market-data snapshot — falls back to the last close when this
account has no live/delayed trade-tick entitlement). Every one of these is
an existing, broadly-verified library capability, not something built
from scratch, per this project's leverage-existing-solutions principle.
All three are REST/RPC polling on a fixed interval
(`PRICE_MONITOR_INTERVAL_SECONDS`, default 15s) — **not a websocket/tick
stream.** ccxt's own websocket ("pro") support, Alpaca's market-data
websocket, an MT5 terminal's tick feed, and IBKR's persistent
`reqMktData` subscription would all be real, lower-latency options for
the brokers that have them, and remain a documented next step, not
implemented yet. Each pass runs its lookups with bounded concurrency (at
most 10 in flight at once, not fully sequential and not unbounded) so the
pass doesn't take longer, position by position, as the number of tracked
positions grows.
`GET /brokers`' `has_last_price_capability` field shows exactly which
brokers are actually being polled today — a managed-lifecycle position on
any other broker (SignalStack, MT5, MetaApi, NinjaTrader, Rithmic) is
correctly protected on entry and on every exit/target event, but its
trailing stop won't move between those events until that broker also gets
a `get_last_price` implementation.

`tests/test_price_monitor.py` drives the exact scenario end-to-end
through `PriceMonitor.poll_once()` (not by calling the lifecycle manager
directly): a position enters, price rallies, and the stop is
cancelled/resubmitted to the new trailing floor — plus the "feed
temporarily returns nothing" and "broker has no feed at all" cases,
neither of which drops or unprotects the position.

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
- **No slippage or fee modeling in the raw replay** — a resolved trade
  fills at the exact stop/target price. **E07 (bounded):** pass
  `slippage_bps`/`fee_per_trade` in the `POST /backtest` request to get
  a `stressed_summary` alongside the raw one (`app/backtest/
  cost_stress.py`) — a flat, linear stress test ("does the apparent edge
  survive if every resolved trade's fill is worse by this much"), not a
  real broker fee schedule or a liquidity/market-impact model. Omit both
  (or leave them at 0) to skip it entirely.
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

### Provider fit simulator ("what would copying this source have done to MY account")

```
POST /providers/{source}/fit-simulation
```

The personalized-to-the-viewer number copy-trading marketing funnels
(the Alertsify pattern: "type your account size, see what every trader
would have paid you") use to answer that question BEFORE anyone
subscribes to anything — distinct from `POST /backtest` above, which
never rescales a signal past its own recorded `quantity`, and distinct
from `app/provider_value.py`, which only scores an account's own real,
already-subscribed fill history. Built entirely on top of
`BacktestEngine`; see `app/backtest/fit_simulator.py`'s own module
docstring for the exact, disclosed sizing methodology before trusting
its output, but the short version:

- Every one of `/backtest`'s own limitations still applies (no
  market-data vendor connected, `AMBIGUOUS`-bar ambiguity preserved not
  guessed, no shared-capital portfolio simulation, no slippage/fees
  unless applied separately).
- A trade **"fits"** your stated `max_per_trade` only if its own real
  entry price is at or under that ceiling (you could take at least 1
  unit) — otherwise it's excluded from the personalized numbers
  entirely, not silently zeroed.
- A fitting trade is rescaled to
  `min(the_source's_own_recorded_quantity, max_per_trade / entry_price)`
  — replicate the source's own size when it's small enough, cap it at
  your own ceiling otherwise — and its P&L is scaled by the exact same
  ratio (correct because P&L is linear in quantity for every asset class
  this engine scores; see `BacktestEngine`'s own `EXIT_UNSCORABLE`
  handling for the one exception, options, which this excludes the same
  way `/backtest` already does).
- The response's own `equity_curve` and `worst_drawdown_at_your_size`
  (a real peak-to-trough dollar drawdown on the rescaled curve, not a
  losing-streak count) are computed only from resolved, fitting trades.
  `full_size_summary` in the response is the source's own real,
  unscaled record — "their record at full size" — for a caller that
  wants to show both numbers side by side.

```bash
curl -X POST http://localhost:8000/providers/alerts_guy/fit-simulation \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <owner-session-token>' \
  -d '{
        "source": "alerts_guy",
        "account_size": 25000,
        "max_per_trade": 2500,
        "lookback_days": 90,
        "csv_paths": {"AAPL": "/path/to/AAPL_daily_bars.csv"}
      }'
```

This route is owner-gated (cookie/CSRF session), same as `/backtest`
above — for the owner's own ad hoc use.

```
POST /catalog/providers/{source}/fit-simulation
```

The real prospect-facing path: the same computation, the same request/
response shape, but authenticated by a signed, audience-bound, expiring
service token (`X-Catalog-Fit-Sim-Signature`, verified against
`CATALOG_FIT_SIM_SIGNING_SECRET` — see
`app/services/catalog_fit_sim_auth.py`'s own module docstring for the
exact scheme) instead of an owner session, and rate-limited separately
(`CATALOG_FIT_SIM_RATE_LIMIT`, `app/rate_limit.py`) since it's reachable
by signal-portfolio-commercial's own backend on behalf of an anonymous
public-catalog visitor. It is a genuinely separate route/dependency, not
`/providers/{source}/fit-simulation` with `require_owner` relaxed — a
valid catalog-fit-sim signature authenticates to this ONE route and
nothing else in this service; it can never satisfy `require_owner`. See
`app/main.py`'s own docstring on `run_catalog_fit_simulation` for why,
and `tests/test_catalog_fit_sim_auth.py` / `tests/test_catalog_fit_sim_endpoint.py`
for the auth and boundary coverage. signal-portfolio-commercial's own
PU-03 "Portfolio detail" page is the real, built caller — see that
repo's `app/services/fit_simulation_client.py`. As of this writing, no
real historical price CSVs are configured anywhere in that repo for any
published product's underlying instrument, so that page's own fit
simulator honestly renders as unavailable pending real historical market
data, rather than fabricating a result — the wiring above is real and
tested end to end (with the cross-service HTTP call mocked at the
boundary, the same way the relay ingest is tested), but no real number
has ever actually been shown to a real visitor from it yet.

See `tests/test_fit_simulator.py` and `tests/test_backtest_api.py`'s
`test_fit_simulation_endpoint_*` tests for the rescaling-correctness and
end-to-end proofs of the underlying computation (shared by both routes).

## Dashboard

```
GET /   # the web dashboard
```

One static HTML page (`app/static/dashboard.html`, served directly — no
build step, no frontend framework, no new dependency) with vanilla JS.
Three kinds of panels:

- **Read-only, polled every 10s**: broker capabilities, open positions,
  managed-lifecycle coverage/deficit detail, recent signals, recent
  orders.
- **Live-managed, via forms**: Accounts, Routing rules, Providers &
  analysts — add/edit/delete, taking effect on the very next signal (see
  "Managing config through the GUI/API" above for exactly what this
  does and doesn't cover).
- **Manual exit controls**: an "Exit now" button on every row of the
  Positions and Managed-lifecycle tables submits an immediate market
  close for that one position (`POST /positions/{account_id}/{symbol}/close`);
  a "Flatten `<account>` (exit all)" button per account with open
  positions exits every symbol on that account, one at a time
  (`POST /accounts/{account_id}/flatten`). Both go through
  `SignalCopierEngine.close_position`, the exact same resolution a real
  provider CLOSE signal uses — on a `managed_lifecycle` account that's
  `PositionLifecycleManager.request_exit` (respecting `CloseArbiter` and
  the pending-exit protection-transfer rules, same as an automatic
  target/stop exit); on a plain account it's the tracked position
  reversed at market, serialized per `(account_id, symbol)` so a second
  close attempt can't race the first (see "Close signals" above). Both
  require an explicit confirm dialog before submitting.

The dashboard itself sits behind a sign-in gate (see "Owner
authentication" above) — it's the same session/CSRF-token flow every
other protected endpoint uses, just wired into the page's own JS rather
than a separate client.

This is the one deliberate exception to "the dashboard can't submit an
order": manual exit only ever *reduces* risk (it can't open a new
position or resize an existing one upward), so it doesn't cross the same
trust boundary a new entry would. Everything that adds risk — an entry,
a target/trailing exit firing automatically — still only comes from a
source's own push (a webhook, SMS) or a pull-based source's background
task, unchanged.

## Monitoring

```
GET /health                  # public, no auth: process liveness + worker progress
GET /positions               # every non-flat tracked position, across all accounts
GET /signals?limit=50        # most recently received signals, newest first
GET /orders?limit=50&account_id=...   # most recent order results, optionally filtered to one account
GET /brokers                 # every registered broker's actual, code-verified capability matrix
GET /signals/{id}/decision   # (WC-21) full admission decision trace for one signal
GET /operations/reservation-health   # (WC-21) hierarchical resource reservation state
GET /operations/intent-health        # (WC-21) order intent queue and delivery status
GET /risk-halts              # (WC-32) all active and recently cleared halts
POST /risk-halts/{account_id}/clear  # (WC-32) clear an account halt with operator evidence
GET /providers               # configured provider/analyst overrides and their effective settings per account
POST /positions/{account_id}/{symbol}/close   # immediately exit one open position at market
POST /accounts/{account_id}/flatten           # exit every open position on that account, one at a time
```

Every route above except `/health` requires an owner session (see "Owner
authentication" above). `/health` is deliberately public and minimal —
`{"status": "ok", "database_ok": ..., "price_monitor_ok": ..., "reconciler_ok": ...}`
— no account IDs or balances, so it's safe to point an uptime checker at
directly. `price_monitor_ok`/`reconciler_ok` reflect whether that
background loop (`app/pricing.py`'s `PriceMonitor`, `app/reconciliation.py`'s
`OrderReconciler`) has completed a pass recently, not just whether the
process is running — a task that's alive but stuck (every broker call
failing, or the loop itself dead) reports `false` here, where a bare
"is the process up" check would say everything's fine.

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

## Market/economic context (read-only, outside the trading path)

```
GET /context/filings/{ticker}          # recent SEC filing history
GET /context/filings/{ticker}/facts    # structured XBRL company facts
GET /context/fred/{series_id}          # FRED (or ALFRED) macro series observations
GET /context/fx/{base}/{quote}         # daily ECB reference FX rate (Frankfurter)
```

`app/context/` wraps three free, publicly documented APIs so an operator
can look something up alongside the live signal/position data already in
the dashboard — a company's last filing, where a rate series stands, a
reference FX rate. **Nothing in this package is imported by `app/engine.py`,
`app/lifecycle/*`, `app/reconciliation.py`, or any `app/brokers/*.py`
adapter** — it can't place, cancel, or modify an order or a protective
stop, and nothing here drives a trading decision automatically.

- **SEC EDGAR** (`app/context/sec_edgar.py`) — keyless; SEC's fair-access
  policy requires only an identifying `SEC_EDGAR_USER_AGENT` env var (e.g.
  `"YourCompany admin@example.com"`) on every request. 501s if unset,
  rather than sending an unidentified request.
- **FRED** (`app/context/fred.py`) — needs a free key
  (`FRED_API_KEY`, register at
  https://fredaccount.stlouisfed.org/apikeys). 501s if unset. Supports
  ALFRED-style vintages via `realtime_start`/`realtime_end` query params —
  use them for a historical decision that needs the value *as it was known
  at that time*, not today's since-revised figure.
- **Frankfurter** (`app/context/fx.py`) — fully keyless daily ECB
  reference FX. Reference only, not an executable bid/ask and not the
  price basis any actual FX broker adapter uses for a real order.

**C07 (bounded):** each of these three modules self-imposes an outbound
call-rate ceiling (`aiolimiter`) — 5/sec for SEC EDGAR (under their
documented 10/sec fair-access policy), 60/min for FRED (under their
documented 120/min), 10/min for Frankfurter (which documents no limit at
all, but still gets a courtesy ceiling). This throttles calls from THIS
process only, protecting against a bug or a misconfigured polling loop
getting this service's IP rate-limited or blocked by the upstream
provider — it isn't the provider's own quota enforcement.

These three were chosen out of a much larger reviewed candidate list
(Alpaca/Tradier/IBKR market data, Alpha Vantage, Polygon, Twelve Data,
FMP, Tiingo, Finnhub, EODHD, Marketstack, FINRA, Nasdaq Trader halts,
OpenFIGI, GLEIF, OCC, Cboe VIX, BLS, BEA, Census, Treasury Fiscal Data,
NY Fed, OFR, FDIC, EIA, CFTC, USDA, NOAA, World Bank, ECB, Eurostat, BIS,
IMF, OECD, Bank of Canada, DBnomics, a dozen public crypto-exchange feeds,
CoinGecko/CoinPaprika/DefiLlama/DEX Screener/Coin Metrics/Etherscan,
GDELT/ClinicalTrials.gov/openFDA, and ~25 MCP server options) specifically
because they're free with no ambiguous "developer/non-production-only"
restriction (unlike, e.g., NewsAPI's or GNews' free tiers), keyless or
simple free-registration, and don't meaningfully overlap each other or
this project's existing broker/CCXT integrations. Everything else in that
list is a candidate for later, one at a time, only once it's clearly
missing a field the existing stack can't provide — not integrated here to
avoid the overlap and maintenance burden of running many similar vendors
at once.

**No MCP server processes were stood up.** Running an actual MCP server
is a host-level configuration decision (e.g. a Claude Desktop/Code MCP
config, or a separately hosted gateway) with its own isolation
requirements — it can't be provisioned by a commit to this application
repo, and doing so without deliberately restricting it from live trading
credentials and the execution database would be irresponsible. The
`/context/*` endpoints above give the same read-only capability directly,
already isolated from the trading path by construction.

**Not independently verified against the live services**: this sandbox's
own network egress policy blocks outbound connections to `data.sec.gov`
and `api.frankfurter.dev` (confirmed directly — both time out with a
policy-denied CONNECT), so these integrations are unit-tested against
mocked HTTP responses (request shape, headers, param construction, and
response parsing are all checked), not against the real APIs. Verify
connectivity and the exact response shape against the live services in
an environment with normal internet access before relying on this.

## What's real vs. stubbed

| Component | Status |
|---|---|
| Core engine, routing, risk sizing, SQLite log | ✅ Working, tested |
| Generic JSON / TradingView webhook source | ✅ Working, tested |
| Paper (mock) broker | ✅ Working, tested |
| ccxt broker (Binance/Bybit/etc crypto exchanges) | ✅ Working, tested (needs `pip install ccxt` + API keys). Native stop-loss/take-profit via unified `stopLossPrice`/`takeProfitPrice` params. |
| SignalStack broker (relays to IBKR, Schwab, Alpaca, Tradier, TradeStation, Bybit, Coinbase Pro, Oanda, etc. via signalstack.com) | ✅ Working, tested (needs a SignalStack account + a webhook URL per connected broker) |
| Alpaca broker (plain REST, no SDK) | ✅ Working, tested (needs API key/secret; defaults to the paper-trading endpoint). Native stop-loss/take-profit via bracket/OTO orders. |
| ccxt broker, multiple simultaneous exchanges | ✅ Working, tested. Set `CCXT_EXCHANGES` (comma-separated) to run several exchanges at once (`ccxt_binance`, `ccxt_kraken`, ...) instead of the single `CCXT_EXCHANGE_ID`-based `ccxt` broker — ccxt itself bundles 100+ real exchange integrations. |
| Tradovate broker (US futures, plain REST, no official SDK) | ✅ Working, tested (needs a Tradovate demo or live account; `TRADOVATE_<id>_*` env vars). Always sends `isAutomated: true` per Tradovate's own real exchange-policy requirement for algorithmic orders. Order-status field mapping disclosed as not independently re-verified against a live account — see the broker's own docstring. |
| OANDA broker (forex, official v20 REST API, no official SDK) | ✅ Working, tested (needs an OANDA practice or live account; `OANDA_<id>_*` env vars). A market order fills or is rejected synchronously in the same response, unlike Alpaca's own always-PENDING market orders. |
| TradeStation broker (equities/options/futures, official REST API v3, no official SDK) | ✅ Working, tested (needs a TradeStation account + a one-time interactive OAuth2 consent to obtain a refresh token; `TRADESTATION_<id>_*` env vars; defaults to the sim/simulated environment). |
| Tastytrade broker (equities/options, real REST API) | ✅ Working, tested (needs a Tastytrade account + a one-time interactive OAuth2 consent; `TASTYTRADE_<id>_*` env vars; defaults to the cert/sandbox environment). **Disclosed, unverified**: always tags an order "to Open" — has not been independently confirmed against a real sandbox account for closing an existing position; see the broker's own docstring. |
| Schwab broker (equities, no official API, **no sandbox at all**) | ⚠️ Working, tested against mocked HTTP, but **every order — including your first test — is real money**: Schwab has no paper/practice environment whatsoever. Deliberately gated behind a second, explicit `SCHWAB_ACKNOWLEDGE_NO_SANDBOX=true` flag on top of `SCHWAB_<id>_*` credentials — `app/main.py`'s broker registry won't register it at all without that. |
| Robinhood broker (equities, **no official API, no sandbox, outside Robinhood's own ToS**) | ⚠️ Working, tested against mocked HTTP, but real money only and automating it this way is outside Robinhood's own Terms of Service. Gated behind a second, explicit `ROBINHOOD_ACKNOWLEDGE_TOS_RISK=true` flag on top of `ROBINHOOD_<id>_*` credentials. A real buy is submitted as a limit order pegged 5% above the current ask (Robinhood's own real order model has no plain market buy) — see the broker's own docstring before using it. |
| Telegram, Discord, Slack sources | ✅ Working (needs `pip install python-telegram-bot` / `discord.py` / `slack-bolt` + a bot token; only starts if its env vars are set) |
| SMS source (Twilio) | ✅ Working (needs a public URL + `TWILIO_AUTH_TOKEN`/`TWILIO_WEBHOOK_URL` for signature validation; route is always mounted at `/sms/twilio`) |
| WhatsApp source, via Meta's official WhatsApp Business Cloud API | ✅ Working, tested (needs a public URL + `WHATSAPP_APP_SECRET`/`WHATSAPP_VERIFY_TOKEN`/`WHATSAPP_ALLOWED_FROM_NUMBERS`; route always mounted at `/whatsapp/webhook`; needs no extra pip package, a plain HMAC-SHA256 check, same as Twilio's). Deliberately not an unofficial WhatsApp Web automation library (whatsapp-web.js/Baileys/OpenWA) — those violate WhatsApp's ToS and risk the number being banned with no appeal. |
| Twitter/X source | ✅ Working, but needs X API v2 filtered-stream access (a paid tier as of X's current pricing — verify current terms) and is the least reliable parser of the bunch since tweets are free text |
| IBKR broker | ✅ Working, tested (needs `pip install ib_async` + a running IB Gateway/TWS with the API enabled; reports PENDING, not a confirmed fill, since IBKR confirms asynchronously — but see "Monitoring" for how PENDING gets reconciled). Native stop-loss/take-profit via bracket orders. |
| MT5 broker (same-host only) | ✅ Working (needs `pip install MetaTrader5`, Windows, and the service running on the same host as a logged-in MT5 terminal — one terminal process per account). Native stop-loss/take-profit via `sl`/`tp` request fields. |
| MT4/MT5 source & broker, via [MetaApi](https://github.com/metaapi/metaapi-python-sdk) | ✅ Working (needs `pip install metaapi-cloud-sdk` + a MetaApi account — free tier covers 1 MT4/MT5 account; no local terminal needed at all). Preferred over the same-host MT5 broker above unless you specifically want to avoid the cloud dependency. The source polls deal history on an interval rather than a real-time push callback — see its docstring for why. Native stop-loss/take-profit via `stop_loss`/`take_profit` params. |
| Rithmic source & broker, via [async_rithmic](https://github.com/rundef/async_rithmic) | ✅ Working (needs `pip install async_rithmic` + licensed Rithmic credentials from your broker — there's no self-serve signup, this is a paid/licensed service regardless of which library talks to it) |
| NinjaTrader broker, via [TradeRouter](https://github.com/roydufek/traderouter)'s `WebhookOrderStrategy.cs` | ✅ Working (needs TradeRouter's NinjaScript strategy file installed and compiled inside NinjaTrader itself — this service just POSTs to its local HTTP listener; see the broker's docstring) |
| NinjaTrader signal *source* | ✅ Working, tested on the Python side (needs `NINJATRADER_WEBHOOK_SECRET` + a public URL; route mounted at `/ninjatrader/webhook`) via `ninjascript/SignalCopierAutoJournal.cs`, a NinjaScript Indicator modeled on Apex-Logics/TradVue's/Shadowscr-7/tradingadmin's verified `Account.ExecutionUpdate` -> `HttpClient.PostAsync` pattern (fetched and read directly to confirm it's real, not guessed — every prior bridge found, TradeRouter/ninja-webhook/tv-ninjatrader-bridge, really is one-way and can't do this). **The C# side is disclosed, unverified**: it hasn't been compiled or run against a real/Sim101 NinjaTrader 8 install (no NinjaTrader/Windows in this environment) — treat it as reviewed, not tested, until run there. |
| Asset-class routing gate (a signal can't reach a broker that can't trade its asset class) | ✅ Working, tested — declared for Alpaca/IBKR (equity-only) and ccxt (crypto-only); undeclared (unrestricted) elsewhere pending verification. See "Multi-asset routing" above. |
| Balance/margin tracking per broker | ✅ Working, tested for **Alpaca** (`GET /v2/account` — cash/equity/buying_power/maintenance_margin) via `get_account_balance`/`GET /accounts/{account_id}/balance`. Deliberately not implemented for **ccxt** (spot crypto has no single account-wide balance figure — see `CCXTBroker`'s docstring). Read-only observability only — sizing (`app/risk.py`) still doesn't read live buying power before submitting an order. |
| Signal-provider value analysis & subscription cost tracking | ✅ Working, tested (`app/provider_value.py`/`app/provider_scout.py`, `GET /providers/value`/`/providers/subscriptions`/`/providers/candidates`, dashboard's "Signal provider value & subscriptions" panel) — FIFO-lot P&L attribution per provider/analyst/asset class, a cost-vs-value verdict, and a scheduled scan recommending free providers to promote. **Does NOT see a managed-lifecycle stop/target/trailing exit at all** (same gap as account economics below) — see that module's docstring before trusting a "cancel this provider" verdict. |
| Live price feed driving trailing/target monitoring (`PriceMonitor`) | ✅ Working, tested for **ccxt, Alpaca, and IBKR** (REST/RPC polling, not a websocket/persistent subscription). Other brokers' (SignalStack/MT5/MetaApi/NinjaTrader/Rithmic) managed-lifecycle positions stay protected but their trailing stop doesn't move between fill/exit events yet — see "Continuous monitoring" above. |

The Telegram/Discord/Slack/SMS/Twitter parsers all share one generic
free-text parser (`app/sources/text_parser.py`) that handles the common
`BUY BTCUSDT @ 65000 SL 63000 TP 70000` family of formats. If a specific
channel's format doesn't fit, override that source's `parse()`.

**E02 (bounded):** every message this parser looks at gets one of five
dispositions -- `parsed`, `ignored` (negated/conditional/past-tense
commentary), `ambiguous` (more than one instruction or take-profit
level), `missing_data` (a structurally invalid value like a negative
quantity), or `no_match` -- not just a binary pass/fail
(`app/sources/text_parser.py`'s `classify_text_signal`/`classify_batch`).
`POST /sources/{source_name}/classify-messages` runs this read-only
against a batch of texts (e.g. a channel's message history, or wording
you're testing before it's live) — it never creates a Signal or touches
routing/positions. This is the classification step a fuller source-
onboarding workflow (the adoption plan's E02: importing and reviewing a
channel's ENTIRE authorized history in one pass) would call per message
— that import/review workflow itself isn't built, a disclosed gap.

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

uvicorn app.main:app --reload
```

Open `http://localhost:8000/` and add an account and a routing rule
through the dashboard's forms (Accounts / Routing rules panels) — no
YAML editing needed. To start from the example config instead (useful if
you're migrating an existing hand-edited setup, or just prefer to see a
realistic starting point), copy the `.example.yaml` files into place
*before* the first startup — it's imported into the database once and
then live-managed from there:

```bash
cp config/routing.example.yaml config/routing.yaml
cp config/accounts.example.yaml config/accounts.yaml
```

Send a test signal. The webhook route is **disabled (HTTP 503) until
`WEBHOOK_SHARED_SECRET` is set** in `.env`, and every request must carry that
value in the `X-Webhook-Secret` header (a missing or wrong header gets 401):

```bash
curl -X POST http://localhost:8000/webhook/tradingview \
  -H 'Content-Type: application/json' \
  -H "X-Webhook-Secret: $WEBHOOK_SHARED_SECRET" \
  -d '{"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0, "price": 65000}'
```

Against an account you created for `broker: paper`, this fills instantly
(no real order placed) — watch it show up live on the dashboard's
Positions/Recent orders panels.

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

**C35:** CI installs dependencies with [uv](https://docs.astral.sh/uv/)
(`uv pip install --system -r requirements.txt`, via
`astral-sh/setup-uv`) instead of plain `pip install`, with its package
cache keyed off `requirements.txt` — a faster, resolver-backed install
on every run, not a change to what gets installed or how tests run
locally (`pip install -r requirements.txt` still works fine for local
dev).

**C38:** CI also runs `ruff check .` (lint) and a scoped `mypy` type
check (`app/`'s core engine/lifecycle/reconciliation/db/broker/context
modules — not the optional-dependency stub adapters like Telegram/IBKR/
MT5, which need their real packages installed to type-check meaningfully).
Neither existed before this. `ruff`'s ruleset is deliberately narrow
(`F` pyflakes + `B` bugbear, real-bug detectors like unused imports/
variables and mutable-default-argument traps — not the opinionated style
families that would produce a repo-wide reformatting diff unrelated to
catching mistakes). The `mypy` pass found several real, if narrow, gaps
— e.g. `MT5Broker` was never actually closed on shutdown (a hand-maintained
broker-name list in `app/main.py` had drifted out of sync and silently
omitted it; now every registered broker's `close()` is called
unconditionally, a safe no-op by default) — alongside the more common
class of "mypy can't see an invariant a boolean/earlier check already
guarantees," each closed with a narrow, commented `assert` rather than
a broad `# type: ignore`. See `pyproject.toml`'s `[tool.ruff]`/
`[tool.mypy]` sections for the exact scope and rationale.

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

`docker-compose.yml`'s port publish is loopback-only
(`127.0.0.1:8000:8000`) by design — with no host address, Docker's
default publishes on every host interface, directly exposing this app's
plain HTTP (no TLS) to the network. Put an HTTPS-terminating reverse
proxy (nginx/Caddy) in front for anything beyond local use, with this
container reachable only from that proxy.

The base image (`python:3.11-slim`) is intentionally a floating minor-
version tag, not pinned to an exact digest — this sandbox has no Docker
daemon available to resolve and verify a pinned digest actually builds
correctly (and, critically, on the Oracle ARM standby's architecture
too, not just the CI runner's amd64). Pin it as part of an actual
release process, once there's a real multi-arch build to test against.

## Multi-site deployment (guarded active/passive, draft)

`deploy/` has a design draft and IaC for running this as one active site
plus one inactive warm standby — **none of it has been applied against a
real cloud account**; it's reviewable Terraform/cloud-init/systemd/Worker
source, not an executed deployment. See `deploy/README.md` for the
recommended topology (existing VPS active, Oracle Always Free A1 standby,
Cloudflare for independent monitoring + off-host backup) and
`deploy/RUNBOOK.md` for the actual promotion procedure — a human-executed
checklist, not automatic failover. `STANDBY_MODE=true` (see
`app/config.py`) is the real, tested mechanism a standby uses to refuse
every financial command independent of any single route's own auth logic
(`tests/test_standby_mode.py`). See `docs/FAILOVER.md` for the
database-backed cross-process/cross-host fencing-token mechanism
(`app/writer_lease.py`) underneath the runbook, and
`python -m app.promote_cli` for the only way a different site ever
becomes the writer — always a deliberate, human-run action with explicit
confirmation flags, never automatic.

## Security notes for when this goes live

- **Owner authentication is mandatory, not optional, once you set `OWNER_PASSWORD`
  and `SESSION_SECRET`.** Every account/routing/provider/position/close/
  flatten/backtest/signals/orders endpoint requires a valid owner session
  (see "Owner authentication" below) — and if either env var is unset,
  those endpoints fail closed with `503`, they do **not** silently become
  public. Generate `SESSION_SECRET` with e.g.
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **C05: `OWNER_PASSWORD_HASH` (recommended) vs. `OWNER_PASSWORD` (legacy).**
  Set exactly one, never both (both set is treated as misconfigured and
  fails closed, same as neither being set — there'd be no reliable way to
  know which one governs). `OWNER_PASSWORD`'s actual value is directly
  usable by anything that can read this process's environment (a log
  dump, a leaked `.env`, a config export); `OWNER_PASSWORD_HASH` is an
  argon2id hash (via `pwdlib`) that isn't itself a usable credential even
  if it leaks. Generate one with:
  `python -c "from pwdlib import PasswordHash; print(PasswordHash.recommended().hash('<your password>'))"`
- Set `WEBHOOK_SHARED_SECRET` before exposing `/webhook/*` publicly — an
  unset secret now makes that route `503` (disabled), not open; the same
  applies to `/sms/twilio` and `TWILIO_AUTH_TOKEN`. The shared-secret
  compare uses `hmac.compare_digest` (C06), same as `OWNER_PASSWORD`'s
  check — a plain `!=` would leak, via response-time variance, how many
  leading characters of a guessed secret are already correct.
- **C06:** both ingress routes (`/webhook/{source}`, `/sms/twilio`) are
  rate-limited to 30 requests/minute per source IP
  (`app/rate_limit.py`) — a defensive ceiling against a flood (malicious
  or a misbehaving/looping sender), not a constraint on legitimate use.
  Exceeding it returns `429`. This is in-memory and per-process; a
  multi-process deployment behind a shared load balancer would need a
  shared backend (Redis, via slowapi's `storage_uri`), not implemented
  here since this project runs one process.
- **C14 (fixed, not just checked):** the dashboard's `escapeAttr` helper
  (`app/static/dashboard.html`) only escaped a single quote, not a
  double quote — every `onclick="fn('...')"` call site embedding an
  attacker-reachable value (e.g. a signal's `symbol`, which
  `app/sources/webhook.py`'s JSON path never validates the character set
  of) could break out of the `onclick` attribute and inject arbitrary
  new HTML attributes. Confirmed exploitable in a real headless Chromium
  browser before the fix (hovering the resulting button fired injected
  JS) and confirmed neutralized after — see
  `tests/test_c14_dashboard_xss_prevention.py`, which reproduces this
  exact attack against the real running app on every CI run, not a mock.
  (A DOMPurify-style library wasn't the fix here — this was a broken
  hand-written escaping function, not a missing sanitizer for rendered
  HTML fragments.)
- **C33/C34 (bounded accessibility check):** an axe-core run against the
  real rendered dashboard
  (`tests/test_c33_c34_dashboard_accessibility.py`) found four
  violations. Two were fixed directly — `landmark-one-main` and `region`,
  by wrapping the dashboard's content in a real `<main>` landmark instead
  of a plain `<div id="app">` — and are asserted gone on every CI run.
  Two are disclosed, known limitations, not fixed in this pass:
  `aria-required-children` (Tabulator, the vendored orders-table library,
  sets `role="grid"` on its container but its virtualized body rows are
  plain unrowed divs, an incomplete ARIA grid pattern; stripping the
  roles from outside Tabulator's own rendering was tried and reverted —
  it chased a moving target across Tabulator's internal re-renders rather
  than converging on a stable fix), and `color-contrast` (~29 nodes
  below WCAG AA thresholds, needing a real palette audit, not a
  one-line change). The test's job is catching new regressions beyond
  this disclosed baseline, not claiming zero violations.
- **C32 (bounded fault injection):** `tests/test_c32_fault_injection.py`
  proves, with real `httpx` transport-level faults injected via
  `httpx.MockTransport` (this sandbox has no infrastructure to run an
  actual Toxiproxy instance), that a broker connection fault
  (`ConnectError`/`ReadTimeout`/`ConnectTimeout`) or a corrupted response
  body doesn't propagate out of `OrderReconciler.reconcile_once()` and
  block reconciling other brokers' orders in the same pass, and that the
  background reconciliation loop itself survives an unanticipated
  exception and keeps running on its next interval — the documented
  "one broker's failure must not block the rest" contract in
  `app/reconciliation.py`, exercised against real fault types instead of
  just asserted in a comment.
- **EXE-01b (fixed):** a managed-lifecycle entry whose `place_order` call
  RETURNED `OrderStatus.ERROR` (rather than raising) was unconditionally
  treated as "never happened" and had its plan unregistered — even
  though several adapters (e.g. `AlpacaBroker`) catch their own
  transport/timeout errors internally and return exactly that ERROR
  result, so a request that actually reached the venue and was accepted
  before a timeout looked identical, from here, to one that never left
  this process. This is the same ambiguity `EXE-01` already handled for
  the raised-exception path; ERROR now gets the same treatment (retained
  as an unresolved pending entry for `OrderReconciler`'s broker-position
  readback to eventually resolve), and only a broker-confirmed
  `REJECTED` unregisters the plan. See
  `tests/test_exe01b_error_result_response_lost.py`.
- **E03 capital allocator reservation timing (fixed for the pollable
  case, bounded):** a provisional notional reservation used to be
  released as soon as `place_order` returned, for every outcome
  including `PENDING` — but a `PENDING` order isn't part of confirmed
  exposure yet (that only counts orders whose stored status is actually
  `FILLED`), so releasing it immediately briefly counted that notional
  toward neither the reservation ledger nor confirmed exposure, letting
  a second signal push real combined exposure past the configured
  ceiling. Now: a `PENDING` result that carries a real `broker_order_id`
  keeps its reservation until `app/reconciliation.py`'s polling loop
  confirms that exact order's terminal status (`FILLED`/`REJECTED`) —
  `app/db.py`'s `orders.reserved_notional` column for a plain account,
  `app/lifecycle/manager.py`'s `PendingEntry.reserved_notional` for a
  `managed_lifecycle` one. A `PENDING` result with no `broker_order_id`
  at all (the same ambiguous "may have reached the venue before an
  error" situation `EXE-01`/`EXE-01b` already handle) still releases
  immediately, same as before this fix — deferring release there has
  nothing that's guaranteed to ever revisit it, which risks a
  reservation that's never released, worse than the timing gap it would
  close. See `app/capital_allocator.py`'s docstring and
  `tests/test_e03_capital_exposure_gate.py`'s
  `test_pending_with_a_broker_order_id_keeps_its_reservation_and_blocks_a_second_entry`,
  `test_reservation_releases_once_reconciliation_confirms_the_pending_order_is_rejected`,
  `test_managed_lifecycle_pending_entry_with_a_broker_order_id_keeps_its_reservation`,
  and `test_pending_with_no_broker_order_id_still_releases_immediately_a_narrower_remaining_gap`
  (the last one documents what's still not closed, not a claim it's
  fine). This adds `orders.reserved_notional` (nullable, additive) via
  `alembic/versions/0002_add_reserved_notional_to_orders.py` — a fresh
  database gets it automatically from `app/db.py`'s `SCHEMA`, but an
  existing, already-Alembic-stamped deployment needs an operator to run
  `alembic upgrade head` once after upgrading (see that migration's own
  docstring and `_stamp_alembic_head_if_needed`'s, which explains why
  `SignalStore`'s own bootstrap can't do this automatically).
- Never commit `.env` or real `config/routing.yaml` /
  `config/accounts.yaml` if they end up containing anything
  account-identifying (they're gitignored by default).
- Every broker adapter should fail loudly (as the ccxt one does) rather
  than silently skip an order when credentials are missing.

## Owner authentication

```
POST /auth/login    {"password": "..."}   -> {"status": "ok", "csrf_token": "..."}
POST /auth/logout
```

Single-owner design (see `app/auth.py`) — there's no user database, just
one shared `OWNER_PASSWORD` compared with a constant-time check, the same
trust level every other secret in this project already gets from an env
var. `POST /auth/login` sets a server-side session (`SignalStore.sessions`
— revocable, survives a restart) as an httponly, samesite=strict cookie,
and returns a separate CSRF token in the response body. Every mutating
request (anything but `GET`) must carry both the session cookie (the
browser attaches this automatically) **and** the `X-CSRF-Token` header set
to that token — a classic double-submit defense: a cross-site page can
make the browser send the cookie, but it has no way to read the token to
put in a custom header. `dashboard.html` does this for you (a login
screen gates the whole page; the token lives in `sessionStorage` for the
tab's lifetime, never the session credential itself). `GET` requests only
need the session cookie, no CSRF token.

`POST /positions/{account_id}/{symbol}/close` and
`POST /accounts/{account_id}/flatten` additionally accept an optional
`Idempotency-Key` header — a retried request with the same key replays
the original result instead of executing the close again. This is
defense in depth on top of, not instead of, the engine's own
per-`(account_id, symbol)` lock (see "Close signals" below), which
already stops two genuinely concurrent close attempts from both
succeeding.
