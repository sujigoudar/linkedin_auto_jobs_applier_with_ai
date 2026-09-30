"""app/account_economics_v2.py: the extended, "full economic account
view" P&L fields (fees/unrealized/NAV/TWR/slippage) -- built alongside
app/economics.py (never modifying it, see that pass's own autonomy-gate
note) and honestly reporting "unknown"/None wherever this schema
genuinely has no real data source, never a fabricated number."""
from datetime import datetime, timedelta, timezone

import pytest

from app.account_economics_v2 import compute_extended_account_economics
from app.db import SignalStore
from app.models import AccountBalance, OrderResult, OrderStatus, Side, Signal


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _fill(store, account_id, symbol, side, quantity, price, when, *, signal_price=None):
    signal = Signal(source="test", symbol=symbol, side=side, price=signal_price)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id, status=OrderStatus.FILLED, signal_id=signal.id,
        filled_quantity=quantity, filled_price=price, executed_at=when,
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


def test_realized_pnl_matches_economics_py_exactly(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1))

    extended = compute_extended_account_economics(store, "acct1")

    assert extended.gross_realized == pytest.approx(100.0)


def test_fees_and_financing_are_unknown_never_zero(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)

    extended = compute_extended_account_economics(store, "acct1")

    assert extended.fees == "unknown"
    assert extended.commissions == "unknown"
    assert extended.financing == "unknown"
    assert extended.net_realized is None


def test_unrealized_is_unavailable_with_no_lifecycle_manager(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)  # still open

    extended = compute_extended_account_economics(store, "acct1")

    assert extended.unrealized_gross is None
    assert "AAPL" in extended.unavailable_marks


def test_twr_is_always_unavailable(store):
    extended = compute_extended_account_economics(store, "acct1")
    assert extended.twr is None
    assert "equity time series" in extended.twr_unavailable_reason


def test_nav_and_equity_are_unavailable_without_a_broker_balance(store):
    extended = compute_extended_account_economics(store, "acct1")
    assert extended.nav is None
    assert extended.equity is None
    assert extended.account_data_source == "unavailable"


def test_nav_and_equity_come_from_a_real_supplied_broker_balance(store):
    balance = AccountBalance(account_id="acct1", cash=5000.0, equity=12000.0, buying_power=8000.0)
    extended = compute_extended_account_economics(store, "acct1", broker_balance=balance)

    assert extended.nav == pytest.approx(12000.0)
    assert extended.equity == pytest.approx(12000.0)
    assert extended.cash == pytest.approx(5000.0)
    assert extended.buying_power == pytest.approx(8000.0)
    assert extended.account_data_source == "broker_reported"


def test_slippage_computed_only_against_signals_with_a_real_reference_price(store):
    t0 = datetime.now(timezone.utc)
    # A BUY filled worse (higher) than the provider's reference price.
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 101.5, t0, signal_price=100.0)
    # A signal with no reference price at all -- must be excluded, not
    # treated as zero slippage.
    _fill(store, "acct1", "MSFT", Side.BUY, 5.0, 200.0, t0 + timedelta(minutes=1))

    extended = compute_extended_account_economics(store, "acct1")

    assert extended.slippage is not None
    assert extended.slippage.sample_count == 1
    assert extended.slippage.mean == pytest.approx(1.5)
    assert extended.implementation_shortfall is extended.slippage


def test_no_reference_prices_at_all_reports_slippage_as_none(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0)

    extended = compute_extended_account_economics(store, "acct1")
    assert extended.slippage is None


def test_to_dict_never_leaks_a_fabricated_zero(store):
    extended = compute_extended_account_economics(store, "acct1")
    d = extended.to_dict()
    assert d["realized"]["fees"] == "unknown"
    assert d["returns"]["twr"] is None
    assert d["account"]["nav"] is None
    assert d["customer"]["model_vs_platform_vs_follower"] == "not_applicable_in_signal_copier"
