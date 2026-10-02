"""Tests for WP-22: client order ids and ledger-driven resolution."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models import Signal, Side, DestinationAccount, OrderStatus, OrderResult
from app.brokers.base import BrokerAdapter
from app.capital_allocator import CapitalAllocator
from app.db import SignalStore
from app.reconciliation import OrderReconciler


class StubBrokerWithClientLookup(BrokerAdapter):
    """A stub broker that supports client_order_id lookup."""

    name = "stub"

    def __init__(self):
        self.placed_orders = {}  # maps client_order_id -> broker_order_id

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        """Stub: tracks client_order_id and returns a pending result."""
        if signal.client_order_id:
            self.placed_orders[signal.client_order_id] = f"broker-order-{signal.client_order_id[:8]}"
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=self.placed_orders.get(signal.client_order_id),
        )

    async def find_order_by_client_id(
        self, account: DestinationAccount, client_order_id: str
    ) -> str | None:
        """Look up order by client_order_id."""
        return self.placed_orders.get(client_order_id)


def test_signal_has_client_order_id_field():
    """Test that Signal has client_order_id field."""
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    assert signal.client_order_id is None

    signal.client_order_id = "test-key"
    assert signal.client_order_id == "test-key"


def test_broker_can_track_client_order_id():
    """Test that engine passes client_order_id to broker."""
    broker = StubBrokerWithClientLookup()

    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, client_order_id="entry:acc1:AAPL:sig123")
    account = DestinationAccount(account_id="acc1", broker="stub")

    # Simulate placing order with client_order_id
    import asyncio
    result = asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

    # Broker should have tracked it
    assert signal.client_order_id in broker.placed_orders
    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id is not None


def test_broker_lookup_by_client_id_found():
    """Test broker can look up order by client_order_id when it exists."""
    broker = StubBrokerWithClientLookup()
    account = DestinationAccount(account_id="acc1", broker="stub")

    # Pre-populate an order
    client_id = "entry:acc1:AAPL:sig123"
    broker.placed_orders[client_id] = "broker-order-123"

    # Look it up
    import asyncio
    result = asyncio.run(broker.find_order_by_client_id(account, client_id))

    assert result == "broker-order-123"


def test_broker_lookup_by_client_id_not_found():
    """Test broker returns None when order not found."""
    broker = StubBrokerWithClientLookup()
    account = DestinationAccount(account_id="acc1", broker="stub")

    import asyncio
    result = asyncio.run(broker.find_order_by_client_id(account, "nonexistent"))

    assert result is None


def test_broker_has_client_id_lookup_capability():
    """Test broker capability detection."""
    broker = StubBrokerWithClientLookup()
    assert broker.has_client_id_lookup_capability is True


def test_alpaca_sends_client_order_id(monkeypatch):
    """Test Alpaca adapter includes client_order_id in order payload."""
    from app.brokers.alpaca import AlpacaBroker

    broker = AlpacaBroker()

    # Mock the HTTP client
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.json.return_value = {"id": "broker-123", "status": "pending"}
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    broker._client = mock_client

    # Mock credentials
    monkeypatch.setenv("ALPACA_MAIN_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_MAIN_API_SECRET", "test-secret")

    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        client_order_id="entry:acc1:AAPL:sig123"
    )
    account = DestinationAccount(account_id="main", broker="alpaca")

    import asyncio
    asyncio.run(broker.place_order(signal, account, 10.0, "AAPL"))

    # Check the payload sent to Alpaca
    call_args = mock_client.post.call_args
    payload = call_args.kwargs.get("json", {})
    assert "client_order_id" in payload
    assert payload["client_order_id"] == "entry:acc1:AAPL:sig123"


def test_ccxt_sends_client_order_id():
    """Test CCXT adapter includes clientOrderId in order params."""
    pytest.importorskip("ccxt")
    from app.brokers.ccxt_broker import CCXTBroker

    broker = CCXTBroker(exchange_id="binance")

    # Mock exchange
    mock_exchange = AsyncMock()
    mock_order = {"id": "broker-123", "status": "open"}
    mock_exchange.create_order = AsyncMock(return_value=mock_order)
    mock_exchange.has = {"createMarketOrder": True}
    broker._exchange_for = lambda account: mock_exchange

    signal = Signal(
        source="test",
        symbol="BTC/USDT",
        side=Side.BUY,
        client_order_id="entry:acc1:BTC/USDT:sig123"
    )
    account = DestinationAccount(account_id="main", broker="ccxt")

    import asyncio
    asyncio.run(broker.place_order(signal, account, 0.1, "BTC/USDT"))

    # Check params sent to exchange
    call_args = mock_exchange.create_order.call_args
    params = call_args.kwargs.get("params", {})
    assert "clientOrderId" in params
    assert params["clientOrderId"] == "entry:acc1:BTC/USDT:sig123"


@pytest.mark.asyncio
async def test_reconcile_unknown_submissions_found_order(tmp_path):
    """Test reconciler resolves UNKNOWN_AMBIGUOUS when order is found by client_id."""
    from app.models import CommandType, UncertaintyState

    # Create test database
    store = SignalStore(str(tmp_path / "test.db"))

    # Create a test broker with lookup capability
    broker = StubBrokerWithClientLookup()
    brokers = {"stub": broker}

    # Pre-place an order so lookup will find it
    client_id = "entry:acc1:AAPL:sig123"
    broker.placed_orders[client_id] = "broker-order-xyz"

    # Create a command ledger entry in UNKNOWN_AMBIGUOUS state
    _ = store.open_command_ledger_entry(
        idempotency_key=client_id,
        command_type=CommandType.ENTRY,
        account_id="acc1",
        environment="test",
        request_fingerprint="fp123",
    )
    store.mark_command_ledger_outcome(
        client_id,
        uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
        terminal_evidence={"signal_id": "sig123", "reserved_notional": 1000.0},
    )

    # Create reconciler and run unknown submissions pass
    reconciler = OrderReconciler(store, brokers)
    resolved = await reconciler._reconcile_unknown_submissions()

    assert resolved == 1

    # Check that the entry was resolved as placed
    resolved_entry = store.get_command_ledger_entry(client_id)
    assert resolved_entry.uncertainty_state == UncertaintyState.CONFIRMED
    assert resolved_entry.remote_identifiers.get("broker_order_id") == "broker-order-xyz"


@pytest.mark.asyncio
async def test_reconcile_unknown_submissions_not_found(tmp_path):
    """Test reconciler resolves UNKNOWN_AMBIGUOUS when order is not found."""
    from app.models import CommandType, UncertaintyState

    # Create test database
    store = SignalStore(str(tmp_path / "test.db"))

    # Create a test broker with lookup capability
    broker = StubBrokerWithClientLookup()
    brokers = {"stub": broker}

    # Don't pre-place the order, so lookup will fail
    client_id = "entry:acc1:AAPL:sig999"

    # Create a command ledger entry in UNKNOWN_AMBIGUOUS state
    _ = store.open_command_ledger_entry(
        idempotency_key=client_id,
        command_type=CommandType.ENTRY,
        account_id="acc1",
        environment="test",
        request_fingerprint="fp999",
    )
    store.mark_command_ledger_outcome(
        client_id,
        uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
        terminal_evidence={"signal_id": "sig999", "reserved_notional": 1000.0},
    )

    # Create reconciler with capital allocator to release reservation
    capital_allocator = CapitalAllocator(store)
    reconciler = OrderReconciler(store, brokers, capital_allocator=capital_allocator)

    resolved = await reconciler._reconcile_unknown_submissions()

    assert resolved == 1

    # Check that the entry was resolved as not placed
    resolved_entry = store.get_command_ledger_entry(client_id)
    assert resolved_entry.uncertainty_state == UncertaintyState.REJECTED_CONFIRMED
    assert resolved_entry.terminal_evidence.get("resolution") == "not_placed"
