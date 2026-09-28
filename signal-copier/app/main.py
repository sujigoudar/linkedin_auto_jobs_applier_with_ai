"""FastAPI entrypoint.

Wires up: config -> routing rules -> broker adapters -> engine -> sources.
Push-based sources (webhook, SMS) get their own HTTP route below.
Pull-based sources (Telegram/Discord/Slack bots, Twitter stream) are only
started if their required env vars are set, and run as background tasks
started in the lifespan handler.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import Cookie, Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
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
from app.backtest.replay import BacktestEngine
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
from app.economics import compute_account_economics
from app.execution_quality import compute_execution_quality
from app.engine import SignalCopierEngine
from app.logging_config import configure_structlog
from app.metrics import render_metrics
from app.errors import SignalValidationError
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AccountBalance, AssetClass
from app.pricing import PriceMonitor
from app.providers import SettingsOverride, load_provider_registry_from_store
from app.provider_scout import ProviderScout
from app.provider_value import compute_provider_value_report
from app.rate_limit import CATALOG_FIT_SIM_RATE_LIMIT, INGRESS_RATE_LIMIT, limiter
from app.reconciliation import OrderReconciler
from app.relay_scheduler import RelayScheduler
from app.services.catalog_fit_sim_auth import (
    CatalogFitSimSignatureMismatchError,
    InvalidCatalogFitSimSignatureHeaderError,
    StaleCatalogFitSimTimestampError,
    verify_catalog_fit_sim_signature,
)
from app.routing import load_routing_config_from_store
from app.sources.text_parser import classify_batch
from app.sources.discord import DiscordSource
from app.sources.mt4_mt5 import MetaApiSource
from app.sources.ninjatrader import NinjaTraderSource
from app.sources.rithmic import RithmicSource
from app.sources.slack import SlackSource
from app.sources.base import SourceAdapter
from app.sources.sms_twilio import TwilioSMSSource
from app.sources.telegram import TelegramSource
from app.sources.twitter import TwitterSource
from app.sources.webhook import WebhookSource
from app.sources.whatsapp import WhatsAppSource

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
relay_scheduler = RelayScheduler(store=store, interval_seconds=config.RELAY_POLL_INTERVAL_SECONDS)

# Pull-based sources only start if fully configured via env vars.
_background_sources: list[SourceAdapter] = []
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
    if config.STANDBY_MODE:
        # A standby serves the read-only status/health surface only -- it must
        # not ingest signals, poll broker order status, or resize/replace a
        # protective stop, all of which are things only the single active
        # writer may do (see deploy/RUNBOOK.md). This is enforced here, not
        # merely by omitting broker credentials, so a misconfigured standby
        # can't silently become a second writer.
        logger.warning("STANDBY_MODE is set -- not starting signal ingestion, reconciliation, or price polling")
        yield
        return

    await webhook_source.start()
    for source in _background_sources:
        try:
            await source.start()
        except Exception:  # noqa: BLE001 - one misconfigured source must not block the app
            logger.exception("failed to start source '%s'", source.name)
    await reconciler.start()
    await price_monitor.start()
    await provider_scout.start()
    if config.RELAY_INGRESS_URL:
        # Same "pull-based, only starts if fully configured" convention
        # as TelegramSource/DiscordSource/etc. above -- a deployment with
        # no commercial platform to export to gets no relay loop at all,
        # not a loop that spins forever raising RelayNotConfiguredError.
        await relay_scheduler.start()
    else:
        logger.info("RELAY_INGRESS_URL is not set -- relay scheduler not started")

    yield

    if config.RELAY_INGRESS_URL:
        await relay_scheduler.stop()
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
    return {
        # OPS-01: `status` was hardcoded to "ok" regardless of the flags
        # right next to it -- a fresh startup (before either worker's
        # first successful pass) or a genuinely stuck worker still
        # reported "ok" overall while its own detail flag said otherwise.
        "status": "ok" if (db_ok and price_monitor_ok and reconciler_ok) else "degraded",
        "database_ok": db_ok,
        "price_monitor_ok": price_monitor_ok,
        "reconciler_ok": reconciler_ok,
        "provider_scout_ok": provider_scout_ok,
        "relay_ok": relay_ok,
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
async def dashboard() -> FileResponse:
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
    add)."""
    return FileResponse(STATIC_DIR / "dashboard.html")


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
    return {"positions": store.list_open_positions(), "managed_lifecycles": _managed_lifecycle_snapshot()}


@app.get("/accounts/{account_id}/economics")
async def get_account_economics(account_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """E06: authoritative realized P&L, cost basis and completed-trade win
    rate for this account, computed by replaying its own confirmed
    executions (see app/economics.py) -- never a simulated equity curve.
    Gross of fees (not yet tracked); no live-market unrealized P&L."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    return compute_account_economics(store, account_id).to_dict()


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


@app.get("/accounts/{account_id}/execution-quality")
async def get_account_execution_quality(account_id: str, _owner: dict = Depends(require_owner_read)) -> dict:
    """E05: signal-to-fill latency per symbol, computed from this schema's
    actual `received_at`/`executed_at` timestamps (see
    app/execution_quality.py for the honest scope disclosure)."""
    if account_id not in routing_config.accounts:
        raise HTTPException(status_code=404, detail=f"no account '{account_id}'")
    return compute_execution_quality(store, account_id).to_dict()


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
            }
            for broker in brokers.values()
        ]
    }


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

    _reject_bool_multiplier = field_validator("multiplier", "fixed_quantity", "max_notional_exposure", mode="before")(
        _reject_bool_scaling_value
    )


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
    return {"orders": store.list_recent_orders(limit=limit, account_id=account_id)}


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


@app.post("/backtest")
async def run_backtest(request: BacktestRequest, _owner: dict = Depends(require_owner)) -> dict:
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

    response: dict[str, Any] = {
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

    if request.slippage_bps or request.fee_per_trade:
        stressed = apply_cost_stress(report, slippage_bps=request.slippage_bps, fee_per_trade=request.fee_per_trade)
        response["stressed_summary"] = stressed.summary()
        response["cost_stress_note"] = (
            "Linear stress test only (flat slippage_bps against every resolved trade's exit price, plus a flat "
            "fee_per_trade) -- not a real broker fee schedule or a liquidity/market-impact model. See "
            "app/backtest/cost_stress.py's module docstring."
        )

    return response


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
        verify_catalog_fit_sim_signature(raw_body, sig_header, config.CATALOG_FIT_SIM_SIGNING_SECRET)
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
