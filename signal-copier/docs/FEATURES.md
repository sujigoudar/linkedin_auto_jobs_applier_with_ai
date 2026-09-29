# Features

What's actually implemented, as of `HEAD` = `cdfee8b` on
`claude/signal-copier-redesign`. Pulled from `app/sources/`,
`app/brokers/`, and the endpoint/screen inventory, not aspirational.

## Core pipeline

Source adapter → `Signal` → `SignalCopierEngine` (routing, sizing) →
Broker adapter → `SignalStore` (SQLite, Alembic-migrated, head `0015`).
Config (accounts, routing rules, provider/analyst overrides) is
live-managed through the dashboard/API, backed by SQLite — not static
YAML requiring a restart.

## Signal sources (`app/sources/`)

- **`webhook.py`** — generic JSON / TradingView webhook. The one fully
  working, primary ingestion path.
- **`text_parser.py`** — generic free-text signal parser shared by every
  text-message source below.
- **`telegram.py`**, **`discord.py`**, **`slack.py`** (Socket Mode, no
  public URL required) — chat-platform sources, built directly.
- **`sms_twilio.py`** — SMS via Twilio.
- **`whatsapp.py`** — via Meta's official WhatsApp Business Cloud API.
- **`twitter.py`** — Twitter/X.
- **`mt4_mt5.py`** — via the MetaApi cloud SDK.
- **`rithmic.py`** — via the `async_rithmic` library.
- **`ninjatrader.py`** — NinjaTrader as a signal source via a NinjaScript
  AddOn/Indicator POSTing fill events to `/ninjatrader/webhook`. The
  Python side (parsing + route) is real and tested; the C# side
  (`ninjascript/SignalCopierAutoJournal.cs`) is a reviewed but
  **unverified** reference implementation — never compiled or run
  against a real NinjaTrader install in this environment. See
  `docs/state/BLOCKERS.md`.

## Broker/execution destinations (`app/brokers/`)

- **`paper.py`** — PaperBroker, with real cash/fee tracking.
- **`alpaca.py`**, **`ibkr.py`** (Interactive Brokers) — built directly.
- **`ccxt_broker.py`** — crypto exchange execution via ccxt; supports
  multiple simultaneous exchanges (Binance, Bybit, Kraken, OKX, …), one
  `CCXTBroker` instance per exchange/account.
- **`signalstack.py`** — SignalStack.
- **`mt4_mt5.py`** — same-host MT4/MT5.
- **`ninjatrader.py`** — via TradeRouter's NinjaScript strategy.
- **`tradovate.py`** — official REST API, futures.
- **`oanda.py`** — official v20 REST API, forex.
- **`tradestation.py`** — official REST API v3.
- **`tastytrade.py`** — real REST API.
- **`schwab.py`** — real REST API, reverse-engineered against the
  community `schwab-py` wrapper's source. **No sandbox — every request
  hits a real account.**
- **`robinhood.py`** — reverse-engineered against `robin_stocks`. **No
  official API, no sandbox, outside Robinhood's own ToS for
  programmatic access.**

## Risk / capital management

- **`app/capital_allocator.py`** — fail-closed admission gate: per-account
  notional ceiling, opt-in owner-wide notional ceiling
  (`MAX_OWNER_NOTIONAL_EXPOSURE`), opt-in risk-basis sizing
  (`DestinationAccount.risk_percent_of_equity`), and an unresolved-
  exposure block (a position it can't price blocks new admissions for
  that account rather than being treated as zero exposure).
- **`app/lifecycle/manager.py`** — managed-lifecycle positions: stop/
  target/trailing-stop management, MAE/MFE excursion tracking, an
  append-only stop/target lifecycle event log.
- **`app/command_ledger.py`** — durable, pre-effect ledger for every
  broker command, with idempotency-key dedup and
  unknown_ambiguous/fingerprint-mismatch handling.
- **`app/writer_lease.py`** / **`app/promote_cli.py`** — cross-process
  writer-lease fencing and a manual, three-flag-confirmed promotion CLI.
  See `docs/FAILOVER.md`.
- **Distinct-field quantity accounting** (`AUD-01`) — five separate
  tracked quantities per order/fill instead of one optimistically-updated
  position field.

## Readiness / operations

- **`GET /system/readiness`** — 6 independent dimensions (liveness,
  data_readiness, market_data_readiness, trading_authority,
  protection_readiness, release_status) plus a rollup derived only from
  those six. `trading_authority` and `release_status` are honest
  placeholders — see `docs/state/PENDING_DECISIONS.md`.
- **`app/reconciliation.py`** — reconciles tracked positions against
  broker truth, including plain-account CLOSE reconciliation.
- **`app/relay_scheduler.py`** / export outbox — storage-ceiling and
  alerting policy for the private export outbox (INT-040).
- Revocable JWT sessions with a real jti-keyed denylist, fail-closed
  verification.
- Owner-gated historical message import/review workflow.
- CURRENT+PREVIOUS dual-secret verification for secret rotation.

## Dashboard / screens

A redesigned, tabbed operational-readiness console (`TR-01`..`TR-16`)
covering: KPI band and attention-required queue, position/order
lifecycle with MAE/MFE and result-attribution waterfalls, strategy/
sleeve portfolio risk (correlation, co-drawdown, contribution), signal
funnel and routing graph, capability/venue/reconciliation status, policy
editor (sizing/protection/targets/trailing/deadlines/limits), saved
filter views, backtest research reports with persisted runs, and real
Chart.js visualizations throughout (equity curves, latency, allocation
donuts). Built on a shared 3-level design-system token/component
foundation. Legacy dashboard is gated behind `LEGACY_DASHBOARD_ENABLED`
(default off).

## Deployment

Docker image + `docker-compose.yml` with a read-only root filesystem,
`cap_drop: ALL`, non-root user, resource limits, and loopback-only port
binding by default. See `docs/process/RELEASE.md`.
