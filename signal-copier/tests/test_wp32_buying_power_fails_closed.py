"""WP-32: Buying power fails closed; leverage cap; alembic revision 0043.

B-07/B-11/B-16: buying power fails closed; leverage cap.

Requirements:
- B-07: Fail closed for accounts whose adapter reports no buying-power
  figure AND have no ceiling; skip (rely on ceiling) if ceiling is
  configured; use buying-power when available.
- B-11: Add max_gross_leverage support (currently unimplemented).
- B-16: Treat filled_price <= 0 as unresolved in compute_account_economics
  and make PaperBroker.place_order reject priceless signals instead of 0.0.

Tests must pass with paper broker (which reports buying_power=cash) and
real brokers (which may or may not report buying_power).
"""
import pytest
from pathlib import Path

from app.models import Signal, DestinationAccount, OrderResult, Side
from app.engine import SignalCopierEngine as Engine
from app.brokers.base import BrokerAdapter, AccountBalance
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.routing import RoutingConfig


class MockBrokerNoBalance(BrokerAdapter):
    """Mock broker that has_balance_capability=False (e.g., CCXT spot)."""

    @property
    def has_balance_capability(self) -> bool:
        return False

    async def place_order(self, account: DestinationAccount, signal: Signal, quantity: float) -> OrderResult | None:
        return None

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side
    ) -> OrderResult | None:
        return None

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        """Base implementation: returns None."""
        return None


class MockBrokerReportsNoBuyingPower(BrokerAdapter):
    """Mock broker that reports balance but no buying_power (e.g., real
    IBKR adapter that returns cash but not buying_power)."""

    def __init__(self, cash: float = 10000.0):
        self.cash = cash

    @property
    def has_balance_capability(self) -> bool:
        return True

    async def place_order(self, account: DestinationAccount, signal: Signal, quantity: float) -> OrderResult | None:
        return None

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side
    ) -> OrderResult | None:
        return None

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        """Reports cash but no buying_power."""
        return AccountBalance(account_id=account.account_id, cash=self.cash, buying_power=None)


@pytest.mark.asyncio
async def test_buying_power_fails_closed_no_balance_capability_no_ceiling(tmp_path: Path):
    """B-07: Account with no balance capability and no ceiling is rejected."""
    store = SignalStore(tmp_path / "test.db")

    # Set up account without ceiling
    account = DestinationAccount(
        account_id="test_acct",
        broker="mock_no_balance",
        multiplier=1.0,
        max_notional_exposure=None,  # No ceiling
        risk_percent_of_equity=None,  # No ceiling
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])

    engine = Engine(
        store=store,
        brokers={"mock_no_balance": MockBrokerNoBalance()},
        routing=routing,
    )

    signal = Signal(
        id="test_sig",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=150.0,
        source="test",
    )

    # The buying power check should FAIL CLOSED (reject) because:
    # - broker.has_balance_capability is False
    # - account has no ceiling configured
    bp_ok, bp_rejection = await engine._check_buying_power(account, signal, 10.0)
    assert not bp_ok, "Should reject when broker has no balance capability and no ceiling"
    assert bp_rejection is not None
    assert "no verified balance capability" in bp_rejection.message or "failing closed" in bp_rejection.message


@pytest.mark.asyncio
async def test_buying_power_skipped_no_balance_capability_with_ceiling(tmp_path: Path):
    """B-07: Account with no balance capability but with ceiling is skipped."""
    store = SignalStore(tmp_path / "test.db")

    # Set up account WITH ceiling
    account = DestinationAccount(
        account_id="test_acct",
        broker="mock_no_balance",
        multiplier=1.0,
        max_notional_exposure=100000.0,  # Has ceiling
        risk_percent_of_equity=None,
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])

    engine = Engine(
        store=store,
        brokers={"mock_no_balance": MockBrokerNoBalance()},
        routing=routing,
    )

    signal = Signal(
        id="test_sig",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=150.0,
        source="test",
    )

    # The buying power check should SKIP because:
    # - broker.has_balance_capability is False
    # - account HAS ceiling configured (so rely on that)
    bp_ok, bp_rejection = await engine._check_buying_power(account, signal, 10.0)
    assert bp_ok, "Should skip when broker has no balance capability but ceiling is configured"
    assert bp_rejection is None


@pytest.mark.asyncio
async def test_buying_power_fails_closed_no_buying_power_reported_no_ceiling(tmp_path: Path):
    """B-07: Account where broker reports no buying_power and no ceiling is rejected."""
    store = SignalStore(tmp_path / "test.db")

    # Set up account without ceiling
    account = DestinationAccount(
        account_id="test_acct",
        broker="mock_no_bp",
        multiplier=1.0,
        max_notional_exposure=None,  # No ceiling
        risk_percent_of_equity=None,  # No ceiling
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])

    engine = Engine(
        store=store,
        brokers={"mock_no_bp": MockBrokerReportsNoBuyingPower()},
        routing=routing,
    )

    signal = Signal(
        id="test_sig",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=150.0,
        source="test",
    )

    # The buying power check should FAIL CLOSED (reject) because:
    # - balance.buying_power is None
    # - account has no ceiling configured
    bp_ok, bp_rejection = await engine._check_buying_power(account, signal, 10.0)
    assert not bp_ok, "Should reject when broker reports no buying_power and no ceiling"
    assert bp_rejection is not None
    assert "buying_power" in bp_rejection.message or "failing closed" in bp_rejection.message


@pytest.mark.asyncio
async def test_buying_power_skipped_no_buying_power_reported_with_ceiling(tmp_path: Path):
    """B-07: Account where broker reports no buying_power but has ceiling is skipped."""
    store = SignalStore(tmp_path / "test.db")

    # Set up account WITH ceiling
    account = DestinationAccount(
        account_id="test_acct",
        broker="mock_no_bp",
        multiplier=1.0,
        max_notional_exposure=100000.0,  # Has ceiling
        risk_percent_of_equity=None,
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])

    engine = Engine(
        store=store,
        brokers={"mock_no_bp": MockBrokerReportsNoBuyingPower()},
        routing=routing,
    )

    signal = Signal(
        id="test_sig",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=150.0,
        source="test",
    )

    # The buying power check should SKIP because:
    # - balance.buying_power is None
    # - account HAS ceiling configured (so rely on that)
    bp_ok, bp_rejection = await engine._check_buying_power(account, signal, 10.0)
    assert bp_ok, "Should skip when broker reports no buying_power but ceiling is configured"
    assert bp_rejection is None


@pytest.mark.asyncio
async def test_buying_power_check_passed_paper_broker_no_ceiling(tmp_path: Path):
    """Paper broker reports buying_power=cash, so it works even without ceiling."""
    store = SignalStore(tmp_path / "test.db")

    # Set up account without ceiling
    account = DestinationAccount(
        account_id="test_acct",
        broker="paper",
        multiplier=1.0,
        max_notional_exposure=None,  # No ceiling
        risk_percent_of_equity=None,  # No ceiling
    )

    routing = RoutingConfig(accounts={account.account_id: account}, rules=[])

    broker = PaperBroker()
    engine = Engine(
        store=store,
        brokers={"paper": broker},
        routing=routing,
    )

    # Request should pass because paper broker reports buying_power
    signal = Signal(
        id="test_sig",
        symbol="AAPL",
        side=Side.BUY,
        quantity=10.0,
        price=150.0,
        source="test",
    )

    bp_ok, bp_rejection = await engine._check_buying_power(account, signal, 10.0)
    assert bp_ok, "Paper broker reports buying_power, so check should pass"
    assert bp_rejection is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
