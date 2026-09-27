"""The economic journal core: an append-only ledger spanning the four
independent books spec/docs/04_metrics_accounting_and_truth.md requires
kept separate -- "source recommendations, canonical portfolio model,
platform strategy and actual follower execution... An alert delivered is
not an executed trade. C2 strategy model performance is not customer
actual performance."

"The execution journal is append-only by identity with correction/
reversal events; projections can be rebuilt" -- enforced here as an
actual Postgres constraint (see `app/db.py`'s `enforce_append_only`,
a BEFORE UPDATE OR DELETE trigger), not merely an application convention
a future caller could route around. A mistaken entry is corrected by
appending a NEW row whose `correction_of` points at the original --the
original is never edited or removed, so the entry that was actually
booked at the time stays reconstructable.

Money fields are `Numeric`, never `Float` -- "Use Decimal or integer
minor/tick units at accounting boundaries; do not calculate exact money
in... floating point."
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Book(str, enum.Enum):
    SOURCE = "source"  #: what the provider/analyst originally recommended
    MODEL = "model"  #: the canonical portfolio-version model's own instructions
    PLATFORM = "platform"  #: the owner's actual discretionary/strategy account
    FOLLOWER = "follower"  #: a specific customer's actual executed account


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class ReconciliationState(str, enum.Enum):
    UNRECONCILED = "unreconciled"
    RECONCILED = "reconciled"
    DISPUTED = "disputed"


_MONEY = Numeric(28, 10)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    entry_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    book: Mapped[Book] = mapped_column(Enum(Book, native_enum=False), nullable=False)

    instrument: Mapped[str] = mapped_column(String, nullable=False)
    side: Mapped[Side] = mapped_column(Enum(Side, native_enum=False), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    price: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    multiplier: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal(1))
    currency: Mapped[str] = mapped_column(String, nullable=False)
    fee: Mapped[Decimal] = mapped_column(_MONEY, nullable=False, default=Decimal(0))

    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    source_authority: Mapped[str] = mapped_column(String, nullable=False)
    reconciliation_state: Mapped[ReconciliationState] = mapped_column(
        Enum(ReconciliationState, native_enum=False), nullable=False, default=ReconciliationState.UNRECONCILED
    )

    #: Points at the entry this one corrects -- NULL for an original entry.
    #: The original row is never updated or deleted; this is how a mistake
    #: is fixed instead.
    correction_of: Mapped[str | None] = mapped_column(ForeignKey("ledger_entries.entry_id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
