"""Append-only writes into the economic journal (app/models/ledger.py).
This is the ONLY sanctioned way to record a ledger entry or fix a mistaken
one -- both paths insert a new row and never touch an existing one, which
is also enforced at the database level (app/db.py's
`enforce_append_only` trigger) so this module is a convenience, not the
only thing standing between the data and a silent edit.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.ledger import Book, EvidenceClass, LedgerEntry, ReconciliationState, Side


def append_entry(
    session: Session,
    *,
    tenant_id: str,
    book: Book,
    instrument: str,
    side: Side,
    quantity: Decimal,
    price: Decimal,
    currency: str,
    event_time: datetime,
    source_authority: str,
    evidence_class: EvidenceClass,
    multiplier: Decimal = Decimal(1),
    fee: Decimal | None = None,
    follower_connection_id: str | None = None,
    originating_analyst_id: str | None = None,
    sleeve_id: str | None = None,
    external_observation_id: str | None = None,
    account_id: str | None = None,
) -> LedgerEntry:
    """`evidence_class` is a required argument, not a default, per
    Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
    S7 -- there is no honest default for "what kind of evidence is this."
    `fee` defaults to `None` (unknown), never `Decimal(0)`: "Importing a
    zero default is not proof of a verified fee" (S7) -- a caller that
    genuinely knows the fee is zero (e.g. a commission-free venue) passes
    `Decimal(0)` explicitly; a caller that doesn't know passes nothing.

    `follower_connection_id` (S7: "FOLLOWER requires an authorized
    observation of that specific customer's account") is meaningful only
    for `book == Book.FOLLOWER` -- passing it for any other book is not
    rejected here (this function trusts its caller the same way it
    already trusts `book` itself), but nothing in this codebase's own
    callers does that today."""
    entry = LedgerEntry(
        tenant_id=tenant_id,
        book=book,
        instrument=instrument,
        side=side,
        quantity=quantity,
        price=price,
        multiplier=multiplier,
        currency=currency,
        fee=fee,
        event_time=event_time,
        source_authority=source_authority,
        evidence_class=evidence_class,
        follower_connection_id=follower_connection_id,
        originating_analyst_id=originating_analyst_id,
        sleeve_id=sleeve_id,
        external_observation_id=external_observation_id,
        account_id=account_id,
    )
    session.add(entry)
    session.flush()
    return entry


class UnknownLedgerEntryError(Exception):
    pass


def append_correction(
    session: Session,
    *,
    original_entry_id: str,
    quantity: Decimal,
    price: Decimal,
    event_time: datetime,
    source_authority: str,
    fee: Decimal | None = None,
    instrument: str | None = None,
    side: Side | None = None,
    currency: str | None = None,
    multiplier: Decimal | None = None,
) -> LedgerEntry:
    """Record a correction for `original_entry_id` as a brand-new row
    referencing it via `correction_of` -- the original is never modified.

    `quantity`/`price` are always supplied fresh (there is no honest
    default for "the corrected number"). `instrument`/`side`/`currency`/
    `multiplier` default to `None`, meaning "inherit the original's own
    value, unchanged" -- the original, fee-correction-only caller
    (`EventType.FEE`, `app/services/integration_inbox.py`) never passes
    these, so its behavior is byte-for-byte unchanged by their addition.
    A caller that genuinely knows one of these changed too (Track 41:
    `EventType.SOURCE_RECEIPT`'s own revision/EDIT handling, where the
    analyst's edited instruction may rename the instrument or flip the
    side, not just the quantity/price) passes the real new value
    explicitly -- same `None`-means-unknown-or-unchanged idiom `fee`
    already used here. Every other identity field (tenant/book/
    evidence_class/follower_connection_id/originating_analyst_id/
    sleeve_id/external_observation_id) always inherits from the
    original verbatim; a correction never reassigns WHO or WHAT BOOK an
    entry belongs to, only ITS OWN economic numbers."""
    original = session.get(LedgerEntry, original_entry_id)
    if original is None:
        raise UnknownLedgerEntryError(f"no ledger entry {original_entry_id!r} to correct")

    correction = LedgerEntry(
        tenant_id=original.tenant_id,
        book=original.book,
        instrument=original.instrument if instrument is None else instrument,
        side=original.side if side is None else side,
        quantity=quantity,
        price=price,
        multiplier=original.multiplier if multiplier is None else multiplier,
        currency=original.currency if currency is None else currency,
        fee=original.fee if fee is None else fee,
        evidence_class=original.evidence_class,
        follower_connection_id=original.follower_connection_id,
        originating_analyst_id=original.originating_analyst_id,
        sleeve_id=original.sleeve_id,
        external_observation_id=original.external_observation_id,
        account_id=original.account_id,
        event_time=event_time,
        source_authority=source_authority,
        reconciliation_state=ReconciliationState.UNRECONCILED,
        correction_of=original.entry_id,
    )
    session.add(correction)
    session.flush()
    return correction
