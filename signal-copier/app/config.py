"""Runtime configuration.

Secrets (broker API keys, bot tokens, webhook secrets) come from environment
variables only — never from the YAML routing/account files, so those files
stay safe to commit as examples.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

ROUTING_CONFIG_PATH = Path(os.getenv("ROUTING_CONFIG_PATH", BASE_DIR / "config" / "routing.yaml"))
ACCOUNTS_CONFIG_PATH = Path(os.getenv("ACCOUNTS_CONFIG_PATH", BASE_DIR / "config" / "accounts.yaml"))
#: Optional per-provider/per-analyst settings overrides — see app/providers.py.
#: Missing file (the default if never created) means no overrides apply.
PROVIDERS_CONFIG_PATH = Path(os.getenv("PROVIDERS_CONFIG_PATH", BASE_DIR / "config" / "providers.yaml"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", BASE_DIR / "signal_copier.db"))

# Shared secret the webhook source checks against a header/query param so
# random requests on the public endpoint can't inject fake signals.
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "")

# Owner authentication (see app/auth.py). Every account/routing/position/
# close/flatten/backtest endpoint requires a valid owner session; both of
# these must be set or every one of those endpoints fails closed (503),
# never silently open. OWNER_PASSWORD is compared with a constant-time
# check, same trust level as every other secret this project keeps in an
# env var (see README's Security notes) -- there is no user database,
# this is a single-owner app. SESSION_SECRET signs/derives session data;
# generate both with e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
SESSION_TTL_SECONDS = float(os.getenv("SESSION_TTL_SECONDS", str(60 * 60 * 12)))  # 12h

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Standby mode (see deploy/RUNBOOK.md): when true, this process serves only
# GET/HEAD/OPTIONS -- no signal ingestion, no background reconciliation/price
# polling, no financial command can reach the engine, regardless of what any
# individual route's own logic does. A promotion is a deliberate, separate
# restart with this unset (or false) AND real broker credentials configured --
# never a config flip on an already-running process.
STANDBY_MODE = os.getenv("STANDBY_MODE", "false").strip().lower() in ("1", "true", "yes")

# Optional pull-based sources: each only starts if its required env vars are
# all set (see .env.example). Push-based sources (webhook, SMS) need no
# startup config beyond their own route.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "")

TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")
TWITTER_RULES = [r.strip() for r in os.getenv("TWITTER_RULES", "").split(",") if r.strip()]

TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
# Full public URL Twilio POSTs to, required for signature validation (Twilio
# signs the exact URL it called, including scheme/host).
TWILIO_WEBHOOK_URL = os.getenv("TWILIO_WEBHOOK_URL", "")

MT4_MT5_METAAPI_TOKEN = os.getenv("MT4_MT5_METAAPI_TOKEN", "")
MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID = os.getenv("MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID", "")

RITHMIC_USER = os.getenv("RITHMIC_USER", "")
RITHMIC_PASSWORD = os.getenv("RITHMIC_PASSWORD", "")
RITHMIC_SYSTEM_NAME = os.getenv("RITHMIC_SYSTEM_NAME", "")
RITHMIC_GATEWAY_URL = os.getenv("RITHMIC_GATEWAY_URL", "")
RITHMIC_SOURCE_ACCOUNT_ID = os.getenv("RITHMIC_SOURCE_ACCOUNT_ID", "")  # optional filter

# How often app/reconciliation.py re-checks PENDING orders on brokers that
# support get_order_status() (currently Alpaca and IBKR).
RECONCILE_INTERVAL_SECONDS = float(os.getenv("RECONCILE_INTERVAL_SECONDS", "30"))

# How often app/pricing.py's PriceMonitor polls each open managed-lifecycle
# position's broker for a current price (currently ccxt only — see
# app/pricing.py's module docstring).
PRICE_MONITOR_INTERVAL_SECONDS = float(os.getenv("PRICE_MONITOR_INTERVAL_SECONDS", "15"))

# Read-only market/economic context lookups (see app/context/ — SEC
# filings, FRED macro series, FX reference rates). None of these are used
# anywhere in the order-management/protective-stop path.
#
# SEC's fair-access policy requires an identifying User-Agent on every
# request (e.g. "YourCompany admin@example.com") — no API key, but the
# /context/filings endpoint 501s if this is blank rather than send an
# unidentified request.
SEC_EDGAR_USER_AGENT = os.getenv("SEC_EDGAR_USER_AGENT", "")
# Free key from https://fredaccount.stlouisfed.org/apikeys -- the
# /context/fred endpoint 501s if this is blank.
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
