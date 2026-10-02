"""WP-17 (B-04): Venue quantization tests.

Tests that quantities are normalized to venue precision/lot-step before order submission.
"""
import pytest

from app.models import (
    DestinationAccount,
    OrderStatus,
)
from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker


class StubQuantizationBroker(BrokerAdapter):
    """A stub broker for testing normalization with a specific lot step."""

    name = "stub_quant"

    def __init__(self, step: float | None = None):
        self.step = step
        self.last_quantity = None
        self.last_symbol = None

    def normalize_quantity(self, account, symbol, quantity) -> float | None:
        """Normalize to the configured step, or return None if unknown."""
        import math
        if self.step is None:
            return None
        self.last_symbol = symbol
        self.last_quantity = quantity
        normalized = math.floor(quantity / self.step) * self.step
        return max(0.0, normalized)

    async def place_order(self, signal, account, quantity, symbol):
        from app.models import OrderResult
        self.last_quantity = quantity
        self.last_symbol = symbol
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=f"stub-{signal.id}",
            filled_quantity=quantity,
            filled_price=signal.price or 100.0,
            message="stub filled",
        )


@pytest.mark.asyncio
async def test_quantization_rounds_down_to_step():
    """Test that quantities are rounded down to the venue's lot step."""
    broker = StubQuantizationBroker(step=1.0)
    account = DestinationAccount(
        account_id="test",
        broker="stub_quant",
        enabled=True,
    )

    # 3.7 shares with step 1.0 should round down to 3.0
    normalized = broker.normalize_quantity(account, "AAPL", 3.7)
    assert normalized == 3.0


@pytest.mark.asyncio
async def test_quantization_unknown_returns_none():
    """Test that unknown precision returns None."""
    broker = StubQuantizationBroker(step=None)
    account = DestinationAccount(
        account_id="test",
        broker="stub_quant",
        enabled=True,
    )

    # Unknown step should return None
    normalized = broker.normalize_quantity(account, "AAPL", 3.7)
    assert normalized is None


@pytest.mark.asyncio
async def test_quantization_below_minimum_returns_zero():
    """Test that quantities below the minimum round to zero."""
    broker = StubQuantizationBroker(step=1.0)
    account = DestinationAccount(
        account_id="test",
        broker="stub_quant",
        enabled=True,
    )

    # 0.5 shares with step 1.0 should round down to 0.0
    normalized = broker.normalize_quantity(account, "AAPL", 0.5)
    assert normalized == 0.0


@pytest.mark.asyncio
async def test_quantization_exit_of_fractional_quantity():
    """Test exit quantization: 3.5 on a step-1 venue should become 3."""
    broker = StubQuantizationBroker(step=1.0)
    account = DestinationAccount(
        account_id="test",
        broker="stub_quant",
        enabled=True,
    )

    # Exit of 3.5 shares with step 1.0 should round down to 3.0
    normalized = broker.normalize_quantity(account, "AAPL", 3.5)
    assert normalized == 3.0


@pytest.mark.asyncio
async def test_paper_broker_quantization():
    """Test paper broker's 1e-8 step quantization."""
    broker = PaperBroker()
    account = DestinationAccount(
        account_id="test",
        broker="paper",
        enabled=True,
    )

    # Paper broker has step of 1e-8
    # 0.000000001 (below step) should round to 0.0
    normalized = broker.normalize_quantity(account, "BTC", 0.000000001)
    assert normalized == 0.0

    # 0.00000001 (exactly at step) should round to 0.00000001
    normalized = broker.normalize_quantity(account, "BTC", 0.00000001)
    assert normalized == pytest.approx(0.00000001, abs=1e-10)

    # 0.000000011 (slightly above step) should round to 0.00000001
    normalized = broker.normalize_quantity(account, "BTC", 0.000000011)
    assert normalized == pytest.approx(0.00000001, abs=1e-10)


@pytest.mark.asyncio
async def test_alpaca_whole_shares_quantization():
    """Test Alpaca's whole-shares quantization."""
    from app.brokers.alpaca import AlpacaBroker

    broker = AlpacaBroker()
    account = DestinationAccount(
        account_id="test",
        broker="alpaca",
        enabled=True,
    )

    # Alpaca enforces whole shares
    # 3.7 shares should round down to 3.0
    normalized = broker.normalize_quantity(account, "AAPL", 3.7)
    assert normalized == 3.0

    # 3.1 shares should round down to 3.0
    normalized = broker.normalize_quantity(account, "AAPL", 3.1)
    assert normalized == 3.0

    # 3.0 shares should stay 3.0
    normalized = broker.normalize_quantity(account, "AAPL", 3.0)
    assert normalized == 3.0

    # 0.5 shares should round to 0.0
    normalized = broker.normalize_quantity(account, "AAPL", 0.5)
    assert normalized == 0.0


@pytest.mark.asyncio
async def test_default_broker_no_normalization():
    """Test that default BrokerAdapter returns quantity unchanged."""

    class DefaultBroker(BrokerAdapter):
        name = "default"

        async def place_order(self, signal, account, quantity, symbol):
            from app.models import OrderResult
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.FILLED,
                signal_id=signal.id,
                broker_order_id="default-1",
                filled_quantity=quantity,
                filled_price=100.0,
                message="filled",
            )

    broker = DefaultBroker()
    account = DestinationAccount(
        account_id="test",
        broker="default",
        enabled=True,
    )

    # Default behavior: return quantity unchanged
    normalized = broker.normalize_quantity(account, "AAPL", 3.7)
    assert normalized == 3.7


@pytest.mark.asyncio
async def test_quantization_with_very_small_step():
    """Test quantization with very small step (1e-8)."""
    broker = StubQuantizationBroker(step=1e-8)
    account = DestinationAccount(
        account_id="test",
        broker="stub_quant",
        enabled=True,
    )

    # 0.000000015 with step 1e-8 should round down to 0.00000001
    # (1.5 steps -> floor to 1 step)
    normalized = broker.normalize_quantity(account, "BTC", 0.000000015)
    assert normalized == pytest.approx(0.00000001, abs=1e-10)

    # 0.000000001 (0.1e-8) should round to 0.0
    normalized = broker.normalize_quantity(account, "BTC", 0.000000001)
    assert normalized == 0.0
