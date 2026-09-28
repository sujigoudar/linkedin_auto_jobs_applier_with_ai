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

`evidence_class` (Signal Platform Integration Correction Pack's own
INTEGRATION_DECISION.md S7): every entry declares, by construction, what
kind of evidence it is -- a synthetic test fixture, an internal paper
trade, a hypothetical backtest, an actually observed owner/follower
execution, or a platform's own reported model result. A dashboard must
never have to guess this from context, and a bridge importing an
observation from signal-copier must never leave it implicit. Reuses
`signal_platform_contracts.EvidenceClass` -- the same enum the export
envelope itself carries -- as the one source of truth for this dimension,
rather than a second, potentially-drifting definition of the same seven
values living only in this app.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from signal_platform_contracts import EvidenceClass

__all__ = [
    "Book",
    "EvidenceClass",
    "LedgerEntry",
    "ReconciliationState",
    "Side",
]


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
    #: NULL means "fee not yet known", never zero -- INTEGRATION_DECISION.md
    #: S7: "Importing a zero default is not proof of a verified zero fee."
    #: A caller that genuinely knows the fee is zero (e.g. a commission-
    #: free venue) passes `Decimal(0)` explicitly; app/services/ledger.py's
    #: own `append_entry` never substitutes one on a caller's behalf.
    fee: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)

    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    source_authority: Mapped[str] = mapped_column(String, nullable=False)
    #: Nullable at the DATABASE level only so an additive migration never
    #: breaks a pre-existing row -- app/services/ledger.py's own
    #: append_entry/append_correction require it as a real argument, so
    #: every entry this application itself ever writes has one. A NULL
    #: here means "written before this column existed", never "no
    #: evidence class applies".
    evidence_class: Mapped[EvidenceClass | None] = mapped_column(Enum(EvidenceClass, native_enum=False), nullable=True)
    reconciliation_state: Mapped[ReconciliationState] = mapped_column(
        Enum(ReconciliationState, native_enum=False), nullable=False, default=ReconciliationState.UNRECONCILED
    )

    #: Which specific customer's own PlatformConnection this entry is an
    #: authorized observation OF -- meaningful only for `book ==
    #: Book.FOLLOWER` entries (S7: "FOLLOWER requires an authorized
    #: observation of that specific customer's account. A copied model
    #: alert or subscription alone cannot populate this book."), NULL for
    #: every other book. No FOREIGN KEY constraint to platform_connections
    #: (a connection can be legitimately disconnected/removed later
    #: without invalidating the historical fact that THIS entry was once
    #: observed through it -- ledger history is append-only and must
    #: survive that). Nothing in this build populates a FOLLOWER-book
    #: entry yet (app/models/platform_connection.py's own docstring: a
    #: connection here is only ever DECLARED, never a real authorized
    #: observation channel) -- this column exists so
    #: app/services/customer_performance_state.py's own query is real and
    #: correct today, ready for the actual observation connector
    #: (INTEGRATION_DECISION.md S12 step 6) to populate for real later.
    follower_connection_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    #: The observation source's own stable identifier for this fill
    #: (e.g. a Collective2 "TradeId"/eToro "PositionID") -- meaningful
    #: only for `book == Book.FOLLOWER` entries, S12 step 6's own real
    #: observation-connector boundary (app/services/
    #: follower_observation.py). NULL for every other book. This is the
    #: idempotency key a redelivered/re-polled observation of the SAME
    #: real fill is deduplicated by -- the same "SAME identity, harmless
    #: re-detect" contract app/services/integration_inbox.py's own
    #: `event_id` already gives the private-relay boundary, applied here
    #: to the third-party-observation boundary instead.
    external_observation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    #: `signal_platform_contracts.ExecutionAppliedPayload.originating_
    #: analyst_id`, carried straight through by app/services/
    #: integration_inbox.py's own `_apply_projection`. NULL means "not
    #: attributed to a specific analyst" (INTEGRATION_ACCEPTANCE_
    #: CASES.json INT-026's own "analyst=None ... never folded into some
    #: other analyst's numbers" -- see app/services/analyst_attribution.py's
    #: own docstring), never assumed to be some other analyst or the
    #: whole account.
    originating_analyst_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    #: Which `Sleeve` (app/services/sleeve_mapping.py's own
    #: `resolve_sleeve_for_source`) this entry was matched to at ingest
    #: time -- meaningful only for `book == Book.SOURCE` entries
    #: (S12 step 5 "Portfolio Lab source feed"). NULL means no sleeve
    #: was admitted for this exact (provider, analyst, parser_version)
    #: tuple yet, never "the whole account" or some other sleeve. No
    #: FOREIGN KEY constraint, same reasoning as `follower_connection_id`
    #: above: a sleeve can be redefined/retired later without
    #: invalidating the historical fact that THIS entry matched it at
    #: the time it was recorded.
    sleeve_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    #: Points at the entry this one corrects -- NULL for an original entry.
    #: The original row is never updated or deleted; this is how a mistake
    #: is fixed instead.
    correction_of: Mapped[str | None] = mapped_column(ForeignKey("ledger_entries.entry_id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
