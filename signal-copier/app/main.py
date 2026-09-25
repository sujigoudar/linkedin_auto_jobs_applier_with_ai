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

from fastapi import FastAPI, Form, Header, HTTPException, Request

from app import config
from app.brokers.alpaca import AlpacaBroker
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.ibkr import IBKRBroker
from app.brokers.mt4_mt5 import MT5Broker
from app.brokers.paper import PaperBroker
from app.brokers.signalstack import SignalStackBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.errors import SignalValidationError
from app.routing import load_routing_config
from app.sources.discord import DiscordSource
from app.sources.slack import SlackSource
from app.sources.sms_twilio import TwilioSMSSource
from app.sources.telegram import TelegramSource
from app.sources.twitter import TwitterSource
from app.sources.webhook import WebhookSource

logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)

routing_config = load_routing_config(config.ROUTING_CONFIG_PATH, config.ACCOUNTS_CONFIG_PATH)
store = SignalStore(config.DATABASE_PATH)

brokers = {
    "paper": PaperBroker(),
    "signalstack": SignalStackBroker(),
    "alpaca": AlpacaBroker(),
}
# These brokers need optional packages installed; only register them if
# available so the paper-only quickstart doesn't need every dependency.
for broker_name, broker_cls in [("ccxt", CCXTBroker), ("ibkr", IBKRBroker), ("mt4_mt5", MT5Broker)]:
    try:
        brokers[broker_name] = broker_cls()
    except RuntimeError as exc:
        logger.info("%s broker not registered: %s", broker_name, exc)

engine = SignalCopierEngine(routing=routing_config, brokers=brokers, store=store)
webhook_source = WebhookSource(on_signal=engine.handle_signal)
sms_source = TwilioSMSSource(on_signal=engine.handle_signal)

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await webhook_source.start()
    for source in _background_sources:
        try:
            await source.start()
        except Exception:  # noqa: BLE001 - one misconfigured source must not block the app
            logger.exception("failed to start source '%s'", source.name)

    yield

    for source in _background_sources:
        await source.stop()
    for broker_name in ("signalstack", "alpaca", "ccxt", "ibkr"):
        if broker_name in brokers:
            await brokers[broker_name].close()


app = FastAPI(title="Trading Signal Copier", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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


def _orders_response(signal_id: str, results) -> dict:
    return {
        "signal_id": signal_id,
        "orders": [
            {"account_id": r.account_id, "status": r.status.value, "message": r.message}
            for r in results
        ],
    }
