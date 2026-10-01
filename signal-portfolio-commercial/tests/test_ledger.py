"""app/services/ledger.py + app/db.py's append-only enforcement -- the
economic journal's core invariant (docs/04: "The execution journal is
append-only by identity with correction/reversal events; projections can
be rebuilt")."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.models.ledger import Book, EvidenceClass, LedgerEntry, ReconciliationState, Side
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
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
    )

    fetched = db_session.get(LedgerEntry, entry.entry_id)
    assert fetched is not None
    assert fetched.quantity == Decimal("10")
    assert fetched.price == Decimal("100.50")
    assert fetched.correction_of is None
    assert fetched.reconciliation_state == ReconciliationState.UNRECONCILED
    assert fetched.evidence_class == EvidenceClass.OBSERVED_FOLLOWER_LIVE


def test_append_entry_persists_every_optional_identity_field(db_session):
    """Mutation-testing follow-up (track39): the test above never sets
    `multiplier`/`follower_connection_id`/`originating_analyst_id`/
    `sleeve_id`/`external_observation_id` to a real, distinguishing
    value, so a mutant that silently drops any one of them to `None`
    (or, for `multiplier`, to the same default it would have had
    anyway) went undetected. A real futures/options contract has a
    multiplier != 1 -- silently losing it would make downstream
    notional math wrong, not just a missing audit field."""
    entry = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.FOLLOWER,
        instrument="ESZ5",
        side=Side.BUY,
        quantity=Decimal("2"),
        price=Decimal("5000"),
        currency="USD",
        multiplier=Decimal("50"),
        event_time=datetime.now(timezone.utc),
        source_authority="broker-fill-confirmation",
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
        follower_connection_id="conn-123",
        originating_analyst_id="analyst-7",
        sleeve_id="sleeve-9",
        external_observation_id="obs-42",
    )

    fetched = db_session.get(LedgerEntry, entry.entry_id)
    assert fetched.multiplier == Decimal("50")
    assert fetched.follower_connection_id == "conn-123"
    assert fetched.originating_analyst_id == "analyst-7"
    assert fetched.sleeve_id == "sleeve-9"
    assert fetched.external_observation_id == "obs-42"


def test_append_entry_leaves_fee_unset_by_default(db_session):
    """Signal Platform Integration Correction Pack's own INTEGRATION_DECISION.md
    S7: "Importing a zero default is not proof of a verified zero fee." --
    an entry with no known fee must record that as unknown, never a
    fabricated $0.00."""
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
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
    )
    assert entry.fee is None


def test_append_entry_records_an_explicitly_known_zero_fee(db_session):
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
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
        fee=Decimal("0"),
    )
    assert entry.fee == Decimal("0")


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
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
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
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
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
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
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
        multiplier=Decimal("50"),
        event_time=datetime.now(timezone.utc),
        source_authority="test",
        evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
        follower_connection_id="conn-456",
        originating_analyst_id="analyst-3",
        sleeve_id="sleeve-4",
        external_observation_id="obs-77",
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
    assert correction.evidence_class == EvidenceClass.OBSERVED_OWNER_LIVE
    # Mutation-testing follow-up (track39): the rest of the identity
    # fields `append_correction` is documented to inherit verbatim --
    # previously none of these were asserted, so silently dropping any
    # of them to `None` went undetected.
    assert correction.multiplier == Decimal("50")
    assert correction.follower_connection_id == "conn-456"
    assert correction.originating_analyst_id == "analyst-3"
    assert correction.sleeve_id == "sleeve-4"
    assert correction.external_observation_id == "obs-77"
    # A correction is always freshly UNRECONCILED, regardless of the
    # original's own reconciliation state -- it is a NEW economic fact
    # that hasn't been reconciled yet.
    assert correction.reconciliation_state == ReconciliationState.UNRECONCILED


def test_correcting_an_unknown_entry_includes_the_real_entry_id_in_the_error(db_session):
    """Mutation-testing follow-up (track39): kills the
    `UnknownLedgerEntryError(None)` mutant -- the raised error's message
    must actually name the unresolvable id, not be blank/`None`, so an
    operator reading logs can tell which id was wrong."""
    with pytest.raises(UnknownLedgerEntryError, match="does-not-exist-xyz"):
        append_correction(
            db_session,
            original_entry_id="does-not-exist-xyz",
            quantity=Decimal("1"),
            price=Decimal("1"),
            event_time=datetime.now(timezone.utc),
            source_authority="test",
        )


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


def test_a_correction_with_an_explicit_new_fee_uses_it_not_the_originals(db_session):
    """Mutation-testing follow-up (track39): `append_correction`'s
    `fee=original.fee if fee is None else fee` ternary was untested on
    its "caller passed an explicit new fee" branch -- every existing
    correction test omits `fee` entirely, exercising only the
    "inherit from original" branch. This is exactly
    `app/services/integration_inbox.py`'s real FEE-event call path
    (`append_correction(..., fee=payload.fee)`): a real, newly-reported
    fee must land on the correction, never be silently discarded in
    favor of the original entry's own (here, unset) fee."""
    original = append_entry(
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
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        # fee intentionally omitted -- original.fee is None
    )
    db_session.commit()

    correction = append_correction(
        db_session,
        original_entry_id=original.entry_id,
        quantity=Decimal("10"),
        price=Decimal("100"),
        event_time=datetime.now(timezone.utc),
        source_authority="fee-event",
        fee=Decimal("1.50"),
    )

    assert correction.fee == Decimal("1.50")


def test_a_correction_with_no_fee_argument_inherits_the_originals_real_fee(db_session):
    """The other half of the same ternary: a correction that does NOT
    pass `fee` at all must inherit the original's real, non-None fee
    (not silently drop to `None`) -- e.g. a price correction for an
    entry that already had a known commission."""
    original = append_entry(
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
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
        fee=Decimal("2.25"),
    )
    db_session.commit()

    correction = append_correction(
        db_session,
        original_entry_id=original.entry_id,
        quantity=Decimal("10"),
        price=Decimal("105"),
        event_time=datetime.now(timezone.utc),
        source_authority="price-correction",
    )

    assert correction.fee == Decimal("2.25")


def test_a_correction_with_no_overrides_still_inherits_instrument_side_currency_multiplier(db_session):
    """Track 41: `append_correction` gained `instrument`/`side`/
    `currency`/`multiplier` override parameters -- when a caller (like
    `EventType.FEE`'s own call site) omits all of them, the correction
    must inherit the original's values UNCHANGED, exactly as before
    this change."""
    original = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.SOURCE,
        instrument="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        currency="USD",
        multiplier=Decimal("1"),
        event_time=datetime.now(timezone.utc),
        source_authority="test",
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
    )
    db_session.commit()

    correction = append_correction(
        db_session,
        original_entry_id=original.entry_id,
        quantity=Decimal("10"),
        price=Decimal("100"),
        event_time=datetime.now(timezone.utc),
        source_authority="fee-event",
    )

    assert correction.instrument == "AAPL"
    assert correction.side == Side.BUY
    assert correction.currency == "USD"
    assert correction.multiplier == Decimal("1")


def test_a_correction_with_explicit_instrument_and_side_overrides_uses_them(db_session):
    """Track 41: a genuine revision/EDIT may rename the instrument or
    flip the side, not only revise quantity/price -- `append_correction`
    must use the explicitly-passed new value, never silently keep the
    original's stale one."""
    original = append_entry(
        db_session,
        tenant_id="tenant-a",
        book=Book.SOURCE,
        instrument="AAPL",
        side=Side.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        currency="USD",
        event_time=datetime.now(timezone.utc),
        source_authority="test",
        evidence_class=EvidenceClass.SYNTHETIC_FIXTURE,
    )
    db_session.commit()

    correction = append_correction(
        db_session,
        original_entry_id=original.entry_id,
        quantity=Decimal("12"),
        price=Decimal("101"),
        instrument="MSFT",
        side=Side.SELL,
        currency="EUR",
        multiplier=Decimal("2"),
        event_time=datetime.now(timezone.utc),
        source_authority="edit-revision",
    )

    assert correction.instrument == "MSFT"
    assert correction.side == Side.SELL
    assert correction.currency == "EUR"
    assert correction.multiplier == Decimal("2")
    assert correction.quantity == Decimal("12")
    assert correction.price == Decimal("101")

    reloaded_original = db_session.get(LedgerEntry, original.entry_id)
    assert reloaded_original.instrument == "AAPL"  # unchanged
    assert reloaded_original.side == Side.BUY  # unchanged
