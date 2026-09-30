"""Track 14: the Provider / Source / Connection data model.

The user's own words (verbatim, from the Track 14 brief) on what this
module exists to represent: "the UI/data model needs to move from
'configure a Telegram/Discord provider' to a more general model:
Provider -> Sources -> Connections -> Capture methods -> Parsing ->
Signal policy -> Account routing -> Validation/certification -> Health.
A signal provider should be addable even when the same provider sends
the same trade through Whop, Telegram, email, SMS and a website. Those
should be five sources feeding one provider identity, not five
independent providers."

This is a genuinely NEW layer, additive on top of everything that came
before it (see each earlier track's own module for what it still does,
unchanged):

- `app/unified_collectors.py` (Track 8) already unified four registries
  (telegram/pull/email/website) onto one `collectors` table, but never
  introduced a concept of "one provider, many sources" -- each
  `collectors` row IS a provider, 1:1, identified by `(kind, provider)`.
  Two Telegram channels for the same real-world signal seller were (and
  still are, at that layer) two unrelated rows with no shared identity.
- `app/notification_bridge.py` (Track 10/12) already has a many-sources-
  behind-one-connection shape (`provider_mapping` on a device resolves
  many providers from one Android package), but it's local to that one
  registry, not general.
- `app/phone_escalation.py` (Track 13) configures active retrieval per
  `app_package`, independent of this module's `sources`/`connections`
  rows -- see this module's own docstring section below on how the two
  relate.

Three tables, three responsibilities (`app/db.py`'s `SCHEMA` has the
authoritative column list/comments for each; this module holds the
Python-side vocabulary/validation, matching the established split in
this codebase between a table's schema comment in `app/db.py` and its
enums/validation in a dedicated module -- e.g. `app/telegram_collectors.py`
vs. `telegram_collectors`):

- `providers` -- ONE row per real-world signal-provider identity (a
  person or desk), independent of how many transports it reaches this
  deployment through.
- `sources` -- MANY rows per provider, one per transport/channel that
  provider's alerts arrive through (a Telegram channel, a Whop
  notification-bridge mapping, an email sender rule, ...). Carries
  `role` (PRIMARY/SECONDARY/FALLBACK/RECONCILIATION/DISCOVERY_ONLY) so
  "this provider has 5 sources" also says which one this deployment
  actually trades off by default and which are backups/cross-checks.
- `connections` -- the reusable, credential-bearing transport itself
  (a Telegram bot, a Gmail mailbox, an Android device's notification
  bridge, a REST API key). ONE connection can serve MANY sources across
  MANY providers -- the user's own example: "One Telegram connection
  could serve several providers. One Gmail account could receive alerts
  from 15 providers." `sources.connection_id` is the many-to-one link.

Credential handling: EXACTLY the same hard rule as every earlier
registry in this codebase (`app/telegram_collectors.py`,
`app/email_collectors.py`, `app/notification_bridge.py`'s hashed pairing
token, Track 13's `phone_escalation_configs`) -- a `connections` row
NEVER stores a raw credential. `credential_reference` is only the NAME
of an environment variable (or, where a registry already uses a hash
instead -- e.g. notification-bridge pairing tokens -- a reference to
that same hash, never re-derived or duplicated here) the real secret is
read from at process startup. `providers`/`sources` never carry a
credential field at all -- that concept lives ONLY on `connections`,
by construction (see the column lists below).

`execution_eligibility` (on both `providers` and `sources`) is an
ADDITIONAL, higher-level, provider/source-scoped gate an operator can
use to hold a provider back from live routing (e.g. a brand-new
provider still in `shadow`) -- it is layered ON TOP of, and must never
bypass or replace, `app/engine.py`'s own `_check_route_qualified` gate,
which is a PER-ROUTE (broker+account+asset_class+product_type) human
sign-off recorded via `POST /qualifications` (`app/qualification.py`).
A provider could have `execution_eligibility="live"` and still have
every one of its signals refused at the route-qualification gate (no
route has been signed off yet); the reverse must also hold -- nothing
in this module weakens `_check_route_qualified` or gives it a way to
skip that check. THIS TRACK DOES NOT WIRE `execution_eligibility` INTO
`app/engine.py`'s actual routing decision -- that a provider's
`execution_eligibility` should also gate `_handle_signal` (alongside
the existing route-qualification and account-enabled gates) is real,
valuable follow-up work for the next phase, deliberately left undone
here per this track's own "data model first, everything else builds on
top of this cleanly" build order. Recording the field now, honestly
defaulted to the most conservative value (`disabled`) and never
fabricated, is what this track is scoped to do.

`correlation_window_seconds` on `providers` is a documented, NOT YET
WIRED, per-provider override of `app/signal_correlation.py`'s global
`SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS`/
`SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT` defaults -- follow-up work, not
done in this track (see that module for where it would be threaded in).

`risk_policy_ref` is a free-text reference string, not a real foreign
key -- this codebase has no standalone "risk policy" table to point at
(see `app/risk.py`, pure position-sizing math with no persisted policy
concept, and `app/capital_allocator.py`, owner-wide/per-account
EXPOSURE caps, also not a named, referenceable "policy" row). Recording
a `risk_policy_ref` string now (e.g. a `DestinationAccount.account_id`
or an operator-chosen label) gives the next phase a place to point once
a real risk-policy concept exists, without this track inventing one
just to fill the field.

`max_entry_age_seconds`/`stale_exit_policy` generalize the existing
staleness convention `app/notification_bridge.py`/`app/main.py`'s
`_process_notification_bridge_event` already use for one specific
transport (`config.NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS`,
comparing a signal's `posted_at` against this server's own receipt
time) into a provider-level setting that could, in a later phase, apply
uniformly across every transport. Not wired into `app/engine.py`'s
general signal-admission path in this track -- same "record honestly,
wire later" scoping as `execution_eligibility` above.

How this relates to `app/phone_escalation.py` (Track 13): that module's
`phone_escalation_configs` table is keyed by `app_package` alone and
configures WHETHER/HOW active phone-control retrieval may run for
notifications from that package -- a capability_state
(DISABLED/SHADOW/ENABLED) that is orthogonal to, and not superseded by,
anything in this module. A `sources` row whose `capture_method` is
`android_active_retrieval` (or whose backing `connections` row is
`connection_type="android_active_retrieval"`) is DESCRIBING that this
provider has such a source; whether that source's active-retrieval
capability is actually armed is still decided entirely by
`phone_escalation_configs`, looked up by `app_package`, exactly as
before. This track does not add a `phone_escalation_configs` foreign
key to `sources`/`connections` -- that table's own `app_package` key
and this module's `sources.source_native_id` (which, for a
notification-bridge-backed source, IS an `app_package`, possibly
alongside a `device_id`) are two independent, string-matched
references to the same real-world thing, not a formal FK, since
`phone_escalation_configs` predates this module and changing its key
shape is out of this track's scope.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ProviderStatus(str, enum.Enum):
    """Lifecycle state of a provider identity, honest and never silently
    defaulted to anything but the most conservative starting point
    (`ONBOARDING`)."""

    ONBOARDING = "onboarding"
    SHADOW = "shadow"
    CERTIFIED = "certified"
    LIVE = "live"
    PAUSED = "paused"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class ExecutionEligibility(str, enum.Enum):
    """The additional, provider/source-scoped gate described in this
    module's own docstring -- composes with, never replaces,
    `app/engine.py`'s `_check_route_qualified`."""

    DISABLED = "disabled"
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"


class ProviderClassification(str, enum.Enum):
    SIGNAL_PROVIDER = "signal_provider"
    RESEARCH_SOURCE = "research_source"
    STRATEGY = "strategy"
    NEWSLETTER = "newsletter"
    SCANNER = "scanner"


class AccountOwnership(str, enum.Enum):
    PERSONAL = "personal"
    PROVIDER_ACCOUNT = "provider-account"


class CertificationState(str, enum.Enum):
    UNCERTIFIED = "uncertified"
    DRAFT = "draft"
    TESTED = "tested"
    SHADOW = "shadow"
    CERTIFIED = "certified"


class SourceRole(str, enum.Enum):
    """Which role this one source plays for its provider, when that
    provider has several. See this module's own docstring for the
    motivating "same trade via Whop, Telegram, email, SMS and a
    website" example -- exactly one of those five sources is typically
    PRIMARY (what this deployment actually trades off), the rest are
    SECONDARY/FALLBACK/RECONCILIATION/DISCOVERY_ONLY."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    FALLBACK = "FALLBACK"
    RECONCILIATION = "RECONCILIATION"
    DISCOVERY_ONLY = "DISCOVERY_ONLY"


class SourceHealth(str, enum.Enum):
    """Same "never silently green" convention as `app/telegram_
    collectors.py`'s `CollectorHealth`/`app/notification_bridge.py`'s
    `DeviceHealth`. A fresh source starts `UNQUALIFIED`, never
    `HEALTHY`."""

    UNQUALIFIED = "unqualified"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    ERROR = "error"
    DISABLED = "disabled"


class ProviderCatalogError(ValueError):
    """Raised by validation in this module -- never a silent best-effort
    accept, same convention as every earlier registry's own error type
    (`TelegramCollectorError`, `NotificationBridgeError`, ...)."""


def _require_nonempty(value: str | None, field_name: str) -> str:
    if not value or not str(value).strip():
        raise ProviderCatalogError(f"{field_name} is required")
    return str(value).strip()


def coerce_enum_or_none(value: Any, enum_cls: type[enum.Enum], field_name: str) -> str | None:
    """Validates `value` against `enum_cls` (raising `ProviderCatalog
    Error` for anything that isn't `None` or a recognized member) and
    returns its plain string value. Public -- `app/connections.py`
    reuses this for its own closed enums (`ConnectionState`/
    `AuthorizationState`) rather than re-implementing the same check."""
    if value is None:
        return None
    try:
        return enum_cls(value).value
    except ValueError as exc:
        raise ProviderCatalogError(
            f"{field_name} must be one of {[e.value for e in enum_cls]}, got {value!r}"
        ) from exc


# Backwards-compatible private alias used by this module's own functions below.
_validate_enum_or_none = coerce_enum_or_none


def validate_provider_registration(
    *,
    provider_id: str,
    display_name: str,
    status: str | None = None,
    classification: str | None = None,
    account_ownership: str | None = None,
    execution_eligibility: str | None = None,
    certification_state: str | None = None,
) -> None:
    """Validates the fields this module itself owns vocabulary for
    BEFORE anything is persisted -- mirrors every pre-existing
    registry's own `validate_registration` split from its `SignalStore`
    persistence methods (see e.g. `app/telegram_collectors.py`)."""
    _require_nonempty(provider_id, "provider_id")
    _require_nonempty(display_name, "display_name")
    _validate_enum_or_none(status, ProviderStatus, "status")
    _validate_enum_or_none(classification, ProviderClassification, "classification")
    _validate_enum_or_none(account_ownership, AccountOwnership, "account_ownership")
    _validate_enum_or_none(execution_eligibility, ExecutionEligibility, "execution_eligibility")
    _validate_enum_or_none(certification_state, CertificationState, "certification_state")


def validate_source_registration(
    *,
    source_id: str,
    provider_id: str,
    platform: str,
    role: str | None = None,
    execution_eligibility: str | None = None,
    health_state: str | None = None,
) -> None:
    _require_nonempty(source_id, "source_id")
    _require_nonempty(provider_id, "provider_id")
    _require_nonempty(platform, "platform")
    _validate_enum_or_none(role, SourceRole, "role")
    _validate_enum_or_none(execution_eligibility, ExecutionEligibility, "execution_eligibility")
    _validate_enum_or_none(health_state, SourceHealth, "health_state")


