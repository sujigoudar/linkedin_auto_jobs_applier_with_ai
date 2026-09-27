"""app/provider_value.py: FIFO-lot P&L attribution per (source, analyst,
asset_class), replayed from the confirmed execution journal across every
account -- distinct from app/economics.py's per-account blended-average
replay because two providers can share one account/symbol position."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import SignalStore
from app.models import AssetClass, OrderResult, OrderStatus, Side, Signal
from app.provider_value import compute_provider_value, compute_provider_value_report


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _fill(
    store,
    account_id,
    symbol,
    side,
    quantity,
    price,
    when,
    *,
    source="test",
    analyst=None,
    asset_class=AssetClass.EQUITY,
):
    signal = Signal(source=source, symbol=symbol, side=side, analyst=analyst, asset_class=asset_class)
    store.save_signal(signal)
    result = OrderResult(
        account_id=account_id,
        status=OrderStatus.FILLED,
        signal_id=signal.id,
        filled_quantity=quantity,
        filled_price=price,
        executed_at=when,
    )
    store.save_order_result(result, broker="paper", symbol=symbol, side=side)


def test_simple_round_trip_attributes_pnl_to_the_entry_provider(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="alice_calls")
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), source="alice_calls")

    values = compute_provider_value(store)
    pv = values[("alice_calls", None, "equity")]

    assert pv.realized_pnl == pytest.approx(100.0)
    assert pv.closing_fills == 1
    assert pv.winning_closing_fills == 1
    assert pv.win_rate == 1.0
    assert pv.profit_factor is None  # no losing trade to divide by yet


def test_losing_trade_reduces_profit_factor(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="bob_signals")
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 90.0, t0 + timedelta(minutes=1), source="bob_signals")

    pv = compute_provider_value(store)[("bob_signals", None, "equity")]

    assert pv.realized_pnl == pytest.approx(-100.0)
    assert pv.win_rate == 0.0
    assert pv.profit_factor == 0.0  # gross_profit=0, gross_loss=100 -> 0/100


def test_two_providers_sharing_one_account_symbol_are_attributed_separately(store):
    """The core case app/economics.py's blended average cannot answer:
    provider A enters first at 100, provider B adds at 120 -- a single
    closing fill of the FULL 20 shares at 130 must realize provider A's
    10 shares at (130-100)=30/share and provider B's 10 shares at
    (130-120)=10/share, via FIFO (oldest entry first), not a blended
    average cost that would erase which provider contributed what."""
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="provider_a")
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 120.0, t0 + timedelta(minutes=1), source="provider_b")
    _fill(store, "acct1", "AAPL", Side.SELL, 20.0, 130.0, t0 + timedelta(minutes=2), source="close_signal")

    values = compute_provider_value(store)

    pv_a = values[("provider_a", None, "equity")]
    pv_b = values[("provider_b", None, "equity")]
    assert pv_a.realized_pnl == pytest.approx(300.0)  # 10 * (130 - 100)
    assert pv_b.realized_pnl == pytest.approx(100.0)  # 10 * (130 - 120)
    # The closing order's OWN signal (source="close_signal") gets no
    # credit or blame at all -- realized P&L is attributed to whichever
    # lot(s) it drew down, never to whoever triggered the close.
    assert ("close_signal", None, "equity") not in values


def test_partial_close_only_consumes_the_oldest_lot_first(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="provider_a")
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 120.0, t0 + timedelta(minutes=1), source="provider_b")
    # Closes only 10 shares -- FIFO means this must be entirely provider_a's lot.
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 130.0, t0 + timedelta(minutes=2), source="close_signal")

    values = compute_provider_value(store)

    assert values[("provider_a", None, "equity")].realized_pnl == pytest.approx(300.0)
    # provider_b's lot is untouched, still open -- it opened an entry but
    # nothing has closed against it yet.
    assert values[("provider_b", None, "equity")].closing_fills == 0
    assert values[("provider_b", None, "equity")].realized_pnl == 0.0


def test_flip_through_flat_attributes_the_remainder_to_the_flipping_signal(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="provider_a")
    # Sells 15 -- closes provider_a's 10 (realizing P&L for provider_a),
    # then the remaining 5 opens a fresh SHORT lot for provider_b.
    _fill(store, "acct1", "AAPL", Side.SELL, 15.0, 110.0, t0 + timedelta(minutes=1), source="provider_b")
    # Buys 5 back to close provider_b's fresh short.
    _fill(store, "acct1", "AAPL", Side.BUY, 5.0, 105.0, t0 + timedelta(minutes=2), source="close_signal")

    values = compute_provider_value(store)

    assert values[("provider_a", None, "equity")].realized_pnl == pytest.approx(100.0)  # 10 * (110-100)
    pv_b = values[("provider_b", None, "equity")]
    assert pv_b.entries_opened == 1  # the flipped remainder's own fresh short lot
    assert pv_b.realized_pnl == pytest.approx(25.0)  # short 5 @ 110, bought back @ 105 -> 5*(110-105)


def test_analyst_and_no_analyst_are_grouped_separately(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="shared_channel", analyst="trader_x")
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), source="shared_channel", analyst="trader_x")
    _fill(store, "acct1", "MSFT", Side.BUY, 5.0, 200.0, t0 + timedelta(minutes=2), source="shared_channel")
    _fill(store, "acct1", "MSFT", Side.SELL, 5.0, 210.0, t0 + timedelta(minutes=3), source="shared_channel")

    values = compute_provider_value(store)

    assert ("shared_channel", "trader_x", "equity") in values
    assert ("shared_channel", None, "equity") in values
    assert values[("shared_channel", "trader_x", "equity")].realized_pnl == pytest.approx(100.0)
    assert values[("shared_channel", None, "equity")].realized_pnl == pytest.approx(50.0)


def test_asset_class_breaks_out_separately_even_for_the_same_source(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="multi_asset", asset_class=AssetClass.EQUITY)
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), source="multi_asset", asset_class=AssetClass.EQUITY)
    _fill(store, "crypto_acct", "BTC/USDT", Side.BUY, 1.0, 50000.0, t0 + timedelta(minutes=2), source="multi_asset", asset_class=AssetClass.CRYPTO)
    _fill(store, "crypto_acct", "BTC/USDT", Side.SELL, 1.0, 51000.0, t0 + timedelta(minutes=3), source="multi_asset", asset_class=AssetClass.CRYPTO)

    values = compute_provider_value(store)

    assert values[("multi_asset", None, "equity")].realized_pnl == pytest.approx(100.0)
    assert values[("multi_asset", None, "crypto")].realized_pnl == pytest.approx(1000.0)


def test_unresolved_or_invalid_fills_are_skipped_not_counted_as_zero(store):
    signal = Signal(source="broken", symbol="AAPL", side=Side.BUY)
    store.save_signal(signal)
    # A FILLED row with no filled_quantity/filled_price -- same "incomplete
    # stays incomplete" rule as app/economics.py.
    result = OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id)
    store.save_order_result(result, broker="paper", symbol="AAPL", side=Side.BUY)

    values = compute_provider_value(store)

    assert ("broken", None, "equity") not in values


# --- Report (verdict/subscription combination) ---


def test_report_filters_by_source_analyst_and_asset_class(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="provider_a")
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), source="provider_a")
    _fill(store, "acct1", "MSFT", Side.BUY, 5.0, 200.0, t0 + timedelta(minutes=2), source="provider_b")
    _fill(store, "acct1", "MSFT", Side.SELL, 5.0, 190.0, t0 + timedelta(minutes=3), source="provider_b")

    report = compute_provider_value_report(store, source="provider_a")

    assert len(report) == 1
    assert report[0]["source"] == "provider_a"


def test_report_attaches_subscription_and_verdict_when_one_exists(store):
    t0 = datetime.now(timezone.utc)
    for i in range(12):
        _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0 + timedelta(minutes=2 * i), source="paid_provider")
        _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=2 * i + 1), source="paid_provider")
    store.upsert_provider_subscription(
        "paid_provider", cost_amount=50.0, billing_cycle="monthly", subscribed_since=t0.date().isoformat()
    )

    report = compute_provider_value_report(store, source="paid_provider")

    assert len(report) == 1
    entry = report[0]
    assert entry["subscription"]["provider_id"] == "paid_provider"
    assert entry["provider_verdict"] == "keep"
    assert entry["provider_verdict_detail"]["net_value"] > 0


def test_report_shows_no_subscription_for_an_untracked_provider(store):
    t0 = datetime.now(timezone.utc)
    _fill(store, "acct1", "AAPL", Side.BUY, 10.0, 100.0, t0, source="free_provider")
    _fill(store, "acct1", "AAPL", Side.SELL, 10.0, 110.0, t0 + timedelta(minutes=1), source="free_provider")

    report = compute_provider_value_report(store, source="free_provider")

    assert report[0]["subscription"] is None
