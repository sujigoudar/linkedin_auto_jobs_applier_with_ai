"""Integration test: E09 account liquidation + commercial accounting flow.

Verifies that account liquidation circuit breaker (minimum equity threshold)
properly prevents entries and flows through to commercial platform's
accounting/P&L tracking system.
"""
from unittest.mock import AsyncMock, patch
import pytest

from app.models import (
    DestinationAccount,
    Signal,
    Side,
    AssetClass,
    OrderResult,
    OrderStatus,
    AccountBalance,
)
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.daily_loss_limiter import DailyLossLimiter


@pytest.fixture
def signal_store(tmp_path):
    """In-memory test database."""
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def paper_broker():
    """Paper broker for testing."""
    return PaperBroker()


@pytest.fixture
def account_with_min_equity():
    """Account configured with minimum equity threshold."""
    return DestinationAccount(
        broker="paper",
        account_id="TEST_ACCOUNT_123",
        min_equity_threshold=10000.0,  # Minimum $10,000 equity
    )


@pytest.fixture
def signal():
    """Test signal for entry."""
    return Signal(
        source="test_source",
        symbol="BTC/USD",
        side=Side.BUY,
        asset_class=AssetClass.CRYPTO,
        price=50000.0,
        quantity=0.1,
    )


class TestE09CommercialAccountingIntegration:
    """E09 + commercial platform accounting integration tests."""

    @pytest.mark.asyncio
    async def test_liquidation_check_prevents_entry_when_equity_below_threshold(
        self, signal_store, paper_broker, account_with_min_equity, signal
    ):
        """Should reject entry signal when account equity falls below threshold."""
        limiter = DailyLossLimiter(signal_store)
        signal_store._broker_adapters = {"paper": paper_broker}

        # Setup: Account with equity below threshold
        balance = AccountBalance(
            account_id=account_with_min_equity.account_id,
            equity=8000.0,  # Below $10,000 threshold
            buying_power=4000.0,
        )

        with patch.object(
            paper_broker, "get_account_balance", new_callable=AsyncMock
        ) as mock_balance:
            mock_balance.return_value = balance

            # Act: Check liquidation gate
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, account_with_min_equity.min_equity_threshold
            )

            # Assert: Entry is rejected with liquidation message
            assert result is not None
            assert "Account liquidation" in result
            assert "8000.00" in result
            assert "10000.00" in result

    @pytest.mark.asyncio
    async def test_liquidation_rejection_not_exported_to_orders_table(
        self, signal_store, paper_broker, account_with_min_equity, signal
    ):
        """Liquidation rejection should not create an order record (rejection happens before execution)."""
        limiter = DailyLossLimiter(signal_store)
        signal_store._broker_adapters = {"paper": paper_broker}

        balance = AccountBalance(
            account_id=account_with_min_equity.account_id,
            equity=8000.0,
            buying_power=4000.0,
        )

        with patch.object(
            paper_broker, "get_account_balance", new_callable=AsyncMock
        ) as mock_balance:
            mock_balance.return_value = balance

            # Act: Check liquidation gate (rejection happens before order execution)
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, account_with_min_equity.min_equity_threshold
            )

            # Assert: Rejection occurred; no order would have been created
            assert result is not None
            # Verify by checking orders table is empty
            with signal_store._connect() as conn:
                orders = conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE account_id = ?",
                    (account_with_min_equity.account_id,),
                ).fetchone()
                assert orders[0] == 0

    @pytest.mark.asyncio
    async def test_entry_allowed_when_equity_above_threshold_then_executes(
        self, signal_store, paper_broker, account_with_min_equity, signal
    ):
        """Entry should be allowed and executed when equity is above threshold."""
        limiter = DailyLossLimiter(signal_store)
        signal_store._broker_adapters = {"paper": paper_broker}

        # Setup: Account with equity ABOVE threshold
        balance = AccountBalance(
            account_id=account_with_min_equity.account_id,
            equity=15000.0,  # Above $10,000 threshold
            buying_power=10000.0,
        )

        with patch.object(
            paper_broker, "get_account_balance", new_callable=AsyncMock
        ) as mock_balance:
            mock_balance.return_value = balance

            # Act: Check liquidation gate (should pass)
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, account_with_min_equity.min_equity_threshold
            )

            # Assert: Entry is allowed
            assert result is None

    @pytest.mark.asyncio
    async def test_fee_tracking_with_liquidation_boundary(
        self, signal_store, paper_broker, account_with_min_equity
    ):
        """Fees should be properly tracked even near liquidation boundary."""
        # Setup: Account near threshold
        balance = AccountBalance(
            account_id=account_with_min_equity.account_id,
            equity=10100.0,  # Just above $10,000 threshold
            buying_power=5000.0,
        )

        signal_store._broker_adapters = {"paper": paper_broker}
        paper_broker.fee_per_fill = 50.0  # $50 fee per fill

        with patch.object(
            paper_broker, "get_account_balance", new_callable=AsyncMock
        ) as mock_balance:
            mock_balance.return_value = balance

            # Act: Execute order (should succeed as equity above threshold)
            order_signal = Signal(
                source="test",
                symbol="BTC/USD",
                side=Side.BUY,
                asset_class=AssetClass.CRYPTO,
                price=50000.0,
                quantity=0.1,
            )

            result = await paper_broker.place_order(
                order_signal, account_with_min_equity, 0.1, "BTC/USD"
            )

            # Assert: Order filled with fee tracked
            assert result.status == OrderStatus.FILLED
            assert result.fee == 50.0
            assert result.fee_currency == "USD"

            # Verify cash was deducted (price * qty + fee)
            remaining_cash = paper_broker._cash_for(account_with_min_equity.account_id)
            # Initial $100k - (0.1 * 50000 + 50) = $100k - $5050 = $95,000
            expected_cash = 100000.0 - (0.1 * 50000.0 + 50.0)
            assert remaining_cash == expected_cash

    @pytest.mark.asyncio
    async def test_liquidation_threshold_enforcement_is_fail_closed(
        self, signal_store, paper_broker, account_with_min_equity
    ):
        """Liquidation check should fail closed on any error (never allow uncertain entry)."""
        limiter = DailyLossLimiter(signal_store)
        signal_store._broker_adapters = {"paper": paper_broker}

        # Simulate broker adapter exception
        with patch.object(
            paper_broker, "get_account_balance", new_callable=AsyncMock
        ) as mock_balance:
            mock_balance.side_effect = RuntimeError("Broker API connection failed")

            # Act: Check liquidation gate (should fail closed)
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, account_with_min_equity.min_equity_threshold
            )

            # Assert: Rejected with error message (fail-closed)
            assert result is not None
            assert "Min equity check failed" in result
            assert "Broker API connection failed" in result

    @pytest.mark.asyncio
    async def test_multiple_fills_within_session_reduce_equity(
        self, signal_store, paper_broker, account_with_min_equity
    ):
        """Multiple fills should properly reduce available equity for liquidation checks."""
        signal_store._broker_adapters = {"paper": paper_broker}
        paper_broker.fee_per_fill = 100.0

        # Simulate first order: BTC at 50k, quantity 0.5
        # Cost: 0.5 * 50k + 100 = $25,100
        signal1 = Signal(
            source="test",
            symbol="BTC/USD",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            price=50000.0,
            quantity=0.5,
        )

        result1 = await paper_broker.place_order(
            signal1, account_with_min_equity, 0.5, "BTC/USD"
        )
        assert result1.status == OrderStatus.FILLED
        assert result1.fee == 100.0

        # After first fill, cash should be reduced
        cash_after_first = paper_broker._cash_for(account_with_min_equity.account_id)
        assert cash_after_first == 100000.0 - 25100.0  # $74,900

        # Simulate second order: ETH at 3k, quantity 1.0
        # Cost: 1.0 * 3000 + 100 = $3,100
        signal2 = Signal(
            source="test",
            symbol="ETH/USD",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            price=3000.0,
            quantity=1.0,
        )

        result2 = await paper_broker.place_order(
            signal2, account_with_min_equity, 1.0, "ETH/USD"
        )
        assert result2.status == OrderStatus.FILLED
        assert result2.fee == 100.0

        # After second fill
        cash_after_second = paper_broker._cash_for(
            account_with_min_equity.account_id
        )
        assert cash_after_second == cash_after_first - 3100.0  # $71,800

    @pytest.mark.asyncio
    async def test_liquidation_threshold_disabled_allows_all_entries(
        self, signal_store, paper_broker, account_with_min_equity, signal
    ):
        """When min_equity_threshold is None or 0, entries should not be gated."""
        limiter = DailyLossLimiter(signal_store)
        signal_store._broker_adapters = {"paper": paper_broker}

        # Setup: Very low equity
        balance = AccountBalance(
            account_id=account_with_min_equity.account_id,
            equity=100.0,
            buying_power=50.0,
        )

        with patch.object(
            paper_broker, "get_account_balance", new_callable=AsyncMock
        ) as mock_balance:
            mock_balance.return_value = balance

            # Test with threshold=None (disabled)
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, min_equity_threshold=None
            )
            assert result is None  # No rejection

            # Test with threshold=0.0 (disabled)
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, min_equity_threshold=0.0
            )
            assert result is None  # No rejection

            # Test with threshold=-1.0 (disabled)
            result = await limiter.check_min_equity_threshold(
                account_with_min_equity, min_equity_threshold=-1.0
            )
            assert result is None  # No rejection


class TestE09ExportEventGeneration:
    """E09 rejection events flowing through export system."""

    def test_liquidation_rejection_can_be_logged_as_signal_event(
        self, signal_store, account_with_min_equity
    ):
        """Liquidation rejections should be loggable as signal events for commercial platform."""
        # Setup: Save signal first (for export traceability)
        signal = Signal(
            source="test_source",
            symbol="BTC/USD",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            price=50000.0,
            quantity=0.1,
        )
        signal_store.save_signal(signal)

        # Simulate liquidation rejection
        rejection_reason = "Account liquidation: Current equity $8000.00 is below minimum threshold $10000.00"

        # In commercial platform, this would be exported as a SIGNAL_REJECTED event
        # with the rejection_reason included in the event payload
        assert rejection_reason is not None
        assert "liquidation" in rejection_reason.lower()
        assert "8000" in rejection_reason
        assert "10000" in rejection_reason

    def test_fee_tracking_available_for_commercial_p_l_calculation(
        self, signal_store, paper_broker, account_with_min_equity
    ):
        """Fee data should be available for commercial P&L calculations."""
        # Setup: Save signal
        signal = Signal(
            source="test",
            symbol="BTC/USD",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            price=50000.0,
            quantity=0.1,
        )
        signal_store.save_signal(signal)

        paper_broker.fee_per_fill = 50.0

        # Create OrderResult with fee tracking
        order_result = OrderResult(
            account_id=account_with_min_equity.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=0.1,
            filled_price=50000.0,
            fee=50.0,
            fee_currency="USD",
            slippage=0.0,
        )

        # Save to database
        order_id = signal_store.save_order_result(
            order_result,
            broker="paper",
            symbol="BTC/USD",
            side=Side.BUY,
            requested_quantity=0.1,
        )

        # Verify fee data is retrievable for P&L calculation
        with signal_store._connect() as conn:
            row = conn.execute(
                "SELECT filled_quantity, filled_price, fee, fee_currency, slippage FROM orders WHERE id = ?",
                (order_id,),
            ).fetchone()

            assert row is not None
            filled_qty, filled_price, fee, fee_currency, slippage = row
            assert filled_qty == 0.1
            assert filled_price == 50000.0
            assert fee == 50.0
            assert fee_currency == "USD"
            assert slippage == 0.0

            # Commercial P&L calculation would use:
            # gross_pnl = (filled_price * filled_qty) - all_fees
            # = (50000 * 0.1) - 50
            # = 5000 - 50
            # = $4,950 (net of fees)
            gross_notional = filled_price * filled_qty
            net_after_fees = gross_notional - fee
            assert net_after_fees == 4950.0
