"""FastAPI entrypoint.

Wires up: config -> routing rules -> broker adapters -> engine -> the
webhook source's HTTP route. Other push-based sources (Slack, Twilio SMS)
would add their own routes here the same way; pull-based sources
(Telegram/Discord bots, MT4/5 bridge listeners) would be started as
background tasks in the lifespan handler below.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from app import config
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.paper import PaperBroker
from app.brokers.signalstack import SignalStackBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.routing import load_routing_config
from app.sources.webhook import SignalValidationError, WebhookSource

logging.basicConfig(level=config.LOG_LEVEL)
logger = logging.getLogger(__name__)

routing_config = load_routing_config(config.ROUTING_CONFIG_PATH, config.ACCOUNTS_CONFIG_PATH)
store = SignalStore(config.DATABASE_PATH)

brokers = {
    "paper": PaperBroker(),
    "signalstack": SignalStackBroker(),
}
# CCXTBroker requires the `ccxt` package; only register it if installed so
# the paper-only quickstart doesn't need every optional dependency.
try:
    brokers["ccxt"] = CCXTBroker()
except RuntimeError:
    logger.info("ccxt not installed; skipping CCXTBroker registration")

engine = SignalCopierEngine(routing=routing_config, brokers=brokers, store=store)
webhook_source = WebhookSource(on_signal=engine.handle_signal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await webhook_source.start()
    yield
    await brokers["signalstack"].close()
    if "ccxt" in brokers:
        await brokers["ccxt"].close()


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
    return {
        "signal_id": signal.id,
        "orders": [
            {"account_id": r.account_id, "status": r.status.value, "message": r.message}
            for r in results
        ],
    }
