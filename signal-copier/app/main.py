"""FastAPI entrypoint.

Wires up: config -> routing rules -> broker adapters -> engine -> sources.
Push-based sources (webhook, SMS) get their own HTTP route below.
Pull-based sources (Telegram/Discord/Slack bots, Twitter stream) are only
started if their required env vars are set, and run as background tasks
started in the lifespan handler.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import config
from app.backtest.models import CsvPriceHistoryProvider
from app.backtest.replay import BacktestEngine
from app.brokers.alpaca import AlpacaBroker
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.ibkr import IBKRBroker
from app.brokers.mt4_mt5 import MetaApiBroker, MT5Broker
from app.brokers.ninjatrader import NinjaTraderBroker
from app.brokers.paper import PaperBroker
from app.brokers.rithmic import RithmicBroker
from app.brokers.signalstack import SignalStackBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.errors import SignalValidationError
from app.lifecycle.manager import PositionLifecycleManager
from app.providers import SettingsOverride, load_provider_registry
from app.reconciliation import OrderReconciler
from app.routing import load_routing_config
from app.sources.discord import DiscordSource
from app.sources.mt4_mt5 import MetaApiSource
from app.sources.rithmic import RithmicSource
from app.sources.slack import SlackSource
from app.sources.sms_twilio import TwilioSMSSource
from app.sources.telegram import TelegramSource
from app.sources.twitter import TwitterSource
from app.sources.webhook import WebhookSource

STATIC_DIR = Path(__file__).resolve().parent / "static"

logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)

routing_config = load_routing_config(config.ROUTING_CONFIG_PATH, config.ACCOUNTS_CONFIG_PATH)
provider_registry = load_provider_registry(config.PROVIDERS_CONFIG_PATH)
store = SignalStore(config.DATABASE_PATH)

brokers = {
    "paper": PaperBroker(),
    "signalstack": SignalStackBroker(),
    "alpaca": AlpacaBroker(),
    "ninjatrader": NinjaTraderBroker(),
}
# These brokers need optional packages installed (and, for Rithmic, connection
# credentials up front); only register them if available so the paper-only
# quickstart doesn't need every dependency.
_optional_brokers = [("ccxt", CCXTBroker), ("ibkr", IBKRBroker), ("mt4_mt5", MT5Broker), ("mt4_mt5_metaapi", MetaApiBroker)]
if config.RITHMIC_USER:
    _optional_brokers.append(
        (
            "rithmic",
            lambda: RithmicBroker(
                config.RITHMIC_USER, config.RITHMIC_PASSWORD, config.RITHMIC_SYSTEM_NAME, config.RITHMIC_GATEWAY_URL
            ),
        )
    )
for broker_name, broker_factory in _optional_brokers:
    try:
        brokers[broker_name] = broker_factory()
    except RuntimeError as exc:
        logger.info("%s broker not registered: %s", broker_name, exc)

lifecycle_manager = PositionLifecycleManager(brokers=brokers, store=store)
lifecycle_manager.restore_from_store()  # resume any managed-lifecycle positions from before a restart
engine = SignalCopierEngine(
    routing=routing_config,
    brokers=brokers,
    store=store,
    lifecycle_manager=lifecycle_manager,
    provider_registry=provider_registry,
)
webhook_source = WebhookSource(on_signal=engine.handle_signal)
sms_source = TwilioSMSSource(on_signal=engine.handle_signal)
reconciler = OrderReconciler(
    store=store,
    brokers=brokers,
    interval_seconds=config.RECONCILE_INTERVAL_SECONDS,
    lifecycle_manager=lifecycle_manager,
)

# Pull-based sources only start if fully configured via env vars.
_background_sources = []
if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
    _background_sources.append(
        TelegramSource(engine.handle_signal, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    )
if config.DISCORD_BOT_TOKEN and config.DISCORD_CHANNEL_ID:
    _background_sources.append(
        DiscordSource(engine.handle_signal, config.DISCORD_BOT_TOKEN, int(config.DISCORD_CHANNEL_ID))
    )
if config.SLACK_BOT_TOKEN and config.SLACK_APP_TOKEN and config.SLACK_CHANNEL_ID:
    _background_sources.append(
        SlackSource(engine.handle_signal, config.SLACK_BOT_TOKEN, config.SLACK_APP_TOKEN, config.SLACK_CHANNEL_ID)
    )
if config.TWITTER_BEARER_TOKEN:
    _background_sources.append(
        TwitterSource(engine.handle_signal, config.TWITTER_BEARER_TOKEN, config.TWITTER_RULES)
    )
if config.MT4_MT5_METAAPI_TOKEN and config.MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID:
    _background_sources.append(
        MetaApiSource(engine.handle_signal, config.MT4_MT5_METAAPI_TOKEN, config.MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID)
    )
if config.RITHMIC_USER and config.RITHMIC_SYSTEM_NAME and config.RITHMIC_GATEWAY_URL:
    _background_sources.append(
        RithmicSource(
            engine.handle_signal,
            config.RITHMIC_USER,
            config.RITHMIC_PASSWORD,
            config.RITHMIC_SYSTEM_NAME,
            config.RITHMIC_GATEWAY_URL,
            account_id=config.RITHMIC_SOURCE_ACCOUNT_ID or None,
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await webhook_source.start()
    for source in _background_sources:
        try:
            await source.start()
        except Exception:  # noqa: BLE001 - one misconfigured source must not block the app
            logger.exception("failed to start source '%s'", source.name)
    await reconciler.start()

    yield

    await reconciler.stop()
    for source in _background_sources:
        await source.stop()
    for broker_name in ("signalstack", "alpaca", "ninjatrader", "ccxt", "ibkr", "mt4_mt5_metaapi", "rithmic"):
        if broker_name in brokers:
            await brokers[broker_name].close()


app = FastAPI(title="Trading Signal Copier", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/")
async def dashboard() -> FileResponse:
    """A minimal, read-only dashboard: one static HTML page with vanilla JS
    that polls the JSON endpoints below (/positions, /brokers, /providers,
    /signals, /orders) and renders them as tables — no build step, no
    frontend framework, no new dependency. It shows exactly what this
    service's own state is; it cannot submit an order, cancel one, or
    change any config — every mutation still only happens through a
    source's own push (webhook/SMS route) or a pull-based source's
    background task, same as before this existed."""
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.post("/webhook/{source_name}")
async def receive_webhook(
    source_name: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    if config.WEBHOOK_SHARED_SECRET and x_webhook_secret != config.WEBHOOK_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    payload = await request.json()
    try:
        signal = webhook_source.parse(payload, source_override=source_name)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = await engine.handle_signal(signal)
    return _orders_response(signal.id, results)


@app.post("/sms/twilio")
async def receive_sms(request: Request, body: str = Form(alias="Body")) -> dict:
    if config.TWILIO_AUTH_TOKEN:
        try:
            from twilio.request_validator import RequestValidator
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(
                status_code=500, detail="twilio package not installed; cannot validate request"
            ) from exc

        signature = request.headers.get("X-Twilio-Signature", "")
        form = await request.form()
        validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
        if not validator.validate(config.TWILIO_WEBHOOK_URL, dict(form), signature):
            raise HTTPException(status_code=401, detail="invalid Twilio signature")

    try:
        signal = sms_source.parse(body)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = await engine.handle_signal(signal)
    return _orders_response(signal.id, results)


@app.get("/positions")
async def list_positions() -> dict:
    """Every non-flat tracked position, across all accounts.

    This is this service's own record of what it has sent (see
    app/engine.py's "Close signals" docstring on why that can drift from
    the broker's real book on brokers that only confirm fills
    asynchronously), not a live read of any broker's account state.
    """
    return {"positions": store.list_open_positions(), "managed_lifecycles": _managed_lifecycle_snapshot()}


@app.get("/brokers")
async def list_broker_capabilities() -> dict:
    """Every registered broker's actual, code-verified capabilities — not a
    broker name or an imported SDK, which prove nothing on their own. Each
    flag is computed from whether the adapter overrides the base no-op
    (see app/brokers/base.py's "Capability introspection" section), so it
    can't silently drift out of sync with the code the way a separately
    maintained boolean could. Any account whose `broker` doesn't appear
    here, or whose entry there is `False` for what it needs, will be
    refused live admission by app/engine.py / app/lifecycle/manager.py
    rather than admitted with silently missing protection."""
    return {
        "brokers": [
            {
                "name": broker.name,
                "supports_native_bracket": broker.supports_native_bracket,
                "has_protective_stop_capability": broker.has_protective_stop_capability,
                "has_cancel_capability": broker.has_cancel_capability,
                "has_replace_stop_capability": broker.has_replace_stop_capability,
                "has_position_readback_capability": broker.has_position_readback_capability,
                "has_order_status_capability": broker.has_order_status_capability,
                "can_protect_a_managed_position": broker.can_protect_a_managed_position(),
            }
            for broker in brokers.values()
        ]
    }


@app.get("/providers")
async def list_provider_overrides() -> dict:
    """Every configured provider/analyst settings override (`config/providers.yaml`)
    and the effective settings it would resolve to for each of this
    service's destination accounts — so "what does this analyst's signal
    actually do to my sizing/protection here" is answerable without
    reading YAML and doing the account->provider->analyst merge by hand.
    See app/providers.py for the precedence rules."""
    result = []
    for provider in provider_registry.providers.values():
        entry = {
            "provider_id": provider.provider_id,
            "display_name": provider.display_name,
            "settings": vars(provider.settings),
            "analysts": [
                {"analyst_id": a.analyst_id, "display_name": a.display_name, "settings": vars(a.settings)}
                for a in provider.analysts.values()
            ],
            "effective_by_account": {},
        }
        for account_id, account in routing_config.accounts.items():
            account_defaults = SettingsOverride(
                multiplier=account.multiplier,
                fixed_quantity=account.fixed_quantity,
                managed_lifecycle=account.managed_lifecycle,
                enabled=account.enabled,
            )
            effective = provider_registry.effective_settings(account_defaults, provider.provider_id, None)
            entry["effective_by_account"][account_id] = vars(effective)
        result.append(entry)
    return {"providers": result}


def _managed_lifecycle_snapshot() -> list[dict]:
    """Coverage/deficit detail for every open `managed_lifecycle` position —
    the quantity-by-quantity picture app/lifecycle/manager.py's module
    docstring calls for (e.g. "54 shares remain, 47 have a confirmed
    working stop, a 7-share target remainder is cancellation-pending"),
    not just a protected/unprotected flag."""
    snapshot = []
    for lifecycle in lifecycle_manager.list_open_lifecycles():
        account_id, symbol = lifecycle.key
        pending = lifecycle.pending_exit
        snapshot.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "owned_quantity": lifecycle.confirmed_owned_quantity,
                "covered_quantity": lifecycle.covered_quantity,
                "uncovered_quantity": lifecycle.uncovered_quantity,
                "stop_status": lifecycle.stop.status.value,
                "stop_price": lifecycle.stop.broker_confirmed_price,
                "halted": lifecycle_manager.arbiter.is_halted(account_id, symbol),
                "halt_reason": lifecycle_manager.arbiter.halt_reason(account_id, symbol) or None,
                "pending_exit": None
                if pending is None
                else {
                    "broker_order_id": pending.broker_order_id,
                    "requested_quantity": pending.requested_quantity,
                    "confirmed_filled_quantity": pending.confirmed_filled_quantity,
                    "unresolved_remainder": pending.unresolved_remainder,
                    "phase": pending.phase.value,
                    "source": pending.source,
                },
            }
        )
    return snapshot


@app.get("/signals")
async def list_signals(limit: int = Query(default=50, le=500)) -> dict:
    """Most recently received signals, newest first."""
    return {"signals": store.list_recent_signals(limit=limit)}


@app.get("/orders")
async def list_orders(
    limit: int = Query(default=50, le=500), account_id: str | None = Query(default=None)
) -> dict:
    """Most recent order results, newest first — optionally filtered to one account."""
    return {"orders": store.list_recent_orders(limit=limit, account_id=account_id)}


class BacktestRequest(BaseModel):
    """See app/backtest/replay.py's module docstring for exactly what this
    does and doesn't simulate before trusting its output."""

    source: str
    symbol: str | None = None
    start: datetime
    end: datetime
    #: symbol -> local CSV path (columns: timestamp,open,high,low,close[,volume]).
    #: No vendor is wired in — see app/backtest/models.py's module docstring
    #: for why this project can't fetch historical bars for you.
    csv_paths: dict[str, str]
    max_hold_days: float = 30.0


@app.post("/backtest")
async def run_backtest(request: BacktestRequest) -> dict:
    """Replays historical signals (from `SignalStore`) against locally
    supplied OHLC data. Only signals SAVED AFTER the stop_loss/take_profit/
    analyst columns were added (see app/db.py's `_COLUMN_MIGRATIONS`) carry
    that data — older rows replay as NO_EXIT_LEVELS. This is a synchronous,
    in-process replay; no results are persisted (there's no Signal Backtests
    workspace yet, just this endpoint — see README.md's "Signal Backtester"
    section for the full list of what's still a documented gap)."""
    csv_paths = {symbol: Path(path) for symbol, path in request.csv_paths.items()}
    provider = CsvPriceHistoryProvider(csv_paths)
    engine = BacktestEngine(provider, max_hold=timedelta(days=request.max_hold_days))

    rows = store.list_signals_in_range(
        source=request.source, symbol=request.symbol, start=request.start, end=request.end
    )
    report = engine.run(rows)

    return {
        "summary": report.summary(),
        "trades": [
            {
                "signal_id": t.signal_id,
                "source": t.source,
                "symbol": t.symbol,
                "side": t.side.value,
                "analyst": t.analyst,
                "entry_time": t.entry_time.isoformat(),
                "entry_price": t.entry_price,
                "quantity": t.quantity,
                "stop_price": t.stop_price,
                "target_price": t.target_price,
                "outcome": t.outcome.value,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "note": t.note,
            }
            for t in report.trades
        ],
    }


def _orders_response(signal_id: str, results) -> dict:
    return {
        "signal_id": signal_id,
        "orders": [
            {"account_id": r.account_id, "status": r.status.value, "message": r.message}
            for r in results
        ],
    }
