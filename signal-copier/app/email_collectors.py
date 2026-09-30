"""Track 7: the persistent email collector registry.

Mirrors `app/telegram_collectors.py`'s split (registry vocabulary/row
shape/validation here, SQL persistence in `app/db.py`'s `SignalStore`) for
the new `EmailSource` adapter (`app/sources/email_source.py`) -- see that
module's own docstring for the transport this registers collectors for.

There was NO email source in this codebase before this track -- this is a
wholly new adapter and a wholly new registry table (`email_collectors`),
not an extension of an existing one.

Security note (SECURITY, hard requirement, same as Track 5): this table
stores ONLY a reference to where a credential lives -- `credential_env_var`
names an environment variable (an IMAP app password, or -- once a future
track implements it -- a Gmail OAuth refresh token), never a credential
VALUE itself. See `docs/security/EMAIL_COLLECTOR.md` and
`docs/security/SECRETS.md`.

Private-ingestion / commercial-redistribution isolation (same point-10
requirement as Track 5): a collector's `allowed_uses` defaults to
`["private_trading"]` ONLY -- registering an email collector here never
grants, and cannot by itself imply, eligibility for
`signal-portfolio-commercial`'s customer-facing publication pipeline.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class ConnectionMode(str, enum.Enum):
    """How this collector actually receives mail.

    `IMAP` (an app-specific password against any IMAP host -- Gmail,
    Outlook, a dedicated signal-forwarding mailbox) is implemented by
    `app.sources.email_source.EmailSource` and is the only mode this
    track actually wires an adapter for -- universal, works for any
    provider, no new third-party dependency (stdlib `imaplib`/`email`
    only).

    `GMAIL_API` (OAuth against Gmail's own API, with `watch()` + Pub/Sub
    push or `historyId`-cursor incremental polling) is recorded here as a
    real, named, forward-compatible value so an operator can register
    which mode a mailbox is INTENDED to use -- but constructing
    `EmailSource` with this mode raises `NotImplementedError` today. See
    `docs/security/EMAIL_COLLECTOR.md`'s "Gmail API (OAuth) -- not yet
    implemented" section for why this was left a documented follow-up
    rather than a rushed half-implementation alongside IMAP."""

    IMAP = "imap"
    GMAIL_API = "gmail_api"


class AllowedUse(str, enum.Enum):
    """Same two values, same isolation guarantee, as
    `app.telegram_collectors.AllowedUse` -- see that enum's own
    docstring. `PRIVATE_TRADING` is the only value a freshly registered
    collector ever gets automatically."""

    PRIVATE_TRADING = "private_trading"
    COMMERCIAL_REDISTRIBUTION = "commercial_redistribution"


#: A freshly registered collector's default `allowed_uses` -- see
#: `AllowedUse`'s own docstring. Never includes `COMMERCIAL_REDISTRIBUTION`.
DEFAULT_ALLOWED_USES: list[str] = [AllowedUse.PRIVATE_TRADING.value]


class CollectorHealth(str, enum.Enum):
    """The explicit, honest incident/health states every email collector
    must be surfaceable as -- never silently green with zero messages.
    Exactly the six states the Track 7 brief names (point 6); no others
    are invented. `NO_MESSAGES_OBSERVED` doubles as BOTH this registry's
    honest default at registration (no evidence yet that this mailbox has
    ever delivered a real message to this collector) AND the steady-state
    "nothing has arrived in an unusually long window" signal -- both are
    the same underlying fact (no confirmed real receipt), so this
    registry does not invent a separate seventh `unqualified` state the
    way Track 5's Telegram registry did."""

    #: `credential_env_var` names an environment variable that isn't set
    #: (or is blank) in this process's environment.
    MISSING_CREDENTIALS = "missing_credentials"
    #: The credential resolves, but IMAP login/SELECT failed -- bad
    #: password, connection refused, host unreachable, folder doesn't
    #: exist.
    NO_MAILBOX_ACCESS = "no_mailbox_access"
    #: Credentials and mailbox access are both fine, but no message has
    #: been observed yet (either ever, or in an unusually long window --
    #: see this enum's own docstring for why both share this one state).
    NO_MESSAGES_OBSERVED = "no_messages_observed"
    #: A real message arrived from an allow-listed sender with no
    #: extractable text at all (no plain-text part, no HTML part with any
    #: readable text after stripping tags -- e.g. an image-only alert) --
    #: point 2's "never silently drop" requirement.
    UNSUPPORTED_FORMAT_ENCOUNTERED = "unsupported_format_encountered"
    #: A real message arrived and `text_parser.py` (or this adapter's own
    #: MIME/header extraction) raised in a way distinct from an ordinary
    #: "not a signal" classification -- a real bug/crash, not routine
    #: non-signal mail.
    PARSER_FAILURE = "parser_failure"
    #: Real evidence has been recorded (see
    #: `record_qualification_evidence`) that this collector is receiving
    #: real, authorized messages -- the only state a dashboard may render
    #: as green.
    HEALTHY_QUALIFIED = "healthy_qualified"


class EmailCollectorError(ValueError):
    """Raised for a registration/update that fails this registry's own
    validation -- never a silent best-effort acceptance."""


@dataclass
class EmailCollector:
    """One row of the `email_collectors` registry -- see this module's
    own docstring and `app/db.py`'s `email_collectors` table comment for
    the persisted shape this mirrors field for field."""

    id: str
    connection_mode: ConnectionMode
    #: Non-secret identity reference: the mailbox address being watched
    #: (e.g. `alerts-watcher@gmail.com`) -- NEVER a password/token (see
    #: `credential_env_var` below for where the actual credential lives).
    identity_ref: str
    #: The name of the environment variable this collector's REAL
    #: credential (an IMAP app password today; a Gmail OAuth refresh
    #: token once that mode is implemented) is read from at process
    #: startup -- never the credential's value itself.
    credential_env_var: str
    imap_host: str
    #: The mailbox folder this collector watches (e.g. `INBOX`) -- see
    #: `Signal.channel_id`'s own docstring in `app/models.py`: this
    #: adapter sets `channel_id` to `f"{imap_host}:{imap_folder}"`, the
    #: mailbox/folder identity the task brief asks for.
    imap_folder: str
    #: The sender address(es) this collector admits -- REQUIRED and never
    #: empty (point 4: "filter to those explicitly rather than parsing
    #: every email in the inbox"). Case-insensitively matched against the
    #: message's own `From` header address.
    sender_allowlist: list[str]
    provider_name: str
    imap_port: int = 993
    #: Optional additional narrowing: a subject must contain at least one
    #: of these substrings (case-insensitive) to be admitted, when this
    #: list is non-empty. Empty (the default) means sender filtering
    #: alone is the admission gate.
    subject_patterns: list[str] = field(default_factory=list)
    allowed_uses: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_USES))
    #: How often `EmailSource.start()` polls this mailbox, in seconds --
    #: see `app/sources/email_source.py`'s module docstring for the
    #: latency/load tradeoff this default (60s) documents. IMAP IDLE
    #: (push-like, near-real-time) was considered and deliberately not
    #: wired in this track -- see that module's docstring for why.
    poll_interval_seconds: int = 60
    last_qualified_at: Optional[datetime] = None
    qualification_evidence: dict[str, Any] = field(default_factory=dict)
    #: Point 5 (checkpoint-based historical-import vs. live-admission
    #: separation): the last IMAP UID this collector has admitted to LIVE
    #: routing. `None` for a collector that has never processed a live
    #: message. Never advanced by a historical import (see
    #: `app/sources/email_source.py`'s `import_history`). IMAP UIDs are
    #: only guaranteed monotonic WITHIN one folder for a given
    #: `UIDVALIDITY` epoch -- see `EmailSource`'s own module docstring for
    #: the disclosed, in-process-only guard against a `UIDVALIDITY`
    #: change and its known cross-restart gap.
    checkpoint_uid: Optional[int] = None
    checkpoint_updated_at: Optional[datetime] = None
    health_state: CollectorHealth = CollectorHealth.NO_MESSAGES_OBSERVED
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
    imap_host: str,
    imap_folder: str,
    sender_allowlist: list[str] | None,
    provider_name: str,
    allowed_uses: list[str] | None,
) -> tuple[ConnectionMode, list[str]]:
    """Shared validation for a new/updated registry row -- raises
    `EmailCollectorError` (never silently accepts) for anything that
    would leave the registry in a state points 4/10 don't allow. Mirrors
    `app.telegram_collectors.validate_registration`'s own checks and
    rationale (see that function's docstring for the `credential_env_var`
    best-effort shape guard)."""
    if (
        not collector_id
        or not identity_ref
        or not credential_env_var
        or not imap_host
        or not imap_folder
        or not provider_name
    ):
        raise EmailCollectorError(
            "collector_id, identity_ref, credential_env_var, imap_host, imap_folder and provider_name are all required"
        )
    try:
        mode = ConnectionMode(connection_mode)
    except ValueError as exc:
        raise EmailCollectorError(
            f"connection_mode must be one of {[m.value for m in ConnectionMode]}, got {connection_mode!r}"
        ) from exc
    if not credential_env_var.isupper() or " " in credential_env_var or credential_env_var.count("=") > 0:
        raise EmailCollectorError(
            f"credential_env_var must be a bare environment-variable NAME (e.g. 'EMAIL_{collector_id.upper()}_APP_PASSWORD'), "
            f"never a credential value itself -- got {credential_env_var!r}"
        )
    if not sender_allowlist:
        raise EmailCollectorError(
            "sender_allowlist must name at least one sender address -- an email collector never parses every "
            "message in the mailbox indiscriminately (point 4)"
        )
    uses = list(allowed_uses) if allowed_uses is not None else list(DEFAULT_ALLOWED_USES)
    for use in uses:
        try:
            AllowedUse(use)
        except ValueError as exc:
            raise EmailCollectorError(
                f"allowed_uses entries must be one of {[u.value for u in AllowedUse]}, got {use!r}"
            ) from exc
    return mode, uses


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
