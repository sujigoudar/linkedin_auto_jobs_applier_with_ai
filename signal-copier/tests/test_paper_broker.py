import pytest

from app.brokers.paper import PaperBroker
from app.models import AssetClass, DestinationAccount, OrderStatus, Signal, Side


@pytest.mark.asyncio
async def test_buy_then_sell_nets_position():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", multiplier=1.0)

    buy_signal = Signal(source="test", symbol="BTCUSDT", side=Side.BUY, asset_class=AssetClass.CRYPTO, price=100)
    result = await broker.place_order(buy_signal, account, quantity=1.0, symbol="BTC/USDT")
    assert result.status == OrderStatus.FILLED
    assert broker.positions["acct1"]["BTC/USDT"] == 1.0

    sell_signal = Signal(source="test", symbol="BTCUSDT", side=Side.SELL, asset_class=AssetClass.CRYPTO, price=110)
    await broker.place_order(sell_signal, account, quantity=0.4, symbol="BTC/USDT")
    assert broker.positions["acct1"]["BTC/USDT"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_close_zeroes_position():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    await broker.place_order(
        Signal(source="test", symbol="ETHUSDT", side=Side.BUY), account, quantity=2.0, symbol="ETH/USDT"
    )
    await broker.place_order(
        Signal(source="test", symbol="ETHUSDT", side=Side.CLOSE), account, quantity=0, symbol="ETH/USDT"
    )
    assert broker.positions["acct1"]["ETH/USDT"] == 0.0


def test_broker_name_matches_the_accounts_yaml_broker_field():
    assert PaperBroker().name == "paper"


@pytest.mark.asyncio
async def test_get_account_balance_starts_at_the_documented_starting_cash():
    """See PaperBroker's own class docstring: STARTING_CASH=100_000.0 is a
    real, genuinely-computed simulated balance (not fabricated) -- a
    fresh, untouched account must report exactly that, both as `cash`
    and as `buying_power` (this broker tracks no margin, so the two are
    the same figure)."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    balance = await broker.get_account_balance(account)
    assert balance is not None
    assert balance.cash == 100_000.0
    assert balance.buying_power == 100_000.0


@pytest.mark.asyncio
async def test_buy_fill_debits_cash_by_notional_plus_fee():
    """`place_order`'s BUY branch must SPEND cash -- `price * quantity`
    plus this broker's own documented per-fill fee -- never credit it.
    FEE_PER_FILL defaults to 0.0, so set a non-zero `fee_per_fill`
    explicitly to also catch a fee-sign mix-up (added instead of
    subtracted), not just a notional-sign mix-up."""
    broker = PaperBroker()
    broker.fee_per_fill = 2.5
    account = DestinationAccount(account_id="acct1", broker="paper")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, price=100.0), account, quantity=10.0, symbol="AAPL"
    )

    balance = await broker.get_account_balance(account)
    # 100_000 - (10 * 100) - 2.5 fee
    assert balance.cash == pytest.approx(100_000.0 - 1_000.0 - 2.5)


@pytest.mark.asyncio
async def test_sell_fill_credits_cash_by_notional_minus_fee():
    """`place_order`'s SELL branch must RECEIVE cash -- never spend it --
    minus the documented per-fill fee."""
    broker = PaperBroker()
    broker.fee_per_fill = 1.5
    account = DestinationAccount(account_id="acct1", broker="paper")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL, price=50.0), account, quantity=4.0, symbol="AAPL"
    )

    balance = await broker.get_account_balance(account)
    # 100_000 + (4 * 50) - 1.5 fee
    assert balance.cash == pytest.approx(100_000.0 + 200.0 - 1.5)


@pytest.mark.asyncio
async def test_fill_with_no_signal_price_reports_none_not_a_fabricated_price():
    """This module's own docstring: "Fills every order instantly at the
    signal's price (or 0.0 if none given)." A signal with no `price` set
    must report `filled_price is None` (and move zero cash), never a
    fabricated non-zero default."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    result = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=10.0, symbol="AAPL"
    )

    assert result.filled_price is None
    balance = await broker.get_account_balance(account)
    assert balance.cash == 100_000.0  # zero notional moved -- no price to fill at


@pytest.mark.asyncio
async def test_successive_fills_get_distinct_incrementing_broker_order_ids():
    """`broker_order_id=f"paper-{len(self.fills) + 1}"` must give every
    fill its own, never-repeated id -- a wrong offset could make a later
    fill collide with an earlier one's id, which downstream reconciliation
    treats as the SAME order."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    first = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )
    second = await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=1.0, symbol="AAPL"
    )

    assert first.broker_order_id == "paper-1"
    assert second.broker_order_id == "paper-2"
    assert first.broker_order_id != second.broker_order_id
