"""Track 5: the persistent Telegram collector registry.

Point 4 of the Track 5 brief: not just one hardcoded `bot_token`/`chat_id`
pair (the old `app.config.TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` globals
that `app/main.py` still reads for backward compatibility) -- a real,
persisted registry (`telegram_collectors` table, `app/db.py`'s
`SignalStore`) of every collector this deployment runs, whether it's the
existing bot-based `TelegramSource` or the new user-account
`TelegramUserSource` (`app/sources/telegram_user.py`).

This module holds the registry's own vocabulary (enums), its row shape
(`TelegramCollector`), and validation that's independent of how the row
happens to be persisted -- mirroring `app/qualification.py`'s split from
`app/db.py` (ladder/vocabulary here, SQL there).

Security note (SECURITY, hard requirement): this table stores ONLY
references to where a credential lives -- `credential_env_var` names an
environment variable, never a token/session-string value itself. See
`docs/security/TELEGRAM_USER_LOGIN.md` and `docs/security/SECRETS.md`
for the actual credential material and its handling.

Private-ingestion / commercial-redistribution isolation (point 10): a
collector's `allowed_uses` defaults to `["private_trading"]` ONLY --
registering a collector here never grants, and cannot by itself imply,
eligibility for `signal-portfolio-commercial`'s customer-facing
publication pipeline (that service's own `RightsGrant`/rights-registry
model, in a completely separate deployment/database, is the only thing
that can grant that). Nothing in this module imports, calls, or is called
by anything in that other service -- see
`tests/test_telegram_collectors_registry.py::test_registering_a_collector_never_touches_commercial_rights`
for the same isolation asserted in code, and this module's own
`AllowedUse` enum for the two real values.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class ConnectionMode(str, enum.Enum):
    """How this collector actually receives messages -- see the Track 5
    brief's own preference order (point 2): a provider-supported webhook/
    API is out of scope for this codebase (a business negotiation, not
    code); `BOT` (a BotFather bot invited into the chat -- the existing
    `app.sources.telegram.TelegramSource`) is preferred when the provider
    allows it; `USER_ACCOUNT` (an authenticated Telethon user session --
    `app.sources.telegram_user.TelegramUserSource`) is the fallback for a
    channel the user can only read via their own personal account."""

    BOT = "bot"
    USER_ACCOUNT = "user_account"


class AllowedUse(str, enum.Enum):
    """What this collector's ingested signals may be used for -- point 10.
    `PRIVATE_TRADING` (this deployment's own engine/routing) is the
    default and, for a freshly registered collector, the ONLY value ever
    set automatically. `COMMERCIAL_REDISTRIBUTION` (eligibility for
    signal-portfolio-commercial's customer-facing publication pipeline)
    is never set by this module -- it exists here only as a documented,
    named value an operator could theoretically record for their own
    bookkeeping; nothing in this codebase reads it to actually grant
    publication eligibility (that lives entirely in the other service's
    own `RightsGrant` model, a separate deployment/database this module
    never touches)."""

    PRIVATE_TRADING = "private_trading"
    COMMERCIAL_REDISTRIBUTION = "commercial_redistribution"


#: A freshly registered collector's default `allowed_uses` -- see
#: `AllowedUse`'s own docstring. Never includes `COMMERCIAL_REDISTRIBUTION`.
DEFAULT_ALLOWED_USES: list[str] = [AllowedUse.PRIVATE_TRADING.value]


class CollectorHealth(str, enum.Enum):
    """Point 8: the explicit, honest incident/health states every
    collector must be surfaceable as -- never silently green with zero
    signals. `UNQUALIFIED` (not `HEALTHY_QUALIFIED`) is the default for a
    freshly registered row: qualification is real evidence
    (`record_qualification_evidence`) that authorized real-message
    receipt has actually been confirmed, never assumed at registration
    time."""

    #: No row has ever recorded a real credential/session as reachable --
    #: e.g. `credential_env_var` names an env var that isn't set in this
    #: process's environment.
    MISSING_CREDENTIALS = "missing_credentials"
    #: The credential resolves, but this collector could not read the
    #: configured chat (not a member, access revoked, chat not found).
    NO_CHANNEL_ACCESS = "no_channel_access"
    #: Credentials and channel access are both fine, but no message has
    #: been observed in an unusually long window -- see
    #: `app/sources/telegram_user.py` for the exact staleness threshold
    #: this is computed against.
    NO_MESSAGES_OBSERVED = "no_messages_observed"
    #: A real message arrived that this adapter cannot represent (no text
    #: and no caption -- a media-only post) -- see point 5's "never
    #: silently drop" requirement.
    UNSUPPORTED_FORMAT_ENCOUNTERED = "unsupported_format_encountered"
    #: This chat's own protected-content (`noforwards`) flag is set,
    #: restricting some downstream forwarding/redistribution action (never
    #: raw ingestion -- see `app/sources/telegram_user.py`'s module
    #: docstring for the ingestion-vs-forwarding distinction this state
    #: exists to make visible, not to bypass).
    PROTECTED_CONTENT_RESTRICTED = "protected_content_restricted"
    #: A real message arrived and text_parser.py raised on it in a way
    #: distinct from an ordinary "not a signal" classification (a real
    #: parser bug/crash, not routine non-signal chatter).
    PARSER_FAILURE = "parser_failure"
    #: The default, honest starting state -- registered, but no evidence
    #: yet that it has ever received a real, authorized message.
    UNQUALIFIED = "unqualified"
    #: Real evidence has been recorded (see `record_qualification_evidence`)
    #: that this collector is receiving real, authorized messages -- the
    #: only state a dashboard may render as green.
    HEALTHY_QUALIFIED = "healthy_qualified"


class TelegramCollectorError(ValueError):
    """Raised for a registration/update that fails this registry's own
    validation -- never a silent best-effort acceptance."""


@dataclass
class TelegramCollector:
    """One row of the `telegram_collectors` registry -- see this module's
    own docstring and `app/db.py`'s `telegram_collectors` table comment
    for the persisted shape this mirrors field for field."""

    id: str
    connection_mode: ConnectionMode
    #: Non-secret identity reference: a bot's own `@username`, or the
    #: user-account's own phone number/username -- NEVER a token/session
    #: string (see `credential_env_var` below for where the actual
    #: credential lives).
    identity_ref: str
    #: The name of the environment variable this collector's REAL
    #: credential (bot token, or Telethon session file path) is read
    #: from at process startup -- never the credential's value itself.
    credential_env_var: str
    chat_id: str
    provider_name: str
    topic_id: Optional[str] = None
    allowed_uses: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_USES))
    #: This chat's own protected-content flag (Telethon's `Chat`/`Channel.
    #: noforwards`) -- `None` until a real connection has actually read
    #: it (never guessed).
    noforwards: Optional[bool] = None
    last_qualified_at: Optional[datetime] = None
    qualification_evidence: dict[str, Any] = field(default_factory=dict)
    #: Point 7: the last message id this collector has admitted to LIVE
    #: routing -- `None` for a collector that has never processed a live
    #: message yet. Never advanced by a historical import (see
    #: `app/sources/telegram_user.py`'s `import_history`).
    checkpoint_message_id: Optional[int] = None
    checkpoint_updated_at: Optional[datetime] = None
    health_state: CollectorHealth = CollectorHealth.UNQUALIFIED
    health_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.connection_mode, str):
            self.connection_mode = ConnectionMode(self.connection_mode)
        if isinstance(self.health_state, str):
            self.health_state = CollectorHealth(self.health_state)


def validate_registration(
    *,
    collector_id: str,
    connection_mode: str,
    identity_ref: str,
    credential_env_var: str,
    chat_id: str,
    provider_name: str,
    allowed_uses: list[str] | None,
) -> tuple[ConnectionMode, list[str]]:
    """Shared validation for a new/updated registry row -- raises
    `TelegramCollectorError` (never silently accepts) for anything that
    would leave the registry in a state point 4/10 don't allow:

    - an unrecognized `connection_mode`.
    - a `credential_env_var` that LOOKS like it's carrying an actual
      secret value rather than naming one (a bare env-var name is
      conventionally `UPPER_SNAKE_CASE`; a real token/session string
      practically never parses as one -- this is a best-effort guard, not
      a cryptographic guarantee, so it's documented as such, never relied
      on as the only protection: the real protection is that this
      registry's persistence layer (`app/db.py`) never accepts a field
      named like a credential value in the first place).
    - any `allowed_uses` entry that isn't a real `AllowedUse` value.
    - any required identity field left blank.
    """
    if not collector_id or not identity_ref or not credential_env_var or not chat_id or not provider_name:
        raise TelegramCollectorError(
            "collector_id, identity_ref, credential_env_var, chat_id and provider_name are all required"
        )
    try:
        mode = ConnectionMode(connection_mode)
    except ValueError as exc:
        raise TelegramCollectorError(
            f"connection_mode must be one of {[m.value for m in ConnectionMode]}, got {connection_mode!r}"
        ) from exc
    if not credential_env_var.isupper() or " " in credential_env_var or credential_env_var.count("=") > 0:
        raise TelegramCollectorError(
            f"credential_env_var must be a bare environment-variable NAME (e.g. 'TELEGRAM_USER_{collector_id.upper()}_SESSION_PATH'), "
            f"never a credential value itself -- got {credential_env_var!r}"
        )
    uses = list(allowed_uses) if allowed_uses is not None else list(DEFAULT_ALLOWED_USES)
    for use in uses:
        try:
            AllowedUse(use)
        except ValueError as exc:
            raise TelegramCollectorError(
                f"allowed_uses entries must be one of {[u.value for u in AllowedUse]}, got {use!r}"
            ) from exc
    return mode, uses


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
