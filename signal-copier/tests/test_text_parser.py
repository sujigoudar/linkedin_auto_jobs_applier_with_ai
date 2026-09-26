import pytest

from app.errors import SignalValidationError
from app.models import Side
from app.sources.text_parser import parse_text_signal


@pytest.mark.parametrize(
    "text,side,symbol,quantity,price,sl,tp",
    [
        ("BUY BTCUSDT", Side.BUY, "BTCUSDT", None, None, None, None),
        ("BUY BTCUSDT @ 65000", Side.BUY, "BTCUSDT", None, 65000.0, None, None),
        ("SELL EURUSD 0.50 lots SL 1.0950 TP 1.1050", Side.SELL, "EURUSD", 0.5, None, 1.0950, 1.1050),
        ("LONG AAPL 10 @ 190.25 SL 185 TP 200", Side.BUY, "AAPL", 10.0, 190.25, 185.0, 200.0),
        ("close ethusdt", Side.CLOSE, "ETHUSDT", None, None, None, None),
        ("Signal: SHORT XAUUSD 1 @ 2400 SL: 2420 TP: 2350", Side.SELL, "XAUUSD", 1.0, 2400.0, 2420.0, 2350.0),
    ],
)
def test_parse_text_signal(text, side, symbol, quantity, price, sl, tp):
    signal = parse_text_signal(text, source="test")
    assert signal.side == side
    assert signal.symbol == symbol
    assert signal.quantity == quantity
    assert signal.price == price
    assert signal.stop_loss == sl
    assert signal.take_profit == tp


def test_unparseable_text_raises():
    with pytest.raises(SignalValidationError):
        parse_text_signal("just chatting, nothing to trade here", source="test")


def test_analyst_is_passed_through():
    signal = parse_text_signal("BUY BTCUSDT", source="test", analyst="alice")
    assert signal.analyst == "alice"


def test_analyst_defaults_to_none():
    signal = parse_text_signal("BUY BTCUSDT", source="test")
    assert signal.analyst is None


@pytest.mark.parametrize(
    "text",
    [
        "DO NOT BUY AAPL 10",
        "please don't buy AAPL 10",
        "if price breaks resistance buy AAPL 10",
        "considering SELL EURUSD 0.5 lots",
        "BUY BTCUSDT -- actually wait, hold off",
        "SELL EURUSD 0.5 lots, cancel that",
    ],
)
def test_negated_or_conditional_commentary_is_refused_not_traded(text):
    """SIG-02: a plain substring match doesn't prove the message is actually
    giving that instruction -- these must be refused outright, not
    silently admitted as a real trade."""
    with pytest.raises(SignalValidationError):
        parse_text_signal(text, source="test")
