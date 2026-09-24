import pytest

from app.models import AssetClass, Side
from app.sources.webhook import SignalValidationError, WebhookSource


@pytest.mark.asyncio
async def test_parse_valid_payload():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = WebhookSource(on_signal=on_signal)
    signal = source.parse(
        {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": 0.5,
            "price": 65000,
            "asset_class": "crypto",
        },
        source_override="tradingview",
    )

    assert signal.source == "tradingview"
    assert signal.symbol == "BTCUSDT"
    assert signal.side == Side.BUY
    assert signal.asset_class == AssetClass.CRYPTO
    assert signal.quantity == 0.5
    assert signal.price == 65000


def test_missing_symbol_raises():
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"side": "buy"})


def test_missing_side_raises():
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT"})


def test_invalid_side_raises():
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT", "side": "yolo"})


@pytest.mark.asyncio
async def test_ingest_calls_on_signal():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = WebhookSource(on_signal=on_signal)
    await source.ingest({"symbol": "ETHUSDT", "side": "sell"})

    assert len(received) == 1
    assert received[0].symbol == "ETHUSDT"
