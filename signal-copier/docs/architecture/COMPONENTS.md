# Component Catalog

A reference table of every real module under `app/`, its responsibility,
and its key public functions/classes. This is a lookup table, not
prose — see docs/architecture/ARCHITECTURE.md for the narrative and
docs/architecture/DATA_FLOWS.md for how these interact end to end.

## Core pipeline

| Module | Responsibility | Key public surface |
|---|---|---|
| `app/models.py` | The shared contract: `Signal`, `OrderResult`, `AccountBalance`, `DestinationAccount`, `CommandLedgerEntry`, and the `Side`/`AssetClass`/`OrderStatus`/`ManagementRecipe`/`CommandType`/`UncertaintyState` enums | `Signal`, `OrderResult`, `AccountBalance`, `DestinationAccount`, `ManagementRecipe`, `CommandType`, `UncertaintyState` |
| `app/engine.py` | Routes a `Signal` to every configured destination account, sized/symbol-mapped/risk-gated; resolves `CLOSE` signals; owns plain-account fill application | `SignalCopierEngine.handle_signal`, `.close_position`, `._reconcile_before_plain_close` |
| `app/routing.py` | Loads/evaluates which sources feed which destination accounts | `RoutingConfig.evaluate`, `.destinations_for`, `load_routing_config_from_store` |
| `app/risk.py` | Per-account position sizing and symbol translation | `size_for_account`, `symbol_for_account` |
| `app/providers.py` | account -> provider -> analyst settings-inheritance overrides | `ProviderRegistry`, `SettingsOverride`, `load_provider_registry_from_store` |
| `app/capital_allocator.py` | Notional/risk-basis admission gates, per-account and owner-wide | `CapitalAllocator`, `confirmed_open_notional`, `owner_wide_exposure`, `ExposureReport` |
| `app/command_ledger.py` | Pre-effect durable command intent + outcome classification | `compute_fingerprint`, `classify_order_result`, `ambiguous_evidence_for_exception`, `is_duplicate_submission` |
| `app/qualification.py` | Route qualification ladder (advisory, human-recorded) | `QualificationState`, `missing_prerequisites`, `state_index`, `requires_feedback` |
| `app/db.py` | SQLite persistence (`SignalStore`), schema, Alembic stamping | `SignalStore` (all reads/writes), `alembic_code_head` |
| `app/writer_lease.py` | Cross-process/cross-host single-writer fencing | `WriterLeaseGuard`, `NullLeaseGuard`, `FencedOutError`, `WriterLeaseRecord` |
| `app/reconciliation.py` | Background loop correcting PENDING orders and resolving pending lifecycle entries/exits | `OrderReconciler` |
| `app/pricing.py` | Background loop polling live price into managed-lifecycle logic | `PriceMonitor` |

## Managed lifecycle — `app/lifecycle/`

| Module | Responsibility | Key public surface |
|---|---|---|
| `app/lifecycle/models.py` | Account-agnostic entry/stop/target/trailing description, including in-flight transfer state | `PositionPlan`, `Target`, `TrailingPolicy`, `PendingEntry`, `PendingExit`, `TransferPhase` |
| `app/lifecycle/close_arbiter.py` | Single serialization point per `(account, symbol)` for exits | `CloseArbiter` |
| `app/lifecycle/manager.py` | Submits entries, protects fills, evaluates targets/trailing, single execution-application owner for entry/exit fills, crash-resumable persistence | `PositionLifecycleManager` (`request_exit`, `on_entry_fill`, `on_price_update`, `resolve_pending_entry`, `resolve_pending_exit`, `restore_from_store`, `validate_plan`) |

## Sources — `app/sources/`

| Module | Responsibility |
|---|---|
| `app/sources/base.py` | `SourceAdapter` interface: `start()`, optional `stop()` |
| `app/sources/webhook.py` | Generic/TradingView JSON webhook, push |
| `app/sources/telegram.py` | Telegram bot, pull |
| `app/sources/discord.py` | Discord bot, pull |
| `app/sources/slack.py` | Slack bot, pull |
| `app/sources/twitter.py` | X/Twitter filtered stream, pull |
| `app/sources/sms_twilio.py` | Twilio SMS webhook, push, HMAC-validated |
| `app/sources/whatsapp.py` | WhatsApp Business Cloud API webhook, push, HMAC-SHA256-validated |
| `app/sources/mt4_mt5.py` | MetaApi-based MT4/MT5 deal-history source, pull |
| `app/sources/ninjatrader.py` | NinjaTrader webhook receiver, push, HMAC-validated |
| `app/sources/rithmic.py` | `async_rithmic`-based trade-event source, pull |
| `app/sources/text_parser.py` | Shared free-text signal parser + `classify_text_signal`/`classify_batch` used by Telegram/Discord/Slack/SMS/Twitter |

## Brokers — `app/brokers/`

| Module | Responsibility |
|---|---|
| `app/brokers/base.py` | `BrokerAdapter` interface, capability introspection properties |
| `app/brokers/paper.py` | In-process mock broker, synchronous fills |
| `app/brokers/alpaca.py` | Plain REST Alpaca adapter (bracket/OTO SL-TP, position/balance/last-price readback) |
| `app/brokers/ccxt_broker.py` | ccxt-based crypto exchange adapter(s), single or multi-exchange |
| `app/brokers/ibkr.py` | `ib_async`-based IBKR adapter (bracket orders, last-price via `reqTickersAsync`) |
| `app/brokers/mt4_mt5.py` | Same-host MT5 terminal adapter and MetaApi cloud adapter |
| `app/brokers/signalstack.py` | Relays orders to IBKR/Schwab/Alpaca/Tradier/etc. via signalstack.com |
| `app/brokers/ninjatrader.py` | Posts to TradeRouter's NinjaScript `WebhookOrderStrategy.cs` listener |
| `app/brokers/rithmic.py` | `async_rithmic`-based futures broker |
| `app/brokers/oanda.py` | OANDA v20 REST forex adapter |
| `app/brokers/tradovate.py` | Tradovate REST futures adapter |
| `app/brokers/tradestation.py` | TradeStation REST v3 adapter, OAuth2 |
| `app/brokers/tastytrade.py` | Tastytrade REST adapter, OAuth2 |
| `app/brokers/schwab.py` | Schwab adapter, no sandbox — gated behind an explicit ack flag |
| `app/brokers/robinhood.py` | Robinhood adapter, no official API/sandbox — gated behind an explicit ack flag |

## Backtesting — `app/backtest/`

| Module | Responsibility |
|---|---|
| `app/backtest/models.py` | `PriceHistoryProvider` seam, trade outcome types |
| `app/backtest/simulator.py` | Core replay engine: resolves each signal's own stop/target against supplied OHLC bars |
| `app/backtest/replay.py` | Orchestrates a replay run against `SignalStore`-held signals |
| `app/backtest/cost_stress.py` | E07: flat slippage/fee stress test on top of a raw replay |
| `app/backtest/fit_simulator.py` | Rescales a source's historical signals to a stated `max_per_trade`/`account_size` ("what would copying this have done to MY account") |

## Market/economic context — `app/context/`

| Module | Responsibility |
|---|---|
| `app/context/sec_edgar.py` | SEC EDGAR filing history + XBRL facts, keyless with required User-Agent |
| `app/context/fred.py` | FRED/ALFRED macro series, requires `FRED_API_KEY` |
| `app/context/fx.py` | Frankfurter daily ECB reference FX rates, keyless |

## Supporting/cross-cutting modules

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app: route registration, `lifespan` startup/shutdown, dashboard serving |
| `app/config.py` | Typed, `pydantic-settings`-based environment configuration |
| `app/config_admin.py` | One-time YAML-to-database config seeding (`seed_from_yaml_if_empty`) |
| `app/auth.py` | Owner password/session/CSRF authentication |
| `app/rate_limit.py` | `slowapi`-based per-IP rate limiting for ingress routes |
| `app/logging_config.py` | `structlog`-based structured logging with secret redaction |
| `app/metrics.py` | Prometheus metrics rendering for `GET /metrics` |
| `app/errors.py` | `SignalValidationError` |
| `app/economics.py` | Confirmed-fill replay -> per-symbol/account P&L (`AccountEconomics`) |
| `app/execution_quality.py` | Per-stage execution latency computation |
| `app/equity_history.py` | Periodic equity snapshotting (`EquitySnapshotter`) |
| `app/statistics.py` | Rolling stats, drawdown, pairwise correlation |
| `app/provider_value.py` | FIFO-lot P&L attribution per provider/analyst/asset class |
| `app/provider_scout.py` | Background scan recommending free providers to promote |
| `app/export_events.py` | Builds `signal_platform_contracts` export envelopes |
| `app/relay_worker.py` / `app/relay_scheduler.py` | Signs and delivers the export outbox to signal-portfolio-commercial |
| `app/services/catalog_fit_sim_auth.py` | Signed-token auth for the cross-service catalog fit-simulation route |
| `app/promote_cli.py` | `python -m app.promote_cli` — the only way a site becomes the writer |
