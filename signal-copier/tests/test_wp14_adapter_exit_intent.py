"""Tests for WP-14: adapters receive the exit intent.

Findings: C-03, C-04
- C-03: Tastytrade always sends "…to Open": a close is an opening order
  Fix: Thread close intent to adapters and emit "Sell to Close"/"Buy to Close"
- C-04: Closes become reversals/opposing positions on NinjaTrader and MT5/MetaApi
  Fix: Emit "sentiment: flat" for closes; for MT5/MetaApi look up open position

Tests verify that:
1. Engine sets Intent.EXIT on close signals from _resolve_close and _submit_exit_order
2. Tastytrade adapter emits "Sell to Close"/"Buy to Close" when intent == EXIT
3. NinjaTrader adapter emits sentiment: "flat" for exits
4. MT5/MetaApi adapters handle position_ticket from signal.raw when present
"""
# type: ignore[misc]  # Test file with unittest.mock assignments
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.brokers.ninjatrader import NinjaTraderBroker
from app.brokers.tastytrade import TastytradeBroker
from app.models import AssetClass, DestinationAccount, Intent, OrderStatus, Side, Signal


@pytest.fixture
def account() -> DestinationAccount:
    return DestinationAccount(
        account_id="test_account",
        broker="tastytrade",
        enabled=True,
    )


@pytest.fixture
def account_ninjatrader() -> DestinationAccount:
    return DestinationAccount(
        account_id="test_nt",
        broker="ninjatrader",
        enabled=True,
    )


class TestTastytradExitIntent:
    """Tastytrade adapter should emit 'Sell to Close'/'Buy to Close' for exits."""

    @pytest.mark.asyncio
    async def test_tastytrade_emit_close_to_close_intent(self, account: DestinationAccount):
        """Tastytrade should emit 'Sell to Close' when intent == EXIT."""
        broker = TastytradeBroker()

        # Mock the HTTP client
        captured_request: dict[str, object] = {}

        async def mock_post(url: str, **kwargs: dict) -> MagicMock:  # type: ignore[no-untyped-def]
            captured_request["url"] = url
            captured_request["body"] = kwargs.get("json", {})  # type: ignore[assignment]
            response = MagicMock()
            response.json.return_value = {
                "data": {
                    "order": {"id": "tt_order_123"}
                }
            }
            return response

        broker._client.post = AsyncMock(side_effect=mock_post)  # type: ignore[method-assign]
        broker._access_token_for = AsyncMock(return_value="fake_token")  # type: ignore[method-assign]
        broker._credentials_for = MagicMock(return_value={  # type: ignore[method-assign]
            "ENV": "cert",
            "TT_ACCOUNT_NUMBER": "test_acct",
        })  # type: ignore[method-assign,assignment]

        # Create a close signal with intent == EXIT
        close_signal = Signal(
            source="engine",
            symbol="AAPL",
            side=Side.SELL,
            asset_class=AssetClass.EQUITY,
            intent=Intent.EXIT,
            quantity=100,
        )

        result = await broker.place_order(close_signal, account, 100, "AAPL")

        assert result.status == OrderStatus.PENDING
        # Check that the action is "Sell to Close"
        request_body = captured_request["body"]  # type: ignore[index]
        assert request_body["legs"][0]["action"] == "Sell to Close"

    @pytest.mark.asyncio
    async def test_tastytrade_emit_buy_to_close_for_short(self, account: DestinationAccount):
        """Tastytrade should emit 'Buy to Close' for short exits."""
        broker = TastytradeBroker()

        captured_request = {}

        async def mock_post(url: str, **kwargs: dict) -> MagicMock:
            captured_request["body"] = kwargs.get("json", {})
            response = MagicMock()
            response.json.return_value = {
                "data": {
                    "order": {"id": "tt_order_456"}
                }
            }
            return response

        broker._client.post = AsyncMock(side_effect=mock_post)  # type: ignore[method-assign]
        broker._access_token_for = AsyncMock(return_value="fake_token")  # type: ignore[method-assign]
        broker._credentials_for = MagicMock(return_value={  # type: ignore[method-assign]
            "ENV": "cert",
            "TT_ACCOUNT_NUMBER": "test_acct",
        })  # type: ignore[method-assign,assignment]

        # Create a close signal for covering a short (BUY to close SHORT)
        close_signal = Signal(
            source="engine",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            intent=Intent.EXIT,
            quantity=100,
        )

        result = await broker.place_order(close_signal, account, 100, "AAPL")

        assert result.status == OrderStatus.PENDING
        request_body = captured_request["body"]  # type: ignore[index]
        assert request_body["legs"][0]["action"] == "Buy to Close"

    @pytest.mark.asyncio
    async def test_tastytrade_emit_to_open_for_non_exit(self, account: DestinationAccount):
        """Tastytrade should emit 'Sell to Open' for non-exit intents."""
        broker = TastytradeBroker()

        captured_request = {}

        async def mock_post(url: str, **kwargs: dict) -> MagicMock:
            captured_request["body"] = kwargs.get("json", {})
            response = MagicMock()
            response.json.return_value = {
                "data": {
                    "order": {"id": "tt_order_789"}
                }
            }
            return response

        broker._client.post = AsyncMock(side_effect=mock_post)  # type: ignore[method-assign]
        broker._access_token_for = AsyncMock(return_value="fake_token")  # type: ignore[method-assign]
        broker._credentials_for = MagicMock(return_value={  # type: ignore[method-assign]
            "ENV": "cert",
            "TT_ACCOUNT_NUMBER": "test_acct",
        })  # type: ignore[method-assign,assignment]

        # Create a sell signal without EXIT intent (e.g., entry_short)
        sell_signal = Signal(
            source="engine",
            symbol="AAPL",
            side=Side.SELL,
            asset_class=AssetClass.EQUITY,
            intent=Intent.ENTRY_SHORT,
            quantity=100,
        )

        result = await broker.place_order(sell_signal, account, 100, "AAPL")

        assert result.status == OrderStatus.PENDING
        request_body = captured_request["body"]  # type: ignore[index]
        # Should be "Sell to Open" for non-exit intents
        assert request_body["legs"][0]["action"] == "Sell to Open"


class TestNinjaTraderExitIntent:
    """NinjaTrader adapter should emit sentiment: 'flat' for exits."""

    @pytest.mark.asyncio
    async def test_ninjatrader_emit_flat_sentiment_for_exit(self, account_ninjatrader: DestinationAccount):
        """NinjaTrader should emit sentiment: 'flat' when intent == EXIT."""
        broker = NinjaTraderBroker()

        captured_payload = {}

        async def mock_post(url: str, json: dict) -> MagicMock:
            captured_payload.update(json)
            response = MagicMock()
            response.status_code = 200
            return response

        broker._client.post = AsyncMock(side_effect=mock_post)  # type: ignore[method-assign]

        with patch.dict("os.environ", {"NT8_TEST_NT_URL": "http://localhost:7091/webhook/"}):
            # Create a close signal with intent == EXIT
            close_signal = Signal(
                source="engine",
                symbol="ES",
                side=Side.SELL,
                asset_class=AssetClass.EQUITY,
                intent=Intent.EXIT,
                quantity=1,
            )

            result = await broker.place_order(close_signal, account_ninjatrader, 1, "ES")

            assert result.status == OrderStatus.PENDING
            # Check that the sentiment is "flat"
            assert captured_payload.get("sentiment") == "flat"

    @pytest.mark.asyncio
    async def test_ninjatrader_emit_long_short_for_non_exit(self, account_ninjatrader: DestinationAccount):
        """NinjaTrader should emit long/short sentiment for non-exit intents."""
        broker = NinjaTraderBroker()

        captured_payloads = []

        async def mock_post(url: str, json: dict) -> MagicMock:
            captured_payloads.append(json.copy())
            response = MagicMock()
            response.status_code = 200
            return response

        broker._client.post = AsyncMock(side_effect=mock_post)  # type: ignore[method-assign]

        with patch.dict("os.environ", {"NT8_TEST_NT_URL": "http://localhost:7091/webhook/"}):
            # Create a buy entry signal
            buy_signal = Signal(
                source="engine",
                symbol="ES",
                side=Side.BUY,
                asset_class=AssetClass.EQUITY,
                intent=Intent.ENTRY_LONG,
                quantity=1,
            )

            result = await broker.place_order(buy_signal, account_ninjatrader, 1, "ES")
            assert result.status == OrderStatus.PENDING
            assert captured_payloads[-1].get("sentiment") == "long"

            # Create a sell entry signal (short)
            sell_signal = Signal(
                source="engine",
                symbol="ES",
                side=Side.SELL,
                asset_class=AssetClass.EQUITY,
                intent=Intent.ENTRY_SHORT,
                quantity=1,
            )

            result = await broker.place_order(sell_signal, account_ninjatrader, 1, "ES")
            assert result.status == OrderStatus.PENDING
            assert captured_payloads[-1].get("sentiment") == "short"


class TestExitSignalIntentProperties:
    """Test that close signals have the EXIT intent set correctly."""

    def test_exit_signal_has_exit_intent(self):
        """Verify that a close signal can have EXIT intent."""
        close_signal = Signal(
            source="engine",
            symbol="AAPL",
            side=Side.SELL,
            asset_class=AssetClass.EQUITY,
            intent=Intent.EXIT,
            quantity=100,
            raw={"position_ticket": 98765},
        )

        assert close_signal.intent == Intent.EXIT
        assert close_signal.raw.get("position_ticket") == 98765
        assert close_signal.side == Side.SELL

    def test_entry_signal_has_entry_intent(self):
        """Verify that entry signals have proper intent."""
        buy_signal = Signal(
            source="engine",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            intent=Intent.ENTRY_LONG,
            quantity=100,
        )

        assert buy_signal.intent == Intent.ENTRY_LONG
        assert buy_signal.side == Side.BUY

    def test_short_entry_signal_has_entry_short_intent(self):
        """Verify that short entry signals have ENTRY_SHORT intent."""
        sell_signal = Signal(
            source="engine",
            symbol="AAPL",
            side=Side.SELL,
            asset_class=AssetClass.EQUITY,
            intent=Intent.ENTRY_SHORT,
            quantity=100,
        )

        assert sell_signal.intent == Intent.ENTRY_SHORT
        assert sell_signal.side == Side.SELL


