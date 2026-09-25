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
outside the `place_order` call). That means tracked positions on those
brokers can drift from the real book if an order is later rejected or
partially filled — there's no fill-confirmation feedback path from those
brokers back into this service yet. Paper, ccxt, and MT5 report real fills
synchronously, so their tracked positions stay accurate.

## What's real vs. stubbed

| Component | Status |
|---|---|
| Core engine, routing, risk sizing, SQLite log | ✅ Working, tested |
| Generic JSON / TradingView webhook source | ✅ Working, tested |
| Paper (mock) broker | ✅ Working, tested |
| ccxt broker (Binance/Bybit/etc crypto exchanges) | ✅ Working (needs `pip install ccxt` + API keys) |
| SignalStack broker (relays to IBKR, Schwab, Alpaca, Tradier, TradeStation, Bybit, Coinbase Pro, Oanda, etc. via signalstack.com) | ✅ Working, tested (needs a SignalStack account + a webhook URL per connected broker) |
| Alpaca broker (plain REST, no SDK) | ✅ Working, tested (needs API key/secret; defaults to the paper-trading endpoint) |
| Telegram, Discord, Slack sources | ✅ Working (needs `pip install python-telegram-bot` / `discord.py` / `slack-bolt` + a bot token; only starts if its env vars are set) |
| SMS source (Twilio) | ✅ Working (needs a public URL + `TWILIO_AUTH_TOKEN`/`TWILIO_WEBHOOK_URL` for signature validation; route is always mounted at `/sms/twilio`) |
| Twitter/X source | ✅ Working, but needs X API v2 filtered-stream access (a paid tier as of X's current pricing — verify current terms) and is the least reliable parser of the bunch since tweets are free text |
| IBKR broker | ✅ Working (needs `pip install ib_insync` + a running IB Gateway/TWS with the API enabled; reports PENDING, not a confirmed fill, since IBKR confirms asynchronously) |
| MT5 broker (same-host only) | ✅ Working (needs `pip install MetaTrader5`, Windows, and the service running on the same host as a logged-in MT5 terminal — one terminal process per account) |
| MT4/MT5 source & broker, via [MetaApi](https://github.com/metaapi/metaapi-python-sdk) | ✅ Working (needs `pip install metaapi-cloud-sdk` + a MetaApi account — free tier covers 1 MT4/MT5 account; no local terminal needed at all). Preferred over the same-host MT5 broker above unless you specifically want to avoid the cloud dependency. The source polls deal history on an interval rather than a real-time push callback — see its docstring for why. |
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
directly. (The Docker setup hasn't been run against a live Docker daemon
in this environment — verify it builds and starts before relying on it.)

## Security notes for when this goes live

- Set `WEBHOOK_SHARED_SECRET` before exposing `/webhook/*` publicly —
  otherwise anyone who finds the URL can inject fake signals.
- Never commit `.env` or real `config/routing.yaml` /
  `config/accounts.yaml` if they end up containing anything
  account-identifying (they're gitignored by default).
- Every broker adapter should fail loudly (as the ccxt one does) rather
  than silently skip an order when credentials are missing.
