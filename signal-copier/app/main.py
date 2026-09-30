"""FastAPI entrypoint.

Wires up: config -> routing rules -> broker adapters -> engine -> sources.
Push-based sources (webhook, SMS) get their own HTTP route below.
Pull-based sources (Telegram/Discord/Slack bots, Twitter stream) are only
started if their required env vars are set, and run as background tasks
started in the lifespan handler.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import hmac
import json
import logging
import os
import sqlite3
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import Cookie, Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from app import config
from app.auth import SESSION_COOKIE_NAME, RequireOwner, auth_configured, create_session, verify_password
from app.backtest.cost_stress import apply_cost_stress
from app.backtest.fit_simulator import simulate_provider_fit
from app.backtest.models import CsvPriceHistoryProvider
from app.backtest.replay import BacktestEngine, CapitalContentionReport
from app.brokers.alpaca import AlpacaBroker
from app.brokers.base import BrokerAdapter
from app.brokers.ccxt_broker import CCXTBroker, build_ccxt_brokers
from app.brokers.ibkr import IBKRBroker
from app.brokers.mt4_mt5 import MetaApiBroker, MT5Broker
from app.brokers.ninjatrader import NinjaTraderBroker
from app.brokers.oanda import OANDABroker
from app.brokers.robinhood import RobinhoodBroker
from app.brokers.schwab import SchwabBroker
from app.brokers.paper import PaperBroker
from app.brokers.rithmic import RithmicBroker
from app.brokers.signalstack import SignalStackBroker
from app.brokers.tastytrade import TastytradeBroker
from app.brokers.tradestation import TradeStationBroker
from app.brokers.tradovate import TradovateBroker
from app.config_admin import seed_from_yaml_if_empty
from app.context import fred as fred_context
from app.context import fx as fx_context
from app.context import sec_edgar
from app.db import SignalStore, alembic_code_head
from app.capital_allocator import confirmed_open_notional
from app.account_economics_v2 import compute_extended_account_economics
from app.economics import compute_account_economics
from app.equity_history import EquitySnapshotter
from app.execution_quality import compute_execution_quality
from app.engine import SignalCopierEngine
from app.statistics import compute_max_drawdown, compute_pairwise_correlation, compute_rolling_stats
from app.logging_config import configure_structlog
from app.metrics import render_metrics
from app.errors import SignalValidationError
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import ProtectionStatus
from app.models import AccountBalance, AssetClass, ManagementRecipe, Side, Signal, SourceEvent, SourceEventKind
from app.pricing import PriceMonitor
from app.qualification import QUALIFICATION_STATE_ORDER, QualificationError
from app.providers import SettingsOverride, load_provider_registry_from_store
from app.provider_scout import ProviderScout
from app.provider_value import compute_provider_value_report, compute_provider_value_report_from_episodes
from app.rate_limit import CATALOG_FIT_SIM_RATE_LIMIT, INGRESS_RATE_LIMIT, limiter
from app.reconciliation import OrderReconciler
from app.relay_scheduler import RelayScheduler
from app.services.catalog_fit_sim_auth import (
    CatalogFitSimSignatureMismatchError,
    InvalidCatalogFitSimSignatureHeaderError,
    StaleCatalogFitSimTimestampError,
    verify_catalog_fit_sim_signature,
)
from app.risk import size_for_account, symbol_for_account
from app.routing import RoutingConfig, RoutingRule, load_routing_config_from_store
from app.sources.text_parser import classify_batch, parse_text_signal
from app.sources.discord import DiscordSource
from app.sources.mt4_mt5 import MetaApiSource
from app.sources.ninjatrader import NinjaTraderSource
from app.sources.rithmic import RithmicSource
from app.sources.slack import SlackSource
from app.sources.base import SourceAdapter
from app.sources.sms_twilio import TwilioSMSSource
from app.sources.telegram import TelegramSource
from app.sources.telegram_user import TelegramUserSource
from app.telegram_collectors import ConnectionMode, TelegramCollectorError
from app.notification_bridge import (
    ContentCompleteness,
    NotificationBridgeError,
    content_fingerprint,
    generate_pairing_token,
    hash_pairing_token,
    validate_content_completeness,
    verify_pairing_token,
)
from app.sources.twitter import TwitterSource
from app.sources.webhook import WebhookSource
from app.sources.whatsapp import WhatsAppSource
from app.writer_lease import FencedOutError, WriterLeaseGuard, WriterLeaseHeldByAnotherSiteError

STATIC_DIR = Path(__file__).resolve().parent / "static"

logging.basicConfig(level=config.LOG_LEVEL)
configure_structlog()
logger = logging.getLogger(__name__)

store = SignalStore(config.DATABASE_PATH)
seed_from_yaml_if_empty(store)  # one-time: import existing config/*.yaml, then the database is live/authoritative
routing_config = load_routing_config_from_store(store)
provider_registry = load_provider_registry_from_store(store)

brokers = {
    "paper": PaperBroker(),
    "signalstack": SignalStackBroker(),
    "alpaca": AlpacaBroker(),
    "ninjatrader": NinjaTraderBroker(),
    "tradovate": TradovateBroker(),
    "oanda": OANDABroker(),
    "tradestation": TradeStationBroker(),
    "tastytrade": TastytradeBroker(),
}
# These brokers need optional packages installed (and, for Rithmic, connection
# credentials up front); only register them if available so the paper-only
# quickstart doesn't need every dependency.
_optional_brokers: list[tuple[str, Callable[[], BrokerAdapter]]] = [
    ("ibkr", lambda: IBKRBroker(config.IBKR_HOST, config.IBKR_PORT, config.IBKR_CLIENT_ID)),
    ("mt4_mt5", MT5Broker),
    ("mt4_mt5_metaapi", MetaApiBroker),
]
if config.CCXT_EXCHANGES:
    # Multiple simultaneous exchanges (e.g. CCXT_EXCHANGES="binance,kraken"):
    # one broker per exchange, named "ccxt_<exchange_id>" -- see
    # build_ccxt_brokers's own docstring and config.py's CCXT_EXCHANGES
    # docstring for why this exists alongside the single-exchange "ccxt"
    # broker below. build_ccxt_brokers constructs every CCXTBroker
    # eagerly (unlike every other entry in this list, which is a lazy
    # factory) -- caught here, not inside the per-broker loop below,
    # so a missing `ccxt` package skips the whole group the same way
    # every other optional broker skips on its own missing dependency,
    # rather than crashing this module's own import.
    try:
        for _name, _instance in build_ccxt_brokers(config.CCXT_EXCHANGES, config.CCXT_SANDBOX).items():
            brokers[_name] = _instance
    except RuntimeError as _exc:
        logger.info("ccxt brokers not registered: %s", _exc)
else:
    _optional_brokers.append(("ccxt", lambda: CCXTBroker(config.CCXT_EXCHANGE_ID, config.CCXT_SANDBOX)))
if config.RITHMIC_USER:
    _optional_brokers.append(
        (
            "rithmic",
            lambda: RithmicBroker(
                config.RITHMIC_USER, config.RITHMIC_PASSWORD, config.RITHMIC_SYSTEM_NAME, config.RITHMIC_GATEWAY_URL
            ),
        )
    )
# See app/brokers/schwab.py's own module docstring and config.py's own
# SCHWAB_ACKNOWLEDGE_NO_SANDBOX docstring: Schwab has no sandbox/paper
# environment at all, unlike every other broker in this registry -- this
# one is deliberately gated behind an explicit acknowledgement on top of
# having its credentials set, not just credentials alone.
if config.SCHWAB_ACKNOWLEDGE_NO_SANDBOX:
    _optional_brokers.append(("schwab", SchwabBroker))
else:
    logger.info(
        "schwab broker not registered: SCHWAB_ACKNOWLEDGE_NO_SANDBOX is not set "
        "(Schwab has no sandbox -- see app/brokers/schwab.py's own module docstring)"
    )
# See app/brokers/robinhood.py's own module docstring and config.py's own
# ROBINHOOD_ACKNOWLEDGE_TOS_RISK docstring: Robinhood has no official
# trading API, no sandbox, and automating trades against it is outside
# Robinhood's own Terms of Service -- gated the same way Schwab is above,
# behind an explicit acknowledgement on top of having credentials set.
if config.ROBINHOOD_ACKNOWLEDGE_TOS_RISK:
    _optional_brokers.append(("robinhood", RobinhoodBroker))
else:
    logger.info(
        "robinhood broker not registered: ROBINHOOD_ACKNOWLEDGE_TOS_RISK is not set "
        "(no official API, no sandbox, outside Robinhood's own ToS -- see "
        "app/brokers/robinhood.py's own module docstring)"
    )
for broker_name, broker_factory in _optional_brokers:
    try:
        brokers[broker_name] = broker_factory()
    except RuntimeError as exc:
        logger.info("%s broker not registered: %s", broker_name, exc)

# Cross-process/cross-host single-writer fencing (see app/writer_lease.py,
# docs/FAILOVER.md). Constructed unconditionally (even for a STANDBY_MODE
# process, which never acquires a lease on it at all -- see below) so
# `engine`/`lifecycle_manager`'s own construction is identical either way.
writer_lease_guard = WriterLeaseGuard(
    store,
    site_id=config.WRITER_SITE_ID or None,
    lease_seconds=config.WRITER_LEASE_SECONDS,
)
if not config.STANDBY_MODE:
    # Acquired here, at import time -- not only inside `lifespan` below --
    # so every command-execution path is already covered the moment this
    # module exists, independent of whether/when an ASGI server's own
    # lifespan startup actually runs (uvicorn always runs it once for a
    # real deployment; a test harness building `TestClient(app)` WITHOUT
    # entering it as a context manager, which many of this repo's own
    # tests do, never triggers `lifespan` at all -- those requests must
    # still be correctly fenced/covered). `WriterLeaseGuard.acquire()` is
    # idempotent per guard instance (see its own docstring) -- a real
    # process restart still gets a brand-new guard object here and
    # genuinely reacquires/bumps the token; `lifespan`'s own call to the
    # same guard object below is then just a cheap, harmless re-verify,
    # not a second real acquisition. Raises WriterLeaseHeldByAnotherSiteError
    # here (crashing import, and so startup) if a different site already
    # holds the lease -- see that error's docstring.
    writer_lease_guard.acquire()
lifecycle_manager = PositionLifecycleManager(brokers=brokers, store=store)
lifecycle_manager.restore_from_store()  # resume any managed-lifecycle positions from before a restart
engine = SignalCopierEngine(
    routing=routing_config,
    brokers=brokers,
    store=store,
    lifecycle_manager=lifecycle_manager,
    provider_registry=provider_registry,
    lease_guard=writer_lease_guard,
)
webhook_source = WebhookSource(on_signal=engine.handle_signal, on_source_event=engine.export_source_event)
sms_source = TwilioSMSSource(on_signal=engine.handle_signal)
whatsapp_source = WhatsAppSource(on_signal=engine.handle_signal)
ninjatrader_source = NinjaTraderSource(on_signal=engine.handle_signal)
reconciler = OrderReconciler(
    store=store,
    brokers=brokers,
    interval_seconds=config.RECONCILE_INTERVAL_SECONDS,
    lifecycle_manager=lifecycle_manager,
    capital_allocator=engine.capital_allocator,
)
price_monitor = PriceMonitor(
    lifecycle_manager=lifecycle_manager,
    brokers=brokers,
    interval_seconds=config.PRICE_MONITOR_INTERVAL_SECONDS,
)
provider_scout = ProviderScout(
    store=store,
    interval_seconds=config.PROVIDER_SCOUT_INTERVAL_SECONDS,
    min_sample_size=config.PROVIDER_VALUE_MIN_SAMPLE_SIZE,
    win_rate_threshold=config.PROVIDER_VALUE_WIN_RATE_THRESHOLD,
    profit_factor_threshold=config.PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD,
)
equity_snapshotter = EquitySnapshotter(
    store=store,
    lifecycle_manager=lifecycle_manager,
    interval_seconds=config.EQUITY_SNAPSHOT_INTERVAL_SECONDS,
)
relay_scheduler = RelayScheduler(store=store, interval_seconds=config.RELAY_POLL_INTERVAL_SECONDS)

# Pull-based sources only start if fully configured via env vars.
_background_sources: list[SourceAdapter] = []
if config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID:
    _background_sources.append(
        TelegramSource(
            engine.handle_signal,
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID,
            on_source_event=engine.export_source_event,
        )
    )
if config.DISCORD_BOT_TOKEN and config.DISCORD_CHANNEL_ID:
    _background_sources.append(
        DiscordSource(engine.handle_signal, config.DISCORD_BOT_TOKEN, int(config.DISCORD_CHANNEL_ID))
    )
# Track 5: every registered user_account collector in the persistent
# registry (app/telegram_collectors.py) -- unlike every other pull-based
# source above, this list is data-driven (one row per collector, not one
# fixed env-var pair), so it's read from the registry rather than a
# single config.* check. A collector whose credential_env_var isn't set
# in THIS process's environment is simply never started -- see
# app/telegram_collectors.py's CollectorHealth.MISSING_CREDENTIALS for
# how that's surfaced instead of silently pretending to be healthy.
if config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH:
    for _collector in store.list_telegram_collectors():
        if _collector["connection_mode"] != ConnectionMode.USER_ACCOUNT.value:
            continue
        _session_path = os.environ.get(_collector["credential_env_var"])
        if not _session_path:
            store.update_telegram_collector_health(
                _collector["id"], "missing_credentials",
                detail=f"env var {_collector['credential_env_var']!r} is not set in this process's environment",
            )
            continue
        _background_sources.append(
            TelegramUserSource(
                engine.handle_signal,
                collector_id=_collector["id"],
                chat_id=_collector["chat_id"],
                topic_id=_collector["topic_id"],
                api_id=int(config.TELEGRAM_API_ID),
                api_hash=config.TELEGRAM_API_HASH,
                session_path=_session_path,
                on_source_event=engine.export_source_event,
                registry=store,
                save_historical_signal=store.save_signal,
            )
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


async def _writer_lease_heartbeat() -> None:
    """Background renewal loop for the ACTIVE writer's own lease -- see
    app/writer_lease.py's module docstring. Renewing does NOT gate any
    command execution itself (that's `require_active()`, checked
    independently on every command-execution path) -- this loop exists
    so a genuinely healthy writer's `expires_at` keeps advancing, so a
    promotion attempt elsewhere correctly sees "still valid" rather than
    treating a merely-slow-to-be-declared-dead writer as safe to promote
    over. If renewal ever fails (this process has been fenced out -- a
    new token was issued elsewhere), this logs and keeps running: it does
    NOT try to re-acquire (that would BE silent, unauthorized
    self-promotion) -- `require_active()` on the next real command is
    what actually stops this process from executing anything further."""
    while True:
        await asyncio.sleep(config.WRITER_LEASE_RENEW_SECONDS)
        try:
            writer_lease_guard.renew()
        except FencedOutError:
            logger.critical(
                "writer lease heartbeat: this process (site=%s holder=%s) has been FENCED OUT -- "
                "a new writer lease token exists. This process must not execute any further "
                "financial commands (already enforced independently by every command-execution "
                "path's own require_active() check) -- see docs/FAILOVER.md.",
                writer_lease_guard.site_id,
                writer_lease_guard.holder_id,
            )
        except Exception:  # noqa: BLE001 - a heartbeat failure must not crash the loop or the app
            logger.exception("writer lease heartbeat failed (will retry next interval)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.STANDBY_MODE:
        # A standby serves the read-only status/health surface only -- it must
        # not ingest signals, poll broker order status, or resize/replace a
        # protective stop, all of which are things only the single active
        # writer may do (see deploy/RUNBOOK.md). This is enforced here, not
        # merely by omitting broker credentials, so a misconfigured standby
        # can't silently become a second writer. A standby never touches
        # writer_lease_guard at all -- it neither acquires nor renews a
        # lease, so it can never itself be "the writer" from the fencing
        # table's own point of view either.
        logger.warning("STANDBY_MODE is set -- not starting signal ingestion, reconciliation, or price polling")
        yield
        return

    # Cross-process/cross-host fencing (app/writer_lease.py): acquired
    # BEFORE any source/reconciler/price-monitor starts, so this process
    # can reach zero command-execution paths before it holds a lease this
    # database agrees is current. Raises WriterLeaseHeldByAnotherSiteError
    # (uncaught here, deliberately -- crashes startup) if a DIFFERENT
    # site already holds the lease: this is the actual enforcement that a
    # second, misconfigured-as-active host can never start trading the
    # same account "merely because the first heartbeat disappeared" --
    # see that error's own docstring. The one and only way past this is
    # the explicit `python -m app.promote_cli` action.
    try:
        writer_lease_guard.acquire()
    except WriterLeaseHeldByAnotherSiteError:
        logger.critical(
            "refusing to start as active writer: the writer lease is already held by a different "
            "site. See docs/FAILOVER.md -- this is not auto-resolved; run `python -m app.promote_cli` "
            "deliberately if this really is a failover."
        )
        raise
    _heartbeat_task = asyncio.create_task(_writer_lease_heartbeat())

    await webhook_source.start()
    for source in _background_sources:
        try:
            await source.start()
        except Exception:  # noqa: BLE001 - one misconfigured source must not block the app
            logger.exception("failed to start source '%s'", source.name)
    await reconciler.start()
    await price_monitor.start()
    await provider_scout.start()
    await equity_snapshotter.start()
    if config.RELAY_INGRESS_URL:
        # Same "pull-based, only starts if fully configured" convention
        # as TelegramSource/DiscordSource/etc. above -- a deployment with
        # no commercial platform to export to gets no relay loop at all,
        # not a loop that spins forever raising RelayNotConfiguredError.
        await relay_scheduler.start()
    else:
        logger.info("RELAY_INGRESS_URL is not set -- relay scheduler not started")

    yield

    _heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _heartbeat_task
    if config.RELAY_INGRESS_URL:
        await relay_scheduler.stop()
    await equity_snapshotter.stop()
    await provider_scout.stop()
    await price_monitor.stop()
    await reconciler.stop()
    for source in _background_sources:
        await source.stop()
    # Every registered broker, not a hand-maintained subset: BrokerAdapter.close()
    # is a safe no-op by default (see its docstring), so this used to silently
    # skip whichever brokers weren't named here -- MT5Broker ("mt4_mt5") was
    # missing, leaking its MetaTrader5 connections on every shutdown.
    for broker in brokers.values():
        await broker.close()


app = FastAPI(title="Trading Signal Copier", lifespan=lifespan)

# C06: HTTP abuse controls on the ingress routes -- see app/rate_limit.py's
# module docstring for exactly what this does and doesn't cover.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]  # slowapi's handler is typed narrower (RateLimitExceeded, not the generic Exception Starlette expects) than the real, correct runtime behavior needs
app.add_middleware(SlowAPIMiddleware)

# C12/C13: locally-pinned Chart.js/Tabulator vendor files (no CDN, no build
# step) for the dashboard's charts/tables -- see app/static/vendor/README.md
# for exact pinned versions. Public, unauthenticated: these are static
# library files, not application data, same trust level as the dashboard
# HTML/JS itself.
app.mount("/static/vendor", StaticFiles(directory=STATIC_DIR / "vendor"), name="vendor")

# TR-01..TR-04 (and every later TR-0x batch's) hash-routed views: the
# shared router/state-matrix helpers and each screen's own view module
# (app/static/router.js, app/static/state-matrix.js, app/static/views/*.js)
# -- same trust level as the vendor files above (public, static JS the
# dashboard itself already ships unauthenticated) and same "no build
# step" convention (see dashboard.html's own module docstring in
# app/main.py's `dashboard()`). Registered AFTER the narrower
# `/static/vendor` mount above so a `/static/vendor/...` request keeps
# matching that mount first; this broader one only catches everything
# else under `/static/` (including `/static/dashboard.html` itself,
# harmlessly served twice alongside `GET /`, since it's the same public,
# no-secrets page either way).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


#: Session-only routes -- creating/destroying a browser session, never a
#: financial effect. A standby must still let its owner log in to inspect
#: its read-only data (see deploy/RUNBOOK.md's "before promotion" checks,
#: and DEP-08: a blanket non-GET block also blocked this). Every other
#: mutating route stays refused.
_STANDBY_ALLOWED_WRITE_PATHS = {"/auth/login", "/auth/logout"}


@app.middleware("http")
async def _standby_read_only_gate(request: Request, call_next):
    """Independent of any route's own logic: in STANDBY_MODE, refuse every
    request that isn't a plain read (GET/HEAD/OPTIONS) -- or one of the
    narrow session-only exceptions above -- with 503, before it reaches any
    handler. A release guard that denies effects, not a convention every
    endpoint has to remember to honor -- see deploy/RUNBOOK.md on why a
    restored/copied config flag must never be what makes a standby a
    writer."""
    if (
        config.STANDBY_MODE
        and request.method not in ("GET", "HEAD", "OPTIONS")
        and request.url.path not in _STANDBY_ALLOWED_WRITE_PATHS
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": "standby mode: read-only, no financial commands accepted"},
        )
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Defense in depth alongside escaping every untrusted value the
    dashboard renders (see SEC-01/app/static/dashboard.html): even a
    rendering bug this doesn't catch can't load or connect to anything
    off-origin, frame this app, or run a plugin/object payload. This does
    not replace correct escaping -- 'unsafe-inline' is still needed for the
    dashboard's existing inline <script>/<style> blocks."""
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


require_owner = RequireOwner(lambda: store)
require_owner_read = RequireOwner(lambda: store, require_csrf=False)  # GET-only routes: session, no CSRF needed


@app.get("/health")
async def health() -> dict:
    """Public, minimal, and truthful: liveness (this response happened at
    all) plus whether the background workers that actually keep positions
    protected are making progress -- no account IDs, balances, or other
    private data belongs here (see docs on why this stays unauthenticated).
    `*_ok` is False both when a worker hasn't completed a pass recently
    (stuck/dead task) and before its very first pass after startup."""
    now = datetime.now(timezone.utc)

    def _fresh(last_success: datetime | None, interval_seconds: float) -> bool:
        if last_success is None:
            return False
        return (now - last_success).total_seconds() < max(interval_seconds * 3, interval_seconds + 30)

    try:
        store.get_position("__healthcheck__", "__healthcheck__")
        db_ok = True
    except Exception:  # noqa: BLE001 - health check must never raise
        db_ok = False

    price_monitor_ok = _fresh(price_monitor.last_success_at, config.PRICE_MONITOR_INTERVAL_SECONDS)
    reconciler_ok = _fresh(reconciler.last_success_at, config.RECONCILE_INTERVAL_SECONDS)
    # Informational only -- deliberately NOT part of the overall `status`
    # gate below. Unlike price_monitor/reconciler, nothing about position
    # protection depends on this job; its interval also defaults to a full
    # day, so gating overall health on it would report "degraded" for
    # hours after every fresh install/restart despite nothing being wrong.
    provider_scout_ok = _fresh(provider_scout.last_success_at, config.PROVIDER_SCOUT_INTERVAL_SECONDS)
    # Same "informational only" reasoning as provider_scout_ok -- a missed
    # or delayed equity snapshot pass doesn't affect position protection,
    # so it must never drag down overall `status`.
    equity_snapshotter_ok = _fresh(equity_snapshotter.last_success_at, config.EQUITY_SNAPSHOT_INTERVAL_SECONDS)
    # Same "informational only" reasoning as provider_scout_ok above: a
    # deployment with no RELAY_INGRESS_URL never starts this scheduler at
    # all (see `lifespan`), so it would report perpetually "not fresh"
    # and falsely degrade overall status for something that was never
    # meant to run here.
    relay_ok = (
        _fresh(relay_scheduler.last_success_at, config.RELAY_POLL_INTERVAL_SECONDS)
        if config.RELAY_INGRESS_URL
        else None
    )
    # INT-040: a real, live storage-ceiling check against the private
    # export outbox's own real backlog (SignalStore.export_outbox_backlog
    # -- SUM(LENGTH(envelope_json)) over undelivered rows, never an
    # estimate). Deliberately NOT folded into the critical `status` gate
    # above alongside database_ok/price_monitor_ok/reconciler_ok: an
    # over-ceiling backlog does not itself compromise this instance's
    # position protection (the same reasoning `provider_scout_ok`/
    # `equity_snapshotter_ok`/`relay_ok` already document) -- it is a
    # slower-building storage/export-reliability risk, surfaced honestly
    # here and on TR-16's Storage row rather than silently, but never
    # allowed to mask (or be masked by) whether positions are actually
    # protected right now. False both on a real ceiling breach and if the
    # backlog query itself fails (e.g. database unreachable) -- the same
    # "can't verify" convention `database_ok` above already uses.
    try:
        outbox_row_count, outbox_backlog_bytes = store.export_outbox_backlog()
        outbox_backlog_ok = outbox_backlog_bytes < config.EXPORT_OUTBOX_SIZE_CEILING_BYTES
    except Exception:  # noqa: BLE001 - health check must never raise
        outbox_row_count, outbox_backlog_bytes = None, None
        outbox_backlog_ok = False
    # Cross-process/cross-host fencing (app/writer_lease.py, docs/FAILOVER.md):
    # None for a standby (it never holds a lease -- see `lifespan`); for the
    # active writer, True only if this process's own fencing token is still
    # the current one -- False means it's been fenced out (a new token was
    # issued elsewhere) and every command-execution path is already
    # refusing to act, independent of this flag. No site_id/holder_id/token
    # exposed here (this endpoint is unauthenticated) -- see /metrics for
    # authenticated operational detail.
    writer_lease_ok: bool | None = None
    if not config.STANDBY_MODE:
        try:
            writer_lease_guard.require_active()
            writer_lease_ok = True
        except Exception:  # noqa: BLE001 - health check must never raise
            writer_lease_ok = False
    return {
        # OPS-01: `status` was hardcoded to "ok" regardless of the flags
        # right next to it -- a fresh startup (before either worker's
        # first successful pass) or a genuinely stuck worker still
        # reported "ok" overall while its own detail flag said otherwise.
        "status": "ok" if (db_ok and price_monitor_ok and reconciler_ok and writer_lease_ok is not False) else "degraded",
        "database_ok": db_ok,
        "price_monitor_ok": price_monitor_ok,
        "reconciler_ok": reconciler_ok,
        "provider_scout_ok": provider_scout_ok,
        "equity_snapshotter_ok": equity_snapshotter_ok,
        "relay_ok": relay_ok,
        "outbox_backlog_ok": outbox_backlog_ok,
        "outbox_backlog_bytes": outbox_backlog_bytes,
        "outbox_backlog_row_count": outbox_row_count,
        "outbox_backlog_ceiling_bytes": config.EXPORT_OUTBOX_SIZE_CEILING_BYTES,
        "writer_lease_ok": writer_lease_ok,
    }


@app.get("/metrics")
async def metrics(_owner: dict = Depends(require_owner_read)) -> Response:
    """C23/E10: private aggregate operational metrics (Prometheus text
    format) -- owner-session protected, unlike /health, since these numbers
    (pending-order counts, protection deficits) are operational detail an
    anonymous caller has no business reading. See app/metrics.py for
    exactly what is and isn't tracked."""
    body = render_metrics(
        store=store,
        price_monitor=price_monitor,
        reconciler=reconciler,
        lifecycle_manager=lifecycle_manager,
    )
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/system/info")
async def system_info(_owner: dict = Depends(require_owner_read)) -> dict:
    """TR-16: private, owner-gated operational facts safe to surface in
    the UI -- deliberately narrow. Every field here was checked one by
    one against app/config.py's `_Settings` and is either a non-secret
    operational fact (an interval, a feature flag, a non-credential
    label) or a boolean ABOUT a secret (is one configured, which kind)
    rather than the secret's own value. No API key, token, password,
    password hash, webhook secret, or session secret is ever read here --
    see this batch's report for the field-by-field check.

    `schema_version`/`schema_head` are real, live evidence (E01 bounded):
    `schema_version` is this exact database file's own stamped Alembic
    revision (`SignalStore.schema_version`), `schema_head` is what the
    currently-deployed code's own migration scripts expect
    (`alembic_code_head`) -- equal means this database's schema is
    reproducible from this exact release; unequal means a migration is
    pending. This is the one real "deployment reproducibility" signal
    this codebase has; it is NOT a data backup/snapshot record (see
    deploy/litestream/litestream.yml and deploy/RUNBOOK.md for this
    project's actual real backup mechanism -- Litestream replicating the
    live SQLite WAL to off-site object storage -- which runs as a
    separate process this API has no live status/API into, so no
    last-replicated-at timestamp is fabricated here)."""
    return {
        "standby_mode": config.STANDBY_MODE,
        "relay_environment": config.RELAY_ENVIRONMENT,
        "relay_evidence_class": config.RELAY_EVIDENCE_CLASS,
        "relay_producer_id": config.RELAY_PRODUCER_ID,
        "relay_ingress_configured": bool(config.RELAY_INGRESS_URL),
        "reconcile_interval_seconds": config.RECONCILE_INTERVAL_SECONDS,
        "price_monitor_interval_seconds": config.PRICE_MONITOR_INTERVAL_SECONDS,
        "provider_scout_interval_seconds": config.PROVIDER_SCOUT_INTERVAL_SECONDS,
        "equity_snapshot_interval_seconds": config.EQUITY_SNAPSHOT_INTERVAL_SECONDS,
        "auth_configured": auth_configured(),
        "owner_credential_kind": (
            "hashed (OWNER_PASSWORD_HASH)"
            if config.OWNER_PASSWORD_HASH
            else ("plain (OWNER_PASSWORD)" if config.OWNER_PASSWORD else "none configured")
        ),
        "session_ttl_seconds": config.SESSION_TTL_SECONDS,
        "force_secure_cookies": config.FORCE_SECURE_COOKIES,
        "schema_version": store.schema_version(),
        "schema_head": alembic_code_head(),
    }


def _readiness_freshness(last_success: datetime | None, interval_seconds: float, now: datetime) -> bool:
    if last_success is None:
        return False
    return (now - last_success).total_seconds() < max(interval_seconds * 3, interval_seconds + 30)


def _compute_readiness_rollup(
    *,
    standby_mode: bool,
    health_ok: bool,
    health_status: str | None,
    trading_authority_status: str,
    data_readiness_status: str,
    market_data_status: str,
    protection_status: str,
    outbox_backlog_ok: bool | None,
    provider_scout_ok: bool,
    equity_snapshotter_ok: bool,
    relay_down: bool,
) -> dict:
    """P0-8: the overall ACTIVE/STANDBY/DEGRADED/NOT READY rollup, computed
    strictly from the independent dimensions below it -- never a separate
    green/red flag of its own. This is deliberately a ROLLUP: it never
    replaces the individual dimensions in the response (each one stays in
    the payload and must be rendered on its own), and gate order below
    fails closed -- an unconfirmed stop or an absent trading authority
    blocks ACTIVE regardless of how healthy every other dimension looks,
    which is the exact "reachable does not mean ready" gap this batch
    closes (see this module's own audit reference in system_readiness's
    docstring)."""
    if standby_mode:
        return {
            "label": "STANDBY",
            "tone": "neutral",
            "reason": "STANDBY_MODE=true -- this instance deliberately does not ingest signals, reconcile orders, or poll prices.",
        }
    if not health_ok:
        return {"label": "NOT READY", "tone": "crit", "reason": "GET /health was unreachable this cycle -- nothing below can be verified live."}
    if health_status != "ok":
        return {
            "label": "NOT READY",
            "tone": "crit",
            "reason": f'GET /health reports status="{health_status}" -- at least one of database_ok/price_monitor_ok/reconciler_ok is false.',
        }
    if trading_authority_status == "not_held":
        return {
            "label": "NOT READY",
            "tone": "crit",
            "reason": "This instance does not currently hold trading authority -- see the Trading authority dimension.",
        }
    if protection_status == "gap":
        return {
            "label": "NOT READY",
            "tone": "crit",
            "reason": "At least one open managed-lifecycle position has an unconfirmed stop -- see the Protection readiness dimension.",
        }
    degraded_reasons = []
    if data_readiness_status in ("unknown", "partial"):
        degraded_reasons.append("account balance/buying-power data is not fully verified this cycle")
    if market_data_status in ("unknown", "stale"):
        degraded_reasons.append("market-data (price) freshness is not current")
    if protection_status == "stale":
        degraded_reasons.append("protection-confirmation state is not current")
    outbox_over_ceiling = outbox_backlog_ok is False
    if not provider_scout_ok or not equity_snapshotter_ok or relay_down or outbox_over_ceiling:
        degraded_reasons.append("an informational-only worker (provider scout/equity snapshotter/relay) or the export outbox is degraded")
    if degraded_reasons:
        return {"label": "DEGRADED", "tone": "warn", "reason": "; ".join(degraded_reasons) + "."}
    return {"label": "ACTIVE", "tone": "ok", "reason": "Every readiness dimension is current, and this instance holds trading authority."}


@app.get("/system/readiness")
async def system_readiness(_owner: dict = Depends(require_owner_read)) -> dict:
    """P0-8 (external release audit): "'Reachable' must not mean 'ready.'"
    -- GET /health and GET /system/info conflated "is this process/worker
    reachable" with "is this account/system actually ready to trade."
    This endpoint splits that into independent, separately-rendered
    dimensions (TR-16 renders each as its own row, never folded into one
    badge -- see tr16.js):

    - `liveness`: is this process/its database probe responding at all
      (heartbeat-level; NOT the same as any account's data being fresh).
    - `data_readiness`: per configured account, was a LIVE broker balance
      (cash/equity/buying_power) read successfully THIS cycle -- honestly
      `not_tracked` for a broker with no verified balance capability
      (`BrokerAdapter.has_balance_capability`), `unknown` for one that
      has the capability but failed/returned nothing this cycle. A
      service can be fully reachable (liveness=up) while this is
      `unknown` -- e.g. a broker session that authenticates but reports
      no account fields -- and both facts must render, never collapsed.
    - `market_data_readiness`: PriceMonitor's own real freshness
      (`price_monitor.last_success_at` against `PRICE_MONITOR_INTERVAL_
      SECONDS`, the same signal GET /health's `price_monitor_ok` uses),
      separated out because "is the broker connection reachable" and "is
      current price flowing for what this account trades" are different
      questions GET /health folded into one boolean.
    - `trading_authority`: whether this process currently holds a valid
      writer lease. This build has no fencing/lease mechanism yet -- only
      `STANDBY_MODE`, a static config flag, distinguishes role -- so this
      is honestly `not_held` while standby (a real signal) or `not_tracked`
      while configured as writer (a config assertion, not a live fenced
      lease). FOLLOW-UP: replace the `not_tracked`/writer-role branch with
      a real fencing-token/lease-expiry check once the P0-6 fencing/lease
      work lands; this field's shape (`status`, `reason`, `fencing_token`)
      is deliberately left room for that without a breaking change.
    - `protection_readiness`: for open managed-lifecycle positions, is
      stop/target protection state both CONFIRMED (`stop_gap_count`, GET
      /positions' own real aggregate) and CURRENT -- current meaning
      OrderReconciler's broker cross-check (`reconciler_ok`) is itself
      fresh, since a confirmed-looking stop_status this process can no
      longer cross-check against the broker is not the same as one that
      genuinely still is confirmed. `not_tracked` when no managed-
      lifecycle position is open at all (nothing to protect).
    - `release_status`: the qualification/release-approval state for this
      deployment. No qualification/release-approval taxonomy exists yet
      in this build. FOLLOW-UP: integrate with the P0-7 qualification/
      release-state work once it lands; until then this is honestly
      `not_tracked`, never a fabricated "approved."

    `rollup` folds all of the above into the existing ACTIVE/STANDBY/
    DEGRADED/NOT READY label (see `_compute_readiness_rollup`) -- it is a
    ROLLUP of the dimensions above, not a replacement for them; TR-16
    keeps every dimension visible in its own row even when the rollup
    reads ACTIVE, which is the entire point of this endpoint."""
    now = datetime.now(timezone.utc)
    health_body = await health()
    health_ok = True  # this function call cannot itself fail to respond the way an HTTP round-trip could

    # --- liveness ---
    liveness: dict[str, Any] = {
        "status": "up" if health_body["database_ok"] else "degraded",
        "reason": (
            "This process answered this request and its database probe (store.get_position) succeeded."
            if health_body["database_ok"]
            else "This process answered this request, but its own database probe raised -- the process is UP but its data store is not reachable."
        ),
    }

    # --- trading_authority (placeholder pending P0-6 fencing/lease integration) ---
    trading_authority: dict[str, Any]
    if config.STANDBY_MODE:
        trading_authority = {
            "status": "not_held",
            "reason": "STANDBY_MODE=true -- this instance deliberately does not act as writer (app/main.py's _standby_read_only_gate); no trade-affecting action is available here regardless of any other dimension.",
            "fencing_token": None,
        }
    else:
        trading_authority = {
            "status": "not_tracked",
            "reason": (
                "This build has no writer-lease/fencing-token mechanism yet -- only STANDBY_MODE (a static config flag) distinguishes role. "
                "This instance is configured as the active writer, but that is a config assertion, not a live, fenced lease. "
                "FOLLOW-UP: integrate the P0-6 fencing/lease work once it lands so this can report a real 'held' state instead."
            ),
            "fencing_token": None,
        }

    # --- market_data_readiness ---
    price_last_success = price_monitor.last_success_at
    price_age_seconds = (now - price_last_success).total_seconds() if price_last_success else None
    market_data_readiness: dict[str, Any]
    if price_last_success is None:
        market_data_readiness = {
            "status": "unknown",
            "reason": "PriceMonitor has not completed a successful pass since this process started.",
            "age_seconds": None,
        }
    elif _readiness_freshness(price_last_success, config.PRICE_MONITOR_INTERVAL_SECONDS, now):
        market_data_readiness = {
            "status": "fresh",
            "reason": "PriceMonitor's last successful pass is within its configured freshness window.",
            "age_seconds": price_age_seconds,
        }
    else:
        market_data_readiness = {
            "status": "stale",
            "reason": "PriceMonitor's last successful pass is older than its configured freshness window -- price-driven protection (targets/trailing/stop resizing) may not reflect the current market.",
            "age_seconds": price_age_seconds,
        }

    # --- data_readiness: a LIVE per-account balance read, honestly bounded ---
    account_rows: list[dict] = []
    for account_id, account in routing_config.accounts.items():
        broker = brokers.get(account.broker)
        if broker is None:
            account_rows.append({"account_id": account_id, "status": "unknown", "reason": f"no broker adapter registered for '{account.broker}'"})
            continue
        if not broker.has_balance_capability:
            account_rows.append({"account_id": account_id, "status": "not_tracked", "reason": f"{broker.name} adapter has no verified get_account_balance implementation."})
            continue
        try:
            balance = await broker.get_account_balance(account)
        except Exception as exc:  # noqa: BLE001 - readiness check must never raise
            account_rows.append({"account_id": account_id, "status": "unknown", "reason": f"live balance read raised: {exc}"})
            continue
        if balance is None or (balance.cash is None and balance.buying_power is None and balance.equity is None):
            account_rows.append({"account_id": account_id, "status": "unknown", "reason": "broker responded with no usable balance field this cycle."})
            continue
        account_rows.append(
            {
                "account_id": account_id,
                "status": "fresh",
                "reason": "live balance read succeeded this cycle.",
                "cash": balance.cash,
                "buying_power": balance.buying_power,
                "equity": balance.equity,
            }
        )

    data_readiness: dict[str, Any]
    if not account_rows:
        data_readiness = {"status": "not_tracked", "reason": "No accounts are configured.", "accounts": account_rows}
    elif all(r["status"] == "fresh" for r in account_rows):
        data_readiness = {
            "status": "fresh",
            "reason": "Every configured account's balance/buying-power was read live and successfully this cycle.",
            "accounts": account_rows,
        }
    elif any(r["status"] == "fresh" for r in account_rows):
        data_readiness = {
            "status": "partial",
            "reason": "At least one configured account's balance/buying-power could not be verified this cycle -- see the per-account detail.",
            "accounts": account_rows,
        }
    else:
        data_readiness = {
            "status": "unknown",
            "reason": "No configured account's balance/buying-power could be verified this cycle.",
            "accounts": account_rows,
        }

    # --- protection_readiness ---
    managed_lifecycles = _managed_lifecycle_snapshot()
    open_managed = [lc for lc in managed_lifecycles if lc["owned_quantity"] > 0]
    stop_gap_count = sum(1 for lc in open_managed if lc["stop_status"] != ProtectionStatus.STOP_CONFIRMED.value)
    reconciler_ok = _readiness_freshness(reconciler.last_success_at, config.RECONCILE_INTERVAL_SECONDS, now)
    protection_readiness: dict[str, Any]
    if not open_managed:
        protection_readiness = {"status": "not_tracked", "reason": "No open managed-lifecycle position exists right now.", "stop_gap_count": 0}
    elif stop_gap_count > 0:
        protection_readiness = {
            "status": "gap",
            "reason": f"{stop_gap_count} open managed-lifecycle position(s) have an unconfirmed stop (GET /positions' stop_status).",
            "stop_gap_count": stop_gap_count,
        }
    elif not reconciler_ok:
        protection_readiness = {
            "status": "stale",
            "reason": "Every open managed-lifecycle position currently shows a confirmed stop, but OrderReconciler's own broker cross-check has not completed a fresh pass -- this confirmed state may not reflect the broker's current reality.",
            "stop_gap_count": 0,
        }
    else:
        protection_readiness = {
            "status": "current",
            "reason": "Every open managed-lifecycle position has a confirmed stop, and OrderReconciler's broker cross-check is fresh.",
            "stop_gap_count": 0,
        }

    # --- release_status (placeholder pending P0-7 qualification/release-taxonomy integration) ---
    release_status = {
        "status": "not_tracked",
        "reason": "No qualification/release-approval taxonomy exists yet in this build. FOLLOW-UP: integrate with the P0-7 qualification/release-state work once it lands.",
    }

    relay_down = bool(config.RELAY_INGRESS_URL) and health_body.get("relay_ok") is False
    rollup = _compute_readiness_rollup(
        standby_mode=config.STANDBY_MODE,
        health_ok=health_ok,
        health_status=health_body["status"],
        trading_authority_status=trading_authority["status"],
        data_readiness_status=data_readiness["status"],
        market_data_status=market_data_readiness["status"],
        protection_status=protection_readiness["status"],
        outbox_backlog_ok=health_body.get("outbox_backlog_ok"),
        provider_scout_ok=bool(health_body.get("provider_scout_ok")),
        equity_snapshotter_ok=bool(health_body.get("equity_snapshotter_ok")),
        relay_down=relay_down,
    )

    return {
        "liveness": liveness,
        "data_readiness": data_readiness,
        "market_data_readiness": market_data_readiness,
        "trading_authority": trading_authority,
        "protection_readiness": protection_readiness,
        "release_status": release_status,
        "rollup": rollup,
        "standby_mode": config.STANDBY_MODE,
    }


class LoginRequest(BaseModel):
    # A strict model (SEC-02) -- FastAPI/pydantic rejects a non-object body,
    # a non-string password, or one exceeding this bound with a clean 422,
    # before any of our own code ever sees it (previously a bare
    # `await request.json()` + `.get()` let a non-object body, a non-string
    # password, or similar malformed input reach `hmac.compare_digest` and
    # raise an uncaught TypeError/AttributeError -- a raw 500). Python's own
    # string handling is unicode-safe throughout, so a non-ASCII password
    # needs no special casing once it's guaranteed to actually be a `str`.
    password: str = Field(max_length=1024)


#: Per-client-IP login throttle (SEC-03): bounded and non-permanent, and
#: deliberately scoped per IP rather than system-wide -- a system-wide
#: lockout would let any anonymous caller lock the real owner out entirely.
#: In-memory (resets on restart); this is a single-process app, and a
#: restart-durable store would still not stop a distributed attempt from
#: many IPs, which is a materially different, larger problem than this
#: closes. Behind a reverse proxy, configure it to pass the real client IP
#: (this uses `request.client.host` as reported to this process) for this
#: to throttle anything more specific than "the proxy's own address."
_LOGIN_WINDOW_SECONDS = 900.0
_LOGIN_MAX_ATTEMPTS = 5
_login_failures: dict[str, list[float]] = defaultdict(list)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.post("/auth/login")
async def login(request: Request, response: Response, body: LoginRequest) -> dict:
    client_key = _client_key(request)
    now = datetime.now(timezone.utc).timestamp()
    recent_failures = [t for t in _login_failures[client_key] if now - t < _LOGIN_WINDOW_SECONDS]
    _login_failures[client_key] = recent_failures
    if len(recent_failures) >= _LOGIN_MAX_ATTEMPTS:
        retry_after = int(_LOGIN_WINDOW_SECONDS - (now - recent_failures[0]))
        raise HTTPException(
            status_code=429,
            detail=f"too many failed login attempts; try again in {max(retry_after, 1)}s",
        )

    if not verify_password(body.password):
        _login_failures[client_key].append(now)
        raise HTTPException(status_code=401, detail="invalid password")

    _login_failures.pop(client_key, None)
    session_id, csrf_token = create_session(store)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        httponly=True,
        samesite="strict",
        # SEC-05: `request.url.scheme` alone is wrong behind a TLS-terminating
        # reverse proxy (nginx/Caddy typically forwards plain HTTP to this
        # process, so the scheme this app sees is always "http" even though
        # the real client used HTTPS). FORCE_SECURE_COOKIES is an explicit
        # operator setting for exactly that deployment, rather than trusting
        # a spoofable X-Forwarded-Proto header by default.
        secure=request.url.scheme == "https" or config.FORCE_SECURE_COOKIES,
        max_age=int(config.SESSION_TTL_SECONDS),
    )
    return {"status": "ok", "csrf_token": csrf_token}


@app.post("/auth/logout")
async def logout(response: Response, scr_session: str | None = Cookie(default=None)) -> dict:
    if scr_session:
        store.delete_session(scr_session)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "ok"}


@app.get("/")
async def dashboard() -> HTMLResponse:
    """One static HTML page with vanilla JS — no build step, no frontend
    framework, no new dependency. It polls the read-only JSON endpoints
    (/positions, /brokers, /signals, /orders) and renders them as tables,
    has forms for the live config-management endpoints (/accounts,
    /routing-rules, /providers) — add/edit/delete an account, a routing
    rule, or a provider/analyst override, taking effect on the very next
    signal — AND has "Exit now" (per position) and "Flatten account"
    buttons that DO submit a live market close via
    `POST /positions/{account}/{symbol}/close` /
    `POST /accounts/{account}/flatten` (see `SignalCopierEngine.close_position`).
    Every other order (an entry, a target/trailing exit) still only comes
    from a source's own push (webhook/SMS route) or a pull-based source's
    background task — manual exit is the one deliberate exception to
    "the dashboard can't submit an order," because letting an account
    owner immediately flatten a position they're watching is a safety
    feature, not new risk. See README.md's "Managing config through the
    GUI/API" section for what's still NOT covered (pull-based bot
    sources like Telegram/Discord still need env vars + a restart to
    add).

    The page itself stays a single static file with no templating engine
    -- the ONE thing that varies per request is LEGACY_DASHBOARD_ENABLED
    (see app/config.py and app/static/dashboard.html's `#legacy-content`
    section), read fresh from `config` on every request (never cached)
    so a test's `monkeypatch.setattr(config, "LEGACY_DASHBOARD_ENABLED", ...)`
    takes effect on its very next request, same as every other config
    read elsewhere in this file. A plain string substitution of one
    placeholder is simpler and safer here than pulling in a template
    engine for a single boolean."""
    html = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    html = html.replace(
        "__LEGACY_DASHBOARD_ENABLED__",
        "true" if config.LEGACY_DASHBOARD_ENABLED else "false",
    )
    return HTMLResponse(content=html)


@app.post("/webhook/{source_name}")
@limiter.limit(INGRESS_RATE_LIMIT)
async def receive_webhook(
    source_name: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """SIG-01: an alerting platform (TradingView included) can and does
    redeliver the same webhook on a timeout/connection error without
    knowing whether the first attempt was received, and `webhook_source.parse`
    stamps a fresh random Signal.id every call -- so `handle_signal`'s own
    dedup-by-signal-id (see app/engine.py) cannot recognize a resend of the
    exact same alert as a duplicate; only the sender knows it's a resend.
    An optional `Idempotency-Key` header (the same header/table
    `POST /positions/.../close` and `POST /accounts/.../flatten` already
    use) lets a sender that can set custom headers mark each distinct
    alert with a stable key; a replayed request with the same key
    replays the cached response instead of submitting again. Most
    alerting platforms (TradingView included) can't set custom headers at
    all, but many alert payloads carry their own stable event id in the
    JSON body itself (an `id`/`event_id` field) -- when no
    `Idempotency-Key` header is present, that field is used as the same
    kind of dedup key instead, so a redelivery of the exact same alert
    still doesn't submit twice."""
    if not config.WEBHOOK_SHARED_SECRET:
        # Fail closed: an unconfigured secret disables this ingress, it does
        # not make it public. Set WEBHOOK_SHARED_SECRET to accept signals here.
        raise HTTPException(status_code=503, detail="webhook ingress is not configured (set WEBHOOK_SHARED_SECRET)")
    if x_webhook_secret is None or not hmac.compare_digest(x_webhook_secret, config.WEBHOOK_SHARED_SECRET):
        # C06: a plain `!=` compare short-circuits on the first mismatched
        # byte, leaking (via response-time variance) how many leading
        # characters of a guessed secret are already correct -- the same
        # class of bug app/auth.py's verify_password already guards
        # against for OWNER_PASSWORD. hmac.compare_digest is unicode-safe
        # for str inputs same as that call site.
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    cache_key = f"webhook:{source_name}:{idempotency_key}" if idempotency_key else None
    if cache_key:
        cached = store.get_idempotent_response(cache_key)
        if cached is not None:
            return cached

    try:
        payload = await request.json()
    except ValueError as exc:
        # RISK-01: a malformed (non-JSON, truncated, wrong-content-type) body
        # must 400, not fall through to an unhandled exception and 500.
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")

    if cache_key is None:
        event_id = payload.get("id") or payload.get("event_id")
        if event_id is not None:
            cache_key = f"webhook:{source_name}:event:{event_id}"
            cached = store.get_idempotent_response(cache_key)
            if cached is not None:
                return cached

    try:
        signal = webhook_source.parse(payload, source_override=source_name)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Source ledger (this is the real production ingress route --
    # `WebhookSource.ingest`'s own emission doesn't run on this path,
    # which parses+dispatches directly): one ORIGINAL SOURCE_EVENT per
    # accepted alert, carrying whatever provider message identity `parse`
    # resolved (see `WebhookSource.parse`'s own docstring).
    await engine.export_source_event(
        SourceEvent(
            source=signal.source,
            kind=SourceEventKind.ORIGINAL,
            channel_id=signal.channel_id,
            message_id=signal.message_id,
            local_receipt_timestamp=signal.received_at,
            signal=signal,
            raw_source_event=payload,
        )
    )

    results = await engine.handle_signal(signal)
    response = _orders_response(signal.id, results)
    if cache_key:
        store.save_idempotent_response(cache_key, response)
    return response


@app.post("/sms/twilio")
@limiter.limit(INGRESS_RATE_LIMIT)
async def receive_sms(
    request: Request, body: str = Form(alias="Body"), from_number: str = Form(alias="From", default="")
) -> dict:
    if not config.TWILIO_AUTH_TOKEN:
        # Fail closed: an unconfigured token disables this ingress, it does
        # not make it public. Set TWILIO_AUTH_TOKEN (and TWILIO_WEBHOOK_URL) to
        # accept SMS signals here.
        raise HTTPException(status_code=503, detail="SMS ingress is not configured (set TWILIO_AUTH_TOKEN)")

    if not config.TWILIO_ALLOWED_FROM_NUMBERS:
        # Same fail-closed rule, for a different question: a valid Twilio
        # signature only proves the request transited Twilio with the right
        # account's auth token -- it is a transport check, not an answer to
        # "is this sender allowed to submit trading instructions." Anyone
        # who can text this number would otherwise generate trades.
        raise HTTPException(
            status_code=503, detail="SMS ingress has no authorized senders configured (set TWILIO_ALLOWED_FROM_NUMBERS)"
        )

    try:
        from twilio.request_validator import RequestValidator
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail="twilio package not installed; cannot validate request") from exc

    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    validator = RequestValidator(config.TWILIO_AUTH_TOKEN)
    if not validator.validate(config.TWILIO_WEBHOOK_URL, dict(form), signature):
        raise HTTPException(status_code=401, detail="invalid Twilio signature")

    if from_number not in config.TWILIO_ALLOWED_FROM_NUMBERS:
        # Transport authentication (the signature above) and trading-source
        # authorization are separate decisions -- a genuine Twilio request
        # from an unrecognized sender is still refused.
        raise HTTPException(status_code=403, detail="sender is not an authorized trading source")

    # SIG-01: Twilio itself can and does redeliver the same inbound-message
    # webhook (e.g. if this service's response is slow or the connection
    # drops before Twilio sees a 200), and `sms_source.parse` stamps a
    # fresh random Signal.id each call, so a resend would otherwise be
    # processed as an entirely new signal and submit a second time.
    # `MessageSid` is Twilio's own stable identifier for the message being
    # delivered -- unlike the webhook route's Idempotency-Key, no
    # cooperation from the sender is needed since Twilio always includes
    # it.
    message_sid = form.get("MessageSid")
    cache_key = f"twilio_sms:{message_sid}" if message_sid else None
    if cache_key:
        cached = store.get_idempotent_response(cache_key)
        if cached is not None:
            return cached

    try:
        signal = sms_source.parse(body, analyst=from_number or None)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = await engine.handle_signal(signal)
    response = _orders_response(signal.id, results)
    if cache_key:
        store.save_idempotent_response(cache_key, response)
    return response


@app.get("/whatsapp/webhook")
@limiter.limit(INGRESS_RATE_LIMIT)
async def verify_whatsapp_webhook(request: Request) -> Response:
    """Meta's one-time subscription handshake: after you set the Callback
    URL in the WhatsApp app dashboard, Meta immediately sends this GET to
    prove you control the endpoint before it'll ever POST a real message
    here. Must echo back `hub.challenge` exactly, and only when
    `hub.verify_token` matches what you configured."""
    if not config.WHATSAPP_VERIFY_TOKEN:
        raise HTTPException(
            status_code=503, detail="WhatsApp ingress is not configured (set WHATSAPP_VERIFY_TOKEN)"
        )
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")
    if mode == "subscribe" and hmac.compare_digest(token, config.WHATSAPP_VERIFY_TOKEN):
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="invalid WhatsApp verify token")


@app.post("/whatsapp/webhook")
@limiter.limit(INGRESS_RATE_LIMIT)
async def receive_whatsapp(request: Request) -> dict:
    """WhatsApp Business Cloud API delivery. Meta signs the raw request
    body with HMAC-SHA256 using the app secret (`X-Hub-Signature-256`) --
    verified here the same way Twilio's signature is for /sms/twilio,
    just a plain HMAC rather than a vendor SDK call (no extra pip package
    needed for this direction).

    A single delivery can batch messages from multiple senders (and
    non-message events like delivery/read receipts, which simply have no
    `messages` entry to match) -- unlike Twilio's one-message-per-POST
    webhook, an unauthorized sender's message is skipped individually,
    not treated as a reason to reject the whole batch. Meta expects a 200
    for any successfully-received (i.e. correctly signed) webhook
    regardless of what happens downstream; returning an error status here
    for a per-message business decision risks Meta retrying or eventually
    disabling the webhook subscription entirely.
    """
    if not config.WHATSAPP_APP_SECRET:
        raise HTTPException(status_code=503, detail="WhatsApp ingress is not configured (set WHATSAPP_APP_SECRET)")
    if not config.WHATSAPP_ALLOWED_FROM_NUMBERS:
        raise HTTPException(
            status_code=503,
            detail="WhatsApp ingress has no authorized senders configured (set WHATSAPP_ALLOWED_FROM_NUMBERS)",
        )

    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    expected_signature = (
        "sha256=" + hmac.new(config.WHATSAPP_APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    )
    if not signature_header or not hmac.compare_digest(signature_header, expected_signature):
        raise HTTPException(status_code=401, detail="invalid WhatsApp webhook signature")

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc

    processed = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                if message.get("type") != "text":
                    continue  # images/audio/location/reactions/etc. carry no signal text
                from_number = message.get("from", "")
                if from_number not in config.WHATSAPP_ALLOWED_FROM_NUMBERS:
                    logger.warning("WhatsApp message from unauthorized sender=%s rejected", from_number)
                    continue

                # WHATSAPP-01: Meta can and does redeliver the same webhook
                # (e.g. if this service's response is slow), and `parse`
                # stamps a fresh random Signal.id each call -- `message["id"]`
                # (WhatsApp's own stable per-message id, e.g. "wamid.XXX")
                # is the same kind of dedup key MessageSid is for Twilio.
                message_id = message.get("id")
                cache_key = f"whatsapp:{message_id}" if message_id else None
                if cache_key and store.get_idempotent_response(cache_key) is not None:
                    continue

                body_text = message.get("text", {}).get("body", "")
                try:
                    signal = whatsapp_source.parse(body_text, analyst=from_number or None)
                except SignalValidationError:
                    continue

                results = await engine.handle_signal(signal)
                response = _orders_response(signal.id, results)
                if cache_key:
                    store.save_idempotent_response(cache_key, response)
                processed += 1

    return {"status": "ok", "processed": processed}


@app.post("/ninjatrader/webhook")
@limiter.limit(INGRESS_RATE_LIMIT)
async def receive_ninjatrader(request: Request) -> dict:
    """Receives fill events from `ninjascript/SignalCopierAutoJournal.cs`
    (see app/sources/ninjatrader.py's docstring for the payload shape and
    what's verified vs. still unverified about that C# side). NinjaScript
    has no built-in request-signing, so this is a plain shared secret in
    a custom header, not an HMAC signature like Twilio/Meta's webhooks."""
    if not config.NINJATRADER_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503, detail="NinjaTrader ingress is not configured (set NINJATRADER_WEBHOOK_SECRET)"
        )
    secret_header = request.headers.get("X-NinjaTrader-Secret", "")
    if not secret_header or not hmac.compare_digest(secret_header, config.NINJATRADER_WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="invalid NinjaTrader webhook secret")

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")

    # A reversal fill reports an "exit" and an "entry" under the SAME
    # order_id (see app/sources/ninjatrader.py's docstring) -- action must
    # be part of the key or those two legs would collide as "duplicates"
    # of each other.
    order_id = payload.get("order_id")
    cache_key = f"ninjatrader:{order_id}:{payload.get('action')}" if order_id else None
    if cache_key:
        cached = store.get_idempotent_response(cache_key)
        if cached is not None:
            return cached

    try:
        signal = ninjatrader_source.parse(payload)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = await engine.handle_signal(signal)
    response = _orders_response(signal.id, results)
    if cache_key:
        store.save_idempotent_response(cache_key, response)
    return response


@app.get("/positions")
async def list_positions(_owner: dict = Depends(require_owner_read)) -> dict:
    """Every non-flat tracked position, across all accounts.

    This is this service's own record of what it has sent (see
    app/engine.py's "Close signals" docstring on why that can drift from
    the broker's real book on brokers that only confirm fills
    asynchronously), not a live read of any broker's account state.
    """
    managed_lifecycles = _managed_lifecycle_snapshot()
    return {
        "positions": store.list_open_positions(),
        "managed_lifecycles": managed_lifecycles,
        # DB-0X: a real, derived aggregate -- an open managed-lifecycle
        # position (owned_quantity > 0) whose `stop_status` isn't
        # `stop_confirmed` right now, straight off the same per-position
        # `stop_status` this response already carries above (never a
        # separately-maintained count that could drift from it). Zero for
        # a deployment with no managed_lifecycle accounts at all, same as
        # an empty `managed_lifecycles` list -- not a sign nothing is
        # tracked.
        "stop_gap_count": sum(
            1
            for lc in managed_lifecycles
            if lc["owned_quantity"] > 0 and lc["stop_status"] != ProtectionStatus.STOP_CONFIRMED.value
        ),
    }


@app.get("/positions/excursions")
async def list_position_excursions(
    account_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """PU-A1: final MAE/MFE for CLOSED positions, newest-closed first —
    the historical counterpart to `GET /positions`' in-progress figures for
    still-open managed lifecycles. Optionally narrowed to one account
    and/or symbol. A later analytics/chart batch queries this directly
    rather than adding its own excursion tracking."""
    return {"excursions": store.list_position_excursions(account_id=account_id, symbol=symbol, limit=limit)}


@app.get("/positions/{account_id}/{symbol}/stop-events")
async def list_stop_target_events(
    account_id: str,
    symbol: str,
    limit: int = Query(default=500, ge=1, le=5000),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """PU-A4: this position's real, append-only stop/target lifecycle
    event history, oldest first -- see app/lifecycle/models.py's
    `StopTargetEventType` for exactly which event types exist (and which
    catalog-requested ones -- a breakeven move, a trailing-stop
    activation -- are a documented gap on this branch rather than a
    fabricated event) and app/lifecycle/manager.py's own call sites for
    where each one is appended. The data prerequisite for a later
    stop/target analytics chart (stop-tightening frequency, TP hit rate,
    etc.), not itself a chart."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    return {
        "account_id": account_id,
        "symbol": symbol,
        "events": store.list_stop_target_events(account_id, symbol, limit=limit),
    }


@app.get("/accounts/{account_id}/economics")
async def get_account_economics(account_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """E06: authoritative realized P&L, cost basis and completed-trade win
    rate for this account, computed by replaying its own confirmed
    executions (see app/economics.py) -- never a simulated equity curve.
    Gross of fees (not yet tracked); no live-market unrealized P&L."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    return compute_account_economics(store, account_id).to_dict()


@app.get("/accounts/{account_id}/economics/extended")
async def get_account_economics_extended(account_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """TR-EPISODE-01 (P&L completeness): the extended economic-account
    view a release review asked for alongside `GET /accounts/{id}/
    economics` above -- NAV/equity (from a fresh, real broker balance
    read, when the broker supports one), unrealized P&L (reusing
    app/equity_history.py's own real last-observed-price mechanism),
    explicit unknown-fee/unavailable-mark/unavailable-TWR states (never a
    fabricated 0), and slippage/implementation-shortfall (from every fill
    whose signal carried a real reference price). See
    app/account_economics_v2.py's module docstring for exactly which
    fields are real and which are honestly disclosed as not yet
    computable in this schema. Added alongside the existing endpoint,
    never replacing it -- that endpoint's response shape is an
    already-depended-on contract this pass doesn't change."""
    account = routing_config.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    broker = brokers.get(account.broker)
    broker_balance = await broker.get_account_balance(account) if broker is not None else None
    return compute_extended_account_economics(
        store, account_id, lifecycle_manager=lifecycle_manager, broker_balance=broker_balance
    ).to_dict()


@app.get("/accounts/{account_id}/balance")
async def get_account_balance(account_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """A live read of this account's actual cash/equity/buying-power/
    margin directly from its broker (see app/brokers/base.py's
    `get_account_balance` and app/models.py's `AccountBalance`) -- NOT
    this service's own tracked position/economics replay, and
    deliberately not what app/capital_allocator.py's notional ceiling
    reads either (see both those modules' docstrings for why).

    404 for an unknown account; 200 with every `AccountBalance` field
    `null` (never a broker lookup error) for a real account whose broker
    has no verified way to report this at all -- `has_balance_capability`
    on `GET /brokers` says which brokers that's true for up front."""
    account = routing_config.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    broker = brokers.get(account.broker)
    if broker is None:
        return AccountBalance(account_id=account_id).to_dict()
    balance = await broker.get_account_balance(account)
    if balance is None:
        return AccountBalance(account_id=account_id).to_dict()
    return balance.to_dict()


@app.get("/capital-allocation")
async def get_capital_allocation(_owner: dict = Depends(require_owner_read)) -> dict:
    """Phase B7: this engine's own real, CURRENT (point-in-time, never
    historical) capital-reservation state per configured account, straight
    off `app/capital_allocator.py`'s single shared `CapitalAllocator`
    instance (`engine.capital_allocator`) -- previously process-internal
    only (used by `app/engine.py`'s admission-control path, no GET route
    read it). This is the one new read-only endpoint this batch adds, kept
    narrowly scoped to that module's own real state:

    - `deployed_notional`: `confirmed_open_notional` -- this account's real
      open notional exposure, replayed from the same confirmed-fill journal
      `app/economics.py` already trusts (identical figure the E03 admission
      gate itself reads before deciding).
    - `reserved_notional`: `CapitalAllocator.pending_reservation` -- real,
      provisional notional this process has admitted for in-flight orders
      on this account that have not yet resolved to FILLED/REJECTED/ERROR
      (see that module's own "PENDING reservation timing" section). This
      figure is in-memory and process-lifetime only, same as the allocator
      itself -- it resets on a restart, it is never a persisted ledger.
    - `max_notional_exposure`: this account's configured ceiling
      (`DestinationAccount.max_notional_exposure`), `null` when the account
      has opted out of E03's exposure gate entirely (the default).
    - `available_notional`: `max_notional_exposure - deployed_notional -
      reserved_notional`, `null` (never a guess) when no ceiling is
      configured for this account, OR when `unresolved_symbols` below is
      non-empty (a known-incomplete `deployed_notional` makes any
      "headroom" figure unreliable -- see app/capital_allocator.py's
      `ExposureReport`).
    - `unresolved_symbols`: symbols this account holds a confirmed-fill
      history for that `confirmed_open_notional` could not resolve an
      average cost for -- non-empty means `deployed_notional` genuinely
      UNDERSTATES this account's real open exposure (never treated as
      zero by the real E03 admission gate itself, which refuses new
      admissions for this account while this is non-empty).

    `deployed_notional` and `reserved_notional` are never double-counted
    against each other: the former only ever counts a symbol once a fill is
    confirmed (see `confirmed_open_notional`'s own docstring), the latter
    only ever counts notional for an order that has NOT yet reached that
    confirmed state -- the same non-overlapping split `app/engine.py`'s
    `admit()` call itself relies on (`confirmed_exposure + pending + new
    notional` in one sum, never twice)."""
    accounts_out = []
    for account_id, account in routing_config.accounts.items():
        exposure = confirmed_open_notional(store, account_id)
        deployed = exposure.notional
        reserved = engine.capital_allocator.pending_reservation(account_id)
        max_exposure = account.max_notional_exposure
        # `available_notional` is only ever a real figure when this
        # account's exposure is fully resolved -- a non-empty
        # `unresolved_symbols` means `deployed` is a known understatement,
        # so reporting a headroom number here would misrepresent real
        # capacity. See app/capital_allocator.py's ExposureReport.
        available = (
            None
            if max_exposure is None or exposure.has_unresolved
            else max_exposure - deployed - reserved
        )
        accounts_out.append(
            {
                "account_id": account_id,
                "deployed_notional": deployed,
                "reserved_notional": reserved,
                "max_notional_exposure": max_exposure,
                "available_notional": available,
                "unresolved_symbols": exposure.unresolved_symbols,
            }
        )
    return {"accounts": accounts_out}


# Representative HYPOTHETICAL signal quantities for TR-12's Sizing tab --
# not real signals, never persisted, never routed. Deliberately spans a
# small/typical/large range so the owner can see how account.fixed_quantity
# vs. account.multiplier actually resolves before a real signal arrives,
# plus the one case (no quantity on the signal at all) app/risk.py's own
# `size_for_account` special-cases to a 1.0 base.
_SIZING_PREVIEW_SCENARIOS: list[dict[str, Any]] = [
    {"label": "No quantity on signal (defaults to 1.0)", "quantity": None},
    {"label": "Small signal (quantity 1)", "quantity": 1.0},
    {"label": "Typical signal (quantity 5)", "quantity": 5.0},
    {"label": "Large signal (quantity 25)", "quantity": 25.0},
]


@app.get("/policies/sizing-preview")
async def get_sizing_preview(
    price: float | None = None, signal_id: str | None = None, _owner: dict = Depends(require_owner_read)
) -> dict:
    """TR-12 Sizing tab (`signal_id` omitted): "expected position size under
    several representative trades and current account conditions" --
    genuinely computed, never a separately-reimplemented approximation.
    TR-12 Preview tab (`signal_id` given): the exact same computation, same
    function call, run against one real, already-received signal instead of
    the hypothetical scenario list -- so the Sizing tab's illustrative
    figures and the Preview tab's "what would happen right now" dry-run can
    never disagree with each other or with the real engine, because both
    are this one endpoint calling this one function.

    Every scenario/signal below calls `app.risk.size_for_account` DIRECTLY
    (the exact function `app/engine.py` calls at real signal-admission time)
    -- a hypothetical `Signal` (never persisted or routed) for
    the scenario case, or the real stored `Signal` row for the `signal_id`
    case -- so this can never drift from what a real signal would actually
    size to. "Current account conditions" is this account's real, current
    capital-allocation state (`app/capital_allocator.py`'s
    `confirmed_open_notional` and `CapitalAllocator.pending_reservation`,
    the same real state `GET /capital-allocation` reports and the same
    figures E03's admission gate itself reads) -- never a separately
    fetched or reimplemented copy.

    `price` (scenario mode only) is optional (this build has no reliable
    "current market price" for an arbitrary symbol independent of a real
    signal) -- when omitted, notional and hard-ceiling headroom are
    honestly reported as unknown rather than guessed at; when given,
    notional = expected quantity * price, checked against the real ceiling
    arithmetic E03 itself uses (`confirmed_exposure + reserved + new
    notional > max_exposure`). In `signal_id` mode, the real signal's own
    `price` (if any) is used instead -- never the `price` query param."""
    if signal_id is not None:
        row = next((s for s in store.list_recent_signals(limit=500) if str(s["id"]) == signal_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no signal '{signal_id}'")
        real_signal = Signal(
            source=row["source"],
            symbol=row["symbol"],
            side=Side(row["side"]),
            quantity=row["quantity"],
            price=row["price"],
            analyst=row["analyst"],
        )
        accounts_out = []
        for account_id, account in routing_config.accounts.items():
            exposure = confirmed_open_notional(store, account_id)
            deployed = exposure.notional
            reserved = engine.capital_allocator.pending_reservation(account_id)
            max_exposure = account.max_notional_exposure
            expected_quantity = size_for_account(real_signal, account)
            notional = expected_quantity * real_signal.price if real_signal.price is not None else None
            # A ceiling check against a known-incomplete `deployed` figure
            # would understate real exposure -- report unknown rather than
            # a falsely-reassuring `False` (see ExposureReport.has_unresolved).
            would_exceed_ceiling = (
                None
                if exposure.has_unresolved
                else (
                    (deployed + reserved + notional) > max_exposure
                    if max_exposure is not None and notional is not None
                    else None
                )
            )
            accounts_out.append(
                {
                    "account_id": account_id,
                    "expected_quantity": expected_quantity,
                    "notional": notional,
                    "deployed_notional": deployed,
                    "reserved_notional": reserved,
                    "max_notional_exposure": max_exposure,
                    "would_exceed_ceiling": would_exceed_ceiling,
                    "unresolved_symbols": exposure.unresolved_symbols,
                }
            )
        return {"signal_id": signal_id, "signal_price": real_signal.price, "accounts": accounts_out}

    accounts_out = []
    for account_id, account in routing_config.accounts.items():
        exposure = confirmed_open_notional(store, account_id)
        deployed = exposure.notional
        reserved = engine.capital_allocator.pending_reservation(account_id)
        max_exposure = account.max_notional_exposure
        scenario_rows = []
        for scenario in _SIZING_PREVIEW_SCENARIOS:
            hypothetical_signal = Signal(
                source="__tr12_sizing_preview__",
                symbol="PREVIEW",
                side=Side.BUY,
                quantity=scenario["quantity"],
            )
            expected_quantity = size_for_account(hypothetical_signal, account)
            notional = expected_quantity * price if price is not None else None
            would_exceed_ceiling = (
                None
                if exposure.has_unresolved
                else (
                    (deployed + reserved + notional) > max_exposure
                    if max_exposure is not None and notional is not None
                    else None
                )
            )
            scenario_rows.append(
                {
                    "label": scenario["label"],
                    "signal_quantity": scenario["quantity"],
                    "expected_quantity": expected_quantity,
                    "notional": notional,
                    "would_exceed_ceiling": would_exceed_ceiling,
                }
            )
        accounts_out.append(
            {
                "account_id": account_id,
                "fixed_quantity": account.fixed_quantity,
                "multiplier": account.multiplier,
                "deployed_notional": deployed,
                "reserved_notional": reserved,
                "max_notional_exposure": max_exposure,
                "unresolved_symbols": exposure.unresolved_symbols,
                "scenarios": scenario_rows,
            }
        )
    return {"price": price, "accounts": accounts_out}


@app.get("/accounts/{account_id}/execution-quality")
async def get_account_execution_quality(account_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """E05: signal-to-fill latency per symbol, computed from this schema's
    actual `received_at`/`executed_at` timestamps (see
    app/execution_quality.py for the honest scope disclosure)."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    return compute_execution_quality(store, account_id).to_dict()


@app.get("/accounts/{account_id}/equity-history")
async def get_account_equity_history(
    account_id: str,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=1000, gt=0, le=10000),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """PU-A3: this account's real, persisted equity/P&L snapshot series
    (see app/equity_history.py's EquitySnapshotter, which writes one row
    per account every `EQUITY_SNAPSHOT_INTERVAL_SECONDS`) -- the queryable
    history Phase B1/B4/B6/B8's equity/P&L/drawdown curves read, so none of
    them needs to add its own tracking.

    `realized_pnl` in each row is exactly what
    `GET /accounts/{account_id}/economics` would have independently
    computed at that snapshot's `captured_at` -- never a second P&L
    calculation. `cumulative_pnl` is `realized_pnl + unrealized_pnl`,
    honestly named that (not "equity") because this account has no
    configured starting-balance baseline -- see EquitySnapshotter's module
    docstring. `unpriced_open_symbols` lists any symbol that had an open
    position but no real observed price at that snapshot, so a chart can
    show that a given point's `unrealized_pnl` is a partial figure rather
    than silently treating it as complete.

    `since`/`until` are optional ISO-8601 timestamps bounding
    `captured_at` (each inclusive); `limit` caps how many rows come back
    (oldest first), default 1000."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    snapshots = store.list_equity_snapshots(account_id, since=since, until=until, limit=limit)
    return {
        "account_id": account_id,
        "note": "cumulative_pnl is realized_pnl + unrealized_pnl -- this account has no configured "
        "starting-balance baseline, so this is a real cumulative P&L series, not a broker-confirmed "
        "absolute equity figure. unpriced_open_symbols on a row lists any open-position symbol with "
        "no real observed price at that snapshot (its unrealized contribution there is 0.0, not a "
        "verified zero).",
        "snapshots": snapshots,
    }


@app.get("/accounts/correlation")
async def get_accounts_correlation(
    account_a: str = Query(...),
    account_b: str = Query(...),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """Phase A5: real Pearson correlation between two accounts' real
    `cumulative_pnl` snapshot series (app/equity_history.py), matched by
    overlapping `captured_at` timestamp -- used as a proxy for "strategy"
    correlation since this codebase has no separate per-provider/per-
    analyst equity attribution (see app/statistics.py's module
    docstring). `correlation` is `null`, never a fabricated 0 or
    NaN-as-zero, when the two accounts have fewer than
    `app.statistics.MIN_CORRELATION_SAMPLES` real overlapping snapshots.

    Registered as a fixed path ahead of no other `/accounts/...` route
    with a conflicting shape (`/accounts/{account_id}/...` all take a
    second path segment), so `correlation` here can never be mistaken by
    FastAPI's router for an `account_id`."""
    for account_id in (account_a, account_b):
        if account_id not in routing_config.accounts:
            raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    snapshots_a = store.list_equity_snapshots(account_a, limit=10000)
    snapshots_b = store.list_equity_snapshots(account_b, limit=10000)
    return compute_pairwise_correlation(account_a, snapshots_a, account_b, snapshots_b).to_dict()


@app.get("/accounts/{account_id}/statistics")
async def get_account_statistics(
    account_id: str,
    window: int = Query(default=30, gt=0, le=10000),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """Phase A5: rolling volatility/Sharpe-equivalent/Sortino-equivalent/
    max-drawdown(+duration), computed from this account's real
    `cumulative_pnl` snapshot series (app/equity_history.py) over the
    last `window` real snapshots -- see app/statistics.py's module
    docstring for the full honest-labeling rationale (absolute
    P&L-delta terms, never a fabricated percentage return; implicit
    zero risk-free rate; each field `null` when the account's real
    history is too short for that statistic to be meaningful)."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    snapshots = store.list_equity_snapshots(account_id, limit=10000)
    return compute_rolling_stats(account_id, snapshots, window=window).to_dict()


@app.post("/positions/{account_id}/{symbol}/close")
async def close_single_position(
    account_id: str,
    symbol: str,
    _owner: dict = Depends(require_owner),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Immediately exit one open position — the dashboard's per-position
    "Exit now" button. Bypasses routing entirely (this targets exactly the
    named account, not "every account subscribed to some source") and goes
    through `SignalCopierEngine.close_position`, which reuses the same
    resolution a real provider CLOSE signal would use: managed-lifecycle
    accounts through `PositionLifecycleManager.request_exit` (respecting
    the protection-transfer rules — see README.md's "Managed lifecycle"
    section), plain accounts against the tracked position at
    `SignalStore.get_position` (serialized per (account, symbol) —
    see `SignalCopierEngine._resolve_and_submit_plain_close`).

    An optional `Idempotency-Key` header makes a retried request (e.g. a
    client that timed out waiting for the first response and retries)
    replay the original result instead of submitting a second close — this
    is on top of, not instead of, the engine's own per-(account, symbol)
    lock, which already prevents two genuinely concurrent requests from
    both executing.

    EXE-11: the key is scoped to THIS action and target (close of this
    exact account/symbol) — reusing the same key for a different close, or
    for `POST /accounts/.../flatten`, is a changed intent under the same
    key and gets a 409 rather than silently replaying the unrelated first
    response."""
    fingerprint = f"close:{account_id}:{symbol}"
    if idempotency_key:
        cached = store.get_idempotent_record(idempotency_key)
        if cached is not None:
            if cached["fingerprint"] != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different action/target",
                )
            return cached["response"]

    account = routing_config.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")

    result = await engine.close_position(account, symbol, reason="dashboard_manual_exit")
    response = {
        "account_id": account_id,
        "symbol": symbol,
        "status": result.status.value,
        "filled_quantity": result.filled_quantity,
        "message": result.message,
    }
    if idempotency_key:
        store.save_idempotent_response(idempotency_key, response, fingerprint=fingerprint)
    return response


@app.post("/accounts/{account_id}/flatten")
async def flatten_account(
    account_id: str,
    _owner: dict = Depends(require_owner),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    """Exit every open position tracked for this account — the dashboard's
    account-level "Flatten account" action. Only positions this service
    itself is tracking (`SignalStore.list_open_positions`, filtered to this
    account) are touched — this is never a broker-wide "flatten everything
    in the account" call, so a manually-held position this copier never
    opened is left alone. Closes positions one at a time (not
    concurrently): each `close_position` call for a managed-lifecycle
    account holds that position's own `CloseArbiter` lock for its full
    duration anyway, so nothing is gained by parallelizing across symbols,
    and doing it sequentially keeps `SignalStore`'s recorded order simple
    to read. One symbol failing to close does not stop the rest — the
    response reports each symbol's own outcome. See the per-position
    endpoint's docstring for what `Idempotency-Key` does and how EXE-11
    scopes it to this action/target -- reusing a key from a `close` call
    (or a flatten of a different account) here is a changed intent and
    gets a 409, not a silent replay of the unrelated first response."""
    fingerprint = f"flatten:{account_id}"
    if idempotency_key:
        cached = store.get_idempotent_record(idempotency_key)
        if cached is not None:
            if cached["fingerprint"] != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for a different action/target",
                )
            return cached["response"]

    account = routing_config.accounts.get(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")

    symbols = [p["symbol"] for p in store.list_open_positions() if p["account_id"] == account_id]
    closed = []
    for symbol in symbols:
        result = await engine.close_position(account, symbol, reason="dashboard_flatten_account")
        closed.append(
            {
                "symbol": symbol,
                "status": result.status.value,
                "filled_quantity": result.filled_quantity,
                "message": result.message,
            }
        )
    response = {"account_id": account_id, "closed": closed}
    if idempotency_key:
        store.save_idempotent_response(idempotency_key, response, fingerprint=fingerprint)
    return response


@app.post("/reconciliation/run-now")
async def run_reconciliation_now(_owner: dict = Depends(require_owner)) -> dict:
    """TR-06-A02/TR-03-A03: the real owner-facing action to trigger an
    on-demand order/position reconciliation pass. Calls
    `OrderReconciler.run_now`, which runs the exact same `reconcile_once()`
    the background loop (app/reconciliation.py) calls on its own schedule --
    synchronously, so this returns the real outcome (how many pending
    orders/exits/entries were re-examined, how many actually changed state)
    rather than just enqueueing something and returning immediately. Guarded
    so a second concurrent click can't stack a second pass against the same
    DB/broker calls -- returns `already_running: true` instead."""
    return await reconciler.run_now()


@app.get("/lifecycle/{account_id}/{symbol}/preview-reduction")
async def preview_position_reduction(
    account_id: str,
    symbol: str,
    quantity: float = Query(..., gt=0),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """TR-03-A01: read-only preview of a hypothetical partial reduction for
    one managed-lifecycle position. Calls
    `PositionLifecycleManager.preview_reduction`, which reuses the exact
    same pure planning function (`_compute_reduction_plan`) the real
    `request_exit` itself calls -- never a second, separately maintained
    computation -- and never places, cancels, or amends any real order.
    `{"supported": false, "reason": ...}` for every case a real reduction
    would itself refuse (no managed lifecycle, halted, a prior exit still
    unresolved, nothing available to sell)."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    account = routing_config.accounts[account_id]
    return await lifecycle_manager.preview_reduction(account, symbol, quantity)


@app.get("/lifecycle/{account_id}/{symbol}/preview-stop-change")
async def preview_position_stop_change(
    account_id: str,
    symbol: str,
    price: float = Query(...),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """TR-03-A02: read-only preview of what the position's real trailing-stop
    computation would produce at a hypothetical market `price`. Calls
    `PositionLifecycleManager.preview_stop_change`, which reuses the exact
    same pure function (`_compute_trailing_candidate`) `_update_trailing`
    itself calls -- never a second, separately maintained formula -- and
    never places or replaces any real stop order. Only covers an ACTIVE
    trailing policy; a TIGHTEN_STOP target (fixed trigger price, evaluated
    only on a live price tick) has no separable pure formula to preview and
    is reported as unsupported rather than faked."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    return lifecycle_manager.preview_stop_change(account_id, symbol, price)


@app.get("/brokers")
async def list_broker_capabilities(_owner: dict = Depends(require_owner_read)) -> dict:
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
                "has_last_price_capability": broker.has_last_price_capability,
                "has_balance_capability": broker.has_balance_capability,
                "can_protect_a_managed_position": broker.can_protect_a_managed_position(),
                "supported_asset_classes": (
                    sorted(a.value for a in broker.supported_asset_classes)
                    if broker.supported_asset_classes is not None
                    else None  # undeclared -- not verified as restricted, see BrokerAdapter's docstring
                ),
                # DB-0X (bounded, PaperBroker-only real value): a per-fill
                # fee this broker adapter genuinely, explicitly charges --
                # `None` ("not_tracked") for every other adapter, which has
                # no real per-fill fee figure to report (see
                # app/brokers/paper.py's own docstring on why a documented
                # simulated fee is honest specifically for a fully
                # internal, fully-controlled broker, and why fabricating
                # one for a real external broker would not be). Read via
                # `getattr` rather than a new `BrokerAdapter` method/field
                # -- no other adapter declares this attribute at all, so
                # this stays additive without touching app/brokers/base.py.
                "fee_per_fill": getattr(broker, "fee_per_fill", None),
                # DB-0X (bounded): this broker instance's own real, already-
                # set attributes for paper/live and venue, where this
                # codebase actually stores them as inspectable state --
                # `None` ("not exposed") for an adapter (Alpaca, Schwab,
                # Robinhood, SignalStack, NinjaTrader, MT4/MT5, Tastytrade,
                # TradeStation, Tradovate, OANDA, Rithmic) that resolves
                # its own paper/live distinction per-account from an
                # environment variable at call time instead of storing it
                # on the instance -- reporting a guess here would be worse
                # than the honest gap. ccxt's own `sandbox`/`exchange_id`
                # and IBKR's own `port` are real, public, already-existing
                # attributes (never added for this), and PaperBroker is
                # unambiguously always the "paper" environment/venue by
                # construction.
                "environment": (
                    "paper"
                    if broker.name == "paper"
                    else "paper" if getattr(broker, "sandbox", None) is True
                    else "live" if getattr(broker, "sandbox", None) is False
                    else "paper" if getattr(broker, "port", None) in (7497, 4002)
                    else "live" if getattr(broker, "port", None) in (7496, 4001)
                    else None
                ),
                "venue": (
                    "paper"
                    if broker.name == "paper"
                    else getattr(broker, "exchange_id", None)
                    or ("ibkr" if hasattr(broker, "port") else None)
                ),
            }
            for broker in brokers.values()
        ]
    }


class QualificationRequest(BaseModel):
    """Body for `POST /qualifications`. See app/qualification.py's module
    docstring for the ladder and why a route is
    (adapter_type, route_key, asset_class, product_type)."""

    adapter_type: str = Field(..., min_length=1, max_length=80)
    #: The exact account/venue variant this record is about -- e.g. a real
    #: config_accounts account_id, or (for a route not yet backed by a
    #: saved account) an operator-chosen identifier such as
    #: "ccxt_binance_spot". Never validated against config_accounts here:
    #: a route can legitimately be qualified before an account exists for
    #: it (or after one was later removed) -- the ladder/feedback checks
    #: below are what actually gate anything.
    route_key: str = Field(..., min_length=1, max_length=200)
    asset_class: str = Field(..., min_length=1, max_length=40)
    #: Free-text refinement distinguishing routes that share the same
    #: asset_class but are genuinely different products (e.g. "spot" vs
    #: "perpetual" on the same crypto exchange) -- this codebase has no
    #: structured enum for this (see app/qualification.py's module
    #: docstring on why CCXT spot/perp are different routes despite
    #: identical asset_class=crypto).
    product_type: str = Field(..., min_length=1, max_length=80)
    state: str = Field(..., min_length=1, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("asset_class")
    @classmethod
    def _valid_asset_class(cls, v: str) -> str:
        try:
            AssetClass(v)
        except ValueError:
            valid = ", ".join(a.value for a in AssetClass)
            raise ValueError(f"asset_class must be one of: {valid}") from None
        return v


@app.get("/qualifications")
async def list_route_qualifications(
    adapter_type: str | None = None,
    route_key: str | None = None,
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """Every route's live qualification history (app/qualification.py) --
    a real, persisted record of what has actually been checked for this
    EXACT (adapter_type, route_key, asset_class, product_type) tuple, never
    inferred from `app/brokers/base.py`'s implementation-derived capability
    introspection (that stays available, unchanged, at `GET /brokers` --
    this is a separate, higher-bar concept: see this module's own
    docstring). `current_state` is the highest ladder rung actually
    achieved for that route; `events` is the full, append-only history of
    every state ever recorded for it, oldest first."""
    return {
        "ladder": [s.value for s in QUALIFICATION_STATE_ORDER],
        "routes": store.list_route_qualifications(adapter_type=adapter_type, route_key=route_key),
    }


@app.post("/qualifications")
async def create_route_qualification(
    request: QualificationRequest, _owner: dict = Depends(require_owner)
) -> dict:
    """Record one live-qualification state for one exact route. Owner-
    gated (session + CSRF), same as every other mutation in this build --
    `release_approved` in particular is a deliberate human sign-off, never
    something code should be able to assert on its own.

    Two structural rejections happen here, BEFORE the write ever reaches
    `SignalStore.record_route_qualification` (which independently enforces
    the ladder-prerequisite and feedback-capability checks itself -- this
    is defense in depth, not the only place they're enforced):

    1. `adapter_type` must match a currently-registered broker adapter's
       real `.name` (`GET /brokers`) -- there is no such thing as a
       qualification record for code that isn't even wired up in this
       deployment.
    2. If that adapter declares a real, code-verified
       `supported_asset_classes` restriction (`BrokerAdapter.
       supported_asset_classes`), the requested `asset_class` must be in
       it -- a route this adapter's own `place_order` cannot even submit
       cannot honestly be qualified for anything.
    """
    brokers_by_name: dict[str, BrokerAdapter] = {}
    for registered_broker in brokers.values():
        brokers_by_name.setdefault(registered_broker.name, registered_broker)
    broker = brokers_by_name.get(request.adapter_type)
    if broker is None:
        raise HTTPException(
            status_code=400,
            detail=f"adapter_type '{request.adapter_type}' is not a currently-registered broker adapter (see GET /brokers)",
        )
    asset_class = AssetClass(request.asset_class)
    if broker.supported_asset_classes is not None and asset_class not in broker.supported_asset_classes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"adapter '{request.adapter_type}' declares supported_asset_classes="
                f"{sorted(a.value for a in broker.supported_asset_classes)}, which does not include "
                f"'{request.asset_class}' -- this adapter's own place_order cannot submit this asset class at all"
            ),
        )

    try:
        result = store.record_route_qualification(
            adapter_type=request.adapter_type,
            route_key=request.route_key,
            asset_class=request.asset_class,
            product_type=request.product_type,
            state=request.state,
            supports_feedback=broker.has_account_order_position_feedback,
            recorded_by="owner",
            notes=request.notes,
        )
    except QualificationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return result


@app.get("/providers")
async def list_provider_overrides(_owner: dict = Depends(require_owner_read)) -> dict:
    """Every configured provider/analyst settings override (`config/providers.yaml`)
    and the effective settings it would resolve to for each of this
    service's destination accounts — so "what does this analyst's signal
    actually do to my sizing/protection here" is answerable without
    reading YAML and doing the account->provider->analyst merge by hand.
    See app/providers.py for the precedence rules."""
    result: list[dict[str, Any]] = []
    for provider in provider_registry.providers.values():
        entry: dict[str, Any] = {
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


# --- Live config management: accounts, routing rules, providers/analysts ---
#
# These persist to SignalStore's config_* tables (app/db.py) AND mutate the
# live `routing_config`/`provider_registry` objects IN PLACE (never
# reassigned — `engine.routing`/`engine.provider_registry` hold the same
# references) so a change here reaches the very next signal, no restart.
# This is the layer that makes "add/manage everything through the GUI"
# real instead of "edit YAML and restart" — see README.md's "Managing
# config through the GUI/API" section for what's still NOT covered here
# (pull-based bot sources like Telegram/Discord still need an env var +
# restart; see that section for exactly why).


def _reload_routing_config() -> None:
    fresh = load_routing_config_from_store(store)
    routing_config.accounts.clear()
    routing_config.accounts.update(fresh.accounts)
    routing_config.rules[:] = fresh.rules


def _reload_provider_registry() -> None:
    fresh = load_provider_registry_from_store(store)
    provider_registry.providers.clear()
    provider_registry.providers.update(fresh.providers)


def _reject_bool_scaling_value(v: Any) -> Any:
    # RISK-04: a bare `float` field still accepts a JSON boolean (pydantic
    # coerces True/False to 1.0/0.0) -- a multiplier or fixed_quantity of
    # `true`/`false` is never a real sizing value, so reject it explicitly
    # before pydantic's own float coercion discards the distinction.
    if isinstance(v, bool):
        raise ValueError("must be a number, not a boolean")
    return v


class AccountRequest(BaseModel):
    account_id: str
    broker: str
    multiplier: float = Field(default=1.0, gt=0)
    fixed_quantity: float | None = Field(default=None, gt=0)
    symbol_map: dict[str, str] = {}
    enabled: bool = True
    managed_lifecycle: bool = False
    max_notional_exposure: float | None = Field(default=None, gt=0)
    risk_percent_of_equity: float | None = Field(default=None, gt=0, le=1)
    #: P0-5: explicit management-recipe declaration -- omitted, this is
    #: derived from `managed_lifecycle` the same way
    #: DestinationAccount.__post_init__ / SignalStore.upsert_config_account
    #: both already do. See app/models.py's `ManagementRecipe`.
    management_recipe: str | None = None
    qualification_level: str | None = None
    #: P0-5: off by default -- see DestinationAccount.exclusive_writer_qualified's
    #: own docstring for exactly what setting this True asserts and allows.
    exclusive_writer_qualified: bool = False

    _reject_bool_multiplier = field_validator(
        "multiplier", "fixed_quantity", "max_notional_exposure", "risk_percent_of_equity", mode="before"
    )(_reject_bool_scaling_value)

    @field_validator("management_recipe")
    @classmethod
    def _validate_management_recipe(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            ManagementRecipe(v)
        except ValueError as exc:
            raise ValueError(
                f"management_recipe must be one of {[m.value for m in ManagementRecipe]}"
            ) from exc
        return v


@app.get("/accounts")
async def list_accounts(_owner: dict = Depends(require_owner_read)) -> dict:
    """Every live-managed destination account. `broker` values not currently
    in `GET /brokers`' list will error at signal time ("no broker adapter
    registered") — creating the account here doesn't itself register a
    broker adapter (that still needs the broker's own env-var credentials
    and, for optional ones, the package installed; see README.md)."""
    return {"accounts": store.list_config_accounts()}


@app.post("/accounts")
async def create_or_update_account(request: AccountRequest, _owner: dict = Depends(require_owner)) -> dict:
    """Create or update (by `account_id`) a destination account. Takes
    effect on the very next signal — no restart. This does NOT set broker
    credentials: those remain environment variables per the project's
    "never store secrets in config" rule (see README.md's Security
    notes) — create the account here, then set that broker's
    `{BROKER}_{ACCOUNT_ID}_...` env vars separately.

    EXE-10: changing `broker` on an account that has real exposure
    (`account_id` still shows up in list_open_positions or an open
    managed lifecycle) is refused -- the tracked position was recorded
    against the OLD broker; retargeting the account to a different one
    would strand it with nothing that ever placed or can now manage its
    exit."""
    existing = next((a for a in store.list_config_accounts() if a["account_id"] == request.account_id), None)
    if existing is not None and existing["broker"] != request.broker and _account_has_exposure(request.account_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"account '{request.account_id}' has an open position/lifecycle tracked against broker "
                f"'{existing['broker']}' -- refusing to change its broker to '{request.broker}' and strand it"
            ),
        )
    store.upsert_config_account(
        account_id=request.account_id,
        broker=request.broker,
        multiplier=request.multiplier,
        fixed_quantity=request.fixed_quantity,
        symbol_map=request.symbol_map,
        enabled=request.enabled,
        managed_lifecycle=request.managed_lifecycle,
        max_notional_exposure=request.max_notional_exposure,
        risk_percent_of_equity=request.risk_percent_of_equity,
        management_recipe=request.management_recipe,
        qualification_level=request.qualification_level,
        exclusive_writer_qualified=request.exclusive_writer_qualified,
    )
    _reload_routing_config()
    return {"account_id": request.account_id, "status": "saved"}


@app.delete("/accounts/{account_id}")
async def delete_account(account_id: str, _owner: dict = Depends(require_owner)) -> dict:
    """EXE-10: refuses to delete an account that still has real exposure
    (an open tracked position or an open managed lifecycle) -- deleting
    it would drop the only routing/broker configuration that could ever
    manage or close that exposure, stranding it. Close or flatten the
    position first (see POST /positions/.../close, POST /accounts/.../flatten)."""
    if _account_has_exposure(account_id):
        raise HTTPException(
            status_code=409,
            detail=f"account '{account_id}' has an open position/lifecycle -- close or flatten it before deleting",
        )
    store.delete_config_account(account_id)
    _reload_routing_config()
    return {"account_id": account_id, "status": "deleted"}


def _account_has_exposure(account_id: str) -> bool:
    if any(p["account_id"] == account_id for p in store.list_open_positions()):
        return True
    return any(lifecycle.key[0] == account_id for lifecycle in lifecycle_manager.list_open_lifecycles())


class RoutingRuleRequest(BaseModel):
    source: str
    destinations: list[str]
    symbol_filter: list[str] | None = None


@app.get("/routing-rules")
async def list_routing_rules(_owner: dict = Depends(require_owner_read)) -> dict:
    return {"routing_rules": store.list_config_routing_rules()}


@app.post("/routing-rules")
async def create_routing_rule(request: RoutingRuleRequest, _owner: dict = Depends(require_owner)) -> dict:
    rule_id = store.insert_config_routing_rule(request.source, request.destinations, request.symbol_filter)
    _reload_routing_config()
    return {"id": rule_id, "status": "created"}


@app.put("/routing-rules/{rule_id}")
async def update_routing_rule(rule_id: int, request: RoutingRuleRequest, _owner: dict = Depends(require_owner)) -> dict:
    store.update_config_routing_rule(rule_id, request.source, request.destinations, request.symbol_filter)
    _reload_routing_config()
    return {"id": rule_id, "status": "updated"}


@app.delete("/routing-rules/{rule_id}")
async def delete_routing_rule(rule_id: int, _owner: dict = Depends(require_owner)) -> dict:
    store.delete_config_routing_rule(rule_id)
    _reload_routing_config()
    return {"id": rule_id, "status": "deleted"}


class RoutingSimulateRequest(BaseModel):
    source: str
    symbol: str
    side: str = "buy"
    asset_class: AssetClass = AssetClass.CRYPTO
    analyst: str | None = None
    quantity: float | None = None
    price: float | None = None


@app.post("/routing-rules/simulate")
async def simulate_routing_rules(request: RoutingSimulateRequest, _owner: dict = Depends(require_owner_read)) -> dict:
    """TR-11 "if this signal arrived now, what would happen" dry run.

    Never ingests a signal (no `store.save_signal`), never places an
    order, never touches `positions`/`orders`, and leaves the capital
    allocator's real in-memory reservation ledger exactly as it found it
    (see the capital_reservation block below) -- same "no side effects"
    guarantee `POST /sources/{x}/classify-messages` already makes for its
    own sandbox.

    Every check below reuses the EXACT real function the engine calls at
    real signal-ingestion time, never a client-side or server-side
    reimplementation that could drift from it:
    - rule matching/precedence/dedup: `RoutingConfig.evaluate` (app/
      routing.py) -- the same code `destinations_for` (and therefore
      `app/engine.py`'s `_handle_signal`) calls for every real signal.
    - provider/analyst entry-admission override: `engine._effective_settings`
      (app/providers.py's `ProviderRegistry.effective_settings`) -- the
      exact same account->provider->analyst merge a real signal resolves.
    - broker/asset-class admission: `broker.can_trade_asset_class` -- the
      same registered broker adapter instance the engine itself holds.
    - capital reservation: `engine._try_reserve_capital`, the same method
      `_handle_signal` calls immediately before submitting to the broker,
      called for real here too (so it reads this account's real confirmed
      exposure and the real shared `CapitalAllocator`'s real current
      pending-reservation state) -- but any reservation it makes is
      released immediately after (`capital_allocator.release`), before
      this request returns, so this dry run never leaves a phantom
      reservation behind for a real signal arriving moments later to
      collide with.

    Deduplication (SIG-01's `handle_signal`-level replay guard, and the
    webhook route's own `Idempotency-Key`/event-id cache) is deliberately
    NOT evaluated here -- both are real mechanisms, but both key off a
    real prior delivery (an existing `signals.id` already processed, or a
    cache entry for a real previously-seen webhook key/event id) that a
    hypothetical signal invented for this dry run has no counterpart to.
    Guessing an answer for it would be worse than admitting there's
    nothing real to check -- see `deduplication` in the response, a
    `Components.renderCapabilityState('not_tracked', ...)` case.
    """
    try:
        side = Side(request.side.strip().lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid side '{request.side}' (must be buy, sell or close)"
        ) from exc

    include_disabled = side == Side.CLOSE
    destinations, trace = routing_config.evaluate(request.source, request.symbol, include_disabled=include_disabled)

    # Same real DB row order `_reload_routing_config` populated
    # `routing_config.rules` from (see app/routing.py's
    # `load_routing_config_from_store` and this file's own docstring on
    # "DB insertion/id order") -- zipped by position only to label each
    # trace entry with its real rule id; the matching decision itself
    # came entirely from `evaluate` above, never recomputed here.
    rule_rows = store.list_config_routing_rules()
    rules_out = []
    for idx, entry in enumerate(trace):
        rule = entry["rule"]
        rule_id = rule_rows[idx]["id"] if idx < len(rule_rows) else None
        row: dict[str, Any] = {
            "id": rule_id,
            "source": rule.source,
            "destinations": rule.destinations,
            "symbol_filter": rule.symbol_filter,
            "matched": entry["matched"],
        }
        if entry["matched"]:
            row["admitted_accounts"] = entry["admitted"]
            row["deduped_accounts"] = entry["deduped"]
            row["paused_accounts"] = entry["paused"]
        else:
            row["reason"] = entry["reason"]
        rules_out.append(row)

    synthetic_signal = Signal(
        source=request.source,
        symbol=request.symbol,
        side=side,
        asset_class=request.asset_class,
        analyst=request.analyst,
        quantity=request.quantity,
        price=request.price,
    )

    accounts_out = []
    final_destinations: list[str] = []
    for account in destinations:
        effective = engine._effective_settings(synthetic_signal, account)
        entry_allowed = not (effective.enabled is False and side != Side.CLOSE)

        broker = brokers.get(account.broker)
        broker_registered = broker is not None
        asset_class_ok = broker is not None and broker.can_trade_asset_class(request.asset_class)

        capital_check: dict[str, Any]
        capital_admitted = True
        if side == Side.CLOSE:
            capital_check = {
                "status": "not_applicable",
                "reason": "capital reservation (E03) only gates new entries -- a CLOSE signal is never admission-gated by it.",
            }
        elif account.managed_lifecycle:
            capital_check = {
                "status": "not_applicable",
                "reason": "this account is managed_lifecycle -- the real engine routes its entries through PositionLifecycleManager, which never calls the capital allocator's admission gate at all.",
            }
        elif not (entry_allowed and broker_registered and asset_class_ok):
            capital_check = {
                "status": "not_applicable",
                "reason": "this signal is already rejected before the real engine would reach the capital-admission step (see entry_admission/asset_class above).",
            }
        elif (
            account.max_notional_exposure is None
            and account.risk_percent_of_equity is None
            and engine.max_owner_notional_exposure is None
        ):
            capital_check = {
                "status": "skipped",
                "reason": "no capital/risk exposure gate (max_notional_exposure, risk_percent_of_equity, or an owner-wide ceiling) is configured for this account -- the real engine's own _try_reserve_capital is a no-op the exact same way (see app/capital_allocator.py).",
            }
        else:
            quantity = account.fixed_quantity if account.fixed_quantity is not None else (
                (request.quantity if request.quantity is not None else 1.0) * account.multiplier
            )
            # A gate IS configured, so a missing price is now a real
            # rejection (fail-closed), not a skip -- `_try_reserve_capital`
            # itself makes that call; this dry run just reports whatever it
            # genuinely decides, never a separately reimplemented "skip".
            admitted, notional, rejection = await engine._try_reserve_capital(account, synthetic_signal, quantity)
            if admitted:
                # Real admit() really reserved `notional` against the real
                # shared CapitalAllocator -- release it immediately so this
                # dry run leaves the real in-memory ledger exactly as it
                # found it (see this endpoint's own docstring).
                engine.capital_allocator.release(account.account_id, notional)
            capital_admitted = admitted
            capital_check = {
                "status": "would_admit" if admitted else "would_reject",
                "requested_notional": notional,
                "deployed_notional": confirmed_open_notional(store, account.account_id).notional,
                "reserved_notional": engine.capital_allocator.pending_reservation(account.account_id),
                "max_notional_exposure": account.max_notional_exposure,
                "reason": None if admitted else rejection.message if rejection else None,
            }

        would_receive = entry_allowed and broker_registered and asset_class_ok and capital_admitted
        if would_receive:
            final_destinations.append(account.account_id)

        accounts_out.append({
            "account_id": account.account_id,
            "broker": account.broker,
            "symbol_for_account": symbol_for_account(synthetic_signal, account),
            "entry_admission": {
                "status": "admitted" if entry_allowed else "rejected",
                "effective_enabled": effective.enabled,
                "reason": None if entry_allowed else "disabled at account/provider/analyst level (EXE-10) and this is not a CLOSE signal",
            },
            "asset_class_admission": {
                "status": "admitted" if asset_class_ok else "rejected",
                "reason": None if broker_registered and asset_class_ok else (
                    f"no broker adapter registered for '{account.broker}'" if not broker_registered
                    else f"broker '{account.broker}' cannot trade asset_class='{request.asset_class.value}'"
                ),
            },
            "capital_reservation": capital_check,
            "would_receive_this_signal": would_receive,
        })

    return {
        "signal": {
            "source": request.source,
            "symbol": request.symbol,
            "side": side.value,
            "asset_class": request.asset_class.value,
            "analyst": request.analyst,
            "quantity": request.quantity,
            "price": request.price,
        },
        "rules_evaluated": rules_out,
        "accounts": accounts_out,
        "deduplication": {
            "status": "not_tracked",
            "reason": (
                "This engine's real dedup guards (SIG-01's handle_signal replay-by-signal-id, and the "
                "webhook route's own Idempotency-Key/event-id response cache) both key off a real prior "
                "delivery this hypothetical signal has no counterpart to -- there is nothing real to "
                "evaluate for a dry run, so this is honestly not_tracked rather than guessed."
            ),
        },
        "final_destinations": final_destinations,
    }


class PositionImpactRequest(BaseModel):
    rule_id: int | None = None
    source: str
    destinations: list[str]
    symbol_filter: list[str] | None = None


@app.post("/routing-rules/position-impact")
async def routing_rule_position_impact(request: PositionImpactRequest, _owner: dict = Depends(require_owner_read)) -> dict:
    """TR-11's "existing positions vs future signals" check for a pending
    routing-rule edit, computed for real from this store's own real open
    positions and their real originating signal (never fabricated
    example positions).

    Builds one hypothetical `RoutingConfig` identical to the live one
    except this one rule (`rule_id`, or a brand-new rule if omitted) is
    replaced by the operator's pending, not-yet-saved field values, then
    calls the SAME real `RoutingConfig.destinations_for` used at real
    signal-ingestion time against both the live config and the
    hypothetical one -- never a reimplemented approximation of what
    routing would do.

    A position is in scope for this rule's diff only if its most recent
    real FILLED order's originating signal (`app/db.py`'s
    `list_filled_orders_with_signal_chronological`, an existing
    signal->order join, not a new query) came from this rule's `source` --
    a position from a different provider was never routed by this rule
    and saving this edit cannot change how it was already opened.
    """
    try:
        pending_rule = RoutingRule(
            source=request.source, destinations=request.destinations, symbol_filter=request.symbol_filter
        )
    except Exception as exc:  # noqa: BLE001 - surface a 400, not a 500, for a malformed pending rule
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pending_rules: list[RoutingRule] = []
    replaced = False
    for row in store.list_config_routing_rules():
        if request.rule_id is not None and row["id"] == request.rule_id:
            pending_rules.append(pending_rule)
            replaced = True
        else:
            pending_rules.append(RoutingRule(source=row["source"], destinations=row["destinations"], symbol_filter=row["symbol_filter"]))
    if not replaced:
        pending_rules.append(pending_rule)
    pending_config = RoutingConfig(rules=pending_rules, accounts=routing_config.accounts)

    # Real origin attribution: the most recent real FILLED order for each
    # (account_id, symbol) this store has recorded, joined to its real
    # originating signal's source/analyst -- never inferred or guessed.
    origin_by_key: dict[tuple[str, str], dict] = {}
    for fill in store.list_filled_orders_with_signal_chronological():
        origin_by_key[(fill["account_id"], fill["symbol"])] = fill  # chronological asc -> last write is most recent

    positions_out = []
    for pos in store.list_open_positions():
        origin = origin_by_key.get((pos["account_id"], pos["symbol"]))
        if origin is None or origin["source"] != request.source:
            continue  # not opened via this rule's source -- out of scope for this edit's diff

        now_entry = any(a.account_id == pos["account_id"] for a in routing_config.destinations_for(request.source, pos["symbol"], include_disabled=False))
        now_close = any(a.account_id == pos["account_id"] for a in routing_config.destinations_for(request.source, pos["symbol"], include_disabled=True))
        pending_entry = any(a.account_id == pos["account_id"] for a in pending_config.destinations_for(request.source, pos["symbol"], include_disabled=False))
        pending_close = any(a.account_id == pos["account_id"] for a in pending_config.destinations_for(request.source, pos["symbol"], include_disabled=True))

        if now_close and not pending_close:
            impact = "exit_path_removed"
        elif now_entry != pending_entry:
            impact = "future_entries_change"
        else:
            impact = "unaffected"

        positions_out.append({
            "account_id": pos["account_id"],
            "symbol": pos["symbol"],
            "net_quantity": pos["net_quantity"],
            "origin_source": origin["source"],
            "origin_analyst": origin["analyst"],
            "would_route_now": {"entry": now_entry, "close": now_close},
            "would_route_after_save": {"entry": pending_entry, "close": pending_close},
            "impact": impact,
        })

    return {"source": request.source, "positions": positions_out}


class ProviderRequest(BaseModel):
    display_name: str = ""
    multiplier: float | None = Field(default=None, gt=0)
    fixed_quantity: float | None = Field(default=None, gt=0)
    managed_lifecycle: bool | None = None
    enabled: bool | None = None

    _reject_bool_multiplier = field_validator("multiplier", "fixed_quantity", mode="before")(
        _reject_bool_scaling_value
    )


@app.post("/providers/{provider_id}")
async def create_or_update_provider(provider_id: str, request: ProviderRequest, _owner: dict = Depends(require_owner)) -> dict:
    store.upsert_config_provider(
        provider_id,
        request.display_name,
        request.multiplier,
        request.fixed_quantity,
        request.managed_lifecycle,
        request.enabled,
    )
    _reload_provider_registry()
    return {"provider_id": provider_id, "status": "saved"}


@app.delete("/providers/{provider_id}")
async def delete_provider(provider_id: str, _owner: dict = Depends(require_owner)) -> dict:
    store.delete_config_provider(provider_id)
    _reload_provider_registry()
    return {"provider_id": provider_id, "status": "deleted"}


class AnalystRequest(BaseModel):
    display_name: str = ""
    multiplier: float | None = Field(default=None, gt=0)
    fixed_quantity: float | None = Field(default=None, gt=0)
    managed_lifecycle: bool | None = None
    enabled: bool | None = None

    _reject_bool_multiplier = field_validator("multiplier", "fixed_quantity", mode="before")(
        _reject_bool_scaling_value
    )


@app.post("/providers/{provider_id}/analysts/{analyst_id}")
async def create_or_update_analyst(provider_id: str, analyst_id: str, request: AnalystRequest, _owner: dict = Depends(require_owner)) -> dict:
    store.upsert_config_analyst(
        provider_id,
        analyst_id,
        request.display_name,
        request.multiplier,
        request.fixed_quantity,
        request.managed_lifecycle,
        request.enabled,
    )
    _reload_provider_registry()
    return {"provider_id": provider_id, "analyst_id": analyst_id, "status": "saved"}


@app.delete("/providers/{provider_id}/analysts/{analyst_id}")
async def delete_analyst(provider_id: str, analyst_id: str, _owner: dict = Depends(require_owner)) -> dict:
    store.delete_config_analyst(provider_id, analyst_id)
    _reload_provider_registry()
    return {"provider_id": provider_id, "analyst_id": analyst_id, "status": "deleted"}


# --- Signal-provider value/subscription management (app/provider_value.py,
# app/provider_scout.py) -- cost/renewal tracking per provider, a
# win-rate/profit-factor breakdown per provider AND asset class computed
# from this service's own confirmed execution journal, and the scheduled
# free-provider promotion scan's results. See both modules' docstrings for
# the FIFO attribution methodology and its disclosed scope limits (most
# importantly: a managed-lifecycle stop/target/trailing exit is NOT
# visible to any of this).


class ProviderSubscriptionRequest(BaseModel):
    display_name: str = ""
    cost_amount: float = Field(default=0.0, ge=0)
    currency: str = "USD"
    billing_cycle: str = "monthly"  # monthly | annual | one_time | free
    subscribed_since: str | None = None  # ISO date; None keeps the existing anchor on an update, else defaults to today
    renewal_date: str | None = None  # ISO date, informational
    status: str = "active"  # active | cancelled | candidate
    notes: str = ""

    _reject_bool_cost = field_validator("cost_amount", mode="before")(_reject_bool_scaling_value)

    @field_validator("billing_cycle")
    @classmethod
    def _valid_billing_cycle(cls, v: str) -> str:
        if v not in ("monthly", "annual", "one_time", "free"):
            raise ValueError("billing_cycle must be one of: monthly, annual, one_time, free")
        return v

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        if v not in ("active", "cancelled", "candidate"):
            raise ValueError("status must be one of: active, cancelled, candidate")
        return v

    @field_validator("subscribed_since", "renewal_date")
    @classmethod
    def _valid_iso_date(cls, v: str | None) -> str | None:
        if v is not None:
            date.fromisoformat(v)  # raises ValueError with a clear message if malformed
        return v


@app.get("/providers/subscriptions")
async def list_provider_subscriptions(_owner: dict = Depends(require_owner_read)) -> dict:
    return {"subscriptions": store.list_provider_subscriptions()}


@app.put("/providers/{provider_id}/subscription")
async def upsert_provider_subscription(
    provider_id: str, request: ProviderSubscriptionRequest, _owner: dict = Depends(require_owner)
) -> dict:
    store.upsert_provider_subscription(
        provider_id,
        display_name=request.display_name,
        cost_amount=request.cost_amount,
        currency=request.currency,
        billing_cycle=request.billing_cycle,
        subscribed_since=request.subscribed_since,
        renewal_date=request.renewal_date,
        status=request.status,
        notes=request.notes,
    )
    # This provider is now explicitly tracked -- it's no longer a "free,
    # unadopted" candidate app/provider_scout.py should keep surfacing.
    store.delete_provider_candidates_for_source(provider_id)
    return {"provider_id": provider_id, "status": "saved"}


@app.delete("/providers/{provider_id}/subscription")
async def delete_provider_subscription(provider_id: str, _owner: dict = Depends(require_owner)) -> dict:
    store.delete_provider_subscription(provider_id)
    return {"provider_id": provider_id, "status": "deleted"}


@app.get("/providers/value")
async def get_provider_value(
    source: str | None = None,
    analyst: str | None = None,
    asset_class: str | None = None,
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """Every (source, analyst, asset_class) this service has real,
    confirmed-fill data for, with a subscription cost + "still worth
    paying for" verdict attached wherever a `provider_subscriptions` row
    exists for that source. Filter with any combination of the three
    query params; `analyst=` (empty string) means "no analyst on the
    signal," matching signals that never carried one."""
    return {"providers": compute_provider_value_report(store, source=source, analyst=analyst, asset_class=asset_class)}


@app.get("/providers/value/episodes")
async def get_provider_value_episodes(
    source: str | None = None,
    analyst: str | None = None,
    asset_class: str | None = None,
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """TR-EPISODE-01: the corrected, episode-based counterpart to
    `GET /providers/value` above -- see app/provider_value.py's module
    docstring for exactly why this replaces it as the source of truth for
    any promotion/cancellation/capital-weighting/portfolio-selection
    decision (a stop/target/time_exit exit is a first-class, episode-
    closing execution here, and a multi-fill reduction of one position
    counts once). Added alongside the existing endpoint rather than
    replacing it in place, since `GET /providers/value`'s response shape
    is a documented, already-depended-on contract this pass doesn't
    change."""
    return {
        "providers": compute_provider_value_report_from_episodes(
            store, source=source, analyst=analyst, asset_class=asset_class
        )
    }


@app.get("/providers/candidates")
async def list_provider_candidates(_owner: dict = Depends(require_owner_read)) -> dict:
    """app/provider_scout.py's last scheduled scan results for every free
    (source, analyst, asset_class) that isn't yet a tracked
    `provider_subscriptions` row -- see that module's docstring for the
    recommendation logic and `PROVIDER_SCOUT_INTERVAL_SECONDS` for how
    often this is recomputed."""
    return {"candidates": store.list_provider_candidates()}


class PromoteCandidateRequest(BaseModel):
    source: str
    analyst: str | None = None
    display_name: str = ""
    cost_amount: float = Field(default=0.0, ge=0)
    billing_cycle: str = "free"

    _reject_bool_cost = field_validator("cost_amount", mode="before")(_reject_bool_scaling_value)


@app.post("/providers/candidates/promote")
async def promote_provider_candidate(request: PromoteCandidateRequest, _owner: dict = Depends(require_owner)) -> dict:
    """Adopt a scouted free provider as a formally tracked one -- creates a
    `provider_subscriptions` row (defaulting to `cost_amount=0`/
    `billing_cycle="free"`, since this is promoting something that was
    free) and clears every candidate snapshot row for this `source` (see
    `delete_provider_candidates_for_source`). Deliberately does NOT touch
    routing rules or provider/analyst settings overrides -- promoting a
    provider here is a cost/value-tracking decision, not a "start trading
    this more aggressively" one; if this source doesn't already have a
    routing rule, its signals were never actually being executed, and this
    endpoint doesn't change that."""
    store.upsert_provider_subscription(
        request.source,
        display_name=request.display_name or request.source,
        cost_amount=request.cost_amount,
        billing_cycle=request.billing_cycle,
        status="active",
    )
    store.delete_provider_candidates_for_source(request.source)
    return {"source": request.source, "status": "promoted"}


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
        pending_entry = lifecycle.pending_entry
        snapshot.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "owned_quantity": lifecycle.confirmed_owned_quantity,
                "covered_quantity": lifecycle.covered_quantity,
                "uncovered_quantity": lifecycle.uncovered_quantity,
                "stop_status": lifecycle.stop.status.value,
                "stop_price": lifecycle.stop.broker_confirmed_price,
                # PU-A1: real MAE/MFE tracking (see app/lifecycle/models.py's
                # PositionLifecycle.mae/mfe/observe_price) -- entry_price/the
                # two extremes/mae/mfe are all None when genuinely unknown
                # (no entry price captured, or no real price observation has
                # arrived yet for this account/symbol's broker), never a
                # fabricated 0. has_price_data distinguishes that from a real
                # observation that simply hasn't moved.
                "entry_price": lifecycle.entry_price,
                "highest_price_since_entry": lifecycle.highest_price_since_entry,
                "highest_price_at": lifecycle.highest_price_at.isoformat() if lifecycle.highest_price_at else None,
                "lowest_price_since_entry": lifecycle.lowest_price_since_entry,
                "lowest_price_at": lifecycle.lowest_price_at.isoformat() if lifecycle.lowest_price_at else None,
                "mae": lifecycle.mae,
                "mfe": lifecycle.mfe,
                "has_price_data": lifecycle.has_price_data,
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
                # Non-null while an entry order's broker response was PENDING and
                # still hasn't resolved -- this position has NO protective stop
                # yet (owned_quantity is still 0 until resolve_pending_entry calls
                # on_entry_fill; see app/lifecycle/models.py's PendingEntry).
                "pending_entry": None
                if pending_entry is None
                else {
                    "broker_order_id": pending_entry.broker_order_id,
                    "requested_quantity": pending_entry.requested_quantity,
                    "confirmed_filled_quantity": pending_entry.confirmed_filled_quantity,
                },
            }
        )
    return snapshot


@app.get("/signals")
async def list_signals(limit: int = Query(default=50, ge=1, le=500), _owner: dict = Depends(require_owner_read)) -> dict:
    """Most recently received signals, newest first."""
    return {"signals": store.list_recent_signals(limit=limit)}


@app.get("/orders")
async def list_orders(
    limit: int = Query(default=50, ge=1, le=500), account_id: str | None = Query(default=None)
, _owner: dict = Depends(require_owner_read)) -> dict:
    """Most recent order results, newest first — optionally filtered to one account."""
    orders = store.list_recent_orders(limit=limit, account_id=account_id)
    # DB-0X: a real, already-tracked count -- exactly the rows
    # app/reconciliation.py's own poll loop treats as still needing a
    # broker readback before their true terminal outcome is known (see
    # SignalStore.list_pending_orders's own docstring: PENDING with a real
    # broker_order_id to re-check). Account-wide (not limited to this
    # page's `limit`), so it isn't silently undercounted by pagination;
    # still narrowed to `account_id` when the caller asked for one.
    unreconciled = [
        row for row in store.list_pending_orders() if account_id is None or row["account_id"] == account_id
    ]
    return {"orders": orders, "unreconciled_order_count": len(unreconciled)}


class ClassifyMessagesRequest(BaseModel):
    """E02 (bounded): batch-classify free-text messages against
    app/sources/text_parser.py's grammar, without ever creating or
    routing a live Signal -- for reviewing a source's message history
    (or trying candidate wording) offline. See that module's
    `classify_text_signal` docstring for the disposition outcomes."""

    texts: list[str]
    asset_class: AssetClass = AssetClass.CRYPTO
    analyst: str | None = None


@app.post("/sources/{source_name}/classify-messages")
async def classify_messages(
    source_name: str, request: ClassifyMessagesRequest, _owner: dict = Depends(require_owner_read)
) -> dict:
    """Never ingests a signal or touches routing/positions -- read-only
    analysis of what app/sources/text_parser.py's grammar would do with
    each message, for reviewing a channel's history or testing new
    wording before it's live. See app/sources/text_parser.py's
    classify_text_signal for the possible outcomes."""
    dispositions = classify_batch(request.texts, source=source_name, asset_class=request.asset_class, analyst=request.analyst)
    return {
        "dispositions": [
            {
                "text": d.text,
                "outcome": d.outcome.value,
                "detail": d.detail,
                "signal": (
                    None
                    if d.signal is None
                    else {
                        "symbol": d.signal.symbol,
                        "side": d.signal.side.value,
                        "asset_class": d.signal.asset_class.value,
                        "quantity": d.signal.quantity,
                        "price": d.signal.price,
                        "stop_loss": d.signal.stop_loss,
                        "take_profit": d.signal.take_profit,
                    }
                ),
            }
            for d in dispositions
        ]
    }


class ImportSignalsRequest(BaseModel):
    """E02 (bounded, history-import workflow): the owner's SELECTED subset
    of raw historical message texts to actually import as real `Signal`
    rows -- e.g. what a review table (raw message | classified result |
    import? checkbox) produced from an earlier `POST
    /sources/{source}/classify-messages` call in the same session.

    This endpoint does NOT trust any classification the caller may have
    seen client-side: every text here is re-run through the real
    `classify_batch` on the server, and only a message that resolves to
    PARSED becomes a Signal. Anything else (IGNORED/AMBIGUOUS/MISSING_DATA/
    NO_MATCH) is reported back as skipped, never imported, and never
    fabricated as parsed."""

    texts: list[str]
    asset_class: AssetClass = AssetClass.CRYPTO
    analyst: str | None = None
    #: Owner-chosen label for this import batch (e.g. "telegram-2024-history").
    #: Defaults to an auto-generated timestamp label when omitted. Stored on
    #: every imported Signal's `import_batch` column -- see that field's
    #: docstring in app/models.py for why this is the one honest,
    #: distinguishing marker between a backfilled and a live-received
    #: signal in this build.
    batch_label: str | None = None


@app.post("/sources/{source_name}/import-signals")
async def import_signals(
    source_name: str, request: ImportSignalsRequest, _owner: dict = Depends(require_owner)
) -> dict:
    """Owner-gated: classify the given historical messages with the real
    `classify_batch` (never a client-supplied classification) and persist
    only the ones that resolve to PARSED as real `Signal` rows, through
    this codebase's one existing signal-creation path
    (`SignalStore.save_signal` -- the same call the live webhook/bot
    ingestion path uses). Every imported row is tagged with
    `import_batch` so it stays honestly distinguishable from a signal
    that arrived live (see ImportSignalsRequest's docstring).

    A message that does not resolve to PARSED is never imported -- it is
    returned under `skipped` with its real outcome/detail instead."""
    label = request.batch_label or f"backfill:{datetime.now(timezone.utc).isoformat()}"
    dispositions = classify_batch(
        request.texts, source=source_name, asset_class=request.asset_class, analyst=request.analyst
    )
    imported = []
    skipped = []
    for d in dispositions:
        if d.signal is not None:
            d.signal.import_batch = label
            store.save_signal(d.signal)
            imported.append(
                {
                    "id": d.signal.id,
                    "text": d.text,
                    "symbol": d.signal.symbol,
                    "side": d.signal.side.value,
                    "asset_class": d.signal.asset_class.value,
                    "import_batch": d.signal.import_batch,
                }
            )
        else:
            skipped.append({"text": d.text, "outcome": d.outcome.value, "detail": d.detail})
    return {"batch_label": label, "imported": imported, "skipped": skipped}


# -- Track 5: Telegram collector registry (app/telegram_collectors.py) ------


class RegisterTelegramCollectorRequest(BaseModel):
    """See app/telegram_collectors.py's module docstring and
    docs/security/TELEGRAM_USER_LOGIN.md for the full registration
    procedure. `credential_env_var` is a REFERENCE (the name of an env
    var), never a credential value -- `validate_registration` rejects
    anything that doesn't look like a bare env-var name."""

    id: str
    connection_mode: str
    identity_ref: str
    credential_env_var: str
    chat_id: str
    provider_name: str
    topic_id: str | None = None
    allowed_uses: list[str] | None = None


@app.post("/telegram-collectors")
async def register_telegram_collector(
    request: RegisterTelegramCollectorRequest, _owner: dict = Depends(require_owner)
) -> dict:
    """Owner-gated: register (or re-describe) one Telegram collector.
    Never accepts or stores a credential value -- see
    `RegisterTelegramCollectorRequest`'s own docstring."""
    try:
        return store.register_telegram_collector(
            collector_id=request.id,
            connection_mode=request.connection_mode,
            identity_ref=request.identity_ref,
            credential_env_var=request.credential_env_var,
            chat_id=request.chat_id,
            provider_name=request.provider_name,
            topic_id=request.topic_id,
            allowed_uses=request.allowed_uses,
        )
    except TelegramCollectorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/telegram-collectors")
async def list_telegram_collectors(_owner: dict = Depends(require_owner_read)) -> dict:
    """Every registered collector, including its current `health_state`
    (point 8) -- a dashboard reads this, never a hardcoded green, for
    every collector regardless of `connection_mode`."""
    return {"collectors": store.list_telegram_collectors()}


@app.get("/telegram-collectors/{collector_id}")
async def get_telegram_collector(collector_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    collector = store.get_telegram_collector(collector_id)
    if collector is None:
        raise HTTPException(status_code=404, detail=f"no telegram collector registered with id={collector_id!r}")
    return collector


class RecordTelegramCollectorQualificationRequest(BaseModel):
    """Point 4/8: real evidence of authorized message receipt -- e.g.
    `{"observed_message_id": "42", "method": "manual_owner_confirmation"}`.
    Never a claim this endpoint verifies itself; the CALLER is asserting
    they have real evidence (this mirrors app/qualification.py's own
    `POST /qualifications` -- a deliberate, owner-asserted record, not an
    automatic technical check)."""

    evidence: dict
    noforwards: bool | None = None


@app.post("/telegram-collectors/{collector_id}/qualification-evidence")
async def record_telegram_collector_qualification_evidence(
    collector_id: str,
    request: RecordTelegramCollectorQualificationRequest,
    _owner: dict = Depends(require_owner),
) -> dict:
    try:
        store.record_telegram_collector_qualification_evidence(
            collector_id, evidence=request.evidence, noforwards=request.noforwards
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return store.get_telegram_collector(collector_id)  # type: ignore[return-value]


# -- Track 10: notification-bridge device registry (app/notification_bridge.py) --


class RegisterNotificationBridgeDeviceRequest(BaseModel):
    """Owner-gated device pairing -- see
    app/notification_bridge.py's module docstring. `provider_mapping`
    (app_package -> {"provider_name": ..., "analyst": ...}) is optional;
    a package with no entry falls back to using its bare package name as
    Signal.source."""

    device_id: str
    app_packages: list[str]
    provider_mapping: dict[str, dict[str, str]] | None = None


@app.post("/notification-bridge/devices")
async def register_notification_bridge_device(
    request: RegisterNotificationBridgeDeviceRequest, _owner: dict = Depends(require_owner)
) -> dict:
    """Owner-gated: register (or re-pair) one Android notification-bridge
    device. Generates a fresh pairing token, returns it ONCE in this
    response body (never stored in plain text -- only its argon2id hash
    is persisted), for the owner to type into the Android app's settings
    screen themselves. This mirrors register_telegram_collector's own
    idempotent re-registration semantics."""
    try:
        token = generate_pairing_token()
        device = store.register_notification_bridge_device(
            device_id=request.device_id,
            pairing_token_hash=hash_pairing_token(token),
            app_packages=request.app_packages,
            provider_mapping=request.provider_mapping,
        )
    except NotificationBridgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response = dict(device)
    response.pop("pairing_token_hash", None)
    response["pairing_token"] = token
    return response


@app.get("/notification-bridge/devices")
async def list_notification_bridge_devices(_owner: dict = Depends(require_owner_read)) -> dict:
    """Every registered device, including its current (read-time-computed,
    never stale-green) `health_state` -- point 8, same convention as
    `GET /telegram-collectors`."""
    devices = [
        {k: v for k, v in d.items() if k != "pairing_token_hash"} for d in store.list_notification_bridge_devices()
    ]
    return {"devices": devices}


@app.get("/notification-bridge/devices/{device_id}")
async def get_notification_bridge_device(device_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    device = store.get_notification_bridge_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no notification-bridge device registered with device_id={device_id!r}")
    device = dict(device)
    device.pop("pairing_token_hash", None)
    return device


@app.get("/notification-bridge/devices/{device_id}/events")
async def list_notification_bridge_device_events(device_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """Audit read: every notification event this device has ever
    submitted, whatever its outcome -- used to verify the real,
    on-device capture/upload flow is working (the user must pair a real
    device and report back; nothing here can be verified from this
    session alone -- see mobile/notification-bridge/README.md)."""
    if store.get_notification_bridge_device(device_id) is None:
        raise HTTPException(status_code=404, detail=f"no notification-bridge device registered with device_id={device_id!r}")
    return {"device_id": device_id, "events": store.list_notification_bridge_events(device_id)}


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


class NotificationBridgeEventPayload(BaseModel):
    """One captured Android notification, exactly as the companion app's
    `NotificationListenerService` observed it -- see
    mobile/notification-bridge/README.md's extraction table for which
    `Notification`/`StatusBarNotification` field each maps to."""

    app_package: str
    notification_key: str
    posted_at: datetime
    title: str | None = None
    text: str | None = None
    expanded_text: str | None = None
    is_group_conversation: bool = False
    conversation_participants: list[str] | None = None
    content_completeness: str
    #: Device-reported receipt time -- audit-only (see
    #: _process_notification_bridge_event's docstring for why the stale-
    #: backlog decision uses this SERVER's own clock, not this field).
    received_at: datetime | None = None


class NotificationBridgeIngestRequest(BaseModel):
    events: list[NotificationBridgeEventPayload]


async def _process_notification_bridge_event(device: dict, event: NotificationBridgeEventPayload) -> dict:
    """Classify and (when eligible) route ONE captured notification.
    Never raises for an individual event's own content problems -- a
    malformed event in a batch is reported back under its own result
    entry, never allowed to fail the whole upload (same "one bad item
    doesn't sink the batch" convention as `classify_batch`/
    `import_signals`).

    Point 4 (stale-backlog policy): staleness is judged against THIS
    SERVER's own current clock, not the device-reported `received_at` --
    a device that was offline and is now uploading a backlog cannot make
    its own delayed notifications look fresh by also lying about when it
    received them. `posted_at` (when the notification actually appeared,
    per the device) vs this server's own `now` is the one honest
    comparison.

    Point 5 (content-completeness policy): a notification whose
    `content_completeness` is not exactly "complete" is NEVER parsed and
    routed as if it were the full alert -- it is recorded under the
    explicit `needs_review_incomplete_content` classification and, if it
    happens to parse anyway, tagged and stored via the same
    import-only path stale backlog uses (never `engine.handle_signal`)."""
    device_id = device["device_id"]
    server_received_at = datetime.now(timezone.utc)

    if event.app_package not in device["app_packages"]:
        store.update_notification_bridge_device_health(
            device_id,
            "unauthorized_app_package",
            detail=f"notification from unauthorized app_package={event.app_package!r}",
        )
        return {
            "notification_key": event.notification_key,
            "app_package": event.app_package,
            "classification": "rejected_unauthorized_app_package",
        }

    try:
        completeness = validate_content_completeness(event.content_completeness)
    except NotificationBridgeError as exc:
        return {
            "notification_key": event.notification_key,
            "app_package": event.app_package,
            "classification": "rejected_invalid_content_completeness",
            "detail": str(exc),
        }

    new_hash = content_fingerprint(event.title, event.text, event.expanded_text)
    existing = store.find_notification_bridge_event(device_id, event.notification_key)

    if existing is not None and existing["content_hash"] == new_hash:
        # Exact retry of an already-recorded notification -- idempotent,
        # never reprocessed (no new evidence, no double order attempt).
        return {
            "notification_key": event.notification_key,
            "app_package": event.app_package,
            "classification": "duplicate_retry",
            "signal_id": existing["signal_id"],
        }

    is_edit = existing is not None
    revision_seq = (existing["revision_seq"] + 1) if existing else 1

    mapping = device["provider_mapping"].get(event.app_package, {})
    provider_name = mapping.get("provider_name") or event.app_package
    analyst = mapping.get("analyst")

    best_text = (event.expanded_text or event.text or event.title or "").strip()

    posted_at = event.posted_at
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    age_seconds = (server_received_at - posted_at).total_seconds()
    is_stale = age_seconds > config.NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS

    channel_id = f"{device_id}:{event.app_package}"
    signal_id: str | None = None
    classification: str
    routed_live = False

    parsed_signal: Signal | None = None
    parse_detail: str | None = None
    if best_text:
        try:
            parsed_signal = parse_text_signal(best_text, source=provider_name, asset_class=AssetClass.CRYPTO, analyst=analyst)
        except SignalValidationError as exc:
            parse_detail = str(exc)
    else:
        parse_detail = "no title/text/expanded_text content at all"

    if completeness is not ContentCompleteness.COMPLETE:
        classification = "needs_review_incomplete_content"
        if parsed_signal is not None:
            parsed_signal.channel_id = channel_id
            parsed_signal.message_id = event.notification_key
            parsed_signal.revision_id = new_hash if is_edit else None
            parsed_signal.original_message_id = event.notification_key if is_edit else None
            parsed_signal.parser_version = "notification-bridge-text-parser-v1"
            parsed_signal.import_batch = f"notification_bridge:needs_review:{device_id}"
            parsed_signal.raw = {
                "app_package": event.app_package,
                "content_completeness": completeness.value,
                "title": event.title,
                "text": event.text,
                "expanded_text": event.expanded_text,
                "is_group_conversation": event.is_group_conversation,
                "conversation_participants": event.conversation_participants,
            }
            store.save_signal(parsed_signal)
            signal_id = parsed_signal.id
    elif is_stale:
        classification = "stale_backlog_import_only"
        if parsed_signal is not None:
            parsed_signal.channel_id = channel_id
            parsed_signal.message_id = event.notification_key
            parsed_signal.revision_id = new_hash if is_edit else None
            parsed_signal.original_message_id = event.notification_key if is_edit else None
            parsed_signal.parser_version = "notification-bridge-text-parser-v1"
            parsed_signal.import_batch = f"notification_bridge:stale_backlog:{device_id}"
            parsed_signal.raw = {
                "app_package": event.app_package,
                "content_completeness": completeness.value,
                "title": event.title,
                "text": event.text,
                "expanded_text": event.expanded_text,
                "posted_at": event.posted_at.isoformat(),
                "age_seconds": age_seconds,
            }
            store.save_signal(parsed_signal)
            signal_id = parsed_signal.id
    elif parsed_signal is None:
        classification = "no_match"
    else:
        parsed_signal.channel_id = channel_id
        parsed_signal.message_id = event.notification_key
        parsed_signal.revision_id = new_hash if is_edit else None
        parsed_signal.original_message_id = event.notification_key if is_edit else None
        parsed_signal.parser_version = "notification-bridge-text-parser-v1"
        parsed_signal.raw = {
            "app_package": event.app_package,
            "content_completeness": completeness.value,
            "title": event.title,
            "text": event.text,
            "expanded_text": event.expanded_text,
            "is_group_conversation": event.is_group_conversation,
            "conversation_participants": event.conversation_participants,
        }
        classification = "live"
        routed_live = True
        # Point 6 (dedup/edit, Track 5's own established pattern): setting
        # channel_id/message_id/revision_id BEFORE calling
        # engine.handle_signal means the engine's own
        # find_signal_id_by_provider_identity canonicalization (a real
        # content edit resends the SAME notification_key with a NEW
        # revision_id) and SIG-01 per-signal-id replay guard both apply
        # here for free -- this route never needs its own duplicate
        # order-submission protection on top of that.
        await engine.handle_signal(parsed_signal)
        signal_id = parsed_signal.id
        await engine.export_source_event(
            SourceEvent(
                source=provider_name,
                kind=SourceEventKind.EDIT if is_edit else SourceEventKind.ORIGINAL,
                channel_id=channel_id,
                message_id=event.notification_key,
                revision_id=new_hash if is_edit else None,
                original_message_id=event.notification_key if is_edit else None,
                provider_timestamp=posted_at,
                local_receipt_timestamp=server_received_at,
                signal=parsed_signal,
                raw_source_event={
                    "app_package": event.app_package,
                    "title": event.title,
                    "text": event.text,
                    "expanded_text": event.expanded_text,
                },
            )
        )

    store.save_notification_bridge_event(
        device_id=device_id,
        app_package=event.app_package,
        notification_key=event.notification_key,
        content_hash=new_hash,
        revision_seq=revision_seq,
        content_completeness=completeness.value,
        posted_at=posted_at,
        received_at=server_received_at,
        classification=classification,
        signal_id=signal_id,
    )

    degraded = store.record_notification_bridge_completeness(
        device_id, complete=(completeness is ContentCompleteness.COMPLETE)
    )
    if degraded:
        store.update_notification_bridge_device_health(
            device_id,
            "content_completeness_degraded",
            detail=f"recent notifications from app_package={event.app_package!r} are systematically "
            "truncated/title_only",
        )
    elif routed_live:
        store.update_notification_bridge_device_health(device_id, "healthy_qualified")

    result = {
        "notification_key": event.notification_key,
        "app_package": event.app_package,
        "classification": classification,
        "revision_seq": revision_seq,
        "is_edit": is_edit,
        "signal_id": signal_id,
    }
    if parse_detail and parsed_signal is None:
        result["detail"] = parse_detail
    return result


@app.post("/ingest/notification-bridge/{device_id}")
@limiter.limit(INGRESS_RATE_LIMIT)
async def ingest_notification_bridge(
    device_id: str,
    request: Request,
    body: NotificationBridgeIngestRequest,
    authorization: str | None = Header(default=None),
) -> dict:
    """The Android companion app's own ingress route -- mirrors
    `POST /webhook/{source_name}`'s auth shape exactly (a shared-secret-
    style header check before any body is trusted), but per-DEVICE
    (`app.notification_bridge.verify_pairing_token` against this
    device's own argon2id-hashed pairing token) rather than one global
    `WEBHOOK_SHARED_SECRET` -- see app/notification_bridge.py's module
    docstring for why a per-device credential, not a single shared one,
    is the right shape here (one compromised phone must not authorize
    forwarding on every other device's behalf).

    Any successfully authenticated contact with this route counts as a
    heartbeat (point 8: `no_heartbeat_recently` detection) -- whether or
    not `events` is empty (the Android app's own dedicated periodic
    heartbeat, see mobile/notification-bridge/README.md, posts an empty
    batch here for exactly this reason, alongside real notification
    uploads)."""
    device = store.get_notification_bridge_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no notification-bridge device registered with device_id={device_id!r}")

    token = _extract_bearer_token(authorization)
    if token is None or not verify_pairing_token(token, device["pairing_token_hash"]):
        # C06: constant-time compare (verify_pairing_token uses pwdlib's
        # own argon2id verify), same discipline as app/auth.py's
        # verify_password and the webhook route's hmac.compare_digest.
        raise HTTPException(status_code=401, detail="invalid device pairing token")

    store.record_notification_bridge_heartbeat(device_id)
    device = store.get_notification_bridge_device(device_id)  # refreshed after heartbeat

    results = [await _process_notification_bridge_event(device, event) for event in body.events]
    return {"device_id": device_id, "results": results}


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
    #: E07 (bounded): optional linear cost-stress applied on top of the raw
    #: replay -- see app/backtest/cost_stress.py's module docstring for
    #: exactly what this is (a stress test, not a claim of real broker
    #: costs) and its limits. 0/0 (the default) skips it entirely, same as
    #: omitting both fields.
    slippage_bps: float = Field(default=0.0, ge=0)
    fee_per_trade: float = Field(default=0.0, ge=0)
    #: B7: which real configured account (app/db.py's `config_accounts`)
    #: this run's signals would have routed to, for the real cross-signal
    #: capital-contention overlay -- see app/backtest/replay.py's
    #: `run_with_capital_contention`. None (the default) means this run
    #: doesn't check capital contention at all; the response/persisted run
    #: honestly discloses that as `not_tracked`, never a fabricated result.
    account_id: str | None = None


def _build_equity_curve_and_drawdown(trades: list[dict]) -> dict:
    """TR-15 research report: a real cumulative-P&L equity curve built from
    this run's own resolved (WIN/LOSS) trades -- each point is one real
    trade's own `exit_time`/`pnl`, running-summed in chronological exit
    order (never a fabricated smooth line: a run with zero resolved
    trades gets an empty curve, not an invented flat one). Max drawdown is
    computed by calling app/statistics.py's `compute_max_drawdown` against
    that same real curve -- reusing the one real, load-bearing peak-to-
    trough walk this codebase already has, rather than re-implementing a
    second (and possibly subtly different) drawdown calculation here."""
    resolved = sorted(
        (t for t in trades if t.get("exit_time") and t.get("pnl") is not None),
        key=lambda t: t["exit_time"],
    )
    cumulative = 0.0
    snapshots: list[dict] = []
    for t in resolved:
        cumulative += t["pnl"]
        snapshots.append({"captured_at": t["exit_time"], "cumulative_pnl": cumulative})

    drawdown = compute_max_drawdown(snapshots)
    return {
        "equity_curve": snapshots,
        "max_drawdown": drawdown[0] if drawdown else None,
        "max_drawdown_duration_seconds": drawdown[1] if drawdown else None,
    }


def _hash_file_contents(path: Path) -> str:
    """Real SHA-256 fingerprint of a CSV file's bytes on disk -- used so
    `compute_backtest_config_hash` is sensitive to the actual bars a run
    replayed against, not just the path string (the same path can hold
    different bars across two runs -- e.g. a re-exported/updated CSV --
    and that's a genuinely different input, not the same config)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_backtest_config_hash(request: "BacktestRequest") -> str:
    """TR-15: a real SHA-256 over every input that actually determines this
    replay's output -- the risk this function exists to guard against is
    two GENUINELY DIFFERENT configs (different period, different policy
    params, different underlying CSV data) silently colliding under the
    same hash and corrupting the run-comparison view. Every field below is
    a real field POST /backtest's own `BacktestRequest` reads and actually
    passes to `BacktestEngine`/`apply_cost_stress` -- nothing here is
    decorative. `csv_paths` is fingerprinted by real file CONTENT (see
    `_hash_file_contents`), not by path string, since the same path can
    legitimately hold different bars across two runs.

    A CSV path that doesn't exist (or can't be read) still gets a stable,
    distinguishing marker (`"unreadable:<path>"`) rather than silently
    omitting it from the hash -- a request with a missing CSV must not
    hash the same as one with a present, empty-fingerprint CSV.
    """
    csv_fingerprints: dict[str, str] = {}
    for symbol, raw_path in sorted(request.csv_paths.items()):
        path = Path(raw_path)
        try:
            csv_fingerprints[symbol] = _hash_file_contents(path)
        except OSError:
            csv_fingerprints[symbol] = f"unreadable:{raw_path}"

    payload = {
        "source": request.source,
        "symbol": request.symbol,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "max_hold_days": request.max_hold_days,
        "slippage_bps": request.slippage_bps,
        "fee_per_trade": request.fee_per_trade,
        "csv_fingerprints": csv_fingerprints,
        # B7: which account (if any) the capital-contention overlay checked
        # this run against genuinely changes this run's real output (the
        # persisted capital_contention field) -- two requests that only
        # differ here must not collide onto the same hash.
        "account_id": request.account_id,
    }
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _compute_capital_contention(
    engine: BacktestEngine, rows: list[dict], account_id: str | None
) -> CapitalContentionReport:
    """B7: real, honest gate in front of `BacktestEngine.
    run_with_capital_contention` -- only actually runs the contention-aware
    replay when this run names a REAL configured account
    (`app/db.py`'s `config_accounts`) that itself carries a real
    `max_notional_exposure` (`app/capital_allocator.py`'s opt-in ceiling).
    Every other case is disclosed as `not_tracked` with the specific real
    reason, never silently defaulted to an invented ceiling -- see this
    function's callers' own docstrings for why that matters."""
    if not account_id:
        return CapitalContentionReport.not_tracked(
            "No account_id was given for this backtest run -- set Account in Run configuration to a "
            "real configured account id to check this run's signals against that account's real "
            "configured capital ceiling (app/capital_allocator.py's max_notional_exposure)."
        )
    account_row = next((a for a in store.list_config_accounts() if a["account_id"] == account_id), None)
    if account_row is None:
        return CapitalContentionReport.not_tracked(
            f"Account {account_id!r} is not a configured account (app/db.py's config_accounts) -- there is "
            "no real account here to check a capital ceiling against."
        )
    max_notional_exposure = account_row["max_notional_exposure"]
    if max_notional_exposure is None:
        return CapitalContentionReport.not_tracked(
            f"Account {account_id!r} has no max_notional_exposure configured (app/capital_allocator.py's "
            "opt-in ceiling, None by default) -- there is no real ceiling to check cross-signal capital "
            "contention against for this run."
        )
    return await engine.run_with_capital_contention(
        rows, account_id=account_id, max_notional_exposure=max_notional_exposure
    )


@app.post("/backtest")
async def run_backtest(request: BacktestRequest, _owner: dict = Depends(require_owner)) -> dict:
    """Replays historical signals (from `SignalStore`) against locally
    supplied OHLC data. Only signals SAVED AFTER the stop_loss/take_profit/
    analyst columns were added (see app/db.py's `_COLUMN_MIGRATIONS`) carry
    that data — older rows replay as NO_EXIT_LEVELS. This is a synchronous,
    in-process replay whose real result (request/summary/trades/config
    hash) is now durably persisted to `backtest_runs` before the response
    is returned (see app/db.py's `save_backtest_run`) -- reloading the
    Signal Backtests screen no longer loses it. `GET /backtest/runs` lists
    every persisted run; `GET /backtest/runs/{id}` returns one run's full
    detail (including its trades)."""
    csv_paths = {symbol: Path(path) for symbol, path in request.csv_paths.items()}
    provider = CsvPriceHistoryProvider(csv_paths)
    engine = BacktestEngine(provider, max_hold=timedelta(days=request.max_hold_days))

    rows = store.list_signals_in_range(
        source=request.source, symbol=request.symbol, start=request.start, end=request.end
    )
    report = engine.run(rows)

    trades_payload = [
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
    ]

    response: dict[str, Any] = {
        "summary": report.summary(),
        "trades": trades_payload,
        **_build_equity_curve_and_drawdown(trades_payload),
    }

    stressed_summary: dict | None = None
    cost_stress_note: str | None = None
    if request.slippage_bps or request.fee_per_trade:
        stressed = apply_cost_stress(report, slippage_bps=request.slippage_bps, fee_per_trade=request.fee_per_trade)
        stressed_summary = stressed.summary()
        cost_stress_note = (
            "Linear stress test only (flat slippage_bps against every resolved trade's exit price, plus a flat "
            "fee_per_trade) -- not a real broker fee schedule or a liquidity/market-impact model. See "
            "app/backtest/cost_stress.py's module docstring."
        )
        response["stressed_summary"] = stressed_summary
        response["cost_stress_note"] = cost_stress_note

    capital_contention = await _compute_capital_contention(engine, rows, request.account_id)
    response["capital_contention"] = dataclasses.asdict(capital_contention)

    config_hash = compute_backtest_config_hash(request)
    request_payload = json.loads(request.model_dump_json())
    run_id = store.save_backtest_run(
        config_hash=config_hash,
        created_at=datetime.now(timezone.utc),
        request=request_payload,
        summary=response["summary"],
        trades=trades_payload,
        stressed_summary=stressed_summary,
        cost_stress_note=cost_stress_note,
        capital_contention=response["capital_contention"],
    )
    response["run_id"] = run_id
    response["config_hash"] = config_hash

    return response


@app.get("/backtest/runs")
async def list_backtest_runs(limit: int = Query(default=50, ge=1, le=500), _owner: dict = Depends(require_owner)) -> dict:
    """TR-15: every persisted `POST /backtest` run's real identity/summary,
    most recent first -- real, durable history (`app/db.py`'s
    `backtest_runs`), not this browser tab's in-memory list. Trades are
    omitted here for payload size; fetch a single run's detail below for
    those."""
    return {"runs": store.list_backtest_runs(limit=limit)}


@app.get("/backtest/runs/{run_id}")
async def get_backtest_run(run_id: int, _owner: dict = Depends(require_owner)) -> dict:
    """TR-15: one persisted run's full real detail, including every
    replayed trade -- exactly what `POST /backtest` computed and persisted
    at run time, never re-derived."""
    run = store.get_backtest_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    run = {**run, **_build_equity_curve_and_drawdown(run["trades"])}
    return run


class SavedViewRequest(BaseModel):
    name: str
    screen: str = "positions"
    filters: dict


@app.get("/saved-views")
async def list_saved_views(screen: str | None = None, _owner: dict = Depends(require_owner_read)) -> dict:
    """TR-02: every real, persisted saved view (app/db.py's `saved_views`
    table) -- optionally narrowed to one screen (`?screen=positions`).
    `filters` is exactly the client-side filter-control state that screen
    saved it with; this codebase has no server-side query-param filtering
    for positions yet, so applying a saved view is the caller's own job
    (re-populate its controls from `filters`), not something this endpoint
    does."""
    return {"saved_views": store.list_saved_views(screen=screen)}


@app.post("/saved-views")
async def create_saved_view(request: SavedViewRequest, _owner: dict = Depends(require_owner)) -> dict:
    """Persist one real named filter set. `name` must be unique across all
    saved views (this engine is single-owner -- no per-user scoping exists
    anywhere in this schema); reusing an existing name is refused with 409
    rather than silently overwriting it (delete the old one first if that's
    really what's wanted)."""
    try:
        view_id = store.save_saved_view(
            name=request.name, screen=request.screen, filters=request.filters, created_at=datetime.now(timezone.utc)
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"a saved view named '{request.name}' already exists") from exc
    return {"id": view_id, "status": "created"}


@app.delete("/saved-views/{view_id}")
async def delete_saved_view(view_id: int, _owner: dict = Depends(require_owner)) -> dict:
    """Deletes one real saved view. A `view_id` that never existed (or was
    already deleted) is a real 404, never a silently-successful no-op."""
    deleted = store.delete_saved_view(view_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="saved view not found")
    return {"id": view_id, "status": "deleted"}


@app.get("/backtest/runs/{run_id}/trades/{signal_id}/market-path")
async def get_backtest_trade_market_path(run_id: int, signal_id: str, _owner: dict = Depends(require_owner)) -> dict:
    """TR-15 trade explorer: the real historical OHLC bars around one
    replayed trade, re-read from the exact local CSV path this run's own
    persisted request used (see `backtest_runs.request_json`'s
    `csv_paths`) -- never a synthesized price path. Real IF that path is
    still readable from this server process; if the file has since moved
    or been deleted, this honestly reports that instead of fabricating a
    path."""
    run = store.get_backtest_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="backtest run not found")
    trade = next((t for t in run["trades"] if str(t["signal_id"]) == str(signal_id)), None)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found in this run")

    raw_path = run["request"].get("csv_paths", {}).get(trade["symbol"])
    if not raw_path:
        return {
            "bars": [],
            "available": False,
            "note": f"This run's persisted request has no CSV path recorded for symbol {trade['symbol']!r}.",
        }
    path = Path(raw_path)
    if not path.exists():
        return {
            "bars": [],
            "available": False,
            "note": f"The original CSV path ({raw_path}) is no longer readable from this server process "
            "(moved or deleted since the run completed) -- the historical market path can't be replayed "
            "from a file that no longer exists, and this endpoint won't fabricate one.",
        }

    provider = CsvPriceHistoryProvider({trade["symbol"]: path})
    entry_time = datetime.fromisoformat(trade["entry_time"])
    max_hold_days = run["request"].get("max_hold_days", 30.0)
    bars = provider.get_bars(trade["symbol"], entry_time, entry_time + timedelta(days=max_hold_days))
    return {
        "bars": [
            {
                "timestamp": b.timestamp.isoformat(),
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ],
        "available": True,
    }


class ProviderFitSimulationRequest(BaseModel):
    """See app/backtest/fit_simulator.py's module docstring for exactly
    what this does and doesn't simulate, and its own disclosed sizing
    methodology, before trusting its output. This is the personalized
    "what would copying this source have done to MY account" number a
    copy-trading marketing funnel shows a PROSPECT before they ever
    subscribe -- not a rename of `/backtest` above (which never rescales
    to a hypothetical account) or `app/provider_value.py` (which only
    scores an account's own real, already-subscribed fill history)."""

    source: str
    account_size: float = Field(gt=0)
    max_per_trade: float = Field(gt=0)
    lookback_days: float = Field(default=90.0, gt=0)
    #: symbol -> local CSV path (columns: timestamp,open,high,low,close[,volume]).
    #: See app/backtest/models.py's module docstring for why this project
    #: can't fetch historical bars for you -- same limitation as `/backtest`.
    csv_paths: dict[str, str]
    max_hold_days: float = 30.0


def _run_fit_simulation_and_build_response(request: "ProviderFitSimulationRequest | CatalogFitSimulationRequest") -> dict:
    """Shared by both the owner-gated `/providers/{source}/fit-simulation`
    and the signed, non-owner `/catalog/providers/{source}/fit-simulation`
    below -- ONE code path decides what a fit-simulation response
    contains, so the two routes can never drift into exposing different
    fields. What it returns is, by construction, only ever the given
    source's own historical-signal replay rescaled to the caller-supplied
    account_size/max_per_trade -- `simulate_provider_fit` (see its own
    module docstring) never reads anything about the OWNER's own
    accounts, positions, or balances; there is no owner data for this
    function to leak even if a caller tried."""
    csv_paths = {symbol: Path(path) for symbol, path in request.csv_paths.items()}
    provider = CsvPriceHistoryProvider(csv_paths)

    report = simulate_provider_fit(
        store,
        provider,
        source=request.source,
        account_size=request.account_size,
        max_per_trade=request.max_per_trade,
        lookback_days=request.lookback_days,
        max_hold=timedelta(days=request.max_hold_days),
    )

    return {
        "summary": report.summary(),
        "equity_curve": [{"time": t.isoformat(), "cumulative_pnl": v} for t, v in report.equity_curve],
        "trades": [
            {
                "signal_id": t.signal_id,
                "symbol": t.symbol,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "outcome": t.outcome.value,
                "fits": t.fits,
                "original_quantity": t.original_quantity,
                "simulated_quantity": t.simulated_quantity,
                "pnl": t.pnl,
            }
            for t in report.trades
        ],
    }


@app.post("/providers/{source}/fit-simulation")
async def run_provider_fit_simulation(
    source: str, request: ProviderFitSimulationRequest, _owner: dict = Depends(require_owner)
) -> dict:
    """Owner-gated, cookie/CSRF session only -- for the owner's own ad hoc
    use (see README.md's "Owner authentication" section). The real
    prospect-facing path is `POST /catalog/providers/{source}/fit-
    simulation` below: a separate, narrowly-scoped, signed-service-token
    route signal-portfolio-commercial's own backend calls on behalf of an
    anonymous public-catalog visitor -- never this one, and never this
    endpoint exposed directly to the public internet."""
    if request.source != source:
        raise HTTPException(status_code=422, detail="path 'source' and body 'source' must match")

    return _run_fit_simulation_and_build_response(request)


#: CATALOG-01 (bounded): hard ceilings on the one request shape the
#: catalog fit-sim route accepts, independent of the owner route's own
#: (looser -- an authenticated owner is trusted with their own compute)
#: limits. A valid signature already proves the caller is signal-
#: portfolio-commercial's own backend, not an arbitrary visitor, but
#: "trusted caller" still isn't "unbounded caller" -- a bug or a
#: compromised commercial deployment must not be able to make this
#: service replay a ten-thousand-symbol, thousand-year backtest on every
#: request. `CsvPriceHistoryProvider` never reads a path outside the
#: caller-supplied dict, but bounding the dict's SIZE (not its contents,
#: which the caller -- not a browser visitor -- controls) keeps one
#: request's own I/O/compute bounded regardless of source.
_CATALOG_FIT_SIM_MAX_SYMBOLS = 25
_CATALOG_FIT_SIM_MAX_LOOKBACK_DAYS = 3650.0
_CATALOG_FIT_SIM_MAX_HOLD_DAYS = 365.0


class CatalogFitSimulationRequest(BaseModel):
    """Identical fields to `ProviderFitSimulationRequest` (same
    disclosed real-CSV-price-path requirement, same sizing methodology --
    see that model's and app/backtest/fit_simulator.py's own docstrings)
    plus real ceilings on lookback/hold window and symbol count, per
    CATALOG-01 above. This is the ONLY shape `POST /catalog/providers/
    {source}/fit-simulation` accepts -- there is no field here (or
    anywhere in `_run_fit_simulation_and_build_response`) that could
    reach an owner account, position, or balance even if a caller tried;
    the request can only name a source, a hypothetical account_size/
    max_per_trade, and the caller's own supplied CSV price paths for that
    source's OWN historical signals."""

    source: str
    account_size: float = Field(gt=0)
    max_per_trade: float = Field(gt=0)
    lookback_days: float = Field(default=90.0, gt=0, le=_CATALOG_FIT_SIM_MAX_LOOKBACK_DAYS)
    csv_paths: dict[str, str] = Field(max_length=_CATALOG_FIT_SIM_MAX_SYMBOLS)
    max_hold_days: float = Field(default=30.0, gt=0, le=_CATALOG_FIT_SIM_MAX_HOLD_DAYS)


@app.post("/catalog/providers/{source}/fit-simulation")
@limiter.limit(CATALOG_FIT_SIM_RATE_LIMIT)
async def run_catalog_fit_simulation(
    source: str, request: Request, catalog_request: CatalogFitSimulationRequest
) -> dict:
    """The real, bounded, non-owner path for a prospect-facing "browse
    providers" surface (signal-portfolio-commercial's own public catalog)
    to run this service's own fit-simulation capability, per that
    endpoint's own long-documented gap above. Authenticated by a signed,
    audience-bound, expiring service token (`X-Catalog-Fit-Sim-Signature`,
    verified against `config.CATALOG_FIT_SIM_SIGNING_SECRET` -- see
    app/services/catalog_fit_sim_auth.py's own module docstring for the
    exact scheme and why it is a SEPARATE secret/verifier from the relay
    ingest's own), never a cookie/CSRF owner session -- this route takes
    no `Depends(require_owner)` and is reachable with no browser session
    at all, by design: a public-catalog visitor has none.

    Deliberately NOT `require_owner` with the CSRF check relaxed, or
    `require_owner` extended to also accept this signature: those would
    make a stolen or forged catalog-fit-sim token a step toward every
    OTHER owner-gated route (accounts, positions, routing rules, backtest
    with no ceilings, etc). This route's own request/response shape
    (`CatalogFitSimulationRequest` in, `_run_fit_simulation_and_build_
    response`'s output out) is the entire capability this token can ever
    be used for -- see tests/test_catalog_fit_sim_endpoint.py's own
    "does not expose owner data" and "cannot authenticate to an owner-
    gated route" coverage."""
    if catalog_request.source != source:
        raise HTTPException(status_code=422, detail="path 'source' and body 'source' must match")

    if not config.CATALOG_FIT_SIM_SIGNING_SECRET:
        # Fail closed, same convention as require_owner/the webhook
        # ingress: an unconfigured secret disables this route, it does
        # not make it public.
        raise HTTPException(
            status_code=503,
            detail="catalog fit-simulation is not configured (set CATALOG_FIT_SIM_SIGNING_SECRET)",
        )

    raw_body = await request.body()
    sig_header = request.headers.get("x-catalog-fit-sim-signature", "")
    try:
        verify_catalog_fit_sim_signature(
            raw_body,
            sig_header,
            config.CATALOG_FIT_SIM_SIGNING_SECRET,
            secret_previous=config.CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS or None,
        )
    except (
        InvalidCatalogFitSimSignatureHeaderError,
        CatalogFitSimSignatureMismatchError,
        StaleCatalogFitSimTimestampError,
    ) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return _run_fit_simulation_and_build_response(catalog_request)


# --- Read-only market/economic context (app/context/) ---
#
# SEC filings, FRED macro series, FX reference rates -- for an operator to
# look something up alongside the live signal/position data above. None of
# these touch routing, engine, lifecycle, or any broker adapter; they can't
# place, cancel, or modify anything. See app/context/__init__.py and
# README.md's "Market/economic context" section.

_SECRET_QUERY_PARAMS = ("api_key", "apikey", "token", "key", "secret", "password", "auth")


def _sanitized_upstream_error(source: str, exc: httpx.HTTPError) -> str:
    """httpx's own `str(exc)` on an HTTPStatusError includes the full
    request URL -- FRED's API takes its key as a `?api_key=...` query
    parameter, so an unsanitized error message put that real key directly
    into this response (SEC-07). Redact any secret-shaped query parameter
    value before it ever leaves this process, in a response or a log line."""
    message = str(exc)
    request = getattr(exc, "request", None)
    if request is not None:
        try:
            url = httpx.URL(str(request.url))
            redacted_params = {
                k: ("REDACTED" if k.lower() in _SECRET_QUERY_PARAMS else v) for k, v in url.params.multi_items()
            }
            safe_url = str(url.copy_with(params=redacted_params))
            message = message.replace(str(request.url), safe_url)
        except Exception:  # noqa: BLE001 - never let sanitization itself fail the error path
            message = f"{type(exc).__name__} (request URL redacted)"
    return f"{source} request failed: {message}"


@app.get("/context/filings/{ticker}")
async def get_sec_filings(ticker: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """Recent SEC filing history for a ticker (data.sec.gov). 501s if
    SEC_EDGAR_USER_AGENT isn't configured; 404s if the ticker isn't in
    SEC's own ticker->CIK directory (e.g. not a US-listed equity)."""
    try:
        submissions = await sec_edgar.get_company_submissions(ticker)
    except sec_edgar.NotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=_sanitized_upstream_error("SEC EDGAR", exc)) from exc
    if submissions is None:
        raise HTTPException(status_code=404, detail=f"no SEC CIK found for ticker '{ticker}'")
    return submissions


@app.get("/context/filings/{ticker}/facts")
async def get_sec_company_facts(ticker: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """Structured XBRL company facts (financial statement line items over
    time, as originally filed/amended) for a ticker."""
    try:
        facts = await sec_edgar.get_company_facts(ticker)
    except sec_edgar.NotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=_sanitized_upstream_error("SEC EDGAR", exc)) from exc
    if facts is None:
        raise HTTPException(status_code=404, detail=f"no XBRL facts found for ticker '{ticker}'")
    return facts


@app.get("/context/fred/{series_id}")
async def get_fred_series(
    series_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    realtime_start: str | None = Query(default=None),
    realtime_end: str | None = Query(default=None),
    _owner: dict = Depends(require_owner_read),
) -> dict:
    """FRED (or ALFRED, via realtime_start/realtime_end) observations for
    one macro series, e.g. `DGS10`, `CPIAUCSL`, `UNRATE`. 501s if
    FRED_API_KEY isn't configured."""
    try:
        return await fred_context.get_series_observations(
            series_id, limit=limit, realtime_start=realtime_start, realtime_end=realtime_end
        )
    except fred_context.NotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=_sanitized_upstream_error("FRED", exc)) from exc


@app.get("/context/fx/{base}/{quote}")
async def get_fx_rate(
    base: str, quote: str, date: str | None = Query(default=None), _owner: dict = Depends(require_owner_read)
) -> dict:
    """Daily ECB reference rate (via Frankfurter) for one currency pair —
    latest by default, or a specific 'YYYY-MM-DD' date. Reference only, not
    an executable price; see app/context/fx.py's module docstring."""
    try:
        if date:
            return await fx_context.get_historical_rate(date, base, quote)
        return await fx_context.get_latest_rate(base, quote)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=_sanitized_upstream_error("Frankfurter", exc)) from exc


def _orders_response(signal_id: str, results) -> dict:
    return {
        "signal_id": signal_id,
        "orders": [
            {"account_id": r.account_id, "status": r.status.value, "message": r.message}
            for r in results
        ],
    }
