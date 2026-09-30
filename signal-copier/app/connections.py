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
from typing import Any

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
