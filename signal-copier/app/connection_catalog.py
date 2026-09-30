"""Track 19: the reusable connection-type catalog -- the "AVAILABLE" list
an integrations-style Settings -> Connections page (the user's own spec)
would read from.

This is a STATIC, code-defined registry of every connection TYPE this
codebase could plausibly support, each honestly marked `status=
"implemented"` (this codebase has a real adapter/registration path for
it today -- see each entry's own `verified_against` list of the exact
module(s) checked) or `status="not_implemented"` (a real, named,
forward-compatible catalog entry with no adapter behind it yet).

Every status below was decided by actually reading the cited module(s),
never guessed from the type's name or from what a real integration could
in principle do -- see `app/connections.py`'s own
`_KNOWN_CONNECTION_TYPE_CAPABILITIES` for the capability-shape evidence
this reuses (`capabilities` here is exactly that dict, so the catalog and
the real per-connection default never drift apart -- see
`_capabilities_for`). A `not_implemented` entry's `capabilities` is
`None` (never a guessed shape for an adapter that doesn't exist) and its
`capabilities_note` documents what such an adapter would plausibly need
to support once built -- explicitly labeled as aspirational, not a
promise.

This module builds no new adapters. Building one for a `not_implemented`
type is explicitly out of this track's scope -- see this track's own
final report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.connections import default_capabilities_for_connection_type


@dataclass(frozen=True)
class ConnectionCatalogEntry:
    connection_type: str
    display_name: str
    status: str  # "implemented" | "not_implemented"
    category: str
    verified_against: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


#: `category` is a free-text grouping only (chat/messaging, email, social,
#: sms, automation-platform, generic, mobile) -- purely for a future
#: onboarding wizard's own UI grouping, not enforced/validated anywhere.
_CATALOG: tuple[ConnectionCatalogEntry, ...] = (
    ConnectionCatalogEntry(
        connection_type="telegram_bot",
        display_name="Telegram Bot",
        status="implemented",
        category="chat",
        verified_against=("app/sources/telegram.py", "app/telegram_collectors.py"),
        notes="BotFather bot invited into a chat/channel -- push-delivered, edit-aware, no delete-notification.",
    ),
    ConnectionCatalogEntry(
        connection_type="telegram_user",
        display_name="Telegram (user account)",
        status="implemented",
        category="chat",
        verified_against=("app/sources/telegram_user.py", "app/telegram_collectors.py"),
        notes="Authenticated Telethon user session -- for a channel only readable via a personal account.",
    ),
    ConnectionCatalogEntry(
        connection_type="discord_bot",
        display_name="Discord Bot",
        status="implemented",
        category="chat",
        verified_against=("app/sources/discord.py",),
        notes="discord.py gateway bot, config-wired (single channel_id). No persisted checkpoint/history registry.",
    ),
    ConnectionCatalogEntry(
        connection_type="slack_bot",
        display_name="Slack (bot, Socket Mode)",
        status="implemented",
        category="chat",
        verified_against=("app/sources/slack.py",),
        notes="slack-bolt Socket Mode -- push, live-only, explicitly skips edit/subtype events.",
    ),
    ConnectionCatalogEntry(
        connection_type="slack_user",
        display_name="Slack (user token, pull)",
        status="implemented",
        category="chat",
        verified_against=("app/sources/slack_user.py", "app/collector_registry.py"),
        notes="channels.history polling with a real checkpoint + historical import.",
    ),
    ConnectionCatalogEntry(
        connection_type="twitter_bot",
        display_name="X / Twitter (filtered stream)",
        status="implemented",
        category="social",
        verified_against=("app/sources/twitter.py",),
        notes="tweepy filtered stream -- push, live-only, no checkpoint/history registry.",
    ),
    ConnectionCatalogEntry(
        connection_type="twitter_user",
        display_name="X / Twitter (user context, pull)",
        status="implemented",
        category="social",
        verified_against=("app/sources/twitter_user.py", "app/collector_registry.py"),
        notes="REST polling with a real checkpoint, historical import, and edit_history_tweet_ids-based edit detection.",
    ),
    ConnectionCatalogEntry(
        connection_type="email_imap",
        display_name="Email (IMAP)",
        status="implemented",
        category="email",
        verified_against=("app/sources/email_source.py", "app/email_collectors.py"),
        notes="Any IMAP host (Gmail, Outlook, a dedicated forwarding mailbox) via an app-specific password.",
    ),
    ConnectionCatalogEntry(
        connection_type="gmail_oauth",
        display_name="Gmail (OAuth API)",
        status="not_implemented",
        category="email",
        verified_against=("app/email_collectors.py",),
        notes=(
            "app.email_collectors.ConnectionMode.GMAIL_API is a named, recorded value, but "
            "EmailSource(mode=GMAIL_API) raises NotImplementedError today -- use email_imap instead."
        ),
    ),
    ConnectionCatalogEntry(
        connection_type="website",
        display_name="Website / article list",
        status="implemented",
        category="web",
        verified_against=("app/sources/website.py", "app/website_collectors.py"),
        notes="FEED (RSS/Atom) or ARTICLE_LIST (seen-URL diff) polling.",
    ),
    ConnectionCatalogEntry(
        connection_type="rss",
        display_name="RSS / Atom feed",
        status="implemented",
        category="web",
        verified_against=("app/sources/website.py", "app/website_collectors.py"),
        notes="The FEED mode of the website collector above -- listed separately per the user's own spec.",
    ),
    ConnectionCatalogEntry(
        connection_type="webhook",
        display_name="Generic Webhook",
        status="implemented",
        category="generic",
        verified_against=("app/sources/webhook.py",),
        notes="POST /webhook/{source_name} -- the one fully working, provider-agnostic JSON ingestion path.",
    ),
    ConnectionCatalogEntry(
        connection_type="sms_twilio",
        display_name="SMS (Twilio)",
        status="implemented",
        category="sms",
        verified_against=("app/sources/sms_twilio.py",),
        notes="POST /sms/twilio, X-Twilio-Signature verified.",
    ),
    ConnectionCatalogEntry(
        connection_type="sms_api",
        display_name="SMS (generic carrier API)",
        status="not_implemented",
        category="sms",
        notes="Only the Twilio-specific adapter (sms_twilio) exists; no carrier-agnostic generic SMS API adapter.",
    ),
    ConnectionCatalogEntry(
        connection_type="android_sms",
        display_name="Android SMS (on-device)",
        status="not_implemented",
        category="mobile",
        notes="No adapter reads a paired Android device's own SMS inbox directly (distinct from android_notification).",
    ),
    ConnectionCatalogEntry(
        connection_type="whatsapp_business",
        display_name="WhatsApp Business (Cloud API)",
        status="implemented",
        category="chat",
        verified_against=("app/sources/whatsapp.py",),
        notes="Meta's official WhatsApp Business Cloud API webhook, HMAC-SHA256 signed.",
    ),
    ConnectionCatalogEntry(
        connection_type="android_notification",
        display_name="Android Notification Bridge",
        status="implemented",
        category="mobile",
        verified_against=("app/notification_bridge.py",),
        notes="Paired Android device forwards notification content -- push-delivered, real heartbeat.",
    ),
    ConnectionCatalogEntry(
        connection_type="android_active_retrieval",
        display_name="Android Active Retrieval (phone control)",
        status="implemented",
        category="mobile",
        verified_against=("app/phone_escalation.py",),
        notes="On-demand accessibility-based phone control, gated by a per-app CapabilityState ladder.",
    ),
    ConnectionCatalogEntry(
        connection_type="x",
        display_name="X (DM / other X surfaces)",
        status="not_implemented",
        category="social",
        notes="twitter_bot/twitter_user cover X's public-tweet surfaces; no DM or other X-specific adapter exists.",
    ),
    ConnectionCatalogEntry(
        connection_type="microsoft_365",
        display_name="Microsoft 365 / Outlook",
        status="not_implemented",
        category="email",
        notes="No Microsoft Graph adapter -- an Outlook mailbox could only be reached today via generic email_imap if IMAP is enabled on it.",
    ),
    ConnectionCatalogEntry(
        connection_type="rest_api",
        display_name="Generic REST API (polling)",
        status="not_implemented",
        category="generic",
        notes="No generic authenticated-REST-polling adapter exists; app/sources/webhook.py is push-only.",
    ),
    ConnectionCatalogEntry(
        connection_type="generic_http",
        display_name="Generic HTTP",
        status="implemented",
        category="generic",
        verified_against=("app/sources/webhook.py",),
        notes="Same adapter as 'webhook' -- a generic push-based HTTP JSON ingestion path.",
    ),
    ConnectionCatalogEntry(
        connection_type="make",
        display_name="Make (Integromat)",
        status="not_implemented",
        category="automation-platform",
        notes="No dedicated adapter; a Make scenario would target the generic webhook today.",
    ),
    ConnectionCatalogEntry(
        connection_type="zapier",
        display_name="Zapier",
        status="not_implemented",
        category="automation-platform",
        notes="No dedicated adapter; a Zap would target the generic webhook today.",
    ),
    ConnectionCatalogEntry(
        connection_type="pipedream",
        display_name="Pipedream",
        status="not_implemented",
        category="automation-platform",
        notes="No dedicated adapter; a Pipedream workflow would target the generic webhook today.",
    ),
    ConnectionCatalogEntry(
        connection_type="activepieces",
        display_name="Activepieces",
        status="not_implemented",
        category="automation-platform",
        notes="No dedicated adapter; an Activepieces flow would target the generic webhook today.",
    ),
    ConnectionCatalogEntry(
        connection_type="n8n",
        display_name="n8n",
        status="not_implemented",
        category="automation-platform",
        notes="No dedicated adapter; an n8n workflow would target the generic webhook today.",
    ),
)


def _capabilities_for(entry: ConnectionCatalogEntry) -> Optional[dict[str, Any]]:
    if entry.status != "implemented":
        return None
    return default_capabilities_for_connection_type(entry.connection_type)


def list_connection_catalog_types() -> list[dict[str, Any]]:
    """The full catalog, one dict per entry, each carrying its real
    (`app.connections.default_capabilities_for_connection_type`-derived)
    capability shape when `status="implemented"`, and `None` (never a
    guessed shape) otherwise. This is what `GET /connections/catalog`
    returns -- the AVAILABLE list the user's own spec describes."""
    return [
        {
            "connection_type": entry.connection_type,
            "display_name": entry.display_name,
            "status": entry.status,
            "category": entry.category,
            "capabilities": _capabilities_for(entry),
            "verified_against": list(entry.verified_against),
            "notes": entry.notes,
        }
        for entry in _CATALOG
    ]


def get_connection_catalog_entry(connection_type: str) -> Optional[dict[str, Any]]:
    for entry in _CATALOG:
        if entry.connection_type == connection_type:
            return {
                "connection_type": entry.connection_type,
                "display_name": entry.display_name,
                "status": entry.status,
                "category": entry.category,
                "capabilities": _capabilities_for(entry),
                "verified_against": list(entry.verified_against),
                "notes": entry.notes,
            }
    return None


# --- Track 21: onboarding-wizard field discovery ---------------------
#
# `GET /connections/catalog/{connection_type}/setup-fields` (app/main.py)
# reads this table to tell the "+Add Signal Provider" wizard which real
# inputs it must collect for a given `connection_type` BEFORE calling
# `POST /connections` / `POST /sources` -- built by reading each real
# adapter's own `__init__` (`app/sources/*.py`) rather than guessed field
# names, same evidence discipline as `_CATALOG` above.
#
# Each field's `target` says where the collected value is meant to land
# once the wizard assembles its `POST /connections` / `POST /sources`
# request bodies: `"connection.<column>"` (a `register_connection` kwarg)
# or `"source.<column>"` (a `register_source` kwarg, `source.freshness_
# policy.poll_interval_seconds` being the one nested exception -- `sources.
# freshness_policy` is itself a free-form JSON object). A field with no
# `target` (`target: None`) is real, adapter-required configuration this
# track's three thin registration endpoints do not have a column for --
# it must still be set up separately (an env var, a per-type collector
# registry row such as `app/telegram_collectors.py`/`app/email_collectors.
# py`, or a paired device from Track 12/20) and is surfaced so the wizard
# can say so honestly rather than silently dropping it.
#
# HONEST SCOPE LIMIT, stated once here rather than per-type below:
# `POST /providers` / `POST /sources` / `POST /connections` (Track 21)
# only persist the Track 14 Provider/Source/Connection catalog rows --
# exactly what `register_provider`/`register_source`/`register_connection`
# already did before this track. They do NOT start a live adapter, open a
# socket, subscribe a webhook, or read a real credential. Standing up the
# actual running transport for most connection types still goes through
# this codebase's pre-existing, type-specific collector registries
# (`app/telegram_collectors.py`, `app/email_collectors.py`, `app/website_
# collectors.py`, `app/notification_bridge.py`, ...) or a push route
# already wired in `app/main.py` (`/webhook/{source_name}`, `/sms/twilio`,
# `/whatsapp/webhook`) -- this wizard's job is to make the CATALOG entry
# (and therefore the provider's presence in certification/shadow-mode/
# health tooling) easy to create, not to replace those registries.
_CREDENTIAL_REF_HELP = (
    "The NAME of an environment variable holding this secret (e.g. "
    "TELEGRAM_MYBOT_TOKEN) -- never the raw token/password itself. See "
    "app/connections.py's own module docstring (looks_like_raw_credential)."
)

SetupField = dict[str, Any]

_SETUP_FIELDS: dict[str, list[SetupField]] = {
    "telegram_bot": [
        {"name": "bot_token_env_var", "label": "Bot token (env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "chat_id", "label": "Chat / channel ID", "type": "text", "required": True,
         "target": "source.url_or_reference",
         "help": "The numeric or @channel chat_id this BotFather bot has been added to (app/sources/telegram.py TelegramSource.chat_id)."},
    ],
    "telegram_user": [
        {"name": "api_hash_env_var", "label": "Telethon api_hash (env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "api_id", "label": "Telethon api_id", "type": "text", "required": True,
         "target": "connection.account_identity",
         "help": "my.telegram.org api_id for the authenticated user session (app/sources/telegram_user.py)."},
        {"name": "chat_id", "label": "Chat ID", "type": "text", "required": True, "target": "source.url_or_reference",
         "help": "Channel/chat this user session reads (only reachable via a personal account, not a bot)."},
        {"name": "topic_id", "label": "Topic ID (optional, forum channels)", "type": "text", "required": False,
         "target": "source.source_native_id", "help": "Forum-topic id within the chat, if this channel uses topics."},
    ],
    "discord_bot": [
        {"name": "bot_token_env_var", "label": "Bot token (env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "channel_id", "label": "Channel ID", "type": "text", "required": True,
         "target": "source.url_or_reference", "help": "Discord channel_id this bot reads (app/sources/discord.py)."},
    ],
    "slack_bot": [
        {"name": "bot_token_env_var", "label": "Bot token (xoxb-..., env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "app_token_env_var", "label": "App-level token (xapp-..., env var name)", "type": "credential_reference",
         "required": True, "target": None,
         "help": "Socket Mode also needs a SECOND secret (app.sources.slack.SlackSource.app_token). This data model "
                 "has one credential_reference column per connection -- record both env var names using the same "
                 "naming convention and wire the app token into deployment config directly; only the bot token is "
                 "stored on this connection row."},
        {"name": "channel_id", "label": "Channel ID", "type": "text", "required": True,
         "target": "source.url_or_reference", "help": "Slack channel_id this bot listens on."},
    ],
    "slack_user": [
        {"name": "user_token_env_var", "label": "User OAuth token (xoxp-..., env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "channel_id", "label": "Channel ID", "type": "text", "required": True,
         "target": "source.url_or_reference", "help": "Channel polled via channels.history (app/sources/slack_user.py)."},
    ],
    "twitter_bot": [
        {"name": "bearer_token_env_var", "label": "App bearer token (env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "stream_rules_note", "label": "Filtered-stream rules", "type": "text", "required": False,
         "target": None,
         "help": "tweepy filtered-stream rules (app/sources/twitter.py TwitterSource.rules) are configured directly "
                 "in this adapter's own startup wiring today -- not a column this wizard's registration endpoints "
                 "persist."},
    ],
    "twitter_user": [
        {"name": "access_token_env_var", "label": "User-context OAuth2 access token (env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "target_user_id", "label": "Target X/Twitter numeric user ID", "type": "text", "required": True,
         "target": "source.source_native_id",
         "help": "The numeric user id being followed (NOT the @handle -- a one-time handle->id lookup is a real "
                 "setup step outside this adapter, see app/sources/twitter_user.py)."},
        {"name": "poll_interval_seconds", "label": "Poll interval (seconds)", "type": "number", "required": False,
         "default": 30, "target": "source.freshness_policy.poll_interval_seconds",
         "help": "app.sources.twitter_user.DEFAULT_POLL_INTERVAL_SECONDS."},
    ],
    "email_imap": [
        {"name": "imap_host", "label": "IMAP host:port", "type": "text", "required": True,
         "target": "source.url_or_reference", "help": "e.g. imap.gmail.com:993 (app/sources/email_source.py EmailSource)."},
        {"name": "username", "label": "Mailbox username / address", "type": "text", "required": True,
         "target": "connection.account_identity", "help": "IMAP login identity for this mailbox."},
        {"name": "password_env_var", "label": "IMAP password / app password (env var name)", "type": "credential_reference",
         "required": True, "target": "connection.credential_reference", "help": _CREDENTIAL_REF_HELP},
        {"name": "sender_allowlist_note", "label": "Sender allowlist / subject patterns", "type": "text", "required": False,
         "target": None,
         "help": "sender_allowlist and subject_patterns (required, real fields on EmailSource) are configured via "
                 "app/email_collectors.py's own registry today, not a column this wizard's endpoints persist."},
    ],
    "website": [
        {"name": "feed_or_article_list_url", "label": "Feed / article-list URL", "type": "text", "required": True,
         "target": "source.url_or_reference", "help": "app/sources/website.py WebsiteSource.feed_url/article_list_url."},
        {"name": "poll_interval_seconds", "label": "Poll interval (seconds)", "type": "number", "required": False,
         "default": 300, "target": "source.freshness_policy.poll_interval_seconds", "help": "WebsiteSource default is 300."},
    ],
    "rss": [
        {"name": "feed_url", "label": "RSS/Atom feed URL", "type": "text", "required": True,
         "target": "source.url_or_reference", "help": "The FEED mode of app/sources/website.py WebsiteSource."},
        {"name": "poll_interval_seconds", "label": "Poll interval (seconds)", "type": "number", "required": False,
         "default": 300, "target": "source.freshness_policy.poll_interval_seconds", "help": "WebsiteSource default is 300."},
    ],
    "webhook": [
        {"name": "source_name", "label": "Source name (URL path segment)", "type": "text", "required": True,
         "target": "source.url_or_reference",
         "help": "Inbound URL will be POST /webhook/{source_name}. Auth is a single, deployment-wide "
                 "X-Webhook-Secret header checked against the WEBHOOK_SHARED_SECRET env var (app/main.py) -- "
                 "NOT a secret generated per connection; this wizard never fabricates one."},
    ],
    "generic_http": [
        {"name": "source_name", "label": "Source name (URL path segment)", "type": "text", "required": True,
         "target": "source.url_or_reference",
         "help": "Same adapter/route as 'webhook' -- POST /webhook/{source_name}, WEBHOOK_SHARED_SECRET-gated."},
    ],
    "sms_twilio": [
        {"name": "note", "label": "No per-connection fields", "type": "text", "required": False, "target": None,
         "help": "SMS arrives at a single global POST /sms/twilio route, X-Twilio-Signature verified against "
                 "TWILIO_AUTH_TOKEN (app/sources/sms_twilio.py) -- there is no per-connection credential or "
                 "address to collect; only a display name is meaningful here."},
    ],
    "whatsapp_business": [
        {"name": "note", "label": "No per-connection fields", "type": "text", "required": False, "target": None,
         "help": "Messages arrive at a single global POST /whatsapp/webhook route, HMAC-SHA256 verified "
                 "(app/sources/whatsapp.py) -- there is no per-connection credential or address to collect; only "
                 "a display name is meaningful here."},
    ],
    "android_notification": [
        {"name": "device_id", "label": "Paired device ID", "type": "text", "required": True,
         "target": "connection.account_identity",
         "help": "Must already be paired via POST /notification-bridge/devices (Track 12/20) -- this wizard does "
                 "not pair a new device."},
        {"name": "app_package", "label": "Android app package", "type": "text", "required": True,
         "target": "source.source_native_id",
         "help": "e.g. com.whop.whop -- must already be in that device's authorized app_packages list."},
    ],
    "android_active_retrieval": [
        {"name": "device_id", "label": "Paired device ID", "type": "text", "required": True,
         "target": "connection.account_identity", "help": "Same paired device as android_notification."},
        {"name": "app_package", "label": "Android app package", "type": "text", "required": True,
         "target": "source.source_native_id",
         "help": "Also gated by app/phone_escalation.py's own per-app CapabilityState ladder and deny-list -- "
                 "registering this source does not itself grant active-retrieval capability."},
    ],
}


def get_connection_setup_fields(connection_type: str) -> Optional[dict[str, Any]]:
    """Returns `{"connection_type", "status", "fields": [...]}` for an
    `implemented` type (real, adapter-sourced field list -- see
    `_SETUP_FIELDS`'s own module-level docstring), or `{"connection_type",
    "status": "not_implemented", "fields": None, "notes": ...}` for one of
    the catalog's `not_implemented` entries -- NEVER a guessed field list
    for an adapter that doesn't exist (hard rule 11). Returns `None` when
    `connection_type` isn't in the catalog at all (the route turns that
    into a 404, same as every other `/connections/catalog/...` route)."""
    entry = get_connection_catalog_entry(connection_type)
    if entry is None:
        return None
    if entry["status"] != "implemented":
        return {
            "connection_type": connection_type,
            "display_name": entry["display_name"],
            "status": "not_implemented",
            "fields": None,
            "notes": entry["notes"],
        }
    fields = _SETUP_FIELDS.get(connection_type)
    if fields is None:
        # Implemented in the catalog but this table hasn't been extended
        # for it yet -- honest empty-list gap, never a guess.
        return {
            "connection_type": connection_type,
            "display_name": entry["display_name"],
            "status": "implemented",
            "fields": [],
            "notes": "This connection type is implemented, but its onboarding-wizard field list has not been "
                     "catalogued yet -- use POST /connections / POST /sources directly.",
        }
    return {
        "connection_type": connection_type,
        "display_name": entry["display_name"],
        "status": "implemented",
        "fields": fields,
        "notes": entry["notes"],
    }
