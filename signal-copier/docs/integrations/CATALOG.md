# Integration catalog

Every real broker (execution destination) and signal-source adapter in
this codebase, with an honest capability level. **No broker or source
integration in this codebase has live credentials configured in this
development/documentation environment** — this sandbox has no network
egress to real broker/exchange APIs or messaging platforms. The
distinction that matters operationally is therefore: which adapters have
been verified against a real remote API (per that adapter's own module
docstring, disclosing exactly what was checked) vs. which are
code-complete against the target platform's documented/community API but
unqualified against the live venue. Every adapter falls into one of those
two buckets; none has been run against a real account in producing this
documentation.

## Broker (execution destination) adapters — `app/brokers/`

| Adapter | File | Integration basis | Sandbox/paper available | Notes |
|---|---|---|---|---|
| **Alpaca** | `alpaca.py` | Plain REST API | Yes — `ALPACA_<ACCT>_BASE_URL` defaults to the paper endpoint | US stocks/options; the only adapter with an implemented `get_account_balance`. |
| **ccxt** (Binance, Bybit, Kraken, OKX, Coinbase, etc.) | `ccxt_broker.py` | ccxt unified library (100+ real exchange integrations) | Yes — `CCXT_SANDBOX=true` uses ccxt's `set_sandbox_mode(True)`; raises if the configured exchange has none | Crypto only. `CCXT_EXCHANGES` allows multiple exchanges simultaneously as separate brokers. |
| **IBKR** (Interactive Brokers) | `ibkr.py` | `ib_async`, talking to a locally-running TWS/IB Gateway | Yes — port selects paper (7497 TWS / 4002 Gateway) vs. live (7496 TWS / 4001 Gateway) | Requires an already-logged-in local TWS/Gateway process; no per-account env-var credentials, connection params only. |
| **MT4/MT5** (local terminal) | `mt4_mt5.py` | Same-host MT4/MT5 terminal | Depends on the account itself (broker-side demo/live) | One terminal instance per account required. |
| **MT4/MT5** (MetaApi cloud) | `mt4_mt5.py` | MetaApi cloud API (`metaapi-cloud-sdk`) | Depends on the underlying MetaApi account | No local terminal needed — recommended over the local-terminal variant unless avoiding the cloud dependency matters. |
| **OANDA** | `oanda.py` | OANDA's official v20 REST API (no official Python SDK; talks to it directly) | Yes, OANDA has a practice/demo environment | Forex/CFD. |
| **Robinhood** | `robinhood.py` | Reverse-engineered, unofficial endpoints | **No — Robinhood has never published an official trading API and has no sandbox** | Gated behind `ROBINHOOD_ACKNOWLEDGE_TOS_RISK` — every order is real money, and this integration approach is outside Robinhood's own Terms of Service for programmatic access. |
| **Schwab** (Charles Schwab) | `schwab.py` | Schwab's own real REST API (via the community `schwab-py` wrapper) | **No — confirmed by reading every request-issuing method in `schwab-py`: only one base URL exists anywhere in that codebase** | Gated behind `SCHWAB_ACKNOWLEDGE_NO_SANDBOX` — every order placed, including a first test, is real. |
| **Tastytrade** | `tastytrade.py` | Tastytrade's own official REST API | Yes, Tastytrade provides a sandbox | US equities/options. |
| **TradeStation** | `tradestation.py` | TradeStation's own official REST API v3 (no official Python SDK) | Yes, TradeStation provides a simulated account environment | US equities/options/futures. |
| **Tradovate** | `tradovate.py` | Tradovate's own documented REST API (no official Python SDK — confirmed) | Yes, DEMO accounts | US futures. Per-account `ACCOUNT_SPEC` is Tradovate's real account name. |
| **SignalStack** | `signalstack.py` | SignalStack's own webhook fan-out (per-account webhook URL) | Depends entirely on what SignalStack itself is configured to route to | Structurally limited: SignalStack only confirms *it* received the request, never returns a fill/position/balance back — see `app/brokers/base.py`'s `has_account_order_position_feedback` docstring, written specifically for this case. |
| **NinjaTrader** | `ninjatrader.py` | Open-source TradeRouter NinjaScript strategy (github.com/roydufek/traderouter) | Depends on the underlying NinjaTrader account | Local relay URL per account (`NT8_<ACCOUNT_ID>_URL`). |
| **Rithmic** | `rithmic.py` | `async_rithmic` library | Depends on the Rithmic system configured (`RITHMIC_SYSTEM_NAME`) | Futures. |
| **Paper** (in-memory) | `paper.py` | Fully in-process, no external API at all | N/A — this *is* the sandbox | The one adapter genuinely exercised end-to-end in this codebase's own CI/sandbox work (verified: a webhook signal routes through to it and updates `/positions` inside a running Docker container). |

## Signal source adapters — `app/sources/`

| Source | File | Auth mechanism | Push or pull |
|---|---|---|---|
| **Webhook** | `webhook.py` | `WEBHOOK_SHARED_SECRET` shared secret | Push |
| **SMS (Twilio)** | `sms_twilio.py` | `TWILIO_AUTH_TOKEN` signature validation + `TWILIO_ALLOWED_FROM_NUMBERS` sender allowlist | Push |
| **WhatsApp** (Meta Business Cloud API) | `whatsapp.py` | `WHATSAPP_APP_SECRET` signature + `WHATSAPP_ALLOWED_FROM_NUMBERS` sender allowlist | Push |
| **Telegram** | `telegram.py` | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` | Pull |
| **Discord** | `discord.py` | `DISCORD_BOT_TOKEN`/`DISCORD_CHANNEL_ID` | Pull |
| **Slack** | `slack.py` | `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/`SLACK_CHANNEL_ID` | Pull (Socket Mode) |
| **X/Twitter** | `twitter.py` | `TWITTER_BEARER_TOKEN`/`TWITTER_RULES` filtered stream | Pull |
| **NinjaTrader** (as a source) | `ninjatrader.py` | `NINJATRADER_WEBHOOK_SECRET` | Push |
| **MT4/MT5** (as a source, copying trades) | `mt4_mt5.py` | `MT4_MT5_METAAPI_TOKEN`/`MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID` | Pull |
| **Rithmic** (as a source) | `rithmic.py` | `RITHMIC_*` credentials, optional `RITHMIC_SOURCE_ACCOUNT_ID` filter | Pull |

Each optional pull-based source only starts if all of its own required env
vars are set — an unconfigured source is simply never started, not a
disabled-but-present risk. Push-based sources (webhook, SMS, WhatsApp,
NinjaTrader) need no startup config beyond their own shared secret.

## Capability level, summarized honestly

- **Genuinely exercised end-to-end in this environment**: the `paper`
  broker only, via the webhook source.
- **Code-complete against each platform's documented or community API,
  verified only by reading that platform's own API surface/SDK source
  (not by a live call in this environment)**: every other broker and
  source adapter listed above. Each adapter's own module docstring states
  exactly what was checked (e.g. "confirmed: Tradovate's ... no official
  Python SDK exists" for Tradovate, "confirmed by reading every
  request-issuing method in the community wrapper schwab-py's own client
  modules" for Schwab's no-sandbox claim). Read that docstring before
  trusting any specific behavioral claim about an adapter beyond what's
  captured here.
- **No adapter in this codebase has been run against a real, live broker
  account by this session's own work.** Treat "code-complete" and "battle
  tested against a live venue" as two different claims, and this catalog
  only supports the former for anything other than the `paper` broker.
