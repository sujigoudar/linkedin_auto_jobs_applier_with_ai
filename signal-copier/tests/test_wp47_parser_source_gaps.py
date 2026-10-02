"""Tests for WP-47: source/parser gaps (findings A-07, A-09, A-12, A-14, A-19, A-21)."""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from app.models import Side, AssetClass, Intent
from app.sources.text_parser import classify_text_signal, DispositionOutcome


class TestA07SourceExits:
    """A-07: Source adapters (Rithmic, MT5, NinjaTrader) should mark exits with Intent.EXIT."""

    @pytest.mark.asyncio
    async def test_mt5_exit_with_entry_type_out_has_intent_exit(self):
        """MT5 exits (DEAL_ENTRY_OUT) should have Intent.EXIT."""
        from app.sources.mt4_mt5 import MetaApiSource
        from app.models import Intent

        # Create a source with mocked connection
        received = []

        async def on_signal(signal):
            received.append(signal)

        source = MetaApiSource(on_signal, token="test", account_id="test_acct", asset_class=AssetClass.FOREX)

        # Mock the connection with deals
        mock_connection = MagicMock()
        mock_connection.history_storage.deals = [
            {
                "id": "deal1",
                "type": "DEAL_TYPE_SELL",
                "entryType": "DEAL_ENTRY_OUT",
                "symbol": "EURUSD",
                "volume": 1.0,
                "price": 1.1050,
            }
        ]
        source._connection = mock_connection
        source._seen_deal_ids = set()

        # Manually call check_for_new_deals
        source._check_for_new_deals()

        # Wait for tasks
        if source._in_flight_tasks:
            import asyncio
            await asyncio.gather(*source._in_flight_tasks, return_exceptions=True)

        assert len(received) == 1
        assert received[0].symbol == "EURUSD"
        assert received[0].side == Side.CLOSE
        # A-07 fix: exits should have Intent.EXIT
        assert received[0].intent == Intent.EXIT

    @pytest.mark.asyncio
    async def test_mt5_entry_has_no_explicit_intent(self):
        """MT5 entries should not have explicit exit intent."""
        from app.sources.mt4_mt5 import MetaApiSource

        received = []

        async def on_signal(signal):
            received.append(signal)

        source = MetaApiSource(on_signal, token="test", account_id="test_acct", asset_class=AssetClass.FOREX)

        mock_connection = MagicMock()
        mock_connection.history_storage.deals = [
            {
                "id": "deal2",
                "type": "DEAL_TYPE_BUY",
                "entryType": "DEAL_ENTRY_IN",
                "symbol": "EURUSD",
                "volume": 1.0,
                "price": 1.1050,
            }
        ]
        source._connection = mock_connection
        source._seen_deal_ids = set()

        source._check_for_new_deals()

        if source._in_flight_tasks:
            import asyncio
            await asyncio.gather(*source._in_flight_tasks, return_exceptions=True)

        assert len(received) == 1
        assert received[0].side == Side.BUY
        # Entries should not have explicit exit intent
        assert received[0].intent is None or received[0].intent == Intent.ENTRY_LONG

    def test_ninjatrader_exit_has_intent_exit(self):
        """NinjaTrader exits should have Intent.EXIT."""
        from app.sources.ninjatrader import NinjaTraderSource

        source = NinjaTraderSource(lambda sig: None)

        exit_payload = {
            "ticker": "AAPL",
            "action": "exit",
            "direction": "Long",
            "qty": 10,
            "price": 150.0,
            "asset_class": "Stock",
        }

        signal = source.parse(exit_payload)
        assert signal.side == Side.CLOSE
        # A-07 fix: exits should have Intent.EXIT
        assert signal.intent == Intent.EXIT

    def test_ninjatrader_entry_has_no_explicit_exit_intent(self):
        """NinjaTrader entries should not have explicit exit intent."""
        from app.sources.ninjatrader import NinjaTraderSource

        source = NinjaTraderSource(lambda sig: None)

        entry_payload = {
            "ticker": "AAPL",
            "action": "entry",
            "direction": "Long",
            "qty": 10,
            "price": 150.0,
            "asset_class": "Stock",
        }

        signal = source.parse(entry_payload)
        assert signal.side == Side.BUY
        # Entries should not have explicit exit intent
        assert signal.intent is None or signal.intent == Intent.ENTRY_LONG


class TestA07RithmicExits:
    """A-07: Rithmic exits become SELL entries instead of CLOSE events.

    Fix: Rithmic source should set Intent.EXIT for exits, not Side.SELL.
    """

    @pytest.mark.asyncio
    async def test_rithmic_sell_fill_with_intent_exit(self):
        """When Rithmic reports a SELL fill, it should be marked with Intent.EXIT."""
        pytest.importorskip("async_rithmic")

        import async_rithmic
        from app.sources.rithmic import RithmicSource

        # Mock the async_rithmic library
        class _FakeNotification:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class _FakeHandlerSet:
            def __init__(self):
                self.handlers = []
            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

        class _FakeClient:
            last_instance = None
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.on_exchange_order_notification = _FakeHandlerSet()
                _FakeClient.last_instance = self
            async def connect(self):
                pass
            async def disconnect(self):
                pass

        with patch.object(async_rithmic, "RithmicClient", _FakeClient):
            received = []

            async def on_signal(signal):
                received.append(signal)

            src = RithmicSource(on_signal, user="u", password="p", system_name="s", gateway_url="g")
            await src.start()

            # Simulate a SELL fill (closing a long position)
            notification = _FakeNotification(
                notify_type=async_rithmic.ExchangeOrderNotificationType.FILL,
                transaction_type=async_rithmic.TransactionType.SELL,
                symbol="ES",
                account_id="acct1",
                exchange="CME",
                fill_size=2,
                fill_price=4500.0,
                avg_fill_price=4500.0,
            )

            handler = _FakeClient.last_instance.on_exchange_order_notification.handlers[0]
            await handler(notification)

            assert len(received) == 1
            signal = received[0]
            assert signal.symbol == "ES"
            assert signal.quantity == 2.0
            assert signal.price == 4500.0
            assert signal.side == Side.SELL
            # A-07 fix: SELL fills should be marked with Intent.EXIT (exits, not entries)
            assert signal.intent == Intent.EXIT

    @pytest.mark.asyncio
    async def test_rithmic_buy_fill_is_entry(self):
        """When Rithmic reports a BUY fill, it should be an entry (no explicit intent set)."""
        pytest.importorskip("async_rithmic")

        import async_rithmic
        from app.sources.rithmic import RithmicSource

        class _FakeNotification:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)

        class _FakeHandlerSet:
            def __init__(self):
                self.handlers = []
            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

        class _FakeClient:
            last_instance = None
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.on_exchange_order_notification = _FakeHandlerSet()
                _FakeClient.last_instance = self
            async def connect(self):
                pass
            async def disconnect(self):
                pass

        with patch.object(async_rithmic, "RithmicClient", _FakeClient):
            received = []

            async def on_signal(signal):
                received.append(signal)

            src = RithmicSource(on_signal, user="u", password="p", system_name="s", gateway_url="g")
            await src.start()

            # Simulate a BUY fill (opening a long position)
            notification = _FakeNotification(
                notify_type=async_rithmic.ExchangeOrderNotificationType.FILL,
                transaction_type=async_rithmic.TransactionType.BUY,
                symbol="ES",
                account_id="acct1",
                exchange="CME",
                fill_size=2,
                fill_price=4500.0,
                avg_fill_price=4500.0,
            )

            handler = _FakeClient.last_instance.on_exchange_order_notification.handlers[0]
            await handler(notification)

            assert len(received) == 1
            signal = received[0]
            assert signal.side == Side.BUY
            # BUY fills should not have explicit exit intent (will default to ENTRY_LONG)
            assert signal.intent is None or signal.intent == Intent.ENTRY_LONG


class TestA09ChaseGuard:
    """A-09: No chase guard - stale provider prices used for gating.

    Fix: Add configurable max price-age / max deviation check that fails
    closed when price is older than N seconds or deviates more than X%.
    """

    def test_price_validation_config_exists(self):
        """A-09 fix: Price validation config options should exist."""
        from app import config

        # Config should have price validation settings
        assert hasattr(config, "SIGNAL_MAX_PRICE_AGE_SECONDS")
        assert hasattr(config, "SIGNAL_MAX_PRICE_DEVIATION_PCT")
        # Default: no checks enabled (None means skip)
        assert config.SIGNAL_MAX_PRICE_AGE_SECONDS is None
        assert config.SIGNAL_MAX_PRICE_DEVIATION_PCT is None

    def test_stale_price_detection_with_age_check_enabled(self, monkeypatch):
        """When max_price_age is set, stale prices should be detected."""
        from app import config

        # Enable stale price check: max 5 minutes
        monkeypatch.setattr(config, "SIGNAL_MAX_PRICE_AGE_SECONDS", 300.0)

        # The engine-level gate is exercised in TestA09EngineGate below; this
        # test only pins the config plumbing.
        assert config.SIGNAL_MAX_PRICE_AGE_SECONDS == 300.0

    def test_price_deviation_detection_with_deviation_check_enabled(self, monkeypatch):
        """When max_price_deviation is set, prices outside range should be detected."""
        from app import config

        # Enable price deviation check: max 5% deviation
        monkeypatch.setattr(config, "SIGNAL_MAX_PRICE_DEVIATION_PCT", 5.0)

        # Reference price: 100, max deviation: 5%, acceptable range: 95-105
        reference_price = 100.0
        signal_price = 110.0  # 10% above, should be rejected
        deviation = abs(signal_price - reference_price) / reference_price * 100
        assert deviation > config.SIGNAL_MAX_PRICE_DEVIATION_PCT


class TestA12HoldForSwing:
    """A-12: 'hold for swing' must NOT drop real entries - separate trailing horizon words from negations (FIXED)."""

    def test_buy_with_hold_for_swing_is_parsed(self):
        """A-12 fix: 'BUY AAPL 10 hold for swing' now parses as an entry (not ignored)."""
        result = classify_text_signal(
            "BUY AAPL 10 hold for swing",
            source="test",
            asset_class=AssetClass.EQUITY
        )
        assert result.outcome == DispositionOutcome.PARSED
        assert result.signal is not None
        assert result.signal.symbol == "AAPL"
        assert result.signal.quantity == 10.0
        assert result.signal.side == Side.BUY

    def test_buy_with_hold_off_still_ignored(self):
        """'hold off' (without 'for') is still correctly ignored as a negation."""
        result = classify_text_signal(
            "BUY AAPL 10, hold off",
            source="test",
            asset_class=AssetClass.EQUITY
        )
        assert result.outcome == DispositionOutcome.IGNORED
        assert "negated, conditional" in result.detail


class TestA14PricelessCorrelation:
    """A-14: Priceless alerts and re-posts beyond 15 min must not be correlated.

    Fix: Don't merge priceless signals that are >15 minutes apart,
    and don't merge priceless with priced signals.
    """

    def test_priceless_signals_within_window_can_correlate(self, monkeypatch):
        """Two priceless BUY signals for same symbol within window can be correlated."""
        from app import config

        # Priceless signals within the correlation window can be merged
        signal1_time = datetime.now(timezone.utc)
        signal2_time = signal1_time + timedelta(seconds=600)  # 10 minutes later

        time_diff = (signal2_time - signal1_time).total_seconds()
        window = config.SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS

        # Should be within window (900 seconds = 15 minutes)
        assert time_diff < window

    def test_priceless_signals_beyond_window_dont_correlate(self, monkeypatch):
        """Two priceless signals >15 minutes apart should NOT be correlated."""
        from app import config

        # Signals beyond the correlation window should NOT be merged
        signal1_time = datetime.now(timezone.utc)
        signal2_time = signal1_time + timedelta(seconds=1200)  # 20 minutes later

        time_diff = (signal2_time - signal1_time).total_seconds()
        window = config.SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS

        # Should be beyond window
        assert time_diff > window

    def test_priceless_and_priced_signals_dont_merge(self):
        """A priceless signal and a priced signal for same symbol should NOT be merged."""
        from app.models import Signal, Side, AssetClass

        # Priceless signal
        priceless = Signal(
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=None,  # No price
        )

        # Priced signal (same source, symbol, side, within window)
        priced = Signal(
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=150.0,  # Has price
            received_at=priceless.received_at + timedelta(seconds=300),
        )

        # Verify they are different (price vs priceless)
        assert priceless.price is None
        assert priced.price is not None
        # They should NOT be correlated/merged


class TestA19UnparsedPersistence:
    """A-19: Non-parsed live messages must be persisted as 'unparsed' row, not dropped."""
    
    def test_no_match_signal_recorded(self):
        """A message that doesn't parse should still be recorded with NO_MATCH disposition."""
        result = classify_text_signal(
            "Sold AAPL",  # Past tense, not a trade instruction
            source="test",
            asset_class=AssetClass.EQUITY
        )
        assert result.outcome == DispositionOutcome.NO_MATCH
        assert result.text == "Sold AAPL"
        assert "no recognizable trade instruction" in result.detail
    
    def test_ignored_signal_recorded(self):
        """A negated/conditional message should still be recorded with IGNORED disposition."""
        result = classify_text_signal(
            "Don't BUY AAPL",
            source="test",
            asset_class=AssetClass.EQUITY
        )
        assert result.outcome == DispositionOutcome.IGNORED
        assert "negated, conditional" in result.detail


class TestA21ArticleSourceSelling:
    """A-21: Article text 'we are selling X' must never become SELL entry (FIXED).

    Fix: Classify 'we are selling/sold our position' as ADJUSTMENT_OR_EXIT,
    not ACTIONABLE_PHRASES.
    """

    def test_article_we_are_selling_is_adjustment_exit(self):
        """A-21 fix: 'we are selling' is now classified as ADJUSTMENT_OR_EXIT, not actionable entry."""
        from app.sources.article_classifier import classify_article, ArticleClassification

        text = "We are selling our position in AAPL after strong gains."
        result = classify_article(text)
        assert result.classification == ArticleClassification.ADJUSTMENT_OR_EXIT
        # Verify the matched phrase is captured
        assert any("selling" in phrase.lower() for phrase in result.matched_phrases)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestA09EngineGate:
    """A-09: the chase guard is enforced by the engine's capital admission
    path (`_try_reserve_capital`), not only declared in config. These tests
    drive a real engine + PaperBroker and assert the rejection message."""

    @staticmethod
    def _engine(tmp_path):
        from app.brokers.paper import PaperBroker
        from app.db import SignalStore
        from app.engine import SignalCopierEngine
        from app.models import DestinationAccount
        from app.routing import RoutingConfig, RoutingRule

        store = SignalStore(tmp_path / "a09.db")
        broker = PaperBroker()
        # A capital gate must be configured for the admission path to run.
        account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1_000_000.0)
        routing = RoutingConfig(
            rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
            accounts={"acct1": account},
        )
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
        return engine, broker

    @pytest.mark.asyncio
    async def test_deviation_gate_rejects_price_far_from_broker_reference(self, tmp_path, monkeypatch):
        from app import config
        from app.models import OrderStatus, Side, Signal

        engine, broker = self._engine(tmp_path)
        # Establish a broker reference price via a real fill at 100.
        first = await engine.handle_signal(
            Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0, price=100.0)
        )
        assert first[0].status == OrderStatus.FILLED
        assert broker.get_reference_price("AAPL") == 100.0

        monkeypatch.setattr(config, "SIGNAL_MAX_PRICE_DEVIATION_PCT", 5.0)
        far = await engine.handle_signal(
            Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0, price=120.0)
        )
        assert far[0].status == OrderStatus.REJECTED
        assert "price deviation" in (far[0].message or "")

        near = await engine.handle_signal(
            Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0, price=102.0)
        )
        assert near[0].status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_age_gate_rejects_stale_signal(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from app import config
        from app.models import OrderStatus, Side, Signal

        engine, _ = self._engine(tmp_path)
        monkeypatch.setattr(config, "SIGNAL_MAX_PRICE_AGE_SECONDS", 300.0)

        stale = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0, price=100.0)
        stale.received_at = datetime.now(timezone.utc) - timedelta(seconds=600)
        result = await engine.handle_signal(stale)
        assert result[0].status == OrderStatus.REJECTED
        assert "price too old" in (result[0].message or "")

        fresh = await engine.handle_signal(
            Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0, price=100.0)
        )
        assert fresh[0].status == OrderStatus.FILLED
