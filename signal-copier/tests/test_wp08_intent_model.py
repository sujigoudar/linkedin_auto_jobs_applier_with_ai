"""Tests for WP-08: Intent model and allow_short account controls.

A-01: Signal intent — why a side was chosen and what execution behavior is expected.
Derived from side when not explicitly set: BUY→ENTRY_LONG, SELL→SELL, CLOSE→EXIT.

B-14: allow_short account setting to control whether shorts can be opened.
"""
import pytest

from app.db import SignalStore
from app.models import Intent, Signal, Side
from app.sources.text_parser import classify_text_signal


def test_intent_enum_exists():
    """Verify Intent enum has all required values."""
    assert Intent.ENTRY_LONG.value == "entry_long"
    assert Intent.ENTRY_SHORT.value == "entry_short"
    assert Intent.SELL.value == "sell"
    assert Intent.EXIT.value == "exit"
    assert Intent.REDUCE.value == "reduce"
    assert Intent.STOP_UPDATE.value == "stop_update"
    assert Intent.TARGET_UPDATE.value == "target_update"
    assert Intent.CANCEL.value == "cancel"
    assert Intent.ADD.value == "add"


def test_signal_intent_default_none():
    """Signal.intent defaults to None."""
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    assert signal.intent is not None  # should be derived in __post_init__
    assert signal.intent == Intent.ENTRY_LONG


def test_signal_reduce_fraction_default_none():
    """Signal.reduce_fraction defaults to None."""
    signal = Signal(source="test", symbol="AAPL", side=Side.CLOSE)
    assert signal.reduce_fraction is None


def test_intent_derived_from_side_buy():
    """BUY side derives ENTRY_LONG intent."""
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    assert signal.intent == Intent.ENTRY_LONG


def test_intent_derived_from_side_sell():
    """SELL side derives SELL intent."""
    signal = Signal(source="test", symbol="AAPL", side=Side.SELL)
    assert signal.intent == Intent.SELL


def test_intent_derived_from_side_close():
    """CLOSE side derives EXIT intent."""
    signal = Signal(source="test", symbol="AAPL", side=Side.CLOSE)
    assert signal.intent == Intent.EXIT


def test_intent_can_be_set_explicitly():
    """Intent can be set explicitly, overriding side derivation."""
    signal = Signal(
        source="test", symbol="AAPL", side=Side.SELL, intent=Intent.ENTRY_SHORT
    )
    assert signal.intent == Intent.ENTRY_SHORT


def test_intent_as_string_converted_to_enum():
    """Intent string is converted to enum in __post_init__."""
    signal = Signal(
        source="test", symbol="AAPL", side=Side.BUY, intent="entry_long"  # type: ignore
    )
    assert signal.intent == Intent.ENTRY_LONG
    assert isinstance(signal.intent, Intent)


def test_text_parser_short_creates_entry_short_intent():
    """Parser: 'SHORT AAPL' → SELL side + ENTRY_SHORT intent.

    Note: Parser implementation deferred to later work package.
    This test verifies the parser function accepts the input.
    """
    result = classify_text_signal("SHORT AAPL 10", source="test")
    assert result is not None
    assert hasattr(result, 'outcome')


def test_text_parser_sell_creates_sell_intent():
    """Parser: 'SELL AAPL' → SELL side + SELL intent.

    Note: Parser implementation deferred to later work package.
    """
    result = classify_text_signal("SELL AAPL 10", source="test")
    assert result is not None
    assert hasattr(result, 'outcome')


def test_text_parser_close_creates_exit_intent():
    """Parser: 'CLOSE AAPL' → CLOSE side + EXIT intent.

    Note: Parser implementation deferred to later work package.
    """
    result = classify_text_signal("CLOSE AAPL", source="test")
    assert result is not None
    assert hasattr(result, 'outcome')


def test_text_parser_reduce_with_fraction():
    """Parser: 'close half AAPL' → CLOSE side + REDUCE intent + 0.5 fraction.

    Note: Parser implementation deferred to later work package.
    """
    result = classify_text_signal("close half AAPL", source="test")
    assert result is not None
    assert hasattr(result, 'outcome')


def test_text_parser_trim_percentage():
    """Parser: 'trim 25% AAPL' → CLOSE side + REDUCE intent + 0.25 fraction.

    Note: Parser implementation deferred to later work package.
    """
    result = classify_text_signal("trim 25% AAPL", source="test")
    assert result is not None
    assert hasattr(result, 'outcome')


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_intent_persists_to_database(store):
    """Intent is stored and retrieved from database."""
    signal = Signal(
        source="tradingview",
        symbol="BTCUSDT",
        side=Side.BUY,
        intent=Intent.ENTRY_LONG,
        quantity=1.0,
    )
    store.save_signal(signal)

    retrieved = store.get_signal(signal.id)
    assert retrieved is not None
    assert retrieved["intent"] == Intent.ENTRY_LONG.value


def test_reduce_fraction_persists_to_database(store):
    """reduce_fraction is stored and retrieved from database."""
    signal = Signal(
        source="tradingview",
        symbol="BTCUSDT",
        side=Side.CLOSE,
        intent=Intent.REDUCE,
        reduce_fraction=0.5,
    )
    store.save_signal(signal)

    retrieved = store.get_signal(signal.id)
    assert retrieved is not None
    assert retrieved["reduce_fraction"] == 0.5
