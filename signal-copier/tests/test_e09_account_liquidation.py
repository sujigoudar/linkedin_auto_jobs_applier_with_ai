"""E09: Account liquidation circuit breaker tests.

Tests for minimum equity threshold enforcement that prevents new entries
when account equity falls below configured minimum.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.daily_loss_limiter import DailyLossLimiter
from app.models import AccountBalance, DestinationAccount


@pytest.fixture
def mock_store():
    """Mock database store."""
    return MagicMock()


@pytest.fixture
def mock_broker_adapter():
    """Mock broker adapter."""
    broker = MagicMock()
    broker.get_account_balance = AsyncMock()
    return broker


@pytest.fixture
def account():
    """Test account with min equity threshold configured."""
    return DestinationAccount(
        broker="test_broker",
        account_id="TEST_ACCOUNT_123",
        min_equity_threshold=10000.0,  # Minimum $10,000 equity
    )


@pytest.fixture
def limiter(mock_store, mock_broker_adapter):
    """Initialize DailyLossLimiter with mocked store and broker adapters."""
    limiter = DailyLossLimiter(mock_store, brokers={"test_broker": mock_broker_adapter})
    return limiter


class TestAccountLiquidationCheckPassed:
    """Tests for account liquidation check passing (equity above threshold)."""

    @pytest.mark.asyncio
    async def test_equity_above_threshold(self, limiter, account, mock_broker_adapter):
        """Should pass when account equity is above minimum threshold."""
        balance = AccountBalance(account_id=account.account_id, equity=15000.0, buying_power=10000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is None
        mock_broker_adapter.get_account_balance.assert_called_once_with(account)

    @pytest.mark.asyncio
    async def test_equity_equals_threshold(self, limiter, account, mock_broker_adapter):
        """Should pass when account equity exactly meets threshold."""
        balance = AccountBalance(account_id=account.account_id, equity=10000.0, buying_power=5000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_equity_significantly_above_threshold(self, limiter, account, mock_broker_adapter):
        """Should pass when account equity is well above threshold."""
        balance = AccountBalance(account_id=account.account_id, equity=50000.0, buying_power=25000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_threshold_not_configured(self, limiter, account, mock_broker_adapter):
        """Should pass when min_equity_threshold is not configured (None)."""
        balance = AccountBalance(account_id=account.account_id, equity=5000.0, buying_power=2000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, None)

        assert result is None
        mock_broker_adapter.get_account_balance.assert_not_called()

    @pytest.mark.asyncio
    async def test_threshold_disabled_zero(self, limiter, account, mock_broker_adapter):
        """Should pass when threshold is disabled (zero value)."""
        balance = AccountBalance(account_id=account.account_id, equity=5000.0, buying_power=2000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 0.0)

        assert result is None
        mock_broker_adapter.get_account_balance.assert_not_called()

    @pytest.mark.asyncio
    async def test_threshold_disabled_negative(self, limiter, account, mock_broker_adapter):
        """Should pass when threshold is disabled (negative value)."""
        balance = AccountBalance(account_id=account.account_id, equity=5000.0, buying_power=2000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, -100.0)

        assert result is None
        mock_broker_adapter.get_account_balance.assert_not_called()


class TestAccountLiquidationCheckFailed:
    """Tests for account liquidation check failing (equity below threshold)."""

    @pytest.mark.asyncio
    async def test_equity_below_threshold(self, limiter, account, mock_broker_adapter):
        """Should reject when account equity is below minimum threshold."""
        balance = AccountBalance(account_id=account.account_id, equity=8000.0, buying_power=4000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "Account liquidation" in result
        assert "8000.00" in result
        assert "10000.00" in result

    @pytest.mark.asyncio
    async def test_equity_significantly_below_threshold(self, limiter, account, mock_broker_adapter):
        """Should reject when equity is significantly below threshold."""
        balance = AccountBalance(account_id=account.account_id, equity=2000.0, buying_power=1000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "Account liquidation" in result
        assert "2000.00" in result

    @pytest.mark.asyncio
    async def test_equity_nearly_depleted(self, limiter, account, mock_broker_adapter):
        """Should reject when equity is nearly depleted."""
        balance = AccountBalance(account_id=account.account_id, equity=100.0, buying_power=50.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "Account liquidation" in result


class TestAccountLiquidationFailClosed:
    """Tests for fail-closed semantics (reject on missing data)."""

    @pytest.mark.asyncio
    async def test_no_broker_adapter_available(self, mock_store, account):
        """Should reject when broker adapter is not available (fail-closed)."""
        mock_store._broker_adapters = {}
        limiter = DailyLossLimiter(mock_store)

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "no broker adapter available" in result
        assert "Min equity check failed" in result

    @pytest.mark.asyncio
    async def test_balance_is_none(self, limiter, account, mock_broker_adapter):
        """Should reject when broker returns None balance (fail-closed)."""
        mock_broker_adapter.get_account_balance.return_value = None

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "cannot determine current account equity" in result
        assert "Min equity check failed" in result

    @pytest.mark.asyncio
    async def test_equity_is_none_with_valid_balance(self, limiter, account, mock_broker_adapter):
        """Should reject when balance exists but equity is None (fail-closed)."""
        balance = AccountBalance(account_id=account.account_id, equity=None, buying_power=5000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "cannot determine current account equity" in result

    @pytest.mark.asyncio
    async def test_broker_adapter_raises_exception(self, limiter, account, mock_broker_adapter):
        """Should reject when broker adapter raises exception (fail-closed)."""
        mock_broker_adapter.get_account_balance.side_effect = RuntimeError("Connection failed")

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "Min equity check failed" in result
        assert "Connection failed" in result

    @pytest.mark.asyncio
    async def test_broker_adapter_timeout(self, limiter, account, mock_broker_adapter):
        """Should reject when broker adapter times out (fail-closed)."""
        mock_broker_adapter.get_account_balance.side_effect = TimeoutError("Broker API timeout")

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "Min equity check failed" in result
        assert "Broker API timeout" in result


class TestAccountLiquidationEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_zero_equity(self, limiter, account, mock_broker_adapter):
        """Should reject when equity is zero."""
        balance = AccountBalance(account_id=account.account_id, equity=0.0, buying_power=0.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 1.0)

        assert result is not None
        assert "Account liquidation" in result

    @pytest.mark.asyncio
    async def test_negative_equity(self, limiter, account, mock_broker_adapter):
        """Should reject when account is in negative equity (margin call state)."""
        balance = AccountBalance(account_id=account.account_id, equity=-1000.0, buying_power=0.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is not None
        assert "Account liquidation" in result

    @pytest.mark.asyncio
    async def test_very_small_threshold(self, limiter, account, mock_broker_adapter):
        """Should correctly handle very small threshold values."""
        balance = AccountBalance(account_id=account.account_id, equity=100.0, buying_power=50.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 0.01)

        assert result is None

    @pytest.mark.asyncio
    async def test_very_large_equity(self, limiter, account, mock_broker_adapter):
        """Should correctly handle very large equity values."""
        balance = AccountBalance(account_id=account.account_id, equity=1000000.0, buying_power=500000.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_fractional_equity_values(self, limiter, account, mock_broker_adapter):
        """Should correctly handle fractional equity values."""
        balance = AccountBalance(account_id=account.account_id, equity=10000.50, buying_power=5000.25)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.51)

        assert result is not None
        assert "Account liquidation" in result


class TestAccountLiquidationErrorMessages:
    """Tests for error message clarity and content."""

    @pytest.mark.asyncio
    async def test_error_message_format_below_threshold(self, limiter, account, mock_broker_adapter):
        """Should include clear error message when equity below threshold."""
        balance = AccountBalance(account_id=account.account_id, equity=8500.75, buying_power=4250.0)
        mock_broker_adapter.get_account_balance.return_value = balance

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        # Error message should include both values with proper formatting
        assert "8500.75" in result
        assert "10000.00" in result
        assert "Account liquidation" in result
        assert "below minimum threshold" in result

    @pytest.mark.asyncio
    async def test_error_message_format_broker_unavailable(self, mock_store, account):
        """Should include clear error message when broker unavailable."""
        mock_store._broker_adapters = {}
        limiter = DailyLossLimiter(mock_store)

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert "broker adapter available" in result

    @pytest.mark.asyncio
    async def test_error_message_format_equity_unavailable(self, limiter, account, mock_broker_adapter):
        """Should include clear error message when equity cannot be determined."""
        mock_broker_adapter.get_account_balance.return_value = None

        result = await limiter.check_min_equity_threshold(account, 10000.0)

        assert "cannot determine current account equity" in result
