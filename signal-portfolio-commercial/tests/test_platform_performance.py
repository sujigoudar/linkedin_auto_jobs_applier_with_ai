"""app/services/platform_performance.py -- the volume-weighted-average-
cost realized-P&L replay ported from signal-copier's own
app/economics.py, exercised the same way that module's own
tests/test_e06_account_economics.py exercises the original."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.ledger import Book, EvidenceClass, Side
from app.services.ledger import append_entry
from app.services.platform_performance import compute_platform_performance

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _fill(db_session, *, tenant_id="tenant-a", instrument="AAPL", side, quantity, price, fee=None, multiplier=Decimal(1), when=None):
    return append_entry(
        db_session, tenant_id=tenant_id, book=Book.PLATFORM, instrument=instrument, side=side,
        quantity=Decimal(str(quantity)), price=Decimal(str(price)), currency="USD",
        event_time=when or _T0, source_authority="test", evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
        fee=fee, multiplier=multiplier,
    )


def test_no_entries_yields_an_empty_report_not_an_error(db_session):
    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(0)
    assert report.per_instrument == {}


def test_simple_round_trip_realizes_expected_pnl(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, side=Side.SELL, quantity=10, price=110, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(100)
    assert report.per_instrument["AAPL"].realized_pnl == Decimal(100)
    assert report.per_instrument["AAPL"].open_quantity == Decimal(0)
    assert report.per_instrument["AAPL"].closing_fills == 1


def test_a_losing_trade_realizes_a_negative_pnl(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, side=Side.SELL, quantity=10, price=90, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(-100)


def test_partial_close_realizes_only_the_closed_portion(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, side=Side.SELL, quantity=4, price=110, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(40)
    assert report.per_instrument["AAPL"].open_quantity == Decimal(6)


def test_average_cost_updates_when_adding_to_the_same_side(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, side=Side.BUY, quantity=10, price=120, when=_T0 + timedelta(minutes=1))
    _fill(db_session, side=Side.SELL, quantity=20, price=130, when=_T0 + timedelta(minutes=2))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    # average cost = (10*100 + 10*120) / 20 = 110; realized = (130-110)*20 = 400
    assert report.realized_pnl == Decimal(400)


def test_flip_through_flat_opens_a_fresh_position_at_the_flip_price(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, side=Side.SELL, quantity=15, price=110, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    ip = report.per_instrument["AAPL"]
    assert ip.realized_pnl == Decimal(100)  # only the closing 10 realize
    assert ip.open_quantity == Decimal(-5)  # flipped short
    assert ip.average_cost == Decimal(110)  # the flip fill's own price


def test_short_side_realizes_pnl_in_the_correct_direction(db_session):
    _fill(db_session, side=Side.SELL, quantity=10, price=100, when=_T0)
    _fill(db_session, side=Side.BUY, quantity=10, price=90, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(100)


def test_multiplier_scales_the_realized_pnl(db_session):
    _fill(db_session, instrument="ES", side=Side.BUY, quantity=1, price=100, multiplier=Decimal(50), when=_T0)
    _fill(db_session, instrument="ES", side=Side.SELL, quantity=1, price=105, multiplier=Decimal(50), when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(250)  # (105-100) * 1 * 50


def test_an_unknown_fee_on_any_contributing_entry_is_counted_not_silently_zeroed(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, fee=None, when=_T0)
    _fill(db_session, side=Side.SELL, quantity=10, price=110, fee=Decimal("1.50"), when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.per_instrument["AAPL"].unknown_fee_entry_count == 1


def test_every_entry_with_a_known_fee_reports_zero_unknown_count(db_session):
    _fill(db_session, side=Side.BUY, quantity=10, price=100, fee=Decimal("0"), when=_T0)
    _fill(db_session, side=Side.SELL, quantity=10, price=110, fee=Decimal("1.50"), when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.per_instrument["AAPL"].unknown_fee_entry_count == 0


def test_a_different_tenants_entries_never_leak_into_this_tenants_report(db_session):
    _fill(db_session, tenant_id="tenant-a", side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, tenant_id="tenant-a", side=Side.SELL, quantity=10, price=110, when=_T0 + timedelta(minutes=1))
    _fill(db_session, tenant_id="tenant-b", side=Side.BUY, quantity=10, price=100, when=_T0)
    _fill(db_session, tenant_id="tenant-b", side=Side.SELL, quantity=10, price=999, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(100)


def test_only_platform_book_entries_are_counted_not_other_books(db_session):
    append_entry(
        db_session, tenant_id="tenant-a", book=Book.FOLLOWER, instrument="AAPL", side=Side.BUY,
        quantity=Decimal(10), price=Decimal(100), currency="USD", event_time=_T0, source_authority="test",
        evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
    )
    append_entry(
        db_session, tenant_id="tenant-a", book=Book.FOLLOWER, instrument="AAPL", side=Side.SELL,
        quantity=Decimal(10), price=Decimal(999), currency="USD", event_time=_T0 + timedelta(minutes=1),
        source_authority="test", evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
    )
    db_session.commit()

    report = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert report.realized_pnl == Decimal(0)
    assert report.per_instrument == {}
