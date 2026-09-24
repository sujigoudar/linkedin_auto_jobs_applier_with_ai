# Trading Signal Copier

Ingests trade signals from multiple sources and copies them, sized and
symbol-mapped per account, to one or more execution destinations.

This is a scaffold, not a finished product: the core pipeline (ingestion →
routing → risk sizing → execution → persistence) is fully working and
tested. Most individual source/broker integrations are stubs with detailed
setup notes in their docstrings, because each one needs real credentials,
a licensed API, or platform-specific bridge software that can't be wired up
sight-unseen. See "What's real vs. stubbed" below.

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
- **`app/db.py`** — SQLite log of every signal received and every order
  result, for audit/debugging.
- **`app/main.py`** — FastAPI app. Push-based sources (webhooks) get an
  HTTP route; pull-based sources (bots, pollers) would be started as
  background tasks in the `lifespan` handler.

Routing and account config are plain YAML (`config/*.yaml`, gitignored —
copy from the `.example.yaml` files). **Credentials are never stored in
YAML** — they're read from environment variables per account
(`CCXT_{ACCOUNT_ID}_API_KEY`, etc.), so the config files stay safe to
commit.

## What's real vs. stubbed

| Component | Status |
|---|---|
| Core engine, routing, risk sizing, SQLite log | ✅ Working, tested |
| Generic JSON / TradingView webhook source | ✅ Working, tested |
| Paper (mock) broker | ✅ Working, tested |
| ccxt broker (Binance/Bybit/etc crypto exchanges) | ✅ Working (needs `pip install ccxt` + API keys) |
| Telegram, Discord, Slack, SMS (Twilio), Twitter sources | 🚧 Stub — each needs its own bot/API credentials and a message-format parser tailored to the actual channel you're copying |
| MT4/MT5 source & broker | 🚧 Stub — MetaTrader has no native API; needs an EA bridge (ZeroMQ or file-based) or the same-host `MetaTrader5` package for MT5 |
| NinjaTrader source & broker | 🚧 Stub — needs a custom NinjaScript AddOn bridge |
| Rithmic source & broker | 🚧 Stub — needs licensed R\|API access from Rithmic/your broker before any code can be written against it |
| Alpaca, IBKR brokers | 🚧 Stub — straightforward with `alpaca-py` / `ib_insync`, just not wired up yet |

Every stub file's docstring spells out exactly what's needed to finish it.
Start with whichever source/broker pair you actually have accounts for.

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

## Security notes for when this goes live

- Set `WEBHOOK_SHARED_SECRET` before exposing `/webhook/*` publicly —
  otherwise anyone who finds the URL can inject fake signals.
- Never commit `.env` or real `config/routing.yaml` /
  `config/accounts.yaml` if they end up containing anything
  account-identifying (they're gitignored by default).
- Every broker adapter should fail loudly (as the ccxt one does) rather
  than silently skip an order when credentials are missing.
