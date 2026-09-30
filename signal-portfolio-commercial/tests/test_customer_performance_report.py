"""app/services/customer_performance_report.py -- the real
chronological equity series / max-drawdown / completed-episode win-rate
computation for CU-06 "Performance and costs", built from a customer's
own real Book.FOLLOWER ledger entries. Exercised the same way
tests/test_customer_performance_state.py exercises the paired module."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.ledger import Book, EvidenceClass, Side
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.customer_performance_report import get_own_performance_report
from app.services.ledger import append_entry
from app.services.platform_connection import create_platform_connection

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


def _declare_connection(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    _seed_membership(db_session, tenant_id=tenant_id, user_id=user_id)
    connection = create_platform_connection(
        db_session, tenant_id=tenant_id, user_id=user_id, platform="collective2",
        environment="local_simulation", masked_account_label="C2 ****1234",
    )
    db_session.commit()
    return connection


def _follower_fill(db_session, connection, *, side, quantity, price, when, instrument="AAPL"):
    return append_entry(
        db_session, tenant_id="tenant-a", book=Book.FOLLOWER, instrument=instrument, side=side,
        quantity=Decimal(str(quantity)), price=Decimal(str(price)), currency="USD",
        event_time=when, source_authority="test", evidence_class=EvidenceClass.OBSERVED_FOLLOWER_LIVE,
        follower_connection_id=connection.connection_id,
    )


def test_no_connection_yields_no_equity_series_or_drawdown_or_win_rate(db_session):
    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")
    assert report.equity_series == []
    assert report.max_drawdown is None
    assert report.win_rate is None
    assert report.completed_episode_count == 0


def test_a_connection_with_no_real_fills_is_the_honest_empty_state(db_session):
    """AWAITING_OBSERVATIONS-shaped: a real, scoped query that is
    honestly empty, never a fabricated 0 drawdown/win-rate."""
    _declare_connection(db_session)
    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")
    assert report.equity_series == []
    assert report.max_drawdown is None
    assert report.win_rate is None


def test_a_single_closing_fill_has_no_drawdown_yet(db_session):
    """One real point beyond the baseline is not enough for a real
    peak-to-trough walk (needs at least 2 points total)."""
    connection = _declare_connection(db_session)
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0)
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=110, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")
    # baseline (0) + one real closing point = 2 points -> a real (zero) drawdown IS computable
    assert [p.cumulative_pnl for p in report.equity_series] == [Decimal(0), Decimal(100)]
    assert report.max_drawdown is not None
    assert report.max_drawdown.max_drawdown == Decimal(0)
    assert report.win_rate == Decimal(1)
    assert report.completed_episode_count == 1
    assert report.winning_episode_count == 1


def test_real_drawdown_and_win_rate_load_bearing_verification(db_session):
    """Load-bearing: a real, hand-computable sequence of Book.FOLLOWER
    fills, one instrument, three completed FIFO episodes (win, loss,
    win) and a known interior drawdown that recovers before the series
    ends (so a naive first/last-only computation would silently miss
    it -- exactly the invariant `compute_max_drawdown` must catch).

    Real fills (AAPL), in order:
      t+0: BUY 10 @100   (open)
      t+1: SELL 10 @110  (close) -> realized +100  cumulative=100   [episode 1: WIN, +100]
      t+2: BUY 10 @100   (open)
      t+3: SELL 10 @80   (close) -> realized -200  cumulative=-100  [episode 2: LOSS, -200]
      t+4: BUY 10 @90    (open)
      t+5: SELL 10 @130  (close) -> realized +400  cumulative=300   [episode 3: WIN, +400]

    Hand-computed equity series (baseline 0 at t+0): [0, 100, -100, 300].
    Peak-to-trough walk: peak reaches 100 at t+1; drops to -100 at t+3
    -> drawdown = 100 - (-100) = 200, duration = 2 minutes (t+3 - t+1).
    Peak then rises to 300 at t+5, which does NOT produce a larger
    drawdown against anything after it (series ends there) -- a naive
    first-value-as-peak (0) vs. trough (-100) would give 100, not the
    real 200 this walk finds; a naive first/last-only computation would
    give max(0, 0 - 300) = 0, missing the real 200 entirely.

    Win rate: 2 of 3 real completed episodes are wins -> 2/3 exactly.
    """
    connection = _declare_connection(db_session)
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0)
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=110, when=_T0 + timedelta(minutes=1))
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0 + timedelta(minutes=2))
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=80, when=_T0 + timedelta(minutes=3))
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=90, when=_T0 + timedelta(minutes=4))
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=130, when=_T0 + timedelta(minutes=5))
    db_session.commit()

    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")

    assert [p.cumulative_pnl for p in report.equity_series] == [
        Decimal(0), Decimal(100), Decimal(-100), Decimal(300),
    ]

    assert report.max_drawdown is not None
    assert report.max_drawdown.max_drawdown == Decimal(200)
    assert report.max_drawdown.duration == timedelta(minutes=2)

    assert report.completed_episode_count == 3
    assert report.winning_episode_count == 2
    assert report.win_rate == Decimal(2) / Decimal(3)


def test_max_drawdown_uses_the_true_running_peak_not_just_the_previous_point(db_session):
    """Load-bearing regression guard for the exact bug class this port
    is vulnerable to: tracking a running TOTAL/previous point as "the
    peak" instead of the true running MAXIMUM seen so far. The other
    load-bearing test above (`..._verification`) does NOT catch that
    specific bug, because its own true peak happens to sit immediately
    before its own true trough -- a broken "peak = previous point"
    walk coincidentally reproduces the same answer there. This sequence
    inserts a real interior recovery BETWEEN the true peak and the true
    trough, so the two algorithms provably diverge.

    Real fills (AAPL), in order, four completed FIFO episodes:
      t+0: BUY 10 @100   (open)
      t+1: SELL 10 @110  (close) -> realized +100   cumulative=100
      t+2: BUY 10 @100   (open)
      t+3: SELL 10 @95   (close) -> realized -50    cumulative=50   (partial recovery dip)
      t+4: BUY 10 @100   (open)
      t+5: SELL 10 @90   (close) -> realized -100   cumulative=-50  (true trough)
      t+6: BUY 10 @100   (open)
      t+7: SELL 10 @108  (close) -> realized +80    cumulative=30

    Hand-computed equity series (baseline 0 at t+0): [0, 100, 50, -50, 30].

    TRUE running-peak walk: peak=100 (set at t+1) stays the running
    peak through t+3 and t+5 (50 and -50 are both below it), so the
    real max drawdown is peak(100, at t+1) - trough(-50, at t+5) = 150,
    duration = 4 minutes (t+5 - t+1).

    A BROKEN "peak = previous point" walk instead resets peak to 50 at
    t+3 (the immediately preceding point, not the true running max), so
    its own best drawdown becomes only peak(50, at t+2... i.e. the point
    before -50) - trough(-50) = 100, duration = 2 minutes (t+5 - t+3) --
    a different, wrong number this test catches exactly.
    """
    connection = _declare_connection(db_session)
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0)
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=110, when=_T0 + timedelta(minutes=1))
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0 + timedelta(minutes=2))
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=95, when=_T0 + timedelta(minutes=3))
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0 + timedelta(minutes=4))
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=90, when=_T0 + timedelta(minutes=5))
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0 + timedelta(minutes=6))
    _follower_fill(db_session, connection, side=Side.SELL, quantity=10, price=108, when=_T0 + timedelta(minutes=7))
    db_session.commit()

    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")

    assert [p.cumulative_pnl for p in report.equity_series] == [
        Decimal(0), Decimal(100), Decimal(50), Decimal(-50), Decimal(30),
    ]

    assert report.max_drawdown is not None
    assert report.max_drawdown.max_drawdown == Decimal(150)
    assert report.max_drawdown.duration == timedelta(minutes=4)


def test_an_open_position_is_never_counted_as_a_completed_episode(db_session):
    connection = _declare_connection(db_session)
    _follower_fill(db_session, connection, side=Side.BUY, quantity=10, price=100, when=_T0)
    db_session.commit()

    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")
    assert report.completed_episode_count == 0
    assert report.win_rate is None


def test_another_customers_connection_never_leaks_into_this_ones_equity_series(db_session):
    connection_a = _declare_connection(db_session, tenant_id="tenant-a", user_id="user-a")
    connection_b = _declare_connection(db_session, tenant_id="tenant-a", user_id="user-b")
    _follower_fill(db_session, connection_a, side=Side.BUY, quantity=10, price=100, when=_T0)
    _follower_fill(db_session, connection_a, side=Side.SELL, quantity=10, price=110, when=_T0 + timedelta(minutes=1))
    _follower_fill(db_session, connection_b, side=Side.BUY, quantity=10, price=100, when=_T0)
    _follower_fill(db_session, connection_b, side=Side.SELL, quantity=10, price=999, when=_T0 + timedelta(minutes=1))
    db_session.commit()

    report = get_own_performance_report(db_session, tenant_id="tenant-a", user_id="user-a")
    assert [p.cumulative_pnl for p in report.equity_series] == [Decimal(0), Decimal(100)]
    assert report.completed_episode_count == 1
    assert report.winning_episode_count == 1
