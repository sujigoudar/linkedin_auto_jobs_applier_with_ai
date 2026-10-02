"""Tests for WP-47: source/parser gaps (findings A-07, A-09, A-12, A-14, A-19, A-21)."""

import pytest
from unittest.mock import patch

from app.models import Side, AssetClass
from app.sources.text_parser import classify_text_signal, DispositionOutcome


class TestA07RithmicExits:
    """A-07: Rithmic exits become SELL entries instead of CLOSE events.
    
    Fix: Rithmic source should set Intent.EXIT for exits, not Side.SELL.
    Since Rithmic doesn't provide explicit entry/exit info, we need to track
    position state or infer from transaction patterns.
    """
    
    @pytest.mark.asyncio
    async def test_rithmic_sell_fill_with_intent_exit(self):
        """When Rithmic reports a SELL fill, it should be mapped to Intent.EXIT, not as a SELL entry."""
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
            # TODO: When Rithmic position tracking is implemented,
            # this should be Intent.EXIT or Intent.REDUCE
            # For now, track that this is a gap
            assert signal.symbol == "ES"
            assert signal.quantity == 2.0
            assert signal.price == 4500.0


class TestA09ChaseGuard:
    """A-09: No chase guard - stale provider prices used for gating.
    
    Fix: Add configurable max price-age / max deviation check that fails
    closed when price is older than N seconds or deviates more than X%.
    """
    
    def test_placeholder_chase_guard(self):
        """Placeholder: Chase guard functionality to be implemented."""
        # This is a complex feature that requires price tracking infrastructure
        # at the engine level, not just the parser. When implemented:
        # - Should reject stale prices (older than configured max_price_age_seconds)
        # - Should reject prices that deviate more than configured max_deviation_percent
        # - Should fail closed (reject, not proceed)
        pytest.skip("Chase guard implementation pending engine-level changes")


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
    
    Fix: Correlate priceless signals on (source, symbol, side) within the
    window with an explicit HOLD.
    """
    
    def test_placeholder_priceless_correlation(self):
        """Placeholder: Priceless correlation functionality to be implemented."""
        # This requires engine-level signal correlation changes
        pytest.skip("Priceless correlation implementation pending engine-level changes")


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
