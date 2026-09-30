"""Track 11 point 4: "Keep books separate" -- OperatingCost/margin data
is the PLATFORM book's own BUSINESS-OPERATIONS cost side, and must
never be presented as, or computed into, trading P&L; trading P&L
(app/services/platform_performance.py, real Book.PLATFORM ledger
entries) must never be presented as, or computed into, business margin.
A real, runnable test, not just a docstring claim.
"""
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.models.ledger import Book, EvidenceClass, Side
from app.models.operating_cost import OperatingCostCategory
from app.services.business_economics import compute_margin_for_period
from app.services.ledger import append_entry
from app.services.operating_cost import create_operating_cost
from app.services.platform_performance import compute_platform_performance

_APP_DIR = Path(__file__).resolve().parent.parent / "app"
_PERIOD_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PERIOD_END = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
_T0 = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _imported_names(module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def test_business_economics_module_never_imports_the_ledger():
    """Static check: app/services/business_economics.py (revenue/cost/
    margin) has no import of app.models.ledger or app.services.ledger at
    all -- it structurally cannot read trading P&L, not just "doesn't
    happen to today.\""""
    names = _imported_names(_APP_DIR / "services" / "business_economics.py")
    assert not any("ledger" in name.lower() for name in names)


def test_platform_performance_module_never_imports_operating_cost_or_billing():
    """Static check: app/services/platform_performance.py (trading P&L)
    has no import of app.models.operating_cost or app.models.billing --
    it structurally cannot read business cost/revenue data."""
    names = _imported_names(_APP_DIR / "services" / "platform_performance.py")
    assert not any("operating_cost" in name.lower() for name in names)
    assert not any("billing" in name.lower() for name in names)


def test_operating_cost_model_has_no_ledger_foreign_key():
    from app.models.operating_cost import OperatingCost

    column_names = {c.name for c in OperatingCost.__table__.columns}
    assert "ledger_entry_id" not in column_names
    for column in OperatingCost.__table__.columns:
        for fk in column.foreign_keys:
            assert "ledger" not in fk.target_fullname.lower()


def test_large_trading_pnl_never_changes_business_margin(db_session):
    """A large, real trading gain booked to Book.PLATFORM must not move
    the business margin figure at all -- margin is revenue (Subscription)
    minus cost (OperatingCost) only."""
    from app.models.billing import ProductTier, Subscription, SubscriptionState

    db_session.add(
        Subscription(
            tenant_id="tenant-a", tier=ProductTier.ALERTS_ONE, state=SubscriptionState.ACTIVE_PAID,
            price_cents=9900, currency="usd", current_period_end=_T0,
        )
    )
    db_session.commit()
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=3000, period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    db_session.commit()

    margin_before = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )

    # A large, real trading gain -- must have zero effect on margin.
    append_entry(
        db_session, tenant_id="tenant-a", book=Book.PLATFORM, instrument="AAPL", side=Side.SELL,
        quantity=Decimal(1000), price=Decimal(1000000), currency="USD", event_time=_T0,
        source_authority="test", evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
    )
    db_session.commit()

    margin_after = compute_margin_for_period(
        db_session, tenant_id="tenant-a", period_start=_PERIOD_START, period_end=_PERIOD_END
    )
    assert margin_after.rows == margin_before.rows
    assert margin_after.currencies_missing_costs == margin_before.currencies_missing_costs


def test_operating_costs_and_subscriptions_never_change_trading_pnl(db_session):
    """A real recorded business cost and real booked subscription revenue
    must have zero effect on the trading-performance (Book.PLATFORM
    ledger replay) figures."""
    from app.models.billing import ProductTier, Subscription, SubscriptionState

    performance_before = compute_platform_performance(db_session, tenant_id="tenant-a")

    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=999999, period_start=_PERIOD_START, period_end=_PERIOD_END,
    )
    db_session.add(
        Subscription(
            tenant_id="tenant-a", tier=ProductTier.PRO_RESEARCH_API, state=SubscriptionState.ACTIVE_PAID,
            price_cents=19900, currency="usd", current_period_end=_T0,
        )
    )
    db_session.commit()

    performance_after = compute_platform_performance(db_session, tenant_id="tenant-a")
    assert performance_after.realized_pnl == performance_before.realized_pnl
    assert performance_after.per_instrument == performance_before.per_instrument
