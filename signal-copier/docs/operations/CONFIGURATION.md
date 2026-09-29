# Configuration reference

Exhaustive reference for every field in `app/config.py`'s `_Settings`
(pydantic-settings `BaseSettings`, `env_prefix=""`, `case_sensitive=True`,
`extra="ignore"`). Values are read once at import time from environment
variables and copied onto `app.config`'s top-level names. Secrets are
covered in more operational depth in `docs/security/SECRETS.md`; this file
is the complete field-by-field reference, including non-secret operational
knobs.

## Paths

| Field | Type | Default | Purpose |
|---|---|---|---|
| `ROUTING_CONFIG_PATH` | Path | `config/routing.yaml` | Where signal→account routing rules are read from. |
| `ACCOUNTS_CONFIG_PATH` | Path | `config/accounts.yaml` | Where destination account definitions are read from. |
| `PROVIDERS_CONFIG_PATH` | Path | `config/providers.yaml` | Optional per-provider/analyst overrides (`app/providers.py`). Missing file (the default) means no overrides apply. |
| `DATABASE_PATH` | Path | `signal_copier.db` | SQLite database file location. |

## Ingress secret

| Field | Type | Default | Purpose |
|---|---|---|---|
| `WEBHOOK_SHARED_SECRET` | str | `""` | Shared secret the webhook source checks against a header/query param. Blank disables `/webhook/*` (`503`). |

## Owner authentication

| Field | Type | Default | Purpose |
|---|---|---|---|
| `OWNER_PASSWORD` | str | `""` | Legacy plaintext owner credential. Mutually exclusive with `OWNER_PASSWORD_HASH`. |
| `OWNER_PASSWORD_HASH` | str | `""` | Preferred: argon2id hash via `pwdlib`. |
| `SESSION_SECRET` | str | `""` | Signs/derives session data. |
| `SESSION_TTL_SECONDS` | float | `43200` (12h) | How long an issued session is valid. |
| `FORCE_SECURE_COOKIES` | bool | `False` | Set `true` when behind a TLS-terminating reverse proxy so the login route marks the session cookie `Secure` even though `request.url.scheme` only ever sees `http` locally (SEC-05). |

## Logging / UI

| Field | Type | Default | Purpose |
|---|---|---|---|
| `LOG_LEVEL` | str | `"INFO"` | Log verbosity. |
| `LEGACY_DASHBOARD_ENABLED` | bool | `False` | Gates whether the empty-hash `/` route and legacy nav link show the pre-redesign "all panels on one page" dashboard instead of redirecting to the hash-routed TR-0X screens. Nothing about the legacy panels is removed by turning this off — only routing to them is gated. |

## Standby / writer fencing

| Field | Type | Default | Purpose |
|---|---|---|---|
| `STANDBY_MODE` | bool | `False` | When true, this process serves only GET/HEAD/OPTIONS — no ingestion, no reconciliation/price polling, no financial command reaches the engine. See `docs/operations/ENVIRONMENTS.md`. |
| `WRITER_SITE_ID` | str | `""` (falls back to hostname) | Identifies this deployed site for cross-host writer-lease fencing. Should be explicit and distinct per site in a real multi-site deployment. |
| `WRITER_LEASE_SECONDS` | float | `30.0` | How long an acquired/renewed writer lease is valid before `promote_cli.py` would treat it as genuinely expired. |
| `WRITER_LEASE_RENEW_SECONDS` | float | `10.0` | How often the active writer renews its own lease. Must be meaningfully shorter than `WRITER_LEASE_SECONDS`. |

## Optional pull-based signal sources

Each source only starts if all of its own required env vars are set.
Push-based sources (webhook, SMS) need no startup config beyond the
ingress secrets above.

| Field | Type | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | str | `""` | Telegram bot API token. |
| `TELEGRAM_CHAT_ID` | str | `""` | Telegram chat to read signals from. |
| `DISCORD_BOT_TOKEN` | str | `""` | Discord bot token. |
| `DISCORD_CHANNEL_ID` | str | `""` | Discord channel to read signals from. |
| `SLACK_BOT_TOKEN` | str | `""` | Slack bot token. |
| `SLACK_APP_TOKEN` | str | `""` | Slack app-level token (Socket Mode). |
| `SLACK_CHANNEL_ID` | str | `""` | Slack channel to read signals from. |
| `TWITTER_BEARER_TOKEN` | str | `""` | X/Twitter API bearer token. |
| `TWITTER_RULES` | str → list[str] | `""` | Comma-separated filtered-stream rules; parsed into a list, empty entries dropped. |

## SMS (Twilio)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `TWILIO_AUTH_TOKEN` | str | `""` | Validates Twilio's request signature. |
| `TWILIO_WEBHOOK_URL` | str | `""` | Full public URL Twilio POSTs to — required for signature validation. |
| `TWILIO_ALLOWED_FROM_NUMBERS` | str → list[str] | `""` | Comma-separated E.164 numbers authorized to send trading SMS. Empty means no sender authorized. |

## WhatsApp

| Field | Type | Default | Purpose |
|---|---|---|---|
| `WHATSAPP_APP_SECRET` | str | `""` | Validates Meta's `X-Hub-Signature-256` header. |
| `WHATSAPP_VERIFY_TOKEN` | str | `""` | Answers Meta's one-time webhook verification GET. |
| `WHATSAPP_ALLOWED_FROM_NUMBERS` | str → list[str] | `""` | Comma-separated `wa_id` numbers (no leading `+`) authorized to send trading instructions. |

## NinjaTrader

| Field | Type | Default | Purpose |
|---|---|---|---|
| `NINJATRADER_WEBHOOK_SECRET` | str | `""` | Shared secret checked against `X-NinjaTrader-Secret` header. |

## MT4/MT5 (MetaApi cloud)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `MT4_MT5_METAAPI_TOKEN` | str | `""` | MetaApi API token. |
| `MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID` | str | `""` | MetaApi account id to copy trades FROM (source, not a destination). |

## Rithmic

| Field | Type | Default | Purpose |
|---|---|---|---|
| `RITHMIC_USER` | str | `""` | Rithmic username. |
| `RITHMIC_PASSWORD` | str | `""` | Rithmic password. |
| `RITHMIC_SYSTEM_NAME` | str | `""` | Rithmic system name. |
| `RITHMIC_GATEWAY_URL` | str | `""` | Rithmic gateway URL. |
| `RITHMIC_SOURCE_ACCOUNT_ID` | str | `""` | Optional filter to one source account. |

## IBKR

| Field | Type | Default | Purpose |
|---|---|---|---|
| `IBKR_HOST` | str | `"127.0.0.1"` | TWS/IB Gateway host. |
| `IBKR_PORT` | int | `7497` | TWS/IB Gateway port. Defaults to TWS paper (7497); 4002 = IB Gateway paper, 7496/4001 = TWS/Gateway live. |
| `IBKR_CLIENT_ID` | int | `1` | ib_async client id. |

## ccxt (crypto)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `CCXT_EXCHANGE_ID` | str | `"binance"` | Which ccxt exchange the single-exchange `"ccxt"` broker talks to. Any ccxt exchange id is valid. |
| `CCXT_SANDBOX` | bool | `False` | Points the exchange at its own sandbox/testnet via ccxt's `set_sandbox_mode(True)`. |
| `CCXT_EXCHANGES` | str → list[str] | `""` | Comma-separated exchange ids to register simultaneously, each its own broker (`ccxt_<id>`). Empty keeps only the single `CCXT_EXCHANGE_ID`-based broker. |

## Broker acknowledgements (no-sandbox / ToS risk)

| Field | Type | Default | Purpose |
|---|---|---|---|
| `SCHWAB_ACKNOWLEDGE_NO_SANDBOX` | bool | `False` | Schwab has no sandbox/paper environment at all — must be explicitly acknowledged true before this adapter is used; every order it places is real. |
| `ROBINHOOD_ACKNOWLEDGE_TOS_RISK` | bool | `False` | Robinhood has no official trading API and no sandbox; automating trades this way is outside Robinhood's ToS for programmatic access. Must be explicitly acknowledged. |

## Background worker intervals

| Field | Type | Default | Purpose |
|---|---|---|---|
| `RECONCILE_INTERVAL_SECONDS` | float | `30.0` | How often `app/reconciliation.py` re-checks PENDING orders on brokers that support `get_order_status()` (currently Alpaca, IBKR, OANDA, Robinhood, Schwab, Tastytrade, TradeStation, Tradovate). |
| `PRICE_MONITOR_INTERVAL_SECONDS` | float | `15.0` | How often `app/pricing.py`'s `PriceMonitor` polls each open managed-lifecycle position's broker for a current price (currently ccxt, Alpaca, IBKR). |
| `EQUITY_SNAPSHOT_INTERVAL_SECONDS` | float | `300.0` (5 min) | How often `app/equity_history.py`'s `EquitySnapshotter` persists one equity/P&L snapshot per configured account. |
| `PROVIDER_SCOUT_INTERVAL_SECONDS` | float | `86400.0` (1 day) | How often `app/provider_scout.py` re-evaluates untracked signal sources/analysts against the provider-value thresholds below. |

## Provider value thresholds

| Field | Type | Default | Purpose |
|---|---|---|---|
| `PROVIDER_VALUE_MIN_SAMPLE_SIZE` | int | `10` | Minimum closed-trade sample size before a provider-value verdict is issued. |
| `PROVIDER_VALUE_WIN_RATE_THRESHOLD` | float | `0.4` | Win-rate threshold used by the verdict heuristic. |
| `PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD` | float | `1.0` | Profit-factor threshold used by the verdict heuristic. Deliberately disclosed as a heuristic, not a claim of statistical significance. |

## Read-only market/economic context

| Field | Type | Default | Purpose |
|---|---|---|---|
| `SEC_EDGAR_USER_AGENT` | str | `""` | Identifying User-Agent required by SEC's fair-access policy. `/context/filings` returns `501` if blank. |
| `FRED_API_KEY` | str | `""` | Free FRED API key. `/context/fred` returns `501` if blank. Neither field is used anywhere in the order-management/protective-stop path. |

## Relay to the commercial platform

| Field | Type | Default | Purpose |
|---|---|---|---|
| `RELAY_INGRESS_URL` | str | `""` | The one allowlisted destination the relay worker posts export batches to. Blank disables the relay entirely (loud failure, not a silent no-op). |
| `RELAY_SIGNING_SECRET` | str | `"LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"` | Signs every export batch; must match the commercial platform's own value. |
| `RELAY_POLL_INTERVAL_SECONDS` | float | `1.0` | How often the relay worker polls for new export events. |
| `RELAY_BATCH_SIZE` | int | `100` | Max events per relay batch. |
| `RELAY_PRODUCER_ID` | str | `"signal-copier-local"` | This deployment's own producer identity on exported envelopes; must be distinct across instances sharing one commercial tenant. |
| `RELAY_EVIDENCE_CLASS` | str | `"INTERNAL_PAPER"` | Evidence-class label on every exported EXECUTION_APPLIED envelope. Never inferred from broker name. |
| `RELAY_ENVIRONMENT` | str | `"LOCAL_SIM"` | Environment label on every exported envelope; same safest-default reasoning as `RELAY_EVIDENCE_CLASS`. |

## Catalog fit-simulation signing

| Field | Type | Default | Purpose |
|---|---|---|---|
| `CATALOG_FIT_SIM_SIGNING_SECRET` | str | `"LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed"` | Signs/verifies `POST /catalog/providers/{source}/fit-simulation` requests from the commercial platform's public catalog. Deliberately separate from `RELAY_SIGNING_SECRET`. |
| `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` | str | `""` | Optional previous value, accepted alongside the current one during a rotation window. Blank means no rotation in progress. See `docs/security/SECRETS.md`. |

## Export outbox

| Field | Type | Default | Purpose |
|---|---|---|---|
| `EXPORT_OUTBOX_SIZE_CEILING_BYTES` | int | `256 * 1024 * 1024` (256 MiB) | Alerting ceiling on the private export outbox's real backlog (`SUM(LENGTH(envelope_json))` over undelivered `export_events` rows). Surfaced via `GET /health`'s `outbox_backlog_ok`. Operator-tunable — see `docs/operations/SLO.md`. |

## Owner-wide exposure ceiling

| Field | Type | Default | Purpose |
|---|---|---|---|
| `MAX_OWNER_NOTIONAL_EXPOSURE` | float \| None | `None` | Opt-in ceiling on the SUM of every configured account's confirmed + pending notional exposure (`app/capital_allocator.py`'s `owner_wide_exposure`), independent of and additional to any per-account `max_notional_exposure`. `None` means no owner-wide ceiling is enforced. |
