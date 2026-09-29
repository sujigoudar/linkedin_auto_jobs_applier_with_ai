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

    # TR-0x redesign transition flag (see app/static/dashboard.html's
    # #legacy-content section): the legacy "all panels on one page"
    # dashboard is superseded by the 16 hash-routed TR-0X screens, which a
    # prior design review confirmed have reached parity+more. Off by
    # default -- the empty-hash `/` route and the legacy nav link both
    # redirect straight to `#/trade` instead of showing the legacy panels.
    # An operator who still needs the legacy view for development sets
    # this true; nothing about the legacy panels' own markup, JS handlers,
    # or backend endpoints is removed -- this only gates whether the page
    # routes a visitor to them.
    LEGACY_DASHBOARD_ENABLED: bool = False

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

    # WhatsApp signal source, via Meta's official WhatsApp Business Cloud
    # API (see app/sources/whatsapp.py's docstring for full setup).
    # APP_SECRET validates the X-Hub-Signature-256 header Meta signs every
    # webhook POST with; VERIFY_TOKEN answers Meta's one-time GET handshake
    # when the webhook is first configured. Neither alone authorizes a
    # sender to submit trading instructions -- same two-part pattern as
    # Twilio's AUTH_TOKEN (transport) + ALLOWED_FROM_NUMBERS (authorization)
    # above.
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    # Comma-separated E.164 numbers with NO leading '+' (WhatsApp's own
    # `wa_id` format, e.g. "15551234567,15557654321"); unset/empty means no
    # sender is authorized, same fail-closed pattern as
    # TWILIO_ALLOWED_FROM_NUMBERS above.
    WHATSAPP_ALLOWED_FROM_NUMBERS: str = ""

    # NinjaTrader signal source (see app/sources/ninjatrader.py's docstring)
    # -- a NinjaScript AddOn/Indicator POSTs fill events here with this
    # value in an X-NinjaTrader-Secret header. NinjaScript has no built-in
    # request-signing the way Twilio/Meta's platforms do, so this is a
    # plain shared secret (same fail-closed pattern: unset means the
    # route stays disabled, not open).
    NINJATRADER_WEBHOOK_SECRET: str = ""

    MT4_MT5_METAAPI_TOKEN: str = ""
    MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID: str = ""

    RITHMIC_USER: str = ""
    RITHMIC_PASSWORD: str = ""
    RITHMIC_SYSTEM_NAME: str = ""
    RITHMIC_GATEWAY_URL: str = ""
    RITHMIC_SOURCE_ACCOUNT_ID: str = ""  # optional filter

    # app/brokers/ibkr.py's own IBKRBroker(host, port, client_id) -- these
    # were hardcoded at every call site (app/main.py's broker registry
    # constructed it with no arguments at all, so a real deployment could
    # never point it at anything but a local TWS/Gateway on the default
    # paper port). IB Gateway's default ports: 7497 paper / 7496 live for
    # TWS, 4002 paper / 4001 live for IB Gateway -- see ibkr.py's own
    # module docstring for the real distinction.
    IBKR_HOST: str = "127.0.0.1"
    IBKR_PORT: int = 7497
    IBKR_CLIENT_ID: int = 1

    # app/brokers/ccxt_broker.py's own CCXTBroker(exchange_id) -- the
    # constructor already took this as a parameter, but app/main.py's
    # broker registry constructed it with no arguments at all, silently
    # defaulting every real deployment to Binance regardless of which
    # exchange the operator's own CCXT_<ACCOUNT_ID>_API_KEY credentials
    # actually belong to. Any ccxt exchange id (e.g. "binance", "bybit",
    # "kraken", "okx") is valid here.
    CCXT_EXCHANGE_ID: str = "binance"
    # ccxt's own unified `set_sandbox_mode(True)` toggle, wired in by
    # CCXTBroker._exchange_for -- there was previously no way at all to
    # point this broker at an exchange's sandbox/testnet, only ever the
    # real live venue. Off (real trading) by default; a real deployment
    # verifying this integration before risking real funds should set
    # this true against whichever exchange's own sandbox ccxt supports.
    CCXT_SANDBOX: bool = False

    # Comma-separated ccxt exchange ids to register SIMULTANEOUSLY, each as
    # its own broker (name f"ccxt_{exchange_id}", e.g. "ccxt_binance",
    # "ccxt_kraken") -- e.g. "binance,kraken,coinbase". A real gap this
    # covers: CCXT_EXCHANGE_ID above only ever configured ONE exchange for
    # the whole deployment (the single broker named "ccxt"), so an operator
    # who wants accounts on two different exchanges at once (common —
    # ccxt itself bundles 100+ real exchange integrations, confirmed via
    # this session's own `python3 -c "import ccxt; print(ccxt.exchanges)"`)
    # had no way to do that in one deployment at all. Empty (the default)
    # changes nothing: only the single CCXT_EXCHANGE_ID-based "ccxt" broker
    # is registered, exactly as before this was added. CCXT_SANDBOX applies
    # to every exchange listed here the same way it already applies to the
    # single-exchange case.
    CCXT_EXCHANGES: str = ""

    # app/brokers/schwab.py's own SchwabBroker -- Schwab has NO sandbox/
    # paper environment at all (confirmed by reading every request-issuing
    # method in the community wrapper schwab-py's own client modules: only
    # one base URL exists anywhere in that codebase). Unlike every other
    # optional broker in this registry (gated only by having its
    # credentials set), this one is ALSO gated behind this explicit
    # acknowledgement -- set it to true only once you understand every
    # order this broker places, including your first test of it, is real
    # and uses real money.
    SCHWAB_ACKNOWLEDGE_NO_SANDBOX: bool = False

    # app/brokers/robinhood.py's own RobinhoodBroker -- Robinhood has
    # never published an official trading API, has no sandbox, and
    # automating trades against it through these reverse-engineered
    # endpoints is outside Robinhood's own Terms of Service for
    # programmatic access. Gated behind this SEPARATE, explicit
    # acknowledgement on top of having credentials set -- set it to true
    # only once you understand every order this broker places is real
    # money AND that using it this way is outside Robinhood's own ToS.
    ROBINHOOD_ACKNOWLEDGE_TOS_RISK: bool = False

    # How often app/reconciliation.py re-checks PENDING orders on brokers that
    # support get_order_status() (currently Alpaca and IBKR).
    RECONCILE_INTERVAL_SECONDS: float = 30.0

    # How often app/pricing.py's PriceMonitor polls each open managed-lifecycle
    # position's broker for a current price (currently ccxt, Alpaca and IBKR —
    # see app/pricing.py's module docstring).
    PRICE_MONITOR_INTERVAL_SECONDS: float = 15.0

    # How often app/equity_history.py's EquitySnapshotter persists one real
    # equity/P&L snapshot per configured account (PU-A3). Default: 5 minutes
    # -- frequent enough for a real intraday equity curve, infrequent enough
    # that a large account roster's snapshot pass stays cheap.
    EQUITY_SNAPSHOT_INTERVAL_SECONDS: float = 300.0

    # How often app/provider_scout.py re-evaluates every signal source/analyst
    # that ISN'T yet a tracked provider_subscriptions row against
    # PROVIDER_VALUE_* below, looking for a free provider worth promoting.
    # Default: once a day -- there's no value in re-scanning more often than
    # new closed trades can plausibly accumulate.
    PROVIDER_SCOUT_INTERVAL_SECONDS: float = 86400.0

    # Thresholds app/provider_value.py's verdict logic uses for both the
    # subscribed-provider "still worth paying for" recommendation and
    # app/provider_scout.py's free-provider "worth promoting" recommendation
    # -- see that module's docstring for the exact decision tree. Deliberately
    # a heuristic disclosed as such, not a claim of statistical significance.
    PROVIDER_VALUE_MIN_SAMPLE_SIZE: int = 10
    PROVIDER_VALUE_WIN_RATE_THRESHOLD: float = 0.4
    PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD: float = 1.0

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

    # Signal Platform Integration Correction Pack's own
    # INTEGRATION_DECISION.md S4.3: the restricted relay worker
    # (app/relay_worker.py) posts export_events batches to exactly this
    # one allowlisted URL -- never a generic proxy, never discovered or
    # overridden at request time. Blank means the relay worker refuses to
    # run (see relay_worker.py's own RelayNotConfiguredError) rather than
    # silently posting nowhere or guessing a default.
    RELAY_INGRESS_URL: str = ""
    # Shared secret with signal-portfolio-commercial's own
    # RELAY_SIGNING_SECRET (app/services/relay_auth.py there) -- signs
    # every batch this worker sends. A placeholder default only; both
    # sides must be configured with the SAME real secret before this
    # worker is ever pointed at a real commercial deployment.
    RELAY_SIGNING_SECRET: str = "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"
    RELAY_POLL_INTERVAL_SECONDS: float = 1.0
    RELAY_BATCH_SIZE: int = 100
    #: This deployment's own `EventEnvelope.producer_id` -- identifies
    #: WHICH signal-copier instance produced an exported event (S6's own
    #: "producer identity, producer generation"). Two signal-copier
    #: deployments sharing one commercial tenant must use distinct values
    #: here, or their exports become indistinguishable at the inbox.
    RELAY_PRODUCER_ID: str = "signal-copier-local"
    #: `signal_platform_contracts.EvidenceClass` member NAME (e.g.
    #: "INTERNAL_PAPER", "OBSERVED_OWNER_LIVE") every exported
    #: EXECUTION_APPLIED envelope is labeled with -- app/export_events.py
    #: never infers this from a broker's name (a "paper"-named broker
    #: adapter reused against a real account would be a real, silent
    #: mislabel). Defaults to INTERNAL_PAPER, the safest value: an
    #: operator who genuinely wants OBSERVED_OWNER_LIVE evidence must
    #: change this deliberately, matching this whole project's standing
    #: "no live orders, everything stays paper-traded/simulated" default.
    RELAY_EVIDENCE_CLASS: str = "INTERNAL_PAPER"
    #: `signal_platform_contracts.Environment` member NAME. Same
    #: safest-default reasoning as RELAY_EVIDENCE_CLASS above.
    RELAY_ENVIRONMENT: str = "LOCAL_SIM"

    #: A SEPARATE shared secret from RELAY_SIGNING_SECRET above, for a
    #: SEPARATE trust boundary: signal-portfolio-commercial's own public
    #: catalog (an anonymous prospect evaluating a PUBLISHED provider
    #: before subscribing) calls this service's own
    #: `POST /catalog/providers/{source}/fit-simulation` -- never
    #: `POST /providers/{source}/fit-simulation` (the owner-gated one,
    #: still cookie/CSRF-only) -- signing its request with this secret.
    #: Deliberately NOT the relay secret, same "a stolen credential must
    #: not become a different credential" reasoning
    #: INTEGRATION_DECISION.md S11 already applies to
    #: RELAY_SIGNING_SECRET vs the billing webhook secret on the
    #: commercial side: a leaked catalog fit-sim secret must never be
    #: usable to forge a relay export-event batch, and vice versa. See
    #: app/services/catalog_fit_sim_auth.py's own module docstring for
    #: the exact audience-bound signing scheme. A placeholder default
    #: only; a real deployment sets this via a secrets manager and keeps
    #: it in sync with signal-portfolio-commercial's own
    #: CATALOG_FIT_SIM_SIGNING_SECRET.
    CATALOG_FIT_SIM_SIGNING_SECRET: str = "LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed"

    # INT-040: a real, configurable ceiling on the private export outbox's
    # own real backlog -- see app/db.py's `SignalStore.export_outbox_backlog`
    # for exactly what is measured (SUM(LENGTH(envelope_json)) over
    # undelivered `export_events` rows -- a real, live number, never an
    # estimate) and app/main.py's `/health` `outbox_backlog_ok` for how
    # it's surfaced. If the commercial platform is unreachable long
    # enough, this table grows unboundedly; this ceiling is the alerting
    # threshold at which an operator should be told, well before an
    # actual out-of-space condition, so it never becomes a reason to
    # prune or truncate real, undelivered financial evidence.
    #
    # Default (256 MiB) -- an operator-tunable starting point, not a
    # claim about any specific deployment's real disk capacity:
    # `signal_platform_contracts.EventEnvelope` (this table's own
    # `envelope_json`) is a handful of scalar identity/timing fields plus
    # one small payload -- a real EXECUTION_APPLIED envelope in this
    # codebase's own test fixtures serializes to roughly 1-2 KB. 256 MiB
    # is therefore on the order of 150k-250k undelivered events: at any
    # plausible per-account signal rate this project's own sources
    # produce (webhook/Telegram/Discord/etc., nowhere near
    # high-frequency-trading volume), that is comfortably weeks of a
    # fully-down commercial ingress before this ceiling trips, while
    # still staying a small, safe fraction (well under 5%) of even a
    # modest 5-10 GB deployment disk -- real headroom between "the alert
    # fires" and "the disk is actually full." Tune this down for a
    # small/constrained disk, or up for a genuinely high-volume
    # deployment.
    EXPORT_OUTBOX_SIZE_CEILING_BYTES: int = 256 * 1024 * 1024


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

LEGACY_DASHBOARD_ENABLED = _settings.LEGACY_DASHBOARD_ENABLED

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

WHATSAPP_APP_SECRET = _settings.WHATSAPP_APP_SECRET
WHATSAPP_VERIFY_TOKEN = _settings.WHATSAPP_VERIFY_TOKEN
WHATSAPP_ALLOWED_FROM_NUMBERS = [n.strip() for n in _settings.WHATSAPP_ALLOWED_FROM_NUMBERS.split(",") if n.strip()]

NINJATRADER_WEBHOOK_SECRET = _settings.NINJATRADER_WEBHOOK_SECRET

MT4_MT5_METAAPI_TOKEN = _settings.MT4_MT5_METAAPI_TOKEN
MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID = _settings.MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID

IBKR_HOST = _settings.IBKR_HOST
IBKR_PORT = _settings.IBKR_PORT
IBKR_CLIENT_ID = _settings.IBKR_CLIENT_ID

CCXT_EXCHANGE_ID = _settings.CCXT_EXCHANGE_ID
CCXT_SANDBOX = _settings.CCXT_SANDBOX
CCXT_EXCHANGES = [e.strip() for e in _settings.CCXT_EXCHANGES.split(",") if e.strip()]
SCHWAB_ACKNOWLEDGE_NO_SANDBOX = _settings.SCHWAB_ACKNOWLEDGE_NO_SANDBOX
ROBINHOOD_ACKNOWLEDGE_TOS_RISK = _settings.ROBINHOOD_ACKNOWLEDGE_TOS_RISK

RITHMIC_USER = _settings.RITHMIC_USER
RITHMIC_PASSWORD = _settings.RITHMIC_PASSWORD
RITHMIC_SYSTEM_NAME = _settings.RITHMIC_SYSTEM_NAME
RITHMIC_GATEWAY_URL = _settings.RITHMIC_GATEWAY_URL
RITHMIC_SOURCE_ACCOUNT_ID = _settings.RITHMIC_SOURCE_ACCOUNT_ID

RECONCILE_INTERVAL_SECONDS = _settings.RECONCILE_INTERVAL_SECONDS
PRICE_MONITOR_INTERVAL_SECONDS = _settings.PRICE_MONITOR_INTERVAL_SECONDS
EQUITY_SNAPSHOT_INTERVAL_SECONDS = _settings.EQUITY_SNAPSHOT_INTERVAL_SECONDS
PROVIDER_SCOUT_INTERVAL_SECONDS = _settings.PROVIDER_SCOUT_INTERVAL_SECONDS
PROVIDER_VALUE_MIN_SAMPLE_SIZE = _settings.PROVIDER_VALUE_MIN_SAMPLE_SIZE
PROVIDER_VALUE_WIN_RATE_THRESHOLD = _settings.PROVIDER_VALUE_WIN_RATE_THRESHOLD
PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD = _settings.PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD

SEC_EDGAR_USER_AGENT = _settings.SEC_EDGAR_USER_AGENT
FRED_API_KEY = _settings.FRED_API_KEY

RELAY_INGRESS_URL = _settings.RELAY_INGRESS_URL
RELAY_SIGNING_SECRET = _settings.RELAY_SIGNING_SECRET
RELAY_POLL_INTERVAL_SECONDS = _settings.RELAY_POLL_INTERVAL_SECONDS
RELAY_BATCH_SIZE = _settings.RELAY_BATCH_SIZE
RELAY_PRODUCER_ID = _settings.RELAY_PRODUCER_ID
RELAY_EVIDENCE_CLASS = _settings.RELAY_EVIDENCE_CLASS
RELAY_ENVIRONMENT = _settings.RELAY_ENVIRONMENT

CATALOG_FIT_SIM_SIGNING_SECRET = _settings.CATALOG_FIT_SIM_SIGNING_SECRET

EXPORT_OUTBOX_SIZE_CEILING_BYTES = _settings.EXPORT_OUTBOX_SIZE_CEILING_BYTES
