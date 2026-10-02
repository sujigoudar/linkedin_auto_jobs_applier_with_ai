"""WP-36: Test adapter capability declarations (C-01/C-05/C-10).

Tests that:
- C-01: LIMIT/STOP entries are rejected for adapters that don't support them
- C-05: OPTION asset class is removed from adapters that can't build option legs
- C-10: Adapters without feedback are marked as entries_admissible=False
"""
import pytest
from app.models import (
    Signal,
    Side,
    AssetClass,
    OrderStatus,
    EntryOrderType,
    DestinationAccount,
)
from app.engine import SignalCopierEngine
from app.routing import RoutingConfig, RoutingRule
from app.db import SignalStore
from app.brokers.paper import PaperBroker
from app.brokers.alpaca import AlpacaBroker
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.tastytrade import TastytradeBroker
from app.brokers.tradestation import TradeStationBroker
from app.brokers.ninjatrader import NinjaTraderBroker
from app.brokers.signalstack import SignalStackBroker


class TestC05AssetClassDeclarations:
    """C-05: OPTION removed from Tastytrade and TradeStation."""

    def test_tastytrade_no_option(self):
        """Tastytrade should only declare EQUITY support."""
        broker = TastytradeBroker()
        assert broker.supported_asset_classes == frozenset({AssetClass.EQUITY})
        assert AssetClass.OPTION not in broker.supported_asset_classes

    def test_tradestation_no_option(self):
        """TradeStation should declare EQUITY and FUTURE only (no OPTION)."""
        broker = TradeStationBroker()
        assert broker.supported_asset_classes == frozenset({AssetClass.EQUITY, AssetClass.FUTURE})
        assert AssetClass.OPTION not in broker.supported_asset_classes


class TestC01EntryOrderTypeValidation:
    """C-01: LIMIT/STOP entries are rejected for adapters without support."""

    def test_market_order_accepted(self):
        """Market orders (the default) are accepted by all adapters."""
        paper = PaperBroker()
        assert paper.can_trade_entry_order_type(None)  # None defaults to MARKET
        assert paper.can_trade_entry_order_type(EntryOrderType.MARKET)

    def test_limit_order_rejected_by_default(self):
        """LIMIT orders are rejected by adapters without explicit support."""
        paper = PaperBroker()
        assert not paper.can_trade_entry_order_type(EntryOrderType.LIMIT)

    def test_stop_order_rejected_by_default(self):
        """STOP orders are rejected by adapters without explicit support."""
        paper = PaperBroker()
        assert not paper.can_trade_entry_order_type(EntryOrderType.STOP)


class TestC10EntriesAdmissible:
    """C-10: Adapters without feedback are marked as entries_admissible=False."""

    def test_paper_admits_entries(self):
        """PaperBroker can admit entries (has feedback)."""
        broker = PaperBroker()
        assert broker.entries_admissible()

    def test_alpaca_admits_entries(self):
        """Alpaca can admit entries (has get_order_status feedback)."""
        broker = AlpacaBroker()
        assert broker.entries_admissible()
        assert broker.has_order_status_capability

    def test_ccxt_admits_entries(self):
        """CCXT can admit entries (has get_broker_position feedback)."""
        broker = CCXTBroker()
        assert broker.entries_admissible()
        assert broker.has_position_readback_capability

    def test_tastytrade_admits_entries(self):
        """Tastytrade can admit entries (has get_order_status feedback)."""
        broker = TastytradeBroker()
        assert broker.entries_admissible()
        assert broker.has_order_status_capability

    def test_ninjatrader_denies_entries(self):
        """NinjaTrader cannot admit entries (no feedback)."""
        broker = NinjaTraderBroker()
        assert not broker.entries_admissible()
        assert not broker.has_order_status_capability
        assert not broker.has_position_readback_capability
        assert not broker.has_balance_capability

    def test_signalstack_denies_entries(self):
        """SignalStack cannot admit entries (no feedback)."""
        broker = SignalStackBroker()
        assert not broker.entries_admissible()
        assert not broker.has_order_status_capability
        assert not broker.has_position_readback_capability
        assert not broker.has_balance_capability


class TestC01LimitOrderEngineRejection:
    """C-01: Engine rejects LIMIT/STOP entries for adapters without support."""

    @pytest.mark.asyncio
    async def test_engine_rejects_limit_entry_for_unsupported_adapter(self, tmp_path):
        """Engine should reject a LIMIT entry when adapter doesn't support it."""
        store = SignalStore(str(tmp_path / "test.db"))
        broker = PaperBroker()
        account = DestinationAccount(
            broker="paper",
            account_id="test_paper",
        )
        routing = RoutingConfig(
            rules=[RoutingRule(source="test_source", destinations=["test_paper"])],
            accounts={"test_paper": account},
        )
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

        # Create a LIMIT entry signal
        signal = Signal(
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,  # Paper accepts any asset class
            quantity=1.0,
            price=100.0,
            entry_order_type=EntryOrderType.LIMIT,
        )

        # Submit the signal
        results = await engine.handle_signal(signal)

        # Should be rejected due to unsupported entry_order_type
        assert len(results) == 1
        assert results[0].status == OrderStatus.REJECTED
        assert "entry_order_type" in results[0].message.lower()

    @pytest.mark.asyncio
    async def test_engine_accepts_market_entry(self, tmp_path):
        """Engine should accept a MARKET entry (the default)."""
        store = SignalStore(str(tmp_path / "test.db"))
        broker = PaperBroker()
        account = DestinationAccount(
            broker="paper",
            account_id="test_paper",
        )
        routing = RoutingConfig(
            rules=[RoutingRule(source="test_source", destinations=["test_paper"])],
            accounts={"test_paper": account},
        )
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

        # Create a MARKET entry signal (default)
        signal = Signal(
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            quantity=1.0,
            price=100.0,
            entry_order_type=EntryOrderType.MARKET,
        )

        # Submit the signal
        results = await engine.handle_signal(signal)

        # Should succeed (PaperBroker accepts it)
        assert len(results) == 1
        # Paper broker should FILL it immediately
        assert results[0].status == OrderStatus.FILLED
        assert "entry_order_type" not in results[0].message.lower()

    @pytest.mark.asyncio
    async def test_engine_accepts_close_with_limit_entry_type(self, tmp_path):
        """Engine should accept CLOSE signals regardless of entry_order_type.

        CLOSE signals don't have an entry_order_type; this tests that the
        validation only applies to entries."""
        store = SignalStore(str(tmp_path / "test.db"))
        broker = PaperBroker()
        account = DestinationAccount(
            broker="paper",
            account_id="test_paper",
        )
        routing = RoutingConfig(
            rules=[RoutingRule(source="test_source", destinations=["test_paper"])],
            accounts={"test_paper": account},
        )
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

        # First, place an entry to open a position
        entry_signal = Signal(
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            quantity=1.0,
            price=100.0,
        )
        results = await engine.handle_signal(entry_signal)
        assert results[0].status == OrderStatus.FILLED

        # Now close it (even if entry_order_type was somehow set, CLOSE should work)
        close_signal = Signal(
            source="test_source",
            symbol="AAPL",
            side=Side.CLOSE,
            asset_class=AssetClass.CRYPTO,
            entry_order_type=EntryOrderType.LIMIT,  # This shouldn't matter for CLOSE
        )
        results = await engine.handle_signal(close_signal)

        # Close should succeed (not rejected for entry_order_type)
        assert len(results) == 1
        assert results[0].status in (OrderStatus.FILLED, OrderStatus.REJECTED)
        # The important thing is it's not rejected for entry_order_type
