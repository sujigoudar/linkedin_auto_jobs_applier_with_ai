"""Track 41 (Gap 2): a real, persisted representation of a
`signal_platform_contracts.SourceEventKind.TARGET_UPDATE`/`STOP_UPDATE`
revision -- see ADR-0011 for the full decision and why this is a
DEDICATED history table, never new columns on `app/models/ledger.py::
LedgerEntry`.

Short version: a stop-loss/take-profit revision carries no quantity/
price economic fact of its own -- it REVISES risk parameters attached
to an existing `Book.SOURCE` recommendation, it does not recommend (or
execute) a trade. `LedgerEntry.quantity`/`.price`/`.side` are NOT NULL
columns because every row in that table genuinely is a trade fact
(ADR-0004); forcing a stop/target revision through that shape would
either fabricate a quantity/price that was never given, or weaken
`LedgerEntry`'s own NOT NULL invariant for every other book's row too.
This table instead mirrors the SAME honest-provenance idiom
`app/services/integration_inbox.py`'s own `SOURCE_EVENT` handling
already uses for DELETE/CANCEL/CLOSE (a real row recording that this
exact, real moment happened, with no fabricated economic content) --
just with real, queryable columns instead of only an opaque
`envelope_json` blob, since a dashboard or a future managed-stop
feature genuinely needs to read "what is this position's current
recommended stop" back out.

Append-only, same reasoning as `ledger_entries`/`portfolio_versions`
(ADR-0008): a later revision of the SAME position's stop/target is
always a NEW row here, never an UPDATE of an earlier one -- the history
of every revision this build ever received is real audit trail, not
just its latest value.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

__all__ = ["SourceStopTargetRevision", "StopTargetRevisionKind"]


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StopTargetRevisionKind(str, enum.Enum):
    """Mirrors exactly the two `signal_platform_contracts.SourceEventKind`
    members this table exists for -- never a third, invented value."""

    TARGET_UPDATE = "target_update"
    STOP_UPDATE = "stop_update"


_MONEY = Numeric(28, 10)


class SourceStopTargetRevision(Base):
    __tablename__ = "source_stop_target_revisions"

    revision_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    kind: Mapped[StopTargetRevisionKind] = mapped_column(
        Enum(StopTargetRevisionKind, native_enum=False), nullable=False
    )

    #: This revision's own native-provider identity -- the SAME
    #: tenant-scoped f"{tenant_id}|{source_provider_id}|
    #: {source_channel_id}|{source_event_id}" shape
    #: `app/services/integration_inbox.py::_source_event_native_key`
    #: already produces for `SOURCE_EVENT`/`SOURCE_RECEIPT` rows --
    #: lets this revision itself be found/correlated later the same
    #: honest way, never a second identity scheme.
    source_event_native_key: Mapped[str] = mapped_column(String, nullable=False, index=True)

    #: The instrument this revision concerns, when the provider event
    #: itself restated one (`SourceEventPayload.instrument`) -- `None`
    #: when it didn't (e.g. a bare "move my stop to breakeven" reply
    #: with no instrument named in the reply itself). Never guessed
    #: from `resolved_source_entry_id`'s own instrument even when that
    #: resolves -- this column is what the REVISION itself said, kept
    #: distinct from what it was matched against.
    instrument: Mapped[str | None] = mapped_column(String, nullable=True)

    #: The revised stop-loss/take-profit, straight from this revision's
    #: own `SourceReceiptPayload.stop_loss`/`.take_profit` -- `None`
    #: means "not given by this revision", never zero (same "importing
    #: a zero default is not proof of a verified zero" discipline
    #: `LedgerEntry.fee`'s own docstring already uses).
    stop_loss: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    #: The revised ordered multi-target collection
    #: (`SourceReceiptPayload.targets`, TradingView/TradeAlgo/BuyAlerts-
    #: style TP1/TP2/TP3...), serialized verbatim as JSON -- `"[]"`
    #: (never `NULL`) when the revision gave none, matching that
    #: payload field's own empty-list default.
    targets_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")

    #: The real, already-applied `Book.SOURCE` `LedgerEntry` this
    #: revision concerns -- resolved via the exact same tenant-scoped
    #: native-key correlation `SourceEventKind.EDIT` uses
    #: (`source.parent_event_id`, when the provider named one), against
    #: an already-applied `SOURCE_RECEIPT` row's own `ledger_entry_id`.
    #: `None` when no `parent_event_id` was given, or it didn't resolve
    #: -- NEVER guessed by any looser match. No FOREIGN KEY, same
    #: reasoning as `LedgerEntry.sleeve_id`/`.follower_connection_id`:
    #: a resolved entry is never deleted (ledger history is append-
    #: only) but this column deliberately stays a soft reference, like
    #: every other cross-identity pointer in this codebase.
    resolved_source_entry_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    #: When the PROVIDER says this revision happened
    #: (`SourceEventPayload.provider_timestamp`) -- distinct from
    #: `received_at` below, same `provider_timestamp`/`local_receipt_
    #: timestamp` split `SourceEventPayload`'s own docstring describes.
    provider_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: When THIS service actually recorded this row.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    #: Free-text provenance tag, same convention as
    #: `LedgerEntry.source_authority` (e.g.
    #: f"signal-copier-relay:{producer_id}").
    source_authority: Mapped[str] = mapped_column(String, nullable=False)
