"""Track 6: the persistent Slack/Twitter USER-CONTEXT collector registry.

Mirrors `app/telegram_collectors.py` (Track 5's own registry) deliberately
closely -- same vocabulary shape (`credential_env_var` names an env var,
never holds a value; `allowed_uses` defaults to private-trading only;
explicit `CollectorHealth` states; a per-collector live-admission
checkpoint) -- but as ONE shared table (`pull_collectors`) for BOTH new
providers instead of two more near-identical tables, since neither
provider needs Telegram's own provider-specific fields (`topic_id`,
`noforwards`). A `provider` column ("slack" or "twitter") distinguishes
rows; `collector_id` is still the single, globally-unique primary key
(same convention as Telegram's own `collector_id`, e.g. `slack-buyalerts`/
`twitter-somehandle`, so ids never collide across providers by
construction even though nothing here enforces a naming convention).

Security note (unchanged from Track 5): this table stores ONLY a
REFERENCE to where a credential lives -- `credential_env_var` names an
environment variable, never a token value itself. See
`docs/security/SLACK_USER_TOKEN.md` / `docs/security/TWITTER_USER_CONTEXT.md`
and `docs/security/SECRETS.md`.

`AllowedUse`/`DEFAULT_ALLOWED_USES` are reused directly from
`app.telegram_collectors` rather than redefined here -- that vocabulary
(private-trading-only by default, commercial-redistribution never
implied) is genuinely provider-independent; only `app.telegram_collectors`
happens to be where it was first introduced (Track 5). `CollectorHealth`
is NOT reused: Telegram's version carries a Telegram-specific
`PROTECTED_CONTENT_RESTRICTED` state (`noforwards`) that has no verified
Slack/Twitter equivalent (see `docs/security/SLACK_USER_TOKEN.md` and
`docs/security/TWITTER_USER_CONTEXT.md` for what was actually checked),
so this module defines its own, smaller, closed set instead of silently
inheriting a state this registry can never honestly set.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.telegram_collectors import DEFAULT_ALLOWED_USES, AllowedUse  # noqa: F401 (re-exported)


class Provider(str, enum.Enum):
    SLACK = "slack"
    TWITTER = "twitter"


class CollectorHealth(str, enum.Enum):
    """The explicit, honest incident/health states a Slack/Twitter
    user-context collector must be surfaceable as -- never silently
    green with zero real signals. See this module's own docstring for
    why this is a distinct (smaller) set from
    `app.telegram_collectors.CollectorHealth`."""

    #: The credential env var this row names isn't set (or doesn't
    #: resolve to a usable token) in this process's environment.
    MISSING_CREDENTIALS = "missing_credentials"
    #: The credential resolves, but this collector could not read the
    #: configured channel/account (not a member, access revoked, not
    #: found, token lacks the needed scope).
    NO_CHANNEL_ACCESS = "no_channel_access"
    #: Credentials and access are both fine, but no message/tweet has
    #: been observed in an unusually long window.
    NO_MESSAGES_OBSERVED = "no_messages_observed"
    #: A real event arrived that this adapter cannot represent as text
    #: (e.g. a Slack file-only message with no text, a media-only tweet
    #: with no caption text) -- a distinct, visible outcome, never a
    #: silent drop indistinguishable from "not a signal."
    UNSUPPORTED_FORMAT_ENCOUNTERED = "unsupported_format_encountered"
    #: A real event arrived and text_parser.py (or the adapter's own
    #: field extraction) raised in a way distinct from ordinary "not a
    #: signal" classification.
    PARSER_FAILURE = "parser_failure"
    #: The default, honest starting state -- registered, but no evidence
    #: yet that it has ever received a real, authorized message.
    UNQUALIFIED = "unqualified"
    #: Real evidence has been recorded that this collector is receiving
    #: real, authorized messages -- the only state a dashboard may
    #: render as green.
    HEALTHY_QUALIFIED = "healthy_qualified"


class PullCollectorError(ValueError):
    """Raised for a registration/update that fails this registry's own
    validation -- never a silent best-effort acceptance."""


@dataclass
class PullCollector:
    """One row of the `pull_collectors` registry."""

    id: str
    provider: Provider
    #: How this collector authenticates -- a free-form, provider-owned
    #: label (e.g. `"oauth_user_token"` for Slack, `"oauth2_user_context"`
    #: for Twitter) rather than a shared closed enum, since the two
    #: providers' real auth mechanisms are genuinely different OAuth
    #: flows, not interchangeable values.
    auth_mode: str
    #: Non-secret identity reference: the Slack user's own display name/
    #: user id, or the Twitter/X handle being read as -- NEVER a token
    #: value (see `credential_env_var` below).
    identity_ref: str
    #: The name of the environment variable this collector's REAL
    #: credential (a Slack `xoxp-` user token, or a Twitter OAuth 2.0
    #: user-context access token) is read from at process startup --
    #: never the credential's value itself.
    credential_env_var: str
    #: The provider-native id this collector reads: a Slack channel id,
    #: or the Twitter/X numeric user id being followed.
    target_id: str
    provider_name: str
    #: Human-readable label for the target (a Slack channel name, an
    #: `@handle`) -- purely cosmetic, never used for lookups.
    target_label: Optional[str] = None
    allowed_uses: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_USES))
    last_qualified_at: Optional[datetime] = None
    qualification_evidence: dict[str, Any] = field(default_factory=dict)
    #: Point 7 (Track 5's own pattern, reused here): the last message/
    #: tweet id this collector has admitted to LIVE routing -- `None`
    #: for a collector that has never processed one. Stored as TEXT
    #: (unlike Telegram's integer checkpoint) since Slack's own message
    #: id (`ts`, e.g. `"1699999999.000100"`) is not an integer -- see
    #: `app/sources/slack_user.py`/`app/sources/twitter_user.py` for how
    #: each adapter compares its own checkpoint values; this registry
    #: persists it as an opaque string and does not itself interpret
    #: ordering.
    checkpoint: Optional[str] = None
    checkpoint_updated_at: Optional[datetime] = None
    health_state: CollectorHealth = CollectorHealth.UNQUALIFIED
    health_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.provider, str):
            self.provider = Provider(self.provider)
        if isinstance(self.health_state, str):
            self.health_state = CollectorHealth(self.health_state)


def validate_registration(
    *,
    collector_id: str,
    provider: str,
    auth_mode: str,
    identity_ref: str,
    credential_env_var: str,
    target_id: str,
    provider_name: str,
    allowed_uses: list[str] | None,
) -> tuple[Provider, list[str]]:
    """Shared validation for a new/updated registry row -- raises
    `PullCollectorError` (never silently accepts). Mirrors
    `app.telegram_collectors.validate_registration`'s own checks."""
    if not all([collector_id, auth_mode, identity_ref, credential_env_var, target_id, provider_name]):
        raise PullCollectorError(
            "collector_id, auth_mode, identity_ref, credential_env_var, target_id and provider_name are all required"
        )
    try:
        provider_enum = Provider(provider)
    except ValueError as exc:
        raise PullCollectorError(
            f"provider must be one of {[p.value for p in Provider]}, got {provider!r}"
        ) from exc
    if not credential_env_var.isupper() or " " in credential_env_var or credential_env_var.count("=") > 0:
        raise PullCollectorError(
            f"credential_env_var must be a bare environment-variable NAME (e.g. "
            f"'SLACK_USER_{collector_id.upper()}_TOKEN'), never a credential value itself -- "
            f"got {credential_env_var!r}"
        )
    uses = list(allowed_uses) if allowed_uses is not None else list(DEFAULT_ALLOWED_USES)
    for use in uses:
        try:
            AllowedUse(use)
        except ValueError as exc:
            raise PullCollectorError(
                f"allowed_uses entries must be one of {[u.value for u in AllowedUse]}, got {use!r}"
            ) from exc
    return provider_enum, uses


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
