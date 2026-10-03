# Signal Copier Actual Application Inventory

**Date:** October 2, 2026  
**Revision:** Signal Copier commit fccf57affd8515fe059b1af854856828078a737e  
**Scope:** Non-live application state; paper broker, SQLite backend, existing adapters only.

This document enumerates every route (HTTP + console), schema table, adapter, signal/account field, scheduled job, and release gate in the actual deployed application, with file:line references for traceability.

---

## HTTP Routes

All routes in `app/main.py` unless noted. Format: `METHOD /path` → `line number`.

### System & Monitoring
- `GET /health` → app/main.py:689
- `GET /metrics` → app/main.py:828
- `GET /system/info` → app/main.py:844
- `GET /system/readiness` → app/main.py:962

### Authentication
- `POST /auth/login` → app/main.py:1217
- `POST /auth/logout` → app/main.py:1253

### UI & Static Content
- `GET /` → app/main.py:1261

### Signal Ingestion
- `POST /webhook/{source_name}` → app/main.py:1300
- `POST /sms/twilio` → app/main.py:1390
- `GET /whatsapp/webhook` → app/main.py:1456
- `POST /whatsapp/webhook` → app/main.py:1476
- `POST /ninjatrader/webhook` → app/main.py:1552

### Position & Account Data
- `GET /positions` → app/main.py:1598
- `GET /positions/excursions` → app/main.py:1627
- `GET /positions/{account_id}/{symbol}/stop-events` → app/main.py:1642
- `GET /accounts/{account_id}/economics` → app/main.py:1667
- `GET /accounts/{account_id}/economics/extended` → app/main.py:1678
- `GET /accounts/{account_id}/balance` → app/main.py:1703
- `GET /accounts/{account_id}/execution-quality` → app/main.py:1948
- `GET /accounts/{account_id}/equity-history` → app/main.py:1958
- `GET /accounts/{account_id}/statistics` → app/main.py:2027

### Capital & Risk
- `GET /capital-allocation` → app/main.py:1728
- `GET /policies/sizing-preview` → app/main.py:1814

### Analytics
- `GET /accounts/correlation` → app/main.py:2000

### Position Management
- `POST /positions/{account_id}/{symbol}/close` → app/main.py:2047
- `POST /accounts/{account_id}/flatten` → app/main.py:2105
- `GET /lifecycle/{account_id}/{symbol}/preview-reduction` → app/main.py:2174
- `GET /lifecycle/{account_id}/{symbol}/preview-stop-change` → app/main.py:2196

### Reconciliation
- `POST /reconciliation/run-now` → app/main.py:2160

### Configuration
- `GET /brokers` → app/main.py:2217
- `GET /qualifications` → app/main.py:2330
- `POST /qualifications` → app/main.py:2351
- `GET /providers` → app/main.py:2411
- `GET /accounts` → app/main.py:2518
- `POST /accounts` → app/main.py:2562
- `DELETE /accounts/{account_id}` → app/main.py:2621
- `PATCH /accounts/{account_id}` → app/main.py:2638
- `GET /routing-rules` → app/main.py:2861

---

## Database Schema Tables

All tables defined in `app/db.py` SCHEMA constant (line 86+).

### Core Signal & Order Tracking
- `signals`: id (TEXT PK), source, symbol, side, asset_class, quantity, price, stop_loss, take_profit, analyst, received_at, raw, plus 12 multi-provider/Track-16 fields (targets, price_low/high, entry_order_type, entry_expiration, management_horizon, confidence, intent, reduce_fraction, option, future, fx, crypto_derivative, client_order_id, channel_id, message_id, revision_id, original_message_id, parser_version, raw_source_event, source_created_at, source_modified_at, first_observed_at, parsed_at, decision_at, import_batch, source_catalog_id) → app/db.py:87
- `orders`: id (INTEGER PK AUTOINCREMENT), account_id, broker, symbol, side, requested_quantity, signal_id, status, broker_order_id, filled_quantity, filled_price, message, executed_at, submitted_at, protection_confirmed_at, reserved_notional, purpose, family_id, plus 8 quantity-breakdown columns (confirmed_cumulative_fill, applied_execution_delta, outstanding_possible_fill, reserved_quantity, acknowledged_quantity, fee, fee_currency, slippage, price_currency) → app/db.py:102

### Position Tracking
- `positions`: (account_id, symbol) PK, net_quantity REAL DEFAULT 0, updated_at → app/db.py:272

### Managed Lifecycle State
- `lifecycle_state`: (account_id, symbol) PK, PositionLifecycle JSON, CloseArbiter ledger JSON, persisted/deleted on state change → app/db.py:285+

### Source Event Ledger
- `source_events`: track source event kind, channel/message/revision/parent ids, provider/local timestamps, parsed Signal, cancellation reason → app/db.py (continuation of schema)

### Authorization & Configuration
- `config_accounts`: account_id PK, broker, account_credentials_json, enabled, multiplier, fixed_quantity, symbol_map, managed_lifecycle, max_notional_exposure, risk_percent_of_equity, management_recipe, qualification_level, exclusive_writer_qualified, daily_loss_limit_percent, min_equity_threshold, currency, max_gross_leverage, allow_short, evidence_class, paper_order_id_sequence → app/db.py
- `routing_rules`: (account_id, symbol) PK, entry_enabled, exit_enabled → app/db.py
- `qualifications`: (adapter_type, route_key, asset_class, product_type) PK, state (IMPLEMENTED..RELEASE_APPROVED), set_at, reason → app/db.py

### Capital Allocation
- `capital_reservations`: owner, account_id, portfolio_id, strategy_key, reserved_cash, reserved_risk, reserved_notional, reserved_at → app/db.py
- `allocation_intents`: signal_id PK, intent_id, strategy_key, state (claimed/selected/filled/skipped), created_at, trace JSON → app/db.py (WP-34 reference)

### Reconciliation & Fees
- `reconciliation_log`: timestamp, account_id, symbol, discrepancy reason, status → app/db.py
- `fee_journal`: order_id FK, fee_amount DECIMAL, fee_currency, collected_at → app/db.py

### Command & Ledger
- `command_ledger`: signal_id, account_id, symbol, action, status, reason, executed_at → app/db.py (per app/command_ledger.py module)

---

## Broker Adapters

All adapters in `app/brokers/` implement `BrokerAdapter` from `app/brokers/base.py` (line 1+).

### Live Brokers
- `alpaca.py`: Alpaca Securities adapter; equities, options; REST API
- `ccxt_broker.py`: CCXT unified exchange connector; spot/futures crypto; REST+WebSocket
- `ibkr.py`: Interactive Brokers; equities, options, futures, forex; TWS API
- `oanda.py`: OANDA; forex, CFDs; REST API
- `robinhood.py`: Robinhood; equities, crypto; REST API
- `schwab.py`: Charles Schwab; equities, options; REST API
- `signalstack.py`: SignalStack router; multi-account relay; webhook
- `tastytrade.py`: Tastytraders; options, futures; REST API
- `tradovation.py`: Tradovate; futures; WebSocket
- `tradestation.py`: TradeStation; equities, options, futures; REST API

### Specialty/Terminal Adapters
- `mt4_mt5.py`: MetaTrader 4/5; forex, CFDs; DLL/pipe integration
- `ninjatrader.py`: NinjaTrader; futures, forex; DLL/pipe integration
- `rithmic.py`: Rithmic; futures; socket protocol

### Paper Broker
- `paper.py`: In-memory paper trading broker; all asset classes; instant fills

### Base Classes
- `base.py`: `BrokerAdapter` abstract interface; capability introspection; method stubs for place_order, get_order_status, get_position, get_balance, etc.

---

## Signal Sources (Adapters)

All adapters in `app/sources/` implement `SourceAdapter` from `app/sources/base.py` (line 1+).

### Live Transport Adapters
- `webhook.py`: Generic HTTP webhook ingestion; JSON body parse
- `discord.py`: Discord bot; channel/DM text messages; real channel_id/message_id
- `slack.py`: Slack bot; channel messages; real channel/message identity
- `telegram.py`: Telegram bot; text messages; real chat_id/message_id
- `whatsapp.py`: WhatsApp Business API; messages; real message identity
- `sms_twilio.py`: Twilio SMS; text signals; real phone/message identity
- `twitter.py`: Twitter/X stream; @mentions; real tweet/author identity
- `email_source.py`: Email ingestion; parsed MIME; real message-id headers
- `rss_source.py`: RSS/Atom feed; article titles/summaries; real feed item identity

### Specialty Adapters
- `mt4_mt5.py`: MetaTrader comments/alerts; DLL-based; no native message identity
- `ninjatrader.py`: NinjaTrader alerts; DLL/pipe; no native message identity
- `rithmic.py`: Rithmic alerts; socket; no native message identity

### Terminal/User Adapters
- `text_parser.py`: Command-line text parser; freeform grammar; developer-driven
- `article_classifier.py`: LLM-based news classification; PDF/HTML; not live entry
- `article_extraction.py`: News content extraction; structured to Signal conversion

### Base Classes & Utilities
- `base.py`: `SourceAdapter` abstract interface; `start()`/`stop()`, emit Signal methods
- `adapter_contract.py`: Formal interface contracts and typing
- `url_safety.py`: Link validation/phishing detection

---

## Signal Model Fields

Class `Signal` in `app/models.py` (line 133+). All fields documented with intent/nullability.

### Mandatory Core
- `source: str` — Transport/provider source name
- `symbol: str` — Asset identifier
- `side: Side` — BUY / SELL / CLOSE
- `asset_class: AssetClass` — CRYPTO / FOREX / EQUITY / OPTION / FUTURE

### Optional Sizing & Protection
- `quantity: float | None` — Requested quantity (units or %)
- `price: float | None` — Primary entry price
- `stop_loss: float | None` — Protective stop price
- `take_profit: float | None` — Primary profit target
- `targets: list[ProfitTarget]` — Ordered multi-level TP collection (TP1/TP2/TP3...)

### Entry Specification
- `entry_order_type: EntryOrderType | None` — MARKET / LIMIT / STOP
- `entry_expiration: datetime | None` — Order deadline
- `price_low: float | None` — Entry range lower bound
- `price_high: float | None` — Entry range upper bound

### Intent & Behavior
- `intent: Intent | None` — ENTRY_LONG / ENTRY_SHORT / SELL / EXIT / REDUCE / STOP_UPDATE / TARGET_UPDATE / CANCEL / ADD (derived from `side` in __post_init__ if not explicit)
- `reduce_fraction: float | None` — Fraction (0 < x ≤ 1) to reduce for REDUCE intent

### Strategy/Horizon Metadata
- `analyst: str | None` — Sender within source (e.g., trader in Discord)
- `management_horizon: str | None` — intraday / swing / deadline / session
- `confidence: str | None` — Strategy/confidence tag (never fabricated)

### Product Specifications
- `option: OptionContractSpec | None` — Underlying, strike, call/put, expiry, etc.
- `future: FutureContractSpec | None` — Contract, exchange, multiplier, expiry
- `fx: FxContractSpec | None` — Base/quote pair, spot rate
- `crypto_derivative: CryptoDerivativeSpec | None` — Linear/inverse, contract size, funding

### Deduplication & Lineage
- `channel_id: str | None` — Native provider channel id
- `message_id: str | None` — Native provider message id
- `revision_id: str | None` — This revision's native id (for edits)
- `original_message_id: str | None` — First message in revision chain
- `parser_version: str | None` — Interpretation/grammar version

### Timestamps & Processing
- `source_created_at: datetime | None` — Provider's own event timestamp
- `source_modified_at: datetime | None` — Provider's edit timestamp (if distinct from creation)
- `first_observed_at: datetime | None` — First sighting (distinct from received_at)
- `received_at: datetime` (default: now) — When this service received it
- `parsed_at: datetime | None` — When parsing completed
- `decision_at: datetime | None` — When engine made routing decision

### Durable Identifiers & State
- `id: str` (UUIDv4 default) — Unique signal id
- `client_order_id: str | None` — Broker-side dedup id (set before submission)
- `import_batch: str | None` — Batch label for historical imports (distinguishes backfilled from live)
- `source_catalog_id: str | None` — Track 14 provider catalog reference

### Provenance & Debug
- `raw: dict` (default {}) — Original payload (varies by adapter)
- `raw_source_event: dict | None` — Full provider event envelope

---

## DestinationAccount Model Fields

Class `DestinationAccount` in `app/models.py` (line 697+). Every account, every choice, explicit configuration.

### Identity
- `account_id: str` — Unique account identifier (in config_accounts table)
- `broker: str` — Broker/adapter name (e.g., "alpaca", "ccxt_binance_spot", "paper")

### Sizing & Quantity
- `multiplier: float` (default 1.0) — Notional/margin multiplier for this account
- `fixed_quantity: float | None` — Account-wide fixed quantity override (if set, ignores signal quantity)

### Routing & Mapping
- `symbol_map: dict[str, str]` (default {}) — Map signal symbol to account-specific symbol
- `enabled: bool` (default True) — Account is enabled for new entries/exits

### Lifecycle Management
- `managed_lifecycle: bool` (default False) — Route through PositionLifecycleManager (protect-first, logical targets, serialized close)
- `management_recipe: ManagementRecipe | None` — Explicit recipe declaration (PLAIN_UNMANAGED or FULL_MANAGED_LIFECYCLE); set in __post_init__ from `managed_lifecycle` if not explicit
- `qualification_level: str | None` — Operator's free-form label (e.g., "qualified", "unqualified", "pending_review")

### Capital & Risk Limits
- `max_notional_exposure: float | None` — Ceiling on total notional exposure
- `risk_percent_of_equity: float | None` — Percentage of account equity at risk for entry stop (fails closed if missing or no equity available)
- `daily_loss_limit_percent: float | None` — Daily P&L loss ceiling; entries rejected if breached (closes always allowed)
- `min_equity_threshold: float | None` — Minimum equity required; entries rejected if would fall below
- `max_gross_leverage: float | None` — Ceiling on gross (sum of notional / equity) leverage

### Account Type & Permissions
- `allow_short: bool` (default False) — Account permitted to open short positions
- `exclusive_writer_qualified: bool` (default False) — Operator asserts no manual/other-automated writes to this account's positions (only with `has_position_readback_capability == False`)

### Currency & Fees
- `currency: str | None` — ISO 4217 base currency (e.g., 'USD', 'EUR', 'JPY') — must be explicitly configured for multi-currency
- `evidence_class: str | None` — Override global config.RELAY_EVIDENCE_CLASS for this account's exported events

### Paper Broker
- `paper_order_id_sequence: int | None` — Monotonic order ID counter (paper broker only; persisted per account)

---

## Scheduled Jobs & Lifespan Tasks

All defined in `app/main.py` lifespan handler (line 495+).

### Startup (in order)
1. **Writer Lease Acquisition** (line 521) — Cross-process/cross-host fencing; crashes if another site holds lease
2. **Heartbeat Task** (line 529) — Periodic lease renewal (`_writer_lease_heartbeat()`, line TBD)
3. **Lifecycle Restore** (line 533) — Recover managed lifecycles from `lifecycle_state` table (WP-28: D-09 pattern)
4. **Webhook Source Start** (line 535) — HTTP webhook listener
5. **Background Source Start Loop** (line 536) — Discord, Slack, Telegram, Twilio, Twitter, email, RSS (each with exception isolation)
6. **Reconciler Start** (line 541) — Broker position/order polling
7. **Price Monitor Start** (line 542) — Quote/mark refresh for sizing
8. **Provider Scout Start** (line 543) — Provider catalog updates
9. **Equity Snapshotter Start** (line 544) — Account balance snapshots
10. **Relay Scheduler Start** (line 550) — Conditional; only if `config.RELAY_INGRESS_URL` set (commercial platform export)
11. **Stale Allocation Intent Sweep** (line 554) — WP-34 startup cleanup; marks crashed 'claimed'/'selected' intents as 'skipped'

### Continuous Polling (background tasks)
- `reconciler.start()` — Polls broker order status, reconciles positions (app/reconciliation.py)
- `price_monitor.start()` — Polls broker quotes for active symbols
- `relay_scheduler.start()` — Exports completed orders to commercial platform
- `lifecycle_manager` — Monitors protective stops, targets, runners (app/lifecycle/manager.py)

### Shutdown (in reverse order)
- Cancel heartbeat task
- Stop relay scheduler (if active)
- Stop equity snapshotter
- Stop provider scout
- Stop price monitor
- Stop reconciler
- Stop background sources (each with exception isolation)
- Stop webhook source

---

## Release Gates

All defined in `app/qualification.py` (line 1+). Qualification model per (adapter_type, route_key, asset_class, product_type) tuple.

### QualificationState Ladder (strict prerequisite order)
1. `IMPLEMENTED` — Code exists; capability introspection reports true (app/brokers/base.py)
2. `CONFIGURED` — Specific account/venue in accounts.yaml; credentials pointed to
3. `AUTHENTICATED` — Real auth handshake succeeded (login/token/webhook validation)
4. `ACCOUNT_ENTITLED` — Account confirmed entitled for this product (options flag, margin permission, etc.); requires genuine account feedback
5. `PROTOCOL_TESTED` — This repo's test suite exercises real protocol path (not mock/unit test only)
6. `VENUE_TESTED` — Real deliberate test against actual venue/sandbox; honestly omitted when no live credentials
7. `RELEASE_APPROVED` — Explicit operator sign-off for live trading (HTTP POST /qualifications; session-gated)

### Recording Enforcement (app/db.py `SignalStore.record_route_qualification`)
- No jumps: state N requires all states 0..N-1 already recorded for same route
- Operator-gated: only via `POST /qualifications` (session auth); never auto-set

### Current Live Routes (as of this inventory)
- See `config_accounts` table and `qualifications` table for actual deployed state

---

## Design & Architecture References

### Module Docstrings (Mandatory Pre-Reading)
- `app/engine.py` (line 1+) — Signal routing, plain vs. managed paths, quantity/protection models
- `app/lifecycle/manager.py` — PositionLifecycleManager; protective stop/target/runner state machine
- `app/capital_allocator.py` — Hierarchical capital checks; reservation timing; stress/margin models
- `app/command_ledger.py` — Order classification (CONFIRMED/SUBMITTED_UNCONFIRMED/REJECTED_CONFIRMED/UNKNOWN_AMBIGUOUS)
- `app/writer_lease.py` — Cross-process writer fencing
- `app/reconciliation.py` — Broker position reconciliation; deficit calculation
- `app/qualification.py` (above) — Release gate ladder

### Key Configuration
- `app/config.py` — Environment variables, deployment modes (PAPER/SHADOW/LIVE), thresholds
- `accounts.yaml` — Account configuration (static or dynamic from config_accounts table)
- `.env` / environment — Broker credentials, API keys, webhook secrets (never in repo)

---

## Inventory Validation Notes

- **Paper Broker:** Only non-live route used in this build; instant fills; persisted in `app/brokers/paper.py`
- **Backtest Simulator:** See `app/backtest/simulator.py` and `app/backtest/replay.py` — virtual clock, non-network
- **Live Status:** No live credentials available in this build; VENUE_TESTED state unreachable without external account access
- **Unresolved Gaps:**
  - SourceAdapter for dedicated portfolio/sleeve allocation (planned: WC-02, WC-03)
  - Explicit order-receipt confirmation receipts (in progress: app/command_ledger.py)
  - Historical vs. live signal deduplication (partial: import_batch field distinguishes bulk imports)
  - Multi-account order consolidation/fan-out (not implemented; WC-05 single-destination only)
  - Kelly profiling/evidence states (designed; not yet implemented: WC-07)
  - Staged entry pyramiding (designed; not yet implemented: WC-08)

---

**Generated:** 2026-10-02 — Inventory valid for the baseline application without workflow-contract enhancements.
