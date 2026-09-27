"""app/sources/ninjatrader.py: NinjaTrader as a signal SOURCE via fill
events reported by ninjascript/SignalCopierAutoJournal.cs. Payload field
names/semantics verified against Apex-Logics/TradVue's
TradVueAutoJournal.cs's own BuildPayload -- see that module's docstring.
"""
import pytest

from app.errors import SignalValidationError
from app.models import AssetClass, Side
from app.sources.ninjatrader import NinjaTraderSource


@pytest.fixture
def source():
    async def on_signal(signal):
        pass

    return NinjaTraderSource(on_signal=on_signal)


def _payload(**overrides):
    payload = {
        "ticker": "ES 12-24",
        "action": "entry",
        "direction": "Long",
        "price": 4500.25,
        "entry_price": 4500.25,
        "exit_price": 0,
        "qty": 2,
        "pnl": 0.0,
        "asset_class": "Futures",
        "order_id": "abc123",
        "time": "2026-01-15T12:00:00.000Z",
        "source": "ninjatrader",
    }
    payload.update(overrides)
    return payload


def test_long_entry_is_a_buy(source):
    signal = source.parse(_payload(action="entry", direction="Long"))
    assert signal.side == Side.BUY
    assert signal.symbol == "ES 12-24"
    assert signal.quantity == 2.0
    assert signal.price == 4500.25
    assert signal.asset_class == AssetClass.FUTURE


def test_short_entry_is_a_sell(source):
    signal = source.parse(_payload(action="entry", direction="Short"))
    assert signal.side == Side.SELL


def test_exit_is_always_close_regardless_of_direction(source):
    """A partial exit quantity is intentionally not modeled -- CLOSE
    resolves against the account's own tracked position (app/engine.py's
    _resolve_close), the same simplification MetaApiSource/RithmicSource
    already make for their own copy-source exit fills."""
    long_exit = source.parse(_payload(action="exit", direction="Long"))
    short_exit = source.parse(_payload(action="exit", direction="Short"))
    assert long_exit.side == Side.CLOSE
    assert short_exit.side == Side.CLOSE


def test_asset_class_mapping(source):
    assert source.parse(_payload(asset_class="Stock")).asset_class == AssetClass.EQUITY
    assert source.parse(_payload(asset_class="Futures")).asset_class == AssetClass.FUTURE
    assert source.parse(_payload(asset_class="Forex")).asset_class == AssetClass.FOREX


def test_raw_payload_is_retained(source):
    payload = _payload()
    signal = source.parse(payload)
    assert signal.raw == payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticker": None},
        {"ticker": ""},
        {"action": "modify"},
        {"direction": "Up"},
        {"qty": 0},
        {"qty": -1},
        {"qty": True},
        {"qty": "two"},
        {"price": "expensive"},
        {"price": True},
        {"asset_class": "Options"},
        {"asset_class": None},
    ],
)
def test_invalid_payloads_are_rejected(source, overrides):
    with pytest.raises(SignalValidationError):
        source.parse(_payload(**overrides))


@pytest.mark.asyncio
async def test_ingest_calls_on_signal():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = NinjaTraderSource(on_signal=on_signal)
    signal = await source.ingest(_payload())

    assert received == [signal]


@pytest.mark.asyncio
async def test_start_is_a_push_based_noop():
    source = NinjaTraderSource(on_signal=lambda s: None)
    assert await source.start() is None
