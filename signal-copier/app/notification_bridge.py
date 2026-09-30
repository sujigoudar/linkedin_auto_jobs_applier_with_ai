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
`ContentCompleteness.TRUNCATED`/`POINTER_ONLY` must never be silently
routed as if it were the full alert. See `app/main.py`'s
`_process_notification_bridge_event` for exactly how each completeness
value changes what happens to the event (never a live order for
anything but `COMPLETE`).

Track 12 extends this from a device-level rolling-window health signal
into an explicit PER-EVENT classification (`ContentCompleteness`, five
states: `COMPLETE`/`PARTIAL`/`POINTER_ONLY`/`TRUNCATED`/`UNKNOWN`),
stored per-event in `notification_bridge_events.content_completeness`
(same column Track 10 already used -- see that table's own CREATE TABLE
comment in app/db.py). This is DISTINCT from `DeviceReportedCompleteness`
below (the three-value vocabulary the Android app itself reports on the
wire, in `NotificationBridgeEventPayload.content_completeness`, based
purely on which `Notification` fields it could extract) -- the device's
own report is one INPUT to `classify_notification_completeness`, which
also looks at the actual extracted text and how far this codebase's own
text parser (`app/sources/text_parser.py`) got with it, since a
provider (Whop chief among them -- it truncates trade content to a bare
"New trade posted" pointer in the OS notification shade even though
Android itself sees nothing elided) can report `complete` while what it
actually gave the OS is not usable content at all. `UNKNOWN` is the
honest fallback when neither signal lets the classifier tell -- it is
NEVER silently defaulted to `COMPLETE`. Only `COMPLETE` is eligible for
live signal routing; every other state is recorded and flagged
`needs_escalation` for Track 13's phone-retrieval escalation layer to
later resolve -- see `SignalStore.list_notification_bridge_events_
needing_escalation`'s own docstring for that read/write interface.

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


class DeviceReportedCompleteness(str, enum.Enum):
    """The wire vocabulary `NotificationBridgeEventPayload.content_
    completeness` carries -- set by the Android app itself based on
    which `Notification`/`StatusBarNotification` fields were populated
    (see `mobile/notification-bridge/README.md`'s own extraction table),
    never guessed server-side. This is the device's own, narrower report
    ("did Android hand me an elided string") -- see
    `classify_notification_completeness` for how this is combined with
    server-side content inspection into the richer, per-event
    `ContentCompleteness` this codebase actually stores/gates on."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    TITLE_ONLY = "title_only"


class ContentCompleteness(str, enum.Enum):
    """Track 12: the explicit, five-state PER-EVENT completeness
    classification this codebase stores and gates live routing on (see
    this module's own docstring). Computed server-side by
    `classify_notification_completeness`, never taken verbatim from the
    device's own `DeviceReportedCompleteness` report -- a provider can
    (and Whop specifically does) report nothing-elided while still
    having put nothing but a bare pointer phrase in the notification."""

    #: A real, actionable trade instruction was both fully captured AND
    #: (when applicable) unambiguously parsed by app/sources/
    #: text_parser.py, OR the text was fully captured and genuinely isn't
    #: a trade instruction at all (recognized commentary/negation) -- see
    #: `classify_notification_completeness`.
    COMPLETE = "complete"
    #: Real trade-instruction shape was recognized (this grammar saw
    #: something that looks like an entry/side) but a required field was
    #: missing or ambiguous -- not nothing, not enough.
    PARTIAL = "partial"
    #: A bare "something happened, open the app" pointer with no
    #: extractable trade content at all -- Whop's own common shape (see
    #: this module's docstring) as well as Android's own TITLE_ONLY case.
    POINTER_ONLY = "pointer_only"
    #: The device itself reports this text was cut off mid-content.
    TRUNCATED = "truncated"
    #: Real, non-empty text was captured, the device reports nothing was
    #: elided, and it isn't a recognized bare-pointer phrase -- but this
    #: codebase's own parser couldn't tell whether it's a trade
    #: instruction in a format it doesn't understand or unrelated
    #: content. The honest "the classifier can't tell" fallback -- NEVER
    #: silently treated as COMPLETE.
    UNKNOWN = "unknown"


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
    #: app_package -> {"provider_name": ..., "analyst": ..., "rules":
    #: [...]} -- how an accepted notification's `Signal.source`/
    #: `Signal.analyst` are resolved. A package with no entry here falls
    #: back to using the bare package name as `Signal.source` (an
    #: honest, undecorated default, never a guessed provider name).
    #:
    #: Track 12 (Whop): a single Android app package can carry alerts
    #: from MANY distinct trading-signal providers at once -- Whop
    #: (`com.whop.whop`) is exactly this shape, since it's a marketplace
    #: app, not a per-provider one, and many different signal sellers'
    #: notifications all arrive through that one package. The optional
    #: `"rules"` list disambiguates: an ordered list of `{"title_
    #: pattern": ..., "provider_name": ..., "analyst": ...}` objects,
    #: each matched by case-insensitive substring against the
    #: notification's own `title` (first match wins, same "explicit
    #: order settles ambiguity" convention as this module's other
    #: ordered lists). A package with `"rules"` but no notification title
    #: matching any of them -- or a package with no `"rules"` at all --
    #: falls back to that package's own top-level `"provider_name"`/
    #: `"analyst"`, or (still no entry at all) the bare package name.
    #: See `resolve_provider_mapping`.
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
        for pkg, entry in provider_mapping.items():
            if pkg not in app_packages:
                raise NotificationBridgeError(
                    f"provider_mapping references app_package {pkg!r}, which is not in app_packages {app_packages!r}"
                )
            rules = entry.get("rules") if isinstance(entry, dict) else None
            if rules is None:
                continue
            if not isinstance(rules, list):
                raise NotificationBridgeError(f"provider_mapping[{pkg!r}]['rules'] must be a list, got {rules!r}")
            for index, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    raise NotificationBridgeError(f"provider_mapping[{pkg!r}]['rules'][{index}] must be an object")
                title_pattern = rule.get("title_pattern")
                if not title_pattern or not str(title_pattern).strip():
                    raise NotificationBridgeError(
                        f"provider_mapping[{pkg!r}]['rules'][{index}] is missing a non-empty 'title_pattern'"
                    )
                if not rule.get("provider_name") or not str(rule.get("provider_name")).strip():
                    raise NotificationBridgeError(
                        f"provider_mapping[{pkg!r}]['rules'][{index}] is missing a non-empty 'provider_name'"
                    )


def resolve_provider_mapping(
    mapping: dict[str, dict[str, Any]], *, app_package: str, title: str | None
) -> tuple[str, Optional[str]]:
    """Resolves ONE accepted notification's `(provider_name, analyst)` --
    the single place this decision is made, used by
    `app/main.py`'s `_process_notification_bridge_event` instead of that
    route reading `provider_mapping` directly. See `NotificationBridge
    Device.provider_mapping`'s own docstring for the Whop-motivated
    `"rules"` (title-pattern) shape this resolves, in order:

    1. `mapping[app_package]["rules"]`, first entry whose `title_pattern`
       is a case-insensitive substring of `title` (`title` is `None` for
       a notification with no title at all, which matches no rule).
    2. `mapping[app_package]["provider_name"]`/`["analyst"]` (the
       existing, pre-Track-12 per-package default).
    3. The bare `app_package` as `provider_name`, `None` as `analyst` --
       the honest, undecorated fallback this module has always used for
       a package with no mapping entry at all."""
    entry = mapping.get(app_package, {})
    rules = entry.get("rules") or []
    if title:
        lowered_title = title.lower()
        for rule in rules:
            pattern = str(rule.get("title_pattern", "")).lower()
            if pattern and pattern in lowered_title:
                return str(rule["provider_name"]), rule.get("analyst")
    provider_name = entry.get("provider_name") or app_package
    analyst = entry.get("analyst")
    return str(provider_name), analyst


def validate_content_completeness(value: str) -> DeviceReportedCompleteness:
    """Validates the WIRE value `NotificationBridgeEventPayload.content_
    completeness` carries -- the device's own three-value self-report
    (see `DeviceReportedCompleteness`'s own docstring), not this
    codebase's richer, five-state stored `ContentCompleteness`
    classification (see `classify_notification_completeness` for that)."""
    try:
        return DeviceReportedCompleteness(value)
    except ValueError as exc:
        raise NotificationBridgeError(
            f"content_completeness must be one of {[c.value for c in DeviceReportedCompleteness]}, got {value!r}"
        ) from exc


#: Track 12: known bare-pointer notification phrasings -- providers
#: (Whop's own OS notification shade chief among them, per this module's
#: docstring) whose in-app content is real but whose OS notification is
#: deliberately just "something happened, open the app" with no trade
#: content at all. Matched as a case-insensitive SUBSTRING of the
#: captured text -- deliberately narrow/literal (never a fuzzy/ML guess)
#: so this can never mis-flag a real, terse trade instruction ("BUY
#: BTCUSDT" has no numbers... actually it does not match any of these
#: phrases at all, by design) as pointer-only.
_POINTER_ONLY_PHRASES = (
    "new trade posted",
    "new trade alert",
    "posted a new trade",
    "new alert posted",
    "new post in",
    "tap to view",
    "open the app to view",
    "check the app for details",
    "view in app",
)


def _looks_like_pointer_only(stripped_text: str) -> bool:
    lowered = stripped_text.lower()
    if any(phrase in lowered for phrase in _POINTER_ONLY_PHRASES):
        return True
    # A short, digit-free line of text is, in practice, never a real
    # trade instruction (every grammar this codebase parses requires at
    # least one numeric field -- a symbol, a price, a level) -- it's
    # either a bare pointer or empty chrome. Kept conservative (both
    # conditions, not either alone) so a genuinely short-but-numeric
    # alert (e.g. "BUY BTC 65000") is never misclassified.
    return len(stripped_text) < 40 and not any(ch.isdigit() for ch in stripped_text)


def classify_notification_completeness(
    *,
    device_reported: DeviceReportedCompleteness,
    best_text: str,
    disposition_outcome: Optional[str],
) -> ContentCompleteness:
    """Track 12: the one place a captured notification's PER-EVENT
    `ContentCompleteness` is decided -- see this module's own docstring
    for why this combines the device's own report with server-side
    content inspection rather than trusting either alone, and why
    `UNKNOWN` (never a silent `COMPLETE`) is the fallback when neither
    signal is conclusive.

    `disposition_outcome` is `app.sources.text_parser.DispositionOutcome
    .value` (a plain `str` here to avoid this module importing that
    parser module at all when the caller has no text to classify) for
    whatever `best_text` resolved to when non-empty, or `None` when
    `best_text` itself is empty (nothing was ever passed to the
    parser)."""
    stripped = (best_text or "").strip()

    if device_reported is DeviceReportedCompleteness.TITLE_ONLY:
        # A title-only notification IS, definitionally, a bare pointer --
        # no body text was ever extracted for Android to even judge as
        # elided or not.
        return ContentCompleteness.POINTER_ONLY
    if not stripped:
        return ContentCompleteness.POINTER_ONLY
    if device_reported is DeviceReportedCompleteness.TRUNCATED:
        # Android itself reports this was cut off -- trust that over any
        # content heuristic, checked BEFORE the parser outcome below on
        # purpose: a truncated fragment can still accidentally parse
        # into a (wrong) resolved instruction -- e.g. "BUY BTCUSDT @ 5"
        # with a trailing "0000" cut off -- and a device-confirmed
        # truncation must never be silently upgraded to COMPLETE just
        # because the fragment happened to parse.
        return ContentCompleteness.TRUNCATED

    if disposition_outcome in ("parsed", "ignored"):
        # PARSED: a real, resolved trade instruction -- checked BEFORE
        # the pointer-phrase/short-text heuristic below on purpose: a
        # short, digit-free instruction this grammar genuinely resolved
        # (e.g. "BUY BTCUSDT", a bare market order with no numeric
        # price) must never be misclassified as a bare pointer just
        # because it happens to be short. IGNORED: this grammar
        # positively recognized negated/conditional/past-tense
        # commentary -- fully captured, correctly not a trade
        # instruction. Both are complete captures of whatever this
        # notification actually was.
        return ContentCompleteness.COMPLETE
    if disposition_outcome in ("ambiguous", "missing_data"):
        # Real trade-instruction SHAPE was recognized but a field was
        # missing/unresolvable -- genuinely partial, not a guess.
        return ContentCompleteness.PARTIAL
    if _looks_like_pointer_only(stripped):
        return ContentCompleteness.POINTER_ONLY
    # disposition_outcome == "no_match" (or None, if this caller never
    # had a parser outcome to pass): real text was captured, the device
    # reports nothing elided, it isn't a recognized bare-pointer phrase,
    # and this grammar found no trade-instruction shape in it at all --
    # could genuinely be unrelated content, or a real alert in a format
    # this parser doesn't understand. Neither signal lets this function
    # tell which -- the honest UNKNOWN fallback, never silently COMPLETE.
    return ContentCompleteness.UNKNOWN


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
