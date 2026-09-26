"""FastAPI entrypoint.

Wires up: config -> routing rules -> broker adapters -> engine -> sources.
Push-based sources (webhook, SMS) get their own HTTP route below.
Pull-based sources (Telegram/Discord/Slack bots, Twitter stream) are only
started if their required env vars are set, and run as background tasks
started in the lifespan handler.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Cookie, Depends, FastAPI, Form, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app import config
from app.auth import SESSION_COOKIE_NAME, RequireOwner, create_session, verify_password
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
from app.config_admin import seed_from_yaml_if_empty
from app.context import fred as fred_context
from app.context import fx as fx_context
from app.context import sec_edgar
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.errors import SignalValidationError
from app.lifecycle.manager import PositionLifecycleManager
from app.pricing import PriceMonitor
from app.providers import SettingsOverride, load_provider_registry_from_store
from app.reconciliation import OrderReconciler
from app.routing import load_routing_config_from_store
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

store = SignalStore(config.DATABASE_PATH)
seed_from_yaml_if_empty(store)  # one-time: import existing config/*.yaml, then the database is live/authoritative
routing_config = load_routing_config_from_store(store)
provider_registry = load_provider_registry_from_store(store)

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
price_monitor = PriceMonitor(
    lifecycle_manager=lifecycle_manager,
    brokers=brokers,
    interval_seconds=config.PRICE_MONITOR_INTERVAL_SECONDS,
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

    yield

    await price_monitor.stop()
    await reconciler.stop()
    for source in _background_sources:
        await source.stop()
    for broker_name in ("signalstack", "alpaca", "ninjatrader", "ccxt", "ibkr", "mt4_mt5_metaapi", "rithmic"):
        if broker_name in brokers:
            await brokers[broker_name].close()


app = FastAPI(title="Trading Signal Copier", lifespan=lifespan)


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

    return {
        "status": "ok",
        "database_ok": db_ok,
        "price_monitor_ok": _fresh(price_monitor.last_success_at, config.PRICE_MONITOR_INTERVAL_SECONDS),
        "reconciler_ok": _fresh(reconciler.last_success_at, config.RECONCILE_INTERVAL_SECONDS),
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
async def receive_webhook(
    source_name: str,
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
) -> dict:
    if not config.WEBHOOK_SHARED_SECRET:
        # Fail closed: an unconfigured secret disables this ingress, it does
        # not make it public. Set WEBHOOK_SHARED_SECRET to accept signals here.
        raise HTTPException(status_code=503, detail="webhook ingress is not configured (set WEBHOOK_SHARED_SECRET)")
    if x_webhook_secret != config.WEBHOOK_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="invalid webhook secret")

    try:
        payload = await request.json()
    except ValueError as exc:
        # RISK-01: a malformed (non-JSON, truncated, wrong-content-type) body
        # must 400, not fall through to an unhandled exception and 500.
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    try:
        signal = webhook_source.parse(payload, source_override=source_name)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = await engine.handle_signal(signal)
    return _orders_response(signal.id, results)


@app.post("/sms/twilio")
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

    try:
        signal = sms_source.parse(body, analyst=from_number or None)
    except SignalValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    results = await engine.handle_signal(signal)
    return _orders_response(signal.id, results)


@app.get("/positions")
async def list_positions(_owner: dict = Depends(require_owner_read)) -> dict:
    """Every non-flat tracked position, across all accounts.

    This is this service's own record of what it has sent (see
    app/engine.py's "Close signals" docstring on why that can drift from
    the broker's real book on brokers that only confirm fills
    asynchronously), not a live read of any broker's account state.
    """
    return {"positions": store.list_open_positions(), "managed_lifecycles": _managed_lifecycle_snapshot()}


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
    both executing."""
    if idempotency_key:
        cached = store.get_idempotent_response(idempotency_key)
        if cached is not None:
            return cached

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
        store.save_idempotent_response(idempotency_key, response)
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
    endpoint's docstring for what `Idempotency-Key` does."""
    if idempotency_key:
        cached = store.get_idempotent_response(idempotency_key)
        if cached is not None:
            return cached

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
        store.save_idempotent_response(idempotency_key, response)
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


class AccountRequest(BaseModel):
    account_id: str
    broker: str
    multiplier: float = 1.0
    fixed_quantity: float | None = None
    symbol_map: dict[str, str] = {}
    enabled: bool = True
    managed_lifecycle: bool = False


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
    `{BROKER}_{ACCOUNT_ID}_...` env vars separately."""
    store.upsert_config_account(
        account_id=request.account_id,
        broker=request.broker,
        multiplier=request.multiplier,
        fixed_quantity=request.fixed_quantity,
        symbol_map=request.symbol_map,
        enabled=request.enabled,
        managed_lifecycle=request.managed_lifecycle,
    )
    _reload_routing_config()
    return {"account_id": request.account_id, "status": "saved"}


@app.delete("/accounts/{account_id}")
async def delete_account(account_id: str, _owner: dict = Depends(require_owner)) -> dict:
    store.delete_config_account(account_id)
    _reload_routing_config()
    return {"account_id": account_id, "status": "deleted"}


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
    multiplier: float | None = None
    fixed_quantity: float | None = None
    managed_lifecycle: bool | None = None
    enabled: bool | None = None


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
    multiplier: float | None = None
    fixed_quantity: float | None = None
    managed_lifecycle: bool | None = None
    enabled: bool | None = None


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
