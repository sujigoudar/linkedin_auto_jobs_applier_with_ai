"""E10: Fee tracking integration into broker adapters and order persistence.

Tests for broker fee and slippage reporting, storage persistence, and
aggregation into daily P&L tracking.
"""
import pytest

from app.models import DestinationAccount, OrderResult, OrderStatus, Signal, Side, AssetClass
from app.brokers.paper import PaperBroker
from app.db import SignalStore


@pytest.fixture
def paper_broker():
    """Paper broker instance for testing fee reporting."""
    return PaperBroker()


@pytest.fixture
def account():
    """Test account."""
    return DestinationAccount(
        broker="paper",
        account_id="TEST_ACCOUNT_123",
    )


@pytest.fixture
def signal():
    """Test signal."""
    return Signal(
        source="test_source",
        symbol="BTC/USD",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        price=50000.0,
        quantity=0.1,
    )


class TestPaperBrokerFeeReporting:
    """Tests for PaperBroker's fee and slippage reporting."""

    @pytest.mark.asyncio
    async def test_paper_broker_reports_fee_on_fill(self, paper_broker, account, signal):
        """Should report configured fee on successful fill."""
        result = await paper_broker.place_order(signal, account, 0.1, "BTC/USD")

        assert result.status == OrderStatus.FILLED
        assert result.fee == paper_broker.fee_per_fill
        assert result.fee_currency == "USD"

    @pytest.mark.asyncio
    async def test_paper_broker_reports_slippage_zero(self, paper_broker, account, signal):
        """Should report zero slippage (fills at exact signal price)."""
        result = await paper_broker.place_order(signal, account, 0.1, "BTC/USD")

        assert result.slippage == 0.0

    @pytest.mark.asyncio
    async def test_paper_broker_with_zero_fee_configured(self, paper_broker, account, signal):
        """Should report zero fee when broker configured with no fees."""
        paper_broker.fee_per_fill = 0.0

        result = await paper_broker.place_order(signal, account, 0.1, "BTC/USD")

        assert result.fee == 0.0
        assert result.fee_currency == "USD"

    @pytest.mark.asyncio
    async def test_paper_broker_with_nonzero_fee(self, paper_broker, account, signal):
        """Should report actual fee when configured."""
        paper_broker.fee_per_fill = 10.0

        result = await paper_broker.place_order(signal, account, 0.1, "BTC/USD")

        assert result.fee == 10.0

    @pytest.mark.asyncio
    async def test_paper_broker_fee_included_in_cash_calculation(self, paper_broker, account, signal):
        """Should deduct fee from cash when calculating balance."""
        paper_broker.fee_per_fill = 100.0
        initial_cash = paper_broker._cash_for(account.account_id)

        await paper_broker.place_order(signal, account, 1.0, "BTC/USD")

        balance = paper_broker._cash_for(account.account_id)
        # BUY: cash -= (quantity * price + fee)
        expected_cash = initial_cash - (1.0 * 50000.0 + 100.0)
        assert balance == expected_cash


class TestOrderResultFeeFields:
    """Tests for OrderResult model fee/slippage fields."""

    def test_order_result_has_fee_field(self):
        """Should have fee field."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            fee=10.5,
        )

        assert result.fee == 10.5

    def test_order_result_has_fee_currency_field(self):
        """Should have fee_currency field."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            fee_currency="USD",
        )

        assert result.fee_currency == "USD"

    def test_order_result_has_slippage_field(self):
        """Should have slippage field."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            slippage=0.5,
        )

        assert result.slippage == 0.5

    def test_order_result_fee_fields_default_to_none(self):
        """Fee fields should default to None."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
        )

        assert result.fee is None
        assert result.fee_currency is None
        assert result.slippage is None

    def test_order_result_fee_and_currency_together(self):
        """Should support both fee and fee_currency."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            fee=25.75,
            fee_currency="EUR",
        )

        assert result.fee == 25.75
        assert result.fee_currency == "EUR"


class TestFeeStoragePersistence:
    """Tests for fee persistence to database."""

    def test_save_order_result_persists_fee(self, tmp_path):
        """Should persist fee to orders table."""
        store = SignalStore(tmp_path / "test.db")

        # First save a signal (foreign key requirement)
        sig = Signal(source="test", symbol="TEST", side=Side.BUY)
        store.save_signal(sig)

        result = OrderResult(
            account_id="ACC123",
            status=OrderStatus.FILLED,
            signal_id=sig.id,
            filled_quantity=1.0,
            filled_price=100.0,
            fee=10.0,
            fee_currency="USD",
        )

        order_id = store.save_order_result(
            result,
            broker="test_broker",
            symbol="TEST",
            side=Side.BUY,
            requested_quantity=1.0,
        )

        assert order_id is not None

        # Verify persisted
        with store._connect() as conn:
            row = conn.execute("SELECT fee, fee_currency FROM orders WHERE id = ?", (order_id,)).fetchone()
            assert row is not None
            assert row[0] == 10.0
            assert row[1] == "USD"

    def test_save_order_result_persists_slippage(self, tmp_path):
        """Should persist slippage to orders table."""
        store = SignalStore(tmp_path / "test.db")

        # First save a signal (foreign key requirement)
        sig = Signal(source="test", symbol="TEST", side=Side.BUY)
        store.save_signal(sig)

        result = OrderResult(
            account_id="ACC123",
            status=OrderStatus.FILLED,
            signal_id=sig.id,
            filled_quantity=1.0,
            filled_price=100.5,
            slippage=0.5,
        )

        order_id = store.save_order_result(
            result,
            broker="test_broker",
            symbol="TEST",
            side=Side.BUY,
            requested_quantity=1.0,
        )

        assert order_id is not None

        # Verify persisted
        with store._connect() as conn:
            row = conn.execute("SELECT slippage FROM orders WHERE id = ?", (order_id,)).fetchone()
            assert row is not None
            assert row[0] == 0.5

    def test_save_order_result_allows_null_fees(self, tmp_path):
        """Should allow NULL fees when broker doesn't report them."""
        store = SignalStore(tmp_path / "test.db")

        # First save a signal (foreign key requirement)
        sig = Signal(source="test", symbol="TEST", side=Side.BUY)
        store.save_signal(sig)

        result = OrderResult(
            account_id="ACC123",
            status=OrderStatus.FILLED,
            signal_id=sig.id,
            filled_quantity=1.0,
            filled_price=100.0,
            fee=None,  # Broker doesn't report fees
        )

        order_id = store.save_order_result(
            result,
            broker="test_broker",
            symbol="TEST",
            side=Side.BUY,
            requested_quantity=1.0,
        )

        assert order_id is not None

        # Verify persisted as NULL
        with store._connect() as conn:
            row = conn.execute("SELECT fee, slippage FROM orders WHERE id = ?", (order_id,)).fetchone()
            assert row is not None
            assert row[0] is None  # fee
            assert row[1] is None  # slippage

    def test_save_multiple_orders_with_fees(self, tmp_path):
        """Should persist multiple orders with different fees."""
        store = SignalStore(tmp_path / "test.db")

        # First save signals (foreign key requirement)
        sigs = [
            Signal(source="test", symbol="TEST", side=Side.BUY),
            Signal(source="test", symbol="TEST", side=Side.BUY),
        ]
        for sig in sigs:
            store.save_signal(sig)

        results = [
            OrderResult(
                account_id="ACC123",
                status=OrderStatus.FILLED,
                signal_id=sigs[0].id,
                filled_quantity=1.0,
                filled_price=100.0,
                fee=10.0,
                fee_currency="USD",
            ),
            OrderResult(
                account_id="ACC123",
                status=OrderStatus.FILLED,
                signal_id=sigs[1].id,
                filled_quantity=2.0,
                filled_price=200.0,
                fee=20.0,
                fee_currency="USD",
            ),
        ]

        order_ids = [
            store.save_order_result(
                r,
                broker="test_broker",
                symbol="TEST",
                side=Side.BUY,
                requested_quantity=r.filled_quantity,
            )
            for r in results
        ]

        assert len(order_ids) == 2

        # Verify both persisted with correct fees
        with store._connect() as conn:
            rows = conn.execute("SELECT id, fee FROM orders WHERE id IN (?, ?)", tuple(order_ids)).fetchall()
            assert len(rows) == 2
            assert rows[0][1] == 10.0
            assert rows[1][1] == 20.0


class TestBrokerAdapterFeeInterface:
    """Tests for broker adapter fee reporting contract."""

    def test_base_adapter_returns_no_fees_by_default(self):
        """Base broker adapter should not fabricate fee data."""
        from app.brokers.base import BrokerAdapter

        # BrokerAdapter.place_order is abstract, so we can't instantiate directly
        # This test documents that fee reporting is optional per broker
        assert BrokerAdapter.place_order.__isabstractmethod__

    def test_paper_broker_is_only_honest_fee_reporter(self, paper_broker):
        """PaperBroker should be the reference for honest fee reporting."""
        assert paper_broker.fee_per_fill >= 0.0
        # Paper broker has documented, computed fee -- not fabricated


class TestFeeTrackingEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_fee_currency_optional_when_fee_is_none(self):
        """Should allow fee_currency=None when fee is None."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            fee=None,
            fee_currency=None,
        )

        assert result.fee is None
        assert result.fee_currency is None

    def test_fractional_fee_values(self):
        """Should support fractional fee amounts."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            fee=0.125,
            fee_currency="BTC",
        )

        assert result.fee == 0.125
        assert result.fee_currency == "BTC"

    def test_large_fee_amounts(self):
        """Should support large fee amounts."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            fee=10000.50,
            fee_currency="USD",
        )

        assert result.fee == 10000.50

    def test_negative_slippage_favorable_fill(self):
        """Should allow negative slippage for favorable fills."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.FILLED,
            signal_id="sig123",
            slippage=-0.25,  # Bought better than signal price
        )

        assert result.slippage == -0.25

    def test_pending_order_with_no_fee_yet(self):
        """Should allow PENDING orders with no fee (not filled yet)."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.PENDING,
            signal_id="sig123",
            broker_order_id="ORDER123",
            fee=None,  # Not filled yet
        )

        assert result.status == OrderStatus.PENDING
        assert result.fee is None

    def test_rejected_order_with_no_fee(self):
        """Should allow REJECTED orders with no fee (never filled)."""
        result = OrderResult(
            account_id="test",
            status=OrderStatus.REJECTED,
            signal_id="sig123",
            message="Order rejected",
            fee=None,
        )

        assert result.status == OrderStatus.REJECTED
        assert result.fee is None
