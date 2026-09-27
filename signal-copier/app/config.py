"""Runtime configuration.

Secrets (broker API keys, bot tokens, webhook secrets) come from environment
variables only — never from the YAML routing/account files, so those files
stay safe to commit as examples.

C01: values are read once at import time through a `pydantic-settings`
`BaseSettings` model (`_Settings` below), then copied onto this module's
top-level names -- every existing `config.SOMETHING` access and every
test's `monkeypatch.setattr(app_config, "SOMETHING", ...)` keeps working
unchanged. What actually changes is boolean parsing: `STANDBY_MODE` and
`FORCE_SECURE_COOKIES` used to treat ANY unrecognized string (a typo, an
empty value from a broken env-file line, "enabled" instead of "true") as
silently False -- for `STANDBY_MODE` that is the UNSAFE direction: a
malformed value defaults to "not in standby," i.e. live trading authority,
exactly backwards from what a misconfigured safety flag should do.
Pydantic's own bool coercion accepts the same values as before
(1/true/yes/on and 0/false/no/off, case-insensitive) but RAISES for
anything else, so a malformed value now fails loud at startup instead of
silently picking the wrong side.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=True, extra="ignore")

    ROUTING_CONFIG_PATH: Path = BASE_DIR / "config" / "routing.yaml"
    ACCOUNTS_CONFIG_PATH: Path = BASE_DIR / "config" / "accounts.yaml"
    #: Optional per-provider/per-analyst settings overrides — see app/providers.py.
    #: Missing file (the default if never created) means no overrides apply.
    PROVIDERS_CONFIG_PATH: Path = BASE_DIR / "config" / "providers.yaml"
    DATABASE_PATH: Path = BASE_DIR / "signal_copier.db"

    # Shared secret the webhook source checks against a header/query param so
    # random requests on the public endpoint can't inject fake signals.
    WEBHOOK_SHARED_SECRET: str = ""

    # Owner authentication (see app/auth.py). Every account/routing/position/
    # close/flatten/backtest endpoint requires a valid owner session; either
    # OWNER_PASSWORD or OWNER_PASSWORD_HASH (never both -- see app/auth.py's
    # auth_configured()) plus SESSION_SECRET must be set or every one of
    # those endpoints fails closed (503), never silently open.
    #
    # OWNER_PASSWORD (legacy, plain): compared with a constant-time check,
    # same trust level as every other secret this project keeps in an env
    # var. Its actual value is directly usable by anything that can read
    # this process's environment (a log dump, a leaked .env, a config
    # export) -- OWNER_PASSWORD_HASH (C05) is the same security property
    # PLUS that: an argon2id hash (via pwdlib) that isn't itself a usable
    # credential even if it leaks. Generate one with:
    #   python -c "from pwdlib import PasswordHash; print(PasswordHash.recommended().hash('<password>'))"
    #
    # SESSION_SECRET signs/derives session data.
    OWNER_PASSWORD: str = ""
    OWNER_PASSWORD_HASH: str = ""
    SESSION_SECRET: str = ""
    SESSION_TTL_SECONDS: float = float(60 * 60 * 12)  # 12h
    # Set true when this process sits behind a TLS-terminating reverse proxy
    # (nginx/Caddy) so the login route always marks its session cookie Secure --
    # `request.url.scheme` alone sees only "http" in that deployment, since the
    # proxy, not this process, terminates TLS (see SEC-05).
    FORCE_SECURE_COOKIES: bool = False

    LOG_LEVEL: str = "INFO"

    # Standby mode (see deploy/RUNBOOK.md): when true, this process serves only
    # GET/HEAD/OPTIONS -- no signal ingestion, no background reconciliation/price
    # polling, no financial command can reach the engine, regardless of what any
    # individual route's own logic does. A promotion is a deliberate, separate
    # restart with this unset (or false) AND real broker credentials configured --
    # never a config flip on an already-running process.
    STANDBY_MODE: bool = False

    # Optional pull-based sources: each only starts if its required env vars are
    # all set (see .env.example). Push-based sources (webhook, SMS) need no
    # startup config beyond their own route.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    DISCORD_BOT_TOKEN: str = ""
    DISCORD_CHANNEL_ID: str = ""

    SLACK_BOT_TOKEN: str = ""
    SLACK_APP_TOKEN: str = ""
    SLACK_CHANNEL_ID: str = ""

    TWITTER_BEARER_TOKEN: str = ""
    TWITTER_RULES: str = ""

    TWILIO_AUTH_TOKEN: str = ""
    # Full public URL Twilio POSTs to, required for signature validation (Twilio
    # signs the exact URL it called, including scheme/host).
    TWILIO_WEBHOOK_URL: str = ""
    # A valid Twilio signature only proves the request transited Twilio with the
    # right account's auth token -- it says nothing about who is allowed to text
    # trading instructions to that number. Comma-separated E.164 sender numbers
    # (e.g. "+15551234567,+15557654321") this route accepts; unset/empty means
    # no sender is authorized, same fail-closed pattern as every other optional
    # ingress here (see app/main.py's receive_sms).
    TWILIO_ALLOWED_FROM_NUMBERS: str = ""

    MT4_MT5_METAAPI_TOKEN: str = ""
    MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID: str = ""

    RITHMIC_USER: str = ""
    RITHMIC_PASSWORD: str = ""
    RITHMIC_SYSTEM_NAME: str = ""
    RITHMIC_GATEWAY_URL: str = ""
    RITHMIC_SOURCE_ACCOUNT_ID: str = ""  # optional filter

    # How often app/reconciliation.py re-checks PENDING orders on brokers that
    # support get_order_status() (currently Alpaca and IBKR).
    RECONCILE_INTERVAL_SECONDS: float = 30.0

    # How often app/pricing.py's PriceMonitor polls each open managed-lifecycle
    # position's broker for a current price (currently ccxt only — see
    # app/pricing.py's module docstring).
    PRICE_MONITOR_INTERVAL_SECONDS: float = 15.0

    # Read-only market/economic context lookups (see app/context/ — SEC
    # filings, FRED macro series, FX reference rates). None of these are used
    # anywhere in the order-management/protective-stop path.
    #
    # SEC's fair-access policy requires an identifying User-Agent on every
    # request (e.g. "YourCompany admin@example.com") — no API key, but the
    # /context/filings endpoint 501s if this is blank rather than send an
    # unidentified request.
    SEC_EDGAR_USER_AGENT: str = ""
    # Free key from https://fredaccount.stlouisfed.org/apikeys -- the
    # /context/fred endpoint 501s if this is blank.
    FRED_API_KEY: str = Field(default="")


_settings = _Settings()

ROUTING_CONFIG_PATH = _settings.ROUTING_CONFIG_PATH
ACCOUNTS_CONFIG_PATH = _settings.ACCOUNTS_CONFIG_PATH
PROVIDERS_CONFIG_PATH = _settings.PROVIDERS_CONFIG_PATH
DATABASE_PATH = _settings.DATABASE_PATH

WEBHOOK_SHARED_SECRET = _settings.WEBHOOK_SHARED_SECRET

OWNER_PASSWORD = _settings.OWNER_PASSWORD
OWNER_PASSWORD_HASH = _settings.OWNER_PASSWORD_HASH
SESSION_SECRET = _settings.SESSION_SECRET
SESSION_TTL_SECONDS = _settings.SESSION_TTL_SECONDS
FORCE_SECURE_COOKIES = _settings.FORCE_SECURE_COOKIES

LOG_LEVEL = _settings.LOG_LEVEL

STANDBY_MODE = _settings.STANDBY_MODE

TELEGRAM_BOT_TOKEN = _settings.TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = _settings.TELEGRAM_CHAT_ID

DISCORD_BOT_TOKEN = _settings.DISCORD_BOT_TOKEN
DISCORD_CHANNEL_ID = _settings.DISCORD_CHANNEL_ID

SLACK_BOT_TOKEN = _settings.SLACK_BOT_TOKEN
SLACK_APP_TOKEN = _settings.SLACK_APP_TOKEN
SLACK_CHANNEL_ID = _settings.SLACK_CHANNEL_ID

TWITTER_BEARER_TOKEN = _settings.TWITTER_BEARER_TOKEN
TWITTER_RULES = [r.strip() for r in _settings.TWITTER_RULES.split(",") if r.strip()]

TWILIO_AUTH_TOKEN = _settings.TWILIO_AUTH_TOKEN
TWILIO_WEBHOOK_URL = _settings.TWILIO_WEBHOOK_URL
TWILIO_ALLOWED_FROM_NUMBERS = [n.strip() for n in _settings.TWILIO_ALLOWED_FROM_NUMBERS.split(",") if n.strip()]

MT4_MT5_METAAPI_TOKEN = _settings.MT4_MT5_METAAPI_TOKEN
MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID = _settings.MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID

RITHMIC_USER = _settings.RITHMIC_USER
RITHMIC_PASSWORD = _settings.RITHMIC_PASSWORD
RITHMIC_SYSTEM_NAME = _settings.RITHMIC_SYSTEM_NAME
RITHMIC_GATEWAY_URL = _settings.RITHMIC_GATEWAY_URL
RITHMIC_SOURCE_ACCOUNT_ID = _settings.RITHMIC_SOURCE_ACCOUNT_ID

RECONCILE_INTERVAL_SECONDS = _settings.RECONCILE_INTERVAL_SECONDS
PRICE_MONITOR_INTERVAL_SECONDS = _settings.PRICE_MONITOR_INTERVAL_SECONDS

SEC_EDGAR_USER_AGENT = _settings.SEC_EDGAR_USER_AGENT
FRED_API_KEY = _settings.FRED_API_KEY
