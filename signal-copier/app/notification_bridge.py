"""Track 10: the persistent notification-bridge device registry.

A fallback capture path for a trading-alert provider that exposes signals
ONLY via an Android push notification -- no webhook, no bot, no API. The
Android companion app (`mobile/notification-bridge/`, a real
`NotificationListenerService`) forwards captured notification text to
this deployment's `POST /ingest/notification-bridge/{device_id}` route;
this module holds the registry's own vocabulary (enums), its row shape
(`NotificationBridgeDevice`), and validation/hashing that's independent
of how the row happens to be persisted -- mirroring
`app/telegram_collectors.py`'s split from `app/db.py` (vocabulary/
validation here, SQL there) exactly, since this is the same kind of
device/credential registry for a different transport.

Security note (SECURITY, hard requirement): this table stores ONLY an
argon2id HASH of each device's pairing token, via the same `pwdlib`
library and recommended algorithm choice `app/auth.py` already uses for
`OWNER_PASSWORD_HASH` -- the raw token is generated once at registration
time, returned to the owner exactly once in the registration response,
and never persisted anywhere in plain text. See
`generate_pairing_token`/`hash_pairing_token`/`verify_pairing_token`
below.

Content completeness (point 5 of the Track 10 brief): a notification
saying "New trade posted" is DISCOVERY, not an executable instruction --
`ContentCompleteness.TRUNCATED`/`TITLE_ONLY` must never be silently
routed as if it were the full alert. See `app/main.py`'s
`_process_notification_bridge_event` for exactly how each completeness
value changes what happens to the event (never a live order for
anything but `COMPLETE`).

Health states (point 8, same "never silently green" convention as
`app/telegram_collectors.py`'s `CollectorHealth`):

  - `NEVER_PAIRED`: registered, but this device has never even sent a
    heartbeat -- the honest default at registration time.
  - `NO_HEARTBEAT_RECENTLY`: a READ-TIME computed state (never persisted
    as such -- see `app/db.py`'s `SignalStore.get_notification_bridge_device`),
    since "is the last heartbeat stale RIGHT NOW" changes every second
    with no write of its own. Overrides whatever health_state was last
    written whenever `last_heartbeat_at` is missing or older than
    `NOTIFICATION_BRIDGE_HEARTBEAT_STALE_SECONDS` -- a device must never
    render as healthy just because nobody has looked recently.
  - `NO_NOTIFICATIONS_OBSERVED`: heartbeats are arriving (the app and its
    pairing are working) but zero notification events have ever been
    recorded for this device.
  - `CONTENT_COMPLETENESS_DEGRADED`: a real, measurable pattern (see
    `SignalStore.record_notification_bridge_completeness`) of truncated/
    title_only content over this device's recent authorized events --
    something about capture (Android's own text-extraction limits, a
    provider that changed its notification shape) is systematically
    broken, not a one-off.
  - `UNAUTHORIZED_APP_PACKAGE`: a notification arrived tagged with an
    `app_package` this device was never registered to forward for --
    rejected outright (see `app/main.py`), never silently accepted.
  - `HEALTHY_QUALIFIED`: real evidence (an authorized, COMPLETE,
    successfully-parsed notification that was actually routed live) has
    been recorded -- the only state a dashboard may render as green,
    same convention as `CollectorHealth.HEALTHY_QUALIFIED`.
"""
from __future__ import annotations

import enum
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError


class ContentCompleteness(str, enum.Enum):
    """What the Android app actually had available to forward -- set by
    the app itself based on which `Notification`/`StatusBarNotification`
    fields were populated (see `mobile/notification-bridge/README.md`'s
    own extraction table), never guessed server-side."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    TITLE_ONLY = "title_only"


class DeviceHealth(str, enum.Enum):
    NEVER_PAIRED = "never_paired"
    NO_HEARTBEAT_RECENTLY = "no_heartbeat_recently"
    NO_NOTIFICATIONS_OBSERVED = "no_notifications_observed"
    CONTENT_COMPLETENESS_DEGRADED = "content_completeness_degraded"
    UNAUTHORIZED_APP_PACKAGE = "unauthorized_app_package"
    HEALTHY_QUALIFIED = "healthy_qualified"


class NotificationBridgeError(ValueError):
    """Raised for a registration/ingest event that fails this registry's
    own validation -- never a silent best-effort acceptance."""


#: argon2id via pwdlib -- same trust level and algorithm choice as
#: app/auth.py's OWNER_PASSWORD_HASH (C05). A SEPARATE `PasswordHash`
#: instance from app/auth.py's own module-level one (not shared,
#: not imported from there) -- same "a stolen credential in one trust
#: domain must never double as a credential in another" reasoning
#: docs/security/SECRETS.md documents for CATALOG_FIT_SIM_SIGNING_SECRET
#: vs RELAY_SIGNING_SECRET; a device pairing token and the owner's own
#: login password are different trust domains even though the
#: underlying hashing algorithm is identical.
_pairing_token_hasher = PasswordHash.recommended()


def generate_pairing_token() -> str:
    """A fresh, high-entropy pairing token -- returned to the OWNER
    exactly once, in the device-registration HTTP response, for them to
    type into the Android app's settings screen themselves. Never
    obtained by, or round-tripped through, an AI-visible session (same
    handling discipline as docs/security/TELEGRAM_USER_LOGIN.md's
    Telegram session credential). Same generation call as
    app/auth.py's `create_session` session/CSRF tokens."""
    return secrets.token_urlsafe(32)


def hash_pairing_token(token: str) -> str:
    return _pairing_token_hasher.hash(token)


def verify_pairing_token(token: str, hashed: str) -> bool:
    """Constant-time-safe verify (pwdlib's own argon2id verify, not a
    plain `==`/`in` compare). Returns `False` (never raises) for a hash
    pwdlib doesn't recognize -- fail closed, same as
    app/auth.py's `verify_password`."""
    if not token or not hashed:
        return False
    try:
        return _pairing_token_hasher.verify(token, hashed)
    except UnknownHashError:
        return False


def content_fingerprint(title: str | None, text: str | None, expanded_text: str | None) -> str:
    """A stable content fingerprint for one notification's actual
    content, used as the REVISION identity when a device redelivers the
    same `notification_key` (Android's `StatusBarNotification.key` is
    stable per notification INSTANCE, but an UPDATE to that same
    notification reuses the key with new content -- see this module's
    own docstring and `app/main.py`'s `_process_notification_bridge_event`).
    A redelivery of the exact same key with the exact same fingerprint is
    a true duplicate/retry; a different fingerprint for the same key is a
    real edit, handled as a `SourceEvent` `EDIT`, never silently dropped
    or treated as a brand-new signal."""
    payload = "\x1f".join([title or "", text or "", expanded_text or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass
class NotificationBridgeDevice:
    """One row of the `notification_bridge_devices` registry -- see this
    module's own docstring and `app/db.py`'s table comment for the
    persisted shape this mirrors field for field."""

    device_id: str
    #: argon2id hash of the pairing token -- NEVER the raw token (see
    #: `hash_pairing_token`).
    pairing_token_hash: str
    #: Android package names (e.g. "com.example.tradingapp") this device
    #: is authorized to forward notifications for. A notification tagged
    #: with any other package is rejected -- see `DeviceHealth.
    #: UNAUTHORIZED_APP_PACKAGE`.
    app_packages: list[str] = field(default_factory=list)
    #: app_package -> {"provider_name": ..., "analyst": ...} -- how an
    #: accepted notification's `Signal.source`/`Signal.analyst` are
    #: resolved. A package with no entry here falls back to using the
    #: bare package name as `Signal.source` (an honest, undecorated
    #: default, never a guessed provider name).
    provider_mapping: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_heartbeat_at: Optional[datetime] = None
    #: Rolling window of up to `_COMPLETENESS_WINDOW_SIZE` most-recent
    #: authorized events' completeness (True == COMPLETE), oldest first
    #: -- see `SignalStore.record_notification_bridge_completeness`.
    recent_completeness: list[bool] = field(default_factory=list)
    health_state: DeviceHealth = DeviceHealth.NEVER_PAIRED
    health_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.health_state, str):
            self.health_state = DeviceHealth(self.health_state)


#: Rolling-window sizing for `CONTENT_COMPLETENESS_DEGRADED` detection --
#: see `SignalStore.record_notification_bridge_completeness`'s docstring
#: for exactly how these are used. A single truncated notification is
#: normal provider noise, never itself a degraded verdict; a real,
#: sustained pattern is.
COMPLETENESS_WINDOW_SIZE = 20
COMPLETENESS_MIN_SAMPLE = 5
COMPLETENESS_DEGRADED_RATIO = 0.5

#: How long a device may go without a heartbeat before it's surfaced as
#: `NO_HEARTBEAT_RECENTLY` (point 8: "visibly stale, not silently
#: green"). The Android app's own WorkManager periodic heartbeat interval
#: (see mobile/notification-bridge/README.md) should be comfortably
#: shorter than this so ordinary network jitter doesn't flap the state.
DEFAULT_HEARTBEAT_STALE_SECONDS = 30 * 60  # 30 minutes

#: How stale a `posted_at` may be, relative to this server's own receipt
#: time, before an otherwise-live-eligible event is instead treated as
#: historical-only backlog (point 4: never let a queued/delayed
#: notification become a fresh order). Configurable via
#: `config.NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS` -- this is only
#: the module-level default a caller may fall back to.
DEFAULT_STALE_THRESHOLD_SECONDS = 5 * 60  # 5 minutes


def validate_device_registration(
    *,
    device_id: str,
    app_packages: list[str],
    provider_mapping: dict[str, dict] | None,
) -> None:
    """Shared validation for a new/updated registry row -- raises
    `NotificationBridgeError` (never silently accepts) for anything that
    would leave the registry in a dishonest or unsafe state."""
    if not device_id or not device_id.strip():
        raise NotificationBridgeError("device_id is required")
    if not app_packages:
        raise NotificationBridgeError(
            "at least one authorized app_package is required -- a device with none can never forward "
            "anything, and would otherwise reject every notification as unauthorized_app_package"
        )
    for pkg in app_packages:
        if not pkg or not pkg.strip() or " " in pkg:
            raise NotificationBridgeError(f"app_package must be a bare Android package name (no spaces), got {pkg!r}")
    if provider_mapping:
        for pkg in provider_mapping:
            if pkg not in app_packages:
                raise NotificationBridgeError(
                    f"provider_mapping references app_package {pkg!r}, which is not in app_packages {app_packages!r}"
                )


def validate_content_completeness(value: str) -> ContentCompleteness:
    try:
        return ContentCompleteness(value)
    except ValueError as exc:
        raise NotificationBridgeError(
            f"content_completeness must be one of {[c.value for c in ContentCompleteness]}, got {value!r}"
        ) from exc


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
