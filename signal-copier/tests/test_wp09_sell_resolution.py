"""Tests for WP-09: resolve SELL against the account's book.

When a signal arrives with Intent.SELL:
- If the account holds a same-symbol LONG → treat as EXIT
- If no long and allow_short=True → treat as ENTRY_SHORT
- If no long and allow_short=False → REJECTED
- Explicit Intent.ENTRY_SHORT on allow_short=False → REJECTED
"""
import pytest
from app.models import DestinationAccount, Intent, OrderStatus, Side, Signal
from app.engine import SignalCopierEngine
from app.db import SignalStore
from app.brokers.paper import PaperBroker
from app.sources.text_parser import parse_text_signal
from app.routing import RoutingConfig, RoutingRule
from app.lifecycle.manager import PositionLifecycleManager


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_sell_on_long_position_plain_account(store):
    """SELL on a plain account holding a long → closes the position."""
    account = DestinationAccount(
        account_id="test-account",
        broker="paper",
        managed_lifecycle=False,
        allow_short=False,
    )

    paper = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test-account"])],
        accounts={"test-account": account},
    )
    lifecycle_manager = PositionLifecycleManager(store)
    engine = SignalCopierEngine(
        routing=routing,
        brokers={"paper": paper},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )

    # Place an entry order to establish a long position
    entry_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.BUY,
        quantity=5.0,
        price=150.0,
    )
    entry_results = await engine.handle_signal(entry_signal)
    assert len(entry_results) == 1
    assert entry_results[0].status == OrderStatus.FILLED

    # Verify position is long
    position = store.get_position("test-account", "AAPL")
    assert position == 5.0

    # Send SELL signal without quantity → should close entire position
    sell_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.SELL,
        intent=Intent.SELL,
    )
    results = await engine.handle_signal(sell_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED

    # Verify position is now flat
    position = store.get_position("test-account", "AAPL")
    assert position == 0.0


@pytest.mark.asyncio
async def test_sell_flat_allow_short_false_rejected(store):
    """SELL on flat account with allow_short=False → REJECTED."""
    account = DestinationAccount(
        account_id="test-account",
        broker="paper",
        managed_lifecycle=False,
        allow_short=False,
    )

    paper = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test-account"])],
        accounts={"test-account": account},
    )
    lifecycle_manager = PositionLifecycleManager(store)
    engine = SignalCopierEngine(
        routing=routing,
        brokers={"paper": paper},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )

    # Account is flat, try to SELL
    sell_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.SELL,
        intent=Intent.SELL,
        quantity=10.0,
    )
    results = await engine.handle_signal(sell_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED
    assert "allow_short" in results[0].message.lower()


@pytest.mark.asyncio
async def test_sell_flat_allow_short_true_entry_short(store):
    """SELL on flat account with allow_short=True → opens short."""
    account = DestinationAccount(
        account_id="test-account",
        broker="paper",
        managed_lifecycle=False,
        allow_short=True,
    )

    paper = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test-account"])],
        accounts={"test-account": account},
    )
    lifecycle_manager = PositionLifecycleManager(store)
    engine = SignalCopierEngine(
        routing=routing,
        brokers={"paper": paper},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )

    # Account is flat, SELL should open short
    sell_signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.SELL,
        intent=Intent.SELL,
        quantity=10.0,
        price=150.0,
    )
    results = await engine.handle_signal(sell_signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.FILLED

    # Position should be -10 (short)
    position = store.get_position("test-account", "AAPL")
    assert position == -10.0


@pytest.mark.asyncio
async def test_explicit_entry_short_allow_short_false_rejected(store):
    """Explicit Intent.ENTRY_SHORT on allow_short=False → REJECTED."""
    account = DestinationAccount(
        account_id="test-account",
        broker="paper",
        managed_lifecycle=False,
        allow_short=False,
    )

    paper = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="test", destinations=["test-account"])],
        accounts={"test-account": account},
    )
    lifecycle_manager = PositionLifecycleManager(store)
    engine = SignalCopierEngine(
        routing=routing,
        brokers={"paper": paper},
        store=store,
        lifecycle_manager=lifecycle_manager,
    )

    # Explicit ENTRY_SHORT intent on allow_short=False → REJECTED
    signal = Signal(
        source="test",
        symbol="AAPL",
        side=Side.SELL,
        intent=Intent.ENTRY_SHORT,
        quantity=10.0,
    )
    results = await engine.handle_signal(signal)
    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED
    assert "allow_short" in results[0].message.lower()


# ============================================================================
# Parser/Webhook Tests (WP-08 completion)
# ============================================================================


def test_parser_short_keyword_sets_entry_short_intent():
    """Parser: 'SHORT' keyword → Intent.ENTRY_SHORT."""
    signal = parse_text_signal("SHORT AAPL 10", source="test")
    assert signal.side == Side.SELL
    assert signal.intent == Intent.ENTRY_SHORT
    assert signal.quantity == 10.0


def test_parser_sell_keyword_sets_sell_intent():
    """Parser: 'SELL' keyword → Intent.SELL."""
    signal = parse_text_signal("SELL AAPL 10", source="test")
    assert signal.side == Side.SELL
    assert signal.intent == Intent.SELL
    assert signal.quantity == 10.0


def test_parser_close_keyword_sets_exit_intent():
    """Parser: 'CLOSE' keyword → Intent.EXIT."""
    signal = parse_text_signal("CLOSE AAPL", source="test")
    assert signal.side == Side.CLOSE
    assert signal.intent == Intent.EXIT


def test_parser_exit_keyword_sets_exit_intent():
    """Parser: 'EXIT' keyword → Intent.EXIT."""
    signal = parse_text_signal("EXIT AAPL", source="test")
    assert signal.side == Side.CLOSE
    assert signal.intent == Intent.EXIT


def test_parser_trim_half_sets_reduce_intent():
    """Parser: 'TRIM half' → Intent.REDUCE with reduce_fraction=0.5."""
    signal = parse_text_signal("TRIM half AAPL", source="test")
    assert signal.side == Side.CLOSE
    assert signal.intent == Intent.REDUCE
    assert signal.reduce_fraction == 0.5


def test_parser_reduce_all_sets_reduce_intent():
    """Parser: 'REDUCE all' → Intent.REDUCE with reduce_fraction=1.0."""
    signal = parse_text_signal("REDUCE all AAPL", source="test")
    assert signal.side == Side.CLOSE
    assert signal.intent == Intent.REDUCE
    assert signal.reduce_fraction == 1.0


def test_parser_reduce_percentage_sets_fraction():
    """Parser: 'REDUCE 25%' → Intent.REDUCE with reduce_fraction=0.25."""
    signal = parse_text_signal("REDUCE 25% AAPL", source="test")
    assert signal.side == Side.CLOSE
    assert signal.intent == Intent.REDUCE
    assert signal.reduce_fraction == 0.25


def test_parser_close_half_sets_reduce_intent():
    """Parser: 'CLOSE half' → Intent.REDUCE with reduce_fraction=0.5."""
    signal = parse_text_signal("CLOSE half AAPL", source="test")
    assert signal.side == Side.CLOSE
    assert signal.intent == Intent.REDUCE
    assert signal.reduce_fraction == 0.5


def test_webhook_accepts_intent_field():
    """Webhook: optional 'intent' field is parsed and validated."""
    from app.sources.webhook import WebhookSource

    async def dummy_signal_handler(signal):
        pass

    webhook = WebhookSource(on_signal=dummy_signal_handler)
    payload = {
        "symbol": "AAPL",
        "side": "buy",
        "intent": "entry_long",
        "quantity": 10,
    }
    signal = webhook.parse(payload)
    assert signal.intent == Intent.ENTRY_LONG


def test_webhook_accepts_reduce_fraction_field():
    """Webhook: optional 'reduce_fraction' field is parsed and validated."""
    from app.sources.webhook import WebhookSource

    async def dummy_signal_handler(signal):
        pass

    webhook = WebhookSource(on_signal=dummy_signal_handler)
    payload = {
        "symbol": "AAPL",
        "side": "close",
        "intent": "reduce",
        "reduce_fraction": 0.5,
    }
    signal = webhook.parse(payload)
    assert signal.intent == Intent.REDUCE
    assert signal.reduce_fraction == 0.5


def test_webhook_rejects_invalid_intent():
    """Webhook: invalid intent value is rejected."""
    from app.sources.webhook import WebhookSource
    from app.errors import SignalValidationError

    async def dummy_signal_handler(signal):
        pass

    webhook = WebhookSource(on_signal=dummy_signal_handler)
    payload = {
        "symbol": "AAPL",
        "side": "buy",
        "intent": "invalid_intent",
    }
    with pytest.raises(SignalValidationError, match="invalid intent"):
        webhook.parse(payload)


def test_webhook_rejects_reduce_fraction_gt_one():
    """Webhook: reduce_fraction > 1.0 is rejected."""
    from app.sources.webhook import WebhookSource
    from app.errors import SignalValidationError

    async def dummy_signal_handler(signal):
        pass

    webhook = WebhookSource(on_signal=dummy_signal_handler)
    payload = {
        "symbol": "AAPL",
        "side": "close",
        "intent": "reduce",
        "reduce_fraction": 1.5,
    }
    with pytest.raises(SignalValidationError, match="reduce_fraction must be <= 1.0"):
        webhook.parse(payload)
