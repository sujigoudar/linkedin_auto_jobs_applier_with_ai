"""Track 14: the `connections` table's vocabulary/validation.

See `app/provider_catalog.py`'s module docstring for the full
Provider/Source/Connection data model this is one third of -- this
module holds only what's specific to `connections`: the reusable,
credential-bearing transport a `sources` row points at via
`sources.connection_id`. Split into its own module (rather than folded
into `app/provider_catalog.py`) because a connection is conceptually
independent of any one provider/source -- the user's own example: "One
Telegram connection could serve several providers. One Gmail account
could receive alerts from 15 providers."

`connection_type` is deliberately an OPEN string, not a closed enum
(unlike `ConnectionState`/`AuthorizationState` below, which ARE closed):
new transports must be addable without a schema/enum change. This
track's own callers use values like `telegram_bot`, `telegram_user`,
`discord_bot`, `slack`, `whatsapp_business`, `gmail`, `imap`, `sms_api`,
`android_sms`, `x`, `rest_api`, `webhook`, `rss`, `website`,
`android_notification`, `android_active_retrieval`, `generic_http` --
see this track's migration backfill for the concrete values it assigns
existing data.

Credential handling: same hard rule as every other registry in this
codebase -- `credential_reference` is ONLY the name of an environment
variable (or, for a registry that already stores a hash instead of an
env-var reference, e.g. `app/notification_bridge.py`'s pairing-token
hash, a reference to that existing hash) the real secret is read from.
NEVER a raw credential value. This module's `validate_connection_
registration` does not (and cannot, in general) detect a raw secret
accidentally passed as `credential_reference` -- callers are expected
to pass an env-var NAME, matching every earlier registry's own
convention (see e.g. `app/email_collectors.py`'s `credential_env_var`
docs) -- but see `looks_like_raw_credential` below for the same
heuristic guard `app/telegram_collectors.py`'s own registration
validation already applies, reused here rather than re-invented.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Optional

from app.notification_bridge import DEFAULT_HEARTBEAT_STALE_SECONDS
from app.provider_catalog import ProviderCatalogError, coerce_enum_or_none


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ConnectionState(str, enum.Enum):
    UNCONFIGURED = "unconfigured"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class AuthorizationState(str, enum.Enum):
    UNAUTHORIZED = "unauthorized"
    AUTHORIZED = "authorized"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ConnectionError_(ProviderCatalogError):
    """Alias kept distinct from the builtin `ConnectionError` -- raised
    for anything this module's own validation rejects."""


def looks_like_raw_credential(value: str | None) -> bool:
    """The same cheap heuristic `app/telegram_collectors.py`'s own
    `validate_registration` already uses to catch an operator pasting a
    real secret into a field meant to hold only an env-var NAME: a
    plausible env-var name is short, upper-snake-case-ish, and has no
    spaces or typical secret punctuation. This is a best-effort guard,
    never a substitute for `docs/security/SECRETS.md`'s actual policy."""
    if not value:
        return False
    if len(value) > 64:
        return True
    if any(c in value for c in (" ", ":", "/")) and not value.startswith("http"):
        return True
    return False


def validate_connection_registration(
    *,
    connection_id: str,
    connection_type: str,
    credential_reference: str | None = None,
    connection_state: str | None = None,
    authorization_state: str | None = None,
) -> None:
    if not connection_id or not connection_id.strip():
        raise ConnectionError_("connection_id is required")
    if not connection_type or not connection_type.strip():
        raise ConnectionError_("connection_type is required")
    if looks_like_raw_credential(credential_reference):
        raise ConnectionError_(
            "credential_reference must be an environment-variable NAME (or existing hash reference), "
            "never a raw credential value -- see this module's own docstring"
        )
    coerce_enum_or_none(connection_state, ConnectionState, "connection_state")
    coerce_enum_or_none(authorization_state, AuthorizationState, "authorization_state")


#: The full, open shape `connections.capabilities` (a JSON object) may
#: describe -- see the Track 14 brief's item 6 (capability discovery).
#: This module does not enforce that only these keys appear (a
#: connection type may need a capability this list hasn't anticipated
#: yet, and the discovery/introspection mechanism itself is explicitly
#: out of scope for this track) -- it only documents the keys this
#: track's own callers/tests use.
CONNECTION_CAPABILITY_KEYS = (
    "realtime_events",
    "history",
    "history_depth",
    "message_edits",
    "deletions",
    "attachments",
    "images",
    "embeds",
    "threads",
    "stable_ids",
    "original_timestamps",
    "delivery_ack",
    "replay",
    "backfill",
    "health_check",
    "push",
    "poll",
    "active_retrieval",
)


def default_capabilities() -> dict[str, Any]:
    """An honest all-unknown/false default -- never a guessed
    capability set. A caller that knows a real connection's actual
    capabilities passes its own dict; this is only the safe starting
    point for a freshly registered connection."""
    return {key: False for key in CONNECTION_CAPABILITY_KEYS}


#: Track 19: per-`connection_type` capability shapes for every transport
#: this codebase has a REAL, existing adapter/registration path for --
#: see `default_capabilities_for_connection_type`'s own docstring for how
#: this is used and, critically, how it is NOT used (never a guess for an
#: unrecognized type). Every `True` below was verified against the real
#: adapter it describes (cited inline) -- never assumed from the
#: connection type's name. A flag this codebase's own adapter code does
#: not actually implement is left `False`/absent, per this track's own
#: "honesty over completeness" instruction, even where a real integration
#: could plausibly support it (e.g. Telegram's Bot API does deliver
#: photo/document messages, but `app/sources/telegram.py`/
#: `app/telegram_collectors.py`'s own `CollectorHealth.
#: UNSUPPORTED_FORMAT_ENCOUNTERED` shows a media-only post is explicitly
#: NOT handled today -- so `attachments`/`images` stay `False` for both
#: Telegram connection types, not guessed `True` from what the Bot API
#: itself could carry).
#:
#: Each entry documents the one adapter module it was verified against.
_KNOWN_CONNECTION_TYPE_CAPABILITIES: dict[str, dict[str, Any]] = {
    # app/sources/telegram.py + app/telegram_collectors.py (ConnectionMode.BOT):
    # push-delivered (getUpdates/webhook), sequential integer message_id
    # (stable_ids), a real edit is recognized via update.edited_message
    # (message_edits) -- but the Bot API has no delete-notification
    # update at all (module docstring, verbatim: "Telegram's Bot API has
    # no delete-notification update at all, so DELETE is not (and cannot
    # honestly be) implemented here"), so deletions=False. No historical
    # import path exists for the bot mode (only telegram_user.py has
    # import_history) -- history/backfill left False rather than guessed
    # from the Bot API's own ~24h unconsumed-update retention, since this
    # adapter never reads it.
    "telegram_bot": {
        "realtime_events": True,
        "message_edits": True,
        "deletions": False,
        "stable_ids": True,
        "original_timestamps": True,
        "push": True,
        "health_check": True,
    },
    # app/sources/telegram_user.py (ConnectionMode.USER_ACCOUNT, Telethon):
    # same push/edit/stable-id/timestamp facts as telegram_bot above, PLUS
    # a real, explicit `import_history` (history/backfill) and a real
    # (if partially limited -- see handle_deleted_event's own docstring on
    # private one-to-one chats) MessageDeleted handler, so deletions=True
    # for the channel/group case this adapter is actually configured for.
    "telegram_user": {
        "realtime_events": True,
        "history": True,
        "backfill": True,
        "message_edits": True,
        "deletions": True,
        "stable_ids": True,
        "original_timestamps": True,
        "push": True,
        "health_check": True,
    },
    # app/sources/discord.py: a real, wired adapter (discord.py's gateway
    # websocket -- push), but `handle_message` only handles brand-new
    # messages -- no edit/delete handler, no persisted checkpoint/
    # registry (unlike the Track 5/6/7/9 unified-collector types), so
    # history/backfill/message_edits/deletions/stable_ids all stay
    # unset/False rather than assumed from what Discord's own API could
    # in principle deliver.
    "discord_bot": {
        "realtime_events": True,
        "push": True,
    },
    # app/sources/slack.py (Socket Mode, push -- bot token + app token):
    # `handle_event` explicitly SKIPS any event with a `subtype` ("edits,
    # joins, bot messages, etc." -- verbatim comment), so message_edits/
    # deletions are False, not merely unset -- this is a documented,
    # deliberate skip, not an unverified gap. No checkpoint/history path.
    "slack_bot": {
        "realtime_events": True,
        "message_edits": False,
        "deletions": False,
        "push": True,
    },
    # app/sources/slack_user.py: a real, checkpointed PULL collector
    # (`pull_collectors` registry, Track 6) with its own `import_history`
    # (read-only, never live-routed) and a monotonic checkpoint keyed off
    # the Slack message `ts` -- poll-based (REST, not Socket Mode), stable
    # per-message id.
    "slack_user": {
        "realtime_events": True,
        "history": True,
        "backfill": True,
        "stable_ids": True,
        "original_timestamps": True,
        "poll": True,
        "health_check": True,
    },
    # app/sources/twitter.py (tweepy filtered stream, push): no
    # checkpoint/history registry, no edit/delete handling wired (X's own
    # `edit_history_tweet_ids` is read by twitter_user.py, not this
    # adapter -- see twitter_user.py's own docstring), so those stay
    # False/unset here.
    "twitter_bot": {
        "realtime_events": True,
        "push": True,
    },
    # app/sources/twitter_user.py: a real, checkpointed PULL collector
    # (`pull_collectors` registry) with `import_history` and explicit
    # `edit_history_tweet_ids`-based edit-revision detection (this
    # module's own docstring: "X does support Tweet edits ... Edit
    # observability") -- deletions are not handled (X's v2 API gives no
    # delete-notification stream this adapter reads).
    "twitter_user": {
        "realtime_events": True,
        "history": True,
        "backfill": True,
        "message_edits": True,
        "deletions": False,
        "stable_ids": True,
        "original_timestamps": True,
        "poll": True,
        "health_check": True,
    },
    # app/sources/email_source.py (ConnectionMode.IMAP) + app/
    # email_collectors.py: IMAP UIDs are a real stable id (module
    # docstring: "IMAP UIDs are ..." used as the live checkpoint), a
    # mailbox retains its own full history (real `import_history`/
    # backfill exists), and this adapter polls on a configured interval
    # (poll=True, not push). `_extract_body` detects content-types
    # present (including attachment parts) but does not extract/store
    # attachment content as a distinct Signal field, so `attachments`
    # stays False rather than guessed True from mere content-type
    # detection. No edit/delete concept exists for email.
    "email_imap": {
        "history": True,
        "backfill": True,
        "stable_ids": True,
        "original_timestamps": True,
        "poll": True,
        "health_check": True,
    },
    # app/sources/website.py + app/website_collectors.py: FEED mode
    # (feedparser over RSS/Atom) gives a real per-entry id/link and
    # published timestamp; ARTICLE_LIST mode diffs a seen-URL checkpoint
    # with no entry-id concept at all. Capabilities below describe the
    # FEED case (the catalog's separate "rss" entry maps to this same
    # adapter/type); only a single most-recent-URL/seen-set checkpoint
    # exists, not a deep historical archive, so history/backfill are left
    # False rather than assumed.
    "website": {
        "poll": True,
        "stable_ids": True,
        "original_timestamps": True,
    },
    "rss": {
        "poll": True,
        "stable_ids": True,
        "original_timestamps": True,
    },
    # app/sources/webhook.py: the one fully push-based, provider-agnostic
    # JSON ingestion path (module docstring: "the one fully working
    # ingestion path"). A caller's JSON body may optionally include a
    # `message_id` (used for dedup/replay detection per-call), but this
    # is opt-in per sender, not a guaranteed property of every webhook
    # connection, so stable_ids is left unset here rather than assumed.
    "webhook": {
        "realtime_events": True,
        "push": True,
    },
    # app/sources/sms_twilio.py + the /sms/twilio route (X-Twilio-Signature
    # verified): push-delivered, real-time, no stable per-message id this
    # adapter reads, no history/backfill (Twilio's webhook is fire-and-
    # forget, this codebase never queries Twilio's own message history).
    "sms_twilio": {
        "realtime_events": True,
        "push": True,
    },
    # app/sources/whatsapp.py (Meta WhatsApp Business Cloud API) + the
    # /whatsapp/webhook route (HMAC-SHA256 signature verified): push-
    # delivered via Meta's webhook. WhatsApp's own `wamid.XXX` message id
    # is real and stable (module reference: "WhatsApp's own stable
    # per-message id") but this adapter does not currently read/carry it
    # into Signal/SourceEvent identity, so stable_ids is left False
    # (not yet wired) rather than assumed from the protocol.
    "whatsapp_business": {
        "realtime_events": True,
        "push": True,
    },
    # app/notification_bridge.py: push-delivered Android notifications,
    # a real periodic heartbeat (health_check), and a real revision
    # concept (`notification_bridge_events.revision_seq`, advanced on a
    # notification UPDATE keyed by Android's own stable
    # `StatusBarNotification.key` -- `notification_key`), so
    # message_edits/stable_ids are real here. No history/backfill concept
    # at all -- this bridge only ever sees notifications live, on-device,
    # from whenever the app was first paired.
    "android_notification": {
        "realtime_events": True,
        "message_edits": True,
        "stable_ids": True,
        "push": True,
        "health_check": True,
    },
    # app/phone_escalation.py: an ACTIVE-RETRIEVAL capability (on-demand
    # phone control to read an app's own UI state), not a passive event
    # stream -- no realtime_events/push/poll/history concept applies the
    # same way. `capability_state` (DISABLED/SHADOW/ENABLED) gates
    # whether this may run at all; it carries no heartbeat of its own.
    "android_active_retrieval": {
        "active_retrieval": True,
    },
}


def default_capabilities_for_connection_type(connection_type: str) -> dict[str, Any]:
    """The honest, per-type capability default `SignalStore.
    register_connection` uses when a caller doesn't pass an explicit
    `capabilities` override (Track 19). For a `connection_type` this
    codebase has verified, real adapter behavior for (see
    `_KNOWN_CONNECTION_TYPE_CAPABILITIES` above -- every entry cites the
    exact adapter module/docstring it was checked against), returns that
    type's real capability shape merged over `default_capabilities()`'s
    all-`False` baseline (so every key in `CONNECTION_CAPABILITY_KEYS` is
    always present, never a partial dict). For any OTHER `connection_type`
    (an unrecognized string, a catalog entry this codebase has no real
    adapter for yet, or simply a typo) returns `default_capabilities()`
    UNCHANGED -- the honest all-`False`/unknown baseline, never a guess.
    This function never raises on an unrecognized type (an open-vocabulary
    field, per this module's own docstring) -- it only ever narrows to
    "capabilities recorded" vs. "nothing claimed"."""
    overrides = _KNOWN_CONNECTION_TYPE_CAPABILITIES.get(connection_type)
    base = default_capabilities()
    if overrides:
        base.update(overrides)
    return base


class ConnectionHealthState(str, enum.Enum):
    """Honest, never-silently-green connection health -- same convention
    as `app/notification_bridge.py`'s `DeviceHealth`/`app/
    telegram_collectors.py`'s `CollectorHealth`. `INSUFFICIENT_DATA` is a
    first-class member, not a fallback swallowed into `UNKNOWN` --
    CLAUDE.md's own hard rule (#11) requires an honest "don't know"
    answer over a fabricated one whenever real data isn't available."""

    #: connection_state/authorization_state both look healthy AND there is
    #: real recent activity (a heartbeat or a successful event) within the
    #: staleness threshold, with no error more recent than that activity.
    HEALTHY = "healthy"
    #: Recognized as connected/authorized, but showing real signs of
    #: trouble: a heartbeat/successful-event is stale, or a `last_error_at`
    #: is more recent than the last known-good activity.
    DEGRADED = "degraded"
    #: `connection_state` is explicitly `ERROR`/`DISCONNECTED`, or
    #: `authorization_state` is `EXPIRED`/`REVOKED` -- this connection is
    #: known, concretely, not to be working right now.
    OFFLINE = "offline"
    #: A freshly registered connection (`connection_state=UNCONFIGURED`)
    #: that has never recorded a heartbeat, a successful event, or an
    #: error -- distinct from DEGRADED/OFFLINE: there is no evidence of
    #: trouble, but also none of health. Never silently reported HEALTHY.
    NEVER_CONNECTED = "never_connected"
    #: Recognized as connected/authorized, but with no heartbeat/
    #: successful-event timestamp recorded AT ALL (not merely stale) to
    #: judge freshness from -- honest "insufficient data", never defaulted
    #: to HEALTHY.
    INSUFFICIENT_DATA = "insufficient_data"
    #: An `connection_state`/`authorization_state` value this function
    #: doesn't recognize (should not happen given this module's own
    #: closed enums, but never silently treated as healthy if it does).
    UNKNOWN = "unknown"


#: Reuses Track 10's own heartbeat-staleness threshold rather than
#: inventing a new arbitrary number -- `app/notification_bridge.py`'s
#: `DEFAULT_HEARTBEAT_STALE_SECONDS` (30 minutes) is this codebase's
#: existing, documented judgment call for "how long may a push-style
#: transport go quiet before that's surfaced as stale, not silently
#: green" -- the same question this module's own connection-health
#: computation is answering for the general `connections` table.
CONNECTION_HEARTBEAT_STALE_SECONDS = DEFAULT_HEARTBEAT_STALE_SECONDS


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def compute_connection_health(
    connection: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    stale_after_seconds: float = CONNECTION_HEARTBEAT_STALE_SECONDS,
) -> dict[str, Any]:
    """Computes a `connections` row's real health from `connection_state`/
    `authorization_state`/`last_heartbeat_at`/`last_successful_event_at`/
    `last_error_at`/`last_error_detail` -- NEVER from "no alerts recently"
    alone (the user's own spec, verbatim: "Don't determine health merely
    from 'no alerts recently'"). Returns a dict (not a bare enum) so the
    reasoning is always visible alongside the verdict: `{"state":
    ConnectionHealthState.value, "reasons": [...], "age_seconds": float |
    None, "stale_after_seconds": float}`.

    Precedence (most-conservative-first, matching every other health
    computation in this codebase's own "never silently green" convention):

    1. `authorization_state` in (EXPIRED, REVOKED) or `connection_state`
       in (ERROR, DISCONNECTED) -> OFFLINE (a known, concrete failure).
    2. `connection_state == UNCONFIGURED` and no heartbeat/successful-
       event/error has EVER been recorded -> NEVER_CONNECTED.
    3. No heartbeat AND no successful-event timestamp recorded at all
       (but `connection_state` claims CONNECTED/DEGRADED) ->
       INSUFFICIENT_DATA -- there is nothing real to judge freshness from.
    4. The most recent of (`last_heartbeat_at`, `last_successful_event_at`)
       is older than `stale_after_seconds`, OR `last_error_at` is more
       recent than that most-recent good activity -> DEGRADED.
    5. Otherwise -> HEALTHY.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []

    raw_connection_state = connection.get("connection_state")
    raw_authorization_state = connection.get("authorization_state")
    try:
        conn_state = ConnectionState(raw_connection_state) if raw_connection_state else None
    except ValueError:
        conn_state = None
    try:
        auth_state = AuthorizationState(raw_authorization_state) if raw_authorization_state else None
    except ValueError:
        auth_state = None

    if raw_connection_state is not None and conn_state is None:
        return {
            "state": ConnectionHealthState.UNKNOWN.value,
            "reasons": [f"unrecognized connection_state={raw_connection_state!r}"],
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }
    if raw_authorization_state is not None and auth_state is None:
        return {
            "state": ConnectionHealthState.UNKNOWN.value,
            "reasons": [f"unrecognized authorization_state={raw_authorization_state!r}"],
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }

    if conn_state in (ConnectionState.ERROR, ConnectionState.DISCONNECTED):
        reasons.append(f"connection_state={conn_state.value}")
    if auth_state in (AuthorizationState.EXPIRED, AuthorizationState.REVOKED):
        reasons.append(f"authorization_state={auth_state.value}")
    if reasons:
        detail = connection.get("last_error_detail")
        if detail:
            reasons.append(f"last_error_detail={detail!r}")
        return {
            "state": ConnectionHealthState.OFFLINE.value,
            "reasons": reasons,
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }

    heartbeat_at = _parse_ts(connection.get("last_heartbeat_at"))
    success_at = _parse_ts(connection.get("last_successful_event_at"))
    error_at = _parse_ts(connection.get("last_error_at"))

    last_good = max((t for t in (heartbeat_at, success_at) if t is not None), default=None)

    if last_good is None:
        if conn_state == ConnectionState.UNCONFIGURED and error_at is None:
            return {
                "state": ConnectionHealthState.NEVER_CONNECTED.value,
                "reasons": ["connection_state=unconfigured; no heartbeat/successful-event/error ever recorded"],
                "age_seconds": None,
                "stale_after_seconds": stale_after_seconds,
            }
        return {
            "state": ConnectionHealthState.INSUFFICIENT_DATA.value,
            "reasons": [
                "no last_heartbeat_at/last_successful_event_at recorded -- cannot judge freshness "
                "(never defaulted to healthy)"
            ],
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }

    age_seconds = max(0.0, (now - last_good).total_seconds())
    degraded_reasons: list[str] = []
    if age_seconds >= stale_after_seconds:
        degraded_reasons.append(
            f"last known-good activity is {age_seconds:.0f}s old (>= {stale_after_seconds:.0f}s threshold)"
        )
    if error_at is not None and error_at > last_good:
        detail = connection.get("last_error_detail")
        degraded_reasons.append(
            f"last_error_at={error_at.isoformat()} is more recent than last known-good activity"
            + (f" (last_error_detail={detail!r})" if detail else "")
        )

    if degraded_reasons:
        return {
            "state": ConnectionHealthState.DEGRADED.value,
            "reasons": degraded_reasons,
            "age_seconds": age_seconds,
            "stale_after_seconds": stale_after_seconds,
        }

    return {
        "state": ConnectionHealthState.HEALTHY.value,
        "reasons": ["recent heartbeat/successful-event activity, no unresolved error"],
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
    }
