"""WP-32b: Leverage cap enforcement and paper broker margin/equity.

B-11: Enforce max_gross_leverage in capital allocation.
- When configured, reject entries if confirmed + pending + new > leverage * (equity - maint)
- Message names all four numbers

Paper broker simulator rules (per app/brokers/paper.py docstring):
- equity = cash + Σ position × last simulated or last fill price
- maintenance_margin = 0.5 × |short notional|
- BUY never takes cash below 0 → reject with "insufficient paper cash"
- Short (SELL beyond owned) requires margin equal to notional → reject if cash insufficient
"""
import pytest
from pathlib import Path

from app.models import Signal, DestinationAccount, OrderResult, Side, OrderStatus
from app.engine import SignalCopierEngine as Engine
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.routing import RoutingConfig


@pytest.mark.asyncio
async def test_paper_broker_equity_calculation_empty(tmp_path: Path):
    """Paper broker equity = cash + position values. Empty account starts at STARTING_CASH."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="test", broker="paper", multiplier=1.0)

    balance = await broker.get_account_balance(account)
    assert balance is not None
    assert balance.cash == PaperBroker.STARTING_CASH
    assert balance.equity == PaperBroker.STARTING_CASH
    assert balance.maintenance_margin == 0.0


@pytest.mark.asyncio
async def test_paper_broker_equity_after_fill(tmp_path: Path):
    """After a fill, equity = cash + position value at fill price."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="test", broker="paper", multiplier=1.0)

    signal = Signal(id="sig1", symbol="AAPL", side=Side.BUY, quantity=100.0, price=150.0, source="test")
    result = await broker.place_order(signal, account, 100.0, "AAPL")

    assert result.status == OrderStatus.FILLED

    balance = await broker.get_account_balance(account)
    assert balance is not None
    # equity = 100,000 - (100 * 150) = 85,000
    # position value = 100 * 150 = 15,000
    # equity = 85,000 + 15,000 = 100,000
    assert balance.cash == 100_000 - (100 * 150)
    assert balance.equity == 100_000  # unchanged, just paper
    assert balance.maintenance_margin == 0.0


@pytest.mark.asyncio
async def test_paper_broker_short_position_maintenance_margin(tmp_path: Path):
    """Maintenance margin = 0.5 × |short notional|."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="test", broker="paper", multiplier=1.0)

    # Sell 100 shares at $150 (shorting 100)
    signal = Signal(id="sig1", symbol="AAPL", side=Side.SELL, quantity=100.0, price=150.0, source="test")
    result = await broker.place_order(signal, account, 100.0, "AAPL")

    assert result.status == OrderStatus.FILLED

    balance = await broker.get_account_balance(account)
    assert balance is not None
    # Short notional = 100 * 150 = 15,000
    # Maintenance margin = 0.5 * 15,000 = 7,500
    assert balance.maintenance_margin == 7_500.0


@pytest.mark.asyncio
async def test_paper_broker_buy_insufficient_cash(tmp_path: Path):
    """BUY that would take cash below 0 is rejected."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="test", broker="paper", multiplier=1.0)

    # Try to buy more than we have cash for (STARTING_CASH = 100,000)
    # BUY 1000 @ $150 = 150,000 > 100,000
    signal = Signal(id="sig1", symbol="AAPL", side=Side.BUY, quantity=1000.0, price=150.0, source="test")
    result = await broker.place_order(signal, account, 1000.0, "AAPL")

    assert result.status == OrderStatus.REJECTED
    assert "insufficient paper cash" in result.message


@pytest.mark.asyncio
async def test_paper_broker_short_insufficient_margin_cash(tmp_path: Path):
    """SELL beyond owned that would exceed available margin cash is rejected."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="test", broker="paper", multiplier=1.0)

    # First, buy 100 @ $150 to have some cash left
    signal1 = Signal(id="sig1", symbol="AAPL", side=Side.BUY, quantity=100.0, price=150.0, source="test")
    result1 = await broker.place_order(signal1, account, 100.0, "AAPL")
    assert result1.status == OrderStatus.FILLED
    # Cash now: 100,000 - 15,000 = 85,000

    # Now try to short 1000 shares @ $150 = 150,000 margin required > 85,000 available
    signal2 = Signal(id="sig2", symbol="AAPL", side=Side.SELL, quantity=1000.0, price=150.0, source="test")
    result2 = await broker.place_order(signal2, account, 1000.0, "AAPL")

    # Short 900 would need 900 * 150 = 135,000 margin > 85,000 cash
    assert result2.status == OrderStatus.REJECTED
    assert "insufficient cash for short margin" in result2.message


@pytest.mark.asyncio
async def test_leverage_cap_with_short_maintenance_margin(tmp_path: Path):
    """Leverage cap accounts for maintenance margin: max = leverage * (equity - maint)."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    account = DestinationAccount(
        account_id="test_acct",
        broker="paper",
        multiplier=1.0,
        max_gross_leverage=2.0,
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])
    engine = Engine(
        store=store,
        brokers={"paper": broker},
        routing=routing,
    )

    # First: short 100 @ $600 = 60,000 notional
    # Maintenance = 0.5 * 60,000 = 30,000
    # equity = 100,000 + 60,000 cash (from short) = 160,000
    # (but short position value = -60,000, so equity = 100,000 cash)
    signal1 = Signal(
        id="sig1",
        symbol="AAPL",
        side=Side.SELL,
        quantity=100.0,
        price=600.0,
        source="test",
    )
    admitted1, notional1, _ = await engine._try_reserve_capital(account, signal1, 100.0)
    assert admitted1
    engine.capital_allocator.release(account.account_id, notional1)
    result1 = await broker.place_order(signal1, account, 100.0, "AAPL")
    assert result1.status == OrderStatus.FILLED

    # Check balance: maint margin should be 30,000
    balance = await broker.get_account_balance(account)
    assert balance is not None
    # cash = 100,000 + 60,000 (from short) = 160,000
    # short position value = -60,000
    # equity = 160,000 - 60,000 = 100,000
    # maintenance_margin = 0.5 * 60,000 = 30,000
    assert balance.cash == 160_000.0
    assert balance.equity == 100_000.0
    assert balance.maintenance_margin == 30_000.0

    # Now leverage cap is: 2.0 * (100,000 - 30,000) = 140,000
    # Confirmed = 60,000, so we can add up to 80,000 more
    signal2 = Signal(
        id="sig2",
        symbol="MSFT",
        side=Side.BUY,
        quantity=100.0,
        price=600.0,
        source="test",
    )
    admitted2, notional2, rejection2 = await engine._try_reserve_capital(account, signal2, 100.0)
    # 60,000 + 60,000 = 120,000 < 140,000, should be admitted
    assert admitted2, f"Entry should be admitted, got: {rejection2}"


@pytest.mark.asyncio
async def test_leverage_cap_two_small_entries(tmp_path: Path):
    """With leverage = 2.0 and sufficient equity, two entries under 2x leverage are admitted."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()

    # Account with leverage = 2.0 (100% leverage allowed)
    # Set starting cash to 200,000 to have enough buying power
    broker._cash["test_acct"] = 200_000.0

    account = DestinationAccount(
        account_id="test_acct",
        broker="paper",
        multiplier=1.0,
        max_gross_leverage=2.0,
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])
    engine = Engine(
        store=store,
        brokers={"paper": broker},
        routing=routing,
    )

    # First entry: 60,000 @ 600
    signal1 = Signal(
        id="sig1",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=600.0,
        source="test",
    )
    admitted1, notional1, _ = await engine._try_reserve_capital(account, signal1, 100.0)
    assert admitted1
    engine.capital_allocator.release(account.account_id, notional1)
    result1 = await broker.place_order(signal1, account, 100.0, "AAPL")
    assert result1.status == OrderStatus.FILLED
    # store.save_order_result(result1, reserved_notional=notional1)

    # Second entry: another 60,000
    # equity = 200k - 60k = 140k
    # max = 2.0 * 140k = 280k
    # confirmed = 60k, new = 60k -> total = 120k < 280k, should be admitted
    signal2 = Signal(
        id="sig2",
        symbol="MSFT",
        side=Side.BUY,
        quantity=100.0,
        price=600.0,
        source="test",
    )
    admitted2, notional2, rejection2 = await engine._try_reserve_capital(account, signal2, 100.0)
    assert admitted2, f"Second entry should be admitted with 2.0 leverage, got: {rejection2}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
