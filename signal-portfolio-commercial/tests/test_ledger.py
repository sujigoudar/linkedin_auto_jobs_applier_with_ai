"""app/services/ledger.py + app/db.py's append-only enforcement -- the
economic journal's core invariant (docs/04: "The execution journal is
append-only by identity with correction/reversal events; projections can
be rebuilt")."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models.ledger import Book, LedgerEntry, ReconciliationState, Side
from app.services.ledger import UnknownLedgerEntryError, append_correction, append_entry


def test_append_entry_persists_the_given_fields(db_session):
    entry = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.FOLLOWER,
        instrument="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100.50"),
        currency="USD",
        event_time=datetime.now(timezone.utc),
        source_authority="broker-fill-confirmation",
    )

    fetched = db_session.get(LedgerEntry, entry.entry_id)
    assert fetched is not None
    assert fetched.quantity == Decimal("10")
    assert fetched.price == Decimal("100.50")
    assert fetched.correction_of is None
    assert fetched.reconciliation_state == ReconciliationState.UNRECONCILED


def test_a_direct_update_against_the_table_is_rejected(db_session):
    entry = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.PLATFORM,
        instrument="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        currency="USD",
        event_time=datetime.now(timezone.utc),
        source_authority="test",
    )
    db_session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(
            text("UPDATE ledger_entries SET price = 999 WHERE entry_id = :id"), {"id": entry.entry_id}
        )
        db_session.commit()
    db_session.rollback()


def test_a_direct_delete_against_the_table_is_rejected(db_session):
    entry = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.PLATFORM,
        instrument="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        currency="USD",
        event_time=datetime.now(timezone.utc),
        source_authority="test",
    )
    db_session.commit()

    with pytest.raises(DBAPIError, match="append-only"):
        db_session.execute(text("DELETE FROM ledger_entries WHERE entry_id = :id"), {"id": entry.entry_id})
        db_session.commit()
    db_session.rollback()


def test_a_correction_adds_a_new_row_and_leaves_the_original_untouched(db_session):
    original = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.MODEL,
        instrument="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        currency="USD",
        event_time=datetime.now(timezone.utc),
        source_authority="analyst-alert",
    )
    db_session.commit()

    correction = append_correction(
        db_session,
        original_entry_id=original.entry_id,
        quantity=Decimal("10"),
        price=Decimal("105"),  # the real, corrected price
        event_time=datetime.now(timezone.utc),
        source_authority="analyst-correction",
    )

    assert correction.entry_id != original.entry_id
    assert correction.correction_of == original.entry_id
    assert correction.price == Decimal("105")

    reloaded_original = db_session.get(LedgerEntry, original.entry_id)
    assert reloaded_original.price == Decimal("100")  # unchanged
    assert reloaded_original.correction_of is None


def test_a_correction_inherits_identity_fields_from_the_original(db_session):
    original = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.SOURCE,
        instrument="MSFT",
        side=Side.SELL,
        quantity=Decimal("5"),
        price=Decimal("50"),
        currency="USD",
        event_time=datetime.now(timezone.utc),
        source_authority="test",
    )
    correction = append_correction(
        db_session,
        original_entry_id=original.entry_id,
        quantity=Decimal("5"),
        price=Decimal("52"),
        event_time=datetime.now(timezone.utc),
        source_authority="test-correction",
    )

    assert correction.tenant_id == original.tenant_id
    assert correction.book == original.book
    assert correction.instrument == original.instrument
    assert correction.side == original.side
    assert correction.currency == original.currency


def test_correcting_an_unknown_entry_raises(db_session):
    with pytest.raises(UnknownLedgerEntryError):
        append_correction(
            db_session,
            original_entry_id="does-not-exist",
            quantity=Decimal("1"),
            price=Decimal("1"),
            event_time=datetime.now(timezone.utc),
            source_authority="test",
        )
