# Secrets reference

All secrets/credentials come from **environment variables only** — never
from the YAML routing/account config files, so those stay safe to commit as
examples (`app/config.py`'s own module docstring). Copy `.env.example` to
`.env` and fill in real values; never commit `.env`.

Every field below is read once at import time by `app/config.py`'s
pydantic-settings `_Settings` model.

## Owner authentication

| Field | Purpose | Rotation |
|---|---|---|
| `OWNER_PASSWORD` | Legacy plaintext owner credential, compared with `hmac.compare_digest`. Mutually exclusive with `OWNER_PASSWORD_HASH` — setting both is treated as misconfigured and fails auth closed. | Change the env var and restart; every existing session is automatically revoked (see `_credential_epoch` in `app/auth.py`). |
| `OWNER_PASSWORD_HASH` | Preferred (C05): an argon2id hash via `pwdlib`. Generate with `python -c "from pwdlib import PasswordHash; print(PasswordHash.recommended().hash('<password>'))"`. | Same as above — rotates the credential epoch and revokes all sessions. |
| `SESSION_SECRET` | Signs/derives session data; combined with the active credential into the session-epoch fingerprint. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. | Rotating this alone also revokes every existing session, even without changing the password. |

If neither password field nor `SESSION_SECRET` is set, every owner-gated
endpoint fails closed with `503` (see `docs/security/ARCHITECTURE.md`).

## Ingress shared secrets

| Field | Purpose |
|---|---|
| `WEBHOOK_SHARED_SECRET` | Required in the `X-Webhook-Secret` header on `POST /webhook/{source}`. Blank disables the route (`503`). |
| `TWILIO_AUTH_TOKEN` | Validates Twilio's own request signature on `POST /sms/twilio`. |
| `TWILIO_WEBHOOK_URL` | The full public URL Twilio POSTs to — required for signature validation, since Twilio signs the exact URL it called. Not itself a secret, but must match exactly. |
| `TWILIO_ALLOWED_FROM_NUMBERS` | Comma-separated E.164 sender numbers authorized to submit trading instructions via SMS. Empty means no sender is authorized (fail-closed). |
| `WHATSAPP_APP_SECRET` | Validates Meta's `X-Hub-Signature-256` header on WhatsApp Business Cloud API webhook POSTs. |
| `WHATSAPP_VERIFY_TOKEN` | Answers Meta's one-time GET handshake when the webhook is first configured. |
| `WHATSAPP_ALLOWED_FROM_NUMBERS` | Comma-separated `wa_id` numbers (no leading `+`) authorized to send trading instructions. Empty means no sender is authorized. |
| `NINJATRADER_WEBHOOK_SECRET` | Plain shared secret checked against the `X-NinjaTrader-Secret` header (NinjaScript has no built-in request signing). |

### Notification-bridge device pairing tokens (`app/notification_bridge.py`)

Track 10: the Android `NotificationListenerService` companion app
(`mobile/notification-bridge/`) authenticates to `POST
/ingest/notification-bridge/{device_id}` with a per-device bearer token,
NOT a global env var like `WEBHOOK_SHARED_SECRET` above -- one compromised
phone must not authorize forwarding on every other device's behalf. The
token is generated server-side by the owner-gated `POST
/notification-bridge/devices` call, returned in that response body
**exactly once**, and only its argon2id hash (via `pwdlib`, the same
algorithm choice as `OWNER_PASSWORD_HASH`) is ever persisted, in the
`notification_bridge_devices` table's `pairing_token_hash` column. There
is no env var for this credential at all -- it lives only (a) briefly in
the registration HTTP response and (b) at rest on the paired Android
device itself (see `mobile/notification-bridge/README.md`'s "Known
limitations" for that storage's own disclosed limitation). Lost/rotated
the same way: re-run `POST /notification-bridge/devices` with the same
`device_id` to issue a fresh token.

## Broker credentials (per destination account)

Broker credentials are namespaced per `account_id` (as defined in
`config/accounts.yaml`), not global config fields, except where noted.
Pattern: `<BROKER>_<ACCOUNT_ID>_<FIELD>`. See `.env.example` for the full
set of examples per adapter. Representative fields:

| Broker | Env var pattern | Notes |
|---|---|---|
| ccxt (crypto) | `CCXT_<ACCOUNT_ID>_API_KEY`, `CCXT_<ACCOUNT_ID>_API_SECRET` | Which exchange: global `CCXT_EXCHANGE_ID` (single-exchange deployments) or `CCXT_EXCHANGES` (comma-separated, multi-exchange). `CCXT_SANDBOX=true` points at the exchange's own testnet. |
| Alpaca | `ALPACA_<ACCOUNT_ID>_API_KEY`, `ALPACA_<ACCOUNT_ID>_API_SECRET`, `ALPACA_<ACCOUNT_ID>_BASE_URL` | `BASE_URL` defaults to the paper endpoint if unset. |
| MT4/MT5 (local terminal) | `MT5_<ACCOUNT_ID>_LOGIN`, `MT5_<ACCOUNT_ID>_PASSWORD`, `MT5_<ACCOUNT_ID>_SERVER` | Requires a same-host terminal, one instance per account. |
| MT4/MT5 (MetaApi cloud) | `MT4_MT5_METAAPI_TOKEN` (global), `MT4_MT5_METAAPI_<ACCOUNT_ID>_ID`, `MT4_MT5_METAAPI_SOURCE_ACCOUNT_ID` | No local terminal needed. |
| SignalStack | `SIGNALSTACK_<ACCOUNT_ID>_WEBHOOK_URL` | The per-account webhook URL from the SignalStack dashboard — SignalStack itself fans out to the real broker/exchange. |
| NinjaTrader | `NT8_<ACCOUNT_ID>_URL` | Local URL of that account's TradeRouter `WebhookOrderStrategy.cs` instance. |
| IBKR | `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` (global, one Gateway/TWS process per account) | No per-account credential env vars — connection is to a locally running, already-logged-in TWS/IB Gateway process. |
| Tradovate | `TRADOVATE_<ACCOUNT_ID>_*` | Per-account credentials, `ACCOUNT_SPEC` is Tradovate's real account name. |
| Rithmic | `RITHMIC_USER`, `RITHMIC_PASSWORD`, `RITHMIC_SYSTEM_NAME`, `RITHMIC_GATEWAY_URL`, `RITHMIC_SOURCE_ACCOUNT_ID` (global — single Rithmic connection, `SOURCE_ACCOUNT_ID` is an optional per-source filter) | |
| Robinhood, Schwab, Tastytrade, TradeStation | Per-adapter env vars — see each adapter's module docstring in `app/brokers/`. Robinhood and Schwab additionally require `ROBINHOOD_ACKNOWLEDGE_TOS_RISK` / `SCHWAB_ACKNOWLEDGE_NO_SANDBOX` set `true` before either will place a real (unavoidably real, since neither has a sandbox) order. | |

**Rotation**: broker credentials are rotated at the broker/exchange itself
(revoke old key, issue new key) and the corresponding env var updated, then
the process restarted. This codebase has no in-process broker-credential
rotation mechanism — restart is required to pick up a new value.

## Optional pull-based source credentials

`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `DISCORD_BOT_TOKEN`/`DISCORD_CHANNEL_ID`,
`SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/`SLACK_CHANNEL_ID`,
`TWITTER_BEARER_TOKEN`/`TWITTER_RULES`. Each source only starts if all of
its own required env vars are set — an unconfigured source is simply never
started, not a security gap by itself. Push-based sources (webhook, SMS)
need no startup config beyond the shared secrets above.

### Telegram USER-ACCOUNT collectors (`app/sources/telegram_user.py`)

Track 5: for a Telegram channel the owner can only read via their own
personal account (the provider won't allow adding a bot, or the channel
restricts forwarding) — see `docs/security/TELEGRAM_USER_LOGIN.md` for
the full, mandatory authorization procedure. **This credential is never
obtained by an AI agent or inside an AI-visible session** — it is
produced entirely by the owner running `scripts/telegram_user_login.py`
themselves, locally, outside this codebase.

| Field | Purpose |
|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | This deployment's Telegram API app credentials (from <https://my.telegram.org/apps>), global — shared across every user-account collector this deployment runs. |
| `TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH` | Per-collector (namespaced by the registry's own `collector_id`, same `<PREFIX>_<ID>_<FIELD>` convention as the broker env vars above): the path to the `.session` file (or session-string file) `scripts/telegram_user_login.py` produced for that collector. Never a session string/token passed directly as a Python argument or logged. |

The `telegram_collectors` registry table (`app/telegram_collectors.py`,
`app/db.py`) never stores a credential VALUE — only `credential_env_var`,
the NAME of the environment variable above that holds it, so the registry
itself stays safe to read/export/back up like any other non-secret
config.

### Slack/Twitter USER-CONTEXT collectors (Track 6: `app/sources/slack_user.py`,
`app/sources/twitter_user.py`, `app/collector_registry.py`)

For a Slack workspace you cannot invite a bot into, or a protected/
private-and-approved-follower-only X account — see
`docs/security/SLACK_USER_TOKEN.md` / `docs/security/TWITTER_USER_CONTEXT.md`
for the full, mandatory authorization procedures. **Neither credential is
ever obtained by an AI agent or inside an AI-visible session** — the
Slack token comes from the owner's own browser-based OAuth install on
`api.slack.com`; the Twitter/X token comes from the owner running
`scripts/twitter_user_oauth.py` themselves, locally, which walks through
X's own browser-based OAuth 2.0 consent flow.

| Field | Purpose |
|---|---|
| `SLACK_USER_APP_TOKEN` | The Socket Mode app-level token (`xapp-...`) for the (separate, user-scoped) Slack app used by every `SlackUserSource` collector this deployment runs — global. |
| `SLACK_USER_<COLLECTOR_ID>_TOKEN` | Per-collector: the OAuth **user** token (`xoxp-...`, NOT a bot token) from that collector's own Slack app install. |
| `TWITTER_USER_<COLLECTOR_ID>_ACCESS_TOKEN` | Per-collector: the OAuth 2.0 user-context access token from `scripts/twitter_user_oauth.py`'s output. |

The `pull_collectors` registry table (`app/collector_registry.py`,
`app/db.py`) never stores a credential VALUE — only `credential_env_var`,
the NAME of the environment variable above that holds it, exactly the
same convention as `telegram_collectors`.

### Email collectors (`app/sources/email_source.py`)

Track 7: IMAP-based ingestion of trading-alert email (a provider's
newsletter/alert emails, or a dedicated signal-forwarding mailbox) — see
`docs/security/EMAIL_COLLECTOR.md` for the full setup procedure (how to
generate an IMAP app password for Gmail/Outlook/other providers). Only
IMAP with an app-specific password is implemented; a Gmail-API/OAuth mode
is a documented, unimplemented follow-up (see that doc's own "Gmail API
(OAuth) — not yet implemented" section).

| Field | Purpose |
|---|---|
| `EMAIL_<COLLECTOR_ID>_APP_PASSWORD` | Per-collector (same `<PREFIX>_<ID>_<FIELD>` convention as every other per-collector/per-account env var in this file): the IMAP app password generated at the provider (never the account's real login password). |
| `EMAIL_<COLLECTOR_ID>_USERNAME` | Per-collector: the mailbox's own IMAP login username, usually the full email address. |

The `email_collectors` registry table (`app/email_collectors.py`,
`app/db.py`) never stores a credential VALUE — only `credential_env_var`,
the NAME of the environment variable above that holds it, plus
non-secret identity (mailbox address, IMAP host/folder, sender
allowlist, provider mapping).

## Export relay to the commercial platform

| Field | Purpose | Rotation |
|---|---|---|
| `RELAY_INGRESS_URL` | The one allowlisted destination `app/relay_worker.py` is ever permitted to POST export batches to. Blank disables the relay (`RelayNotConfiguredError`, loud, never a silent no-op). | Changed by the operator when repointing at a different commercial deployment; no in-app rotation. |
| `RELAY_SIGNING_SECRET` | Signs every export batch (`sign_relay_payload`). Must match signal-portfolio-commercial's own `RELAY_SIGNING_SECRET`. Defaults to a `LOCAL_SIM`-prefixed placeholder — safe only for local/paper use; **must** be changed before pointing at a real commercial deployment. | Coordinated rotation with the commercial side: change both simultaneously (no dual-secret support here — see the catalog fit-sim secret below for the pattern that does support staged rotation). |
| `RELAY_PRODUCER_ID` | This deployment's own producer identity on every exported envelope. Not a secret, but must be distinct across signal-copier instances sharing one commercial tenant. | N/A |
| `RELAY_EVIDENCE_CLASS` | Truth claim about the evidence quality of exported executions (e.g. `INTERNAL_PAPER`, `OBSERVED_OWNER_LIVE`). Defaults to the safest value. | Changed deliberately, never inferred. |
| `RELAY_ENVIRONMENT` | Same safest-default reasoning as `RELAY_EVIDENCE_CLASS`. Defaults to `LOCAL_SIM`. | Changed deliberately. |

### `CATALOG_FIT_SIM_SIGNING_SECRET` / `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS`

A **separate** shared secret from `RELAY_SIGNING_SECRET`, for a separate
trust boundary: the public catalog's `POST
/catalog/providers/{source}/fit-simulation` endpoint, called by the
commercial platform on behalf of an anonymous prospect. Deliberately never
reused from `RELAY_SIGNING_SECRET` — a leaked fit-simulation secret must
never forge a relay export-event batch, and vice versa.

**This is the one secret pair in this codebase with a built-in, supported
two-step rotation procedure:**

1. Set `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` to the current (soon to be
   old) value of `CATALOG_FIT_SIM_SIGNING_SECRET`.
2. Set `CATALOG_FIT_SIM_SIGNING_SECRET` to the new value, and update it on
   signal-portfolio-commercial's own config to match.
3. During the transition, `verify_catalog_fit_sim_signature`
   (`app/services/catalog_fit_sim_auth.py`) accepts a request signed with
   either the current or the previous secret.
4. Once every caller (i.e. the commercial platform) is confirmed using the
   new secret, clear `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` back to
   blank. Blank means no previous secret is accepted — rotation complete.

## Never committed

`.gitignore` excludes `.env`; `.gitleaks.toml` provides secret-scanning
configuration for this repository. `config/routing.yaml` and
`config/accounts.yaml` (the real, filled-in versions, as opposed to their
`.example` counterparts) contain account identifiers but never secrets —
secrets are looked up from the environment at broker-adapter construction
time in `app/main.py`, keyed by account id.
