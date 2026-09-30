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
