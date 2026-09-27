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

from app.models.ledger import Book, LedgerEntry, ReconciliationState, Side


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
    multiplier: Decimal = Decimal(1),
    fee: Decimal = Decimal(0),
) -> LedgerEntry:
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
) -> LedgerEntry:
    """Record a correction for `original_entry_id` as a brand-new row
    referencing it via `correction_of` -- the original is never modified.
    The correction inherits the original's tenant/book/instrument/side/
    currency/multiplier (those identify WHAT was being recorded; only the
    numbers being corrected are supplied fresh here)."""
    original = session.get(LedgerEntry, original_entry_id)
    if original is None:
        raise UnknownLedgerEntryError(f"no ledger entry {original_entry_id!r} to correct")

    correction = LedgerEntry(
        tenant_id=original.tenant_id,
        book=original.book,
        instrument=original.instrument,
        side=original.side,
        quantity=quantity,
        price=price,
        multiplier=original.multiplier,
        currency=original.currency,
        fee=original.fee if fee is None else fee,
        event_time=event_time,
        source_authority=source_authority,
        reconciliation_state=ReconciliationState.UNRECONCILED,
        correction_of=original.entry_id,
    )
    session.add(correction)
    session.flush()
    return correction
