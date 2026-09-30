"""Track 8: the unified collector-registry abstraction.

Tracks 5/6/7/9/10 each built a SEPARATE persistent collector/source
registry, independently, following the same shape: an enum for how the
collector connects, a `validate_registration`-style function, a SQLite
table with near-identical columns (`qualification_evidence`,
`last_qualified_at`, some form of `checkpoint`, `health_state`,
`health_detail`, `created_at`, `updated_at`), and 6-10 near-identical
CRUD methods on `SignalStore` (register_X, get_X, list_X,
update_X_health, record_X_qualification_evidence, get_X_checkpoint,
advance_X_checkpoint):

- `app/telegram_collectors.py` (`telegram_collectors` table) -- THE
  CANONICAL TEMPLATE the other four copied.
- `app/collector_registry.py` (`pull_collectors` table) -- Slack/Twitter.
- `app/email_collectors.py` (`email_collectors` table).
- `app/website_collectors.py` (`website_collectors` table, plus the
  unrelated `website_article_candidates` table -- article dedup, not a
  collector registry, out of scope here).
- `app/notification_bridge.py` (`notification_bridge_devices` table).

This module is the unified layer: ONE table (`collectors`, see its own
`CREATE TABLE` comment in `app/db.py`'s `SCHEMA`) and ONE set of generic
CRUD methods on `SignalStore` (`register_collector`, `get_collector`,
`list_collectors`, `update_collector_health`,
`record_collector_qualification_evidence`, `get_collector_checkpoint`,
`advance_collector_checkpoint`), parameterized by `kind` (which
pre-existing registry a row belongs to) and `provider` (that registry's
own existing discriminator).

FOUR of the five registries were migrated onto this table: telegram,
pull (slack/twitter), email, website -- see `CollectorKind` below and
`alembic/versions/0025_add_unified_collectors_table.py` for the
backfill migration that copied their existing rows across. Each of
those four pre-existing modules' own `register_*`/`get_*`/`list_*`/
`update_*_health`/`record_*_qualification_evidence`/
`get_*_checkpoint`/`advance_*_checkpoint` methods on `SignalStore` (in
`app/db.py`) are now thin wrappers over this module's generic methods --
same public signature and same returned dict shape as before migration,
so every existing call site (every route in `app/main.py`, every
adapter in `app/sources/*.py`, every test) keeps working completely
unchanged; what changed is that they all now read/write the SAME table
through the SAME generic implementation instead of five parallel copies
of near-identical SQL.

`app/notification_bridge.py`'s device registry (`notification_bridge_
devices`) was DELIBERATELY LEFT OUT of this migration -- see this
module's own `CollectorKind` docstring for why: its shape is genuinely
not the same as the other four (no `allowed_uses`/`qualification_
evidence` concept at all; a hashed pairing token rather than a
`credential_env_var` reference; a read-time health-state OVERRIDE tied
to heartbeat staleness -- `SignalStore._notification_bridge_device_row_
to_dict` -- that has no equivalent anywhere else). Forcing it onto this
table's generic vocabulary would have meant either losing that fidelity
or special-casing the "generic" methods per kind anyway, defeating the
point of unifying them. It keeps its own dedicated table and methods,
unchanged.

Every real per-provider VALIDATION RULE (a bad `connection_mode`, a
`credential_env_var` that looks like a secret value rather than a name,
an unrecognized `allowed_uses`/`health_state` entry, IMAP-specific or
website-format-specific required-field checks) is still enforced by
each provider module's own `validate_registration`/`CollectorHealth`
(`app.telegram_collectors`, `app.collector_registry`,
`app.email_collectors`, `app.website_collectors`) -- this module never
re-implements or weakens any of it; it only supplies the common
persistence plumbing those four modules' `SignalStore` wrapper methods
now call into.

`CollectorAdapter` is the common Python interface the per-provider
SOURCE adapters (`app/sources/telegram_user.py`,
`app/sources/slack_user.py`, `app/sources/twitter_user.py`,
`app/sources/email_source.py`, `app/sources/*website*`) already conform
to in spirit (each has its own `import_history`/live-poll method that
never advances the live checkpoint during historical import, and its
own qualification-evidence recording once a real message is observed --
see each module's own docstring for the historical-import-vs-live-catch-
up distinction this mirrors). It's declared here as a `Protocol` so
`app/engine.py`/`app/main.py` code that wants to treat collector kinds
uniformly (e.g. a future generic "list every collector across every
provider with its health" endpoint) has a real, checked common surface
to depend on, without forcing every existing adapter class to change
its base class today -- structural typing (`Protocol`) means a class
that already has the right methods satisfies it with zero changes.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


class CollectorKind(str, enum.Enum):
    """Which pre-existing registry a `collectors` row belongs to. See
    this module's own docstring for why `NOTIFICATION_BRIDGE_DEVICE` is
    deliberately NOT a value here -- that registry was not migrated onto
    the unified `collectors` table and keeps its own dedicated table."""

    TELEGRAM = "telegram"
    PULL = "pull"
    EMAIL = "email"
    WEBSITE = "website"


class UnifiedCollectorError(ValueError):
    """Raised by a generic `SignalStore` collector method for a row that
    fails validation this module itself is responsible for (an
    unrecognized `kind`). Per-provider field validation still raises
    each provider module's own error type (`TelegramCollectorError`,
    `PullCollectorError`, etc.) -- this module never intercepts or
    downgrades those."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@runtime_checkable
class CollectorAdapter(Protocol):
    """The common shape every per-provider collector source adapter
    already has, expressed as a structural `Protocol` -- see this
    module's own docstring. Nothing in this codebase requires a class to
    literally inherit from this; any adapter whose methods already match
    this shape satisfies it as-is (`isinstance(adapter, CollectorAdapter)`
    works via `runtime_checkable` structural typing)."""

    def validate_registration(self, **fields: Any) -> Any:
        """Validate a registration request for this provider BEFORE
        anything is persisted -- raises on any field this provider's own
        vocabulary doesn't recognize. Never a silent best-effort accept."""
        ...

    def historical_import(self, **kwargs: Any) -> Any:
        """One-time backfill of pre-existing messages/articles/events.
        MUST NEVER call the engine's `on_signal` and MUST NEVER advance
        this collector's live checkpoint -- see e.g.
        `app/sources/telegram_user.py`'s module docstring for the
        historical-import-vs-live-catch-up distinction every adapter
        that implements this must preserve."""
        ...

    def poll_live(self, **kwargs: Any) -> Any:
        """Poll/consume live events, admitting each one to routing (and
        advancing the live checkpoint) only after it has genuinely been
        processed -- gated by the qualification-evidence/health state
        this row's generic CRUD methods (`SignalStore.record_collector_
        qualification_evidence` et al.) persist."""
        ...
