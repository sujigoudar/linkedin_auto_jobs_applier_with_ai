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


def test_parse_reads_analyst_field():
    source = WebhookSource(on_signal=None)
    signal = source.parse({"symbol": "BTCUSDT", "side": "buy", "analyst": "alice"})
    assert signal.analyst == "alice"


def test_parse_without_analyst_field_leaves_it_none():
    source = WebhookSource(on_signal=None)
    signal = source.parse({"symbol": "BTCUSDT", "side": "buy"})
    assert signal.analyst is None


# -- Multi-provider representability: ordered targets, entry range, ---------
# -- entry order type, provider message identity -----------------------------


def test_parse_reads_ordered_multiple_targets():
    source = WebhookSource(on_signal=None)
    signal = source.parse(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "targets": [
                {"price": 68000, "fraction": 0.5, "label": "TP1"},
                {"price": 70000, "fraction": 0.5, "label": "TP2"},
            ],
        }
    )
    assert [t.label for t in signal.targets] == ["TP1", "TP2"]
    assert signal.targets[0].price == 68000
    # Back-compat: no bare take_profit given, so the first target's price
    # becomes the single/primary take_profit value.
    assert signal.take_profit == 68000


def test_parse_bare_take_profit_is_not_overridden_by_targets():
    source = WebhookSource(on_signal=None)
    signal = source.parse(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "take_profit": 69000,
            "targets": [{"price": 68000}, {"price": 70000}],
        }
    )
    assert signal.take_profit == 69000
    assert len(signal.targets) == 2


def test_parse_without_targets_leaves_empty_list():
    source = WebhookSource(on_signal=None)
    signal = source.parse({"symbol": "BTCUSDT", "side": "buy"})
    assert signal.targets == []


def test_malformed_targets_field_raises_not_500():
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT", "side": "buy", "targets": "not-a-list"})
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT", "side": "buy", "targets": [{"label": "TP1"}]})  # missing price
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT", "side": "buy", "targets": [{"price": 100, "fraction": 1.5}]})


def test_parse_reads_entry_range_and_order_type():
    source = WebhookSource(on_signal=None)
    signal = source.parse(
        {
            "symbol": "BTCUSDT", "side": "buy",
            "price_low": 64800, "price_high": 65200, "entry_order_type": "limit",
        }
    )
    assert signal.price_low == 64800
    assert signal.price_high == 65200
    assert signal.entry_order_type.value == "limit"


def test_parse_populates_provider_message_identity_when_given():
    source = WebhookSource(on_signal=None)
    signal = source.parse(
        {"symbol": "BTCUSDT", "side": "buy", "message_id": "alert-123"},
        source_override="tradingview",
    )
    assert signal.channel_id == "tradingview"
    assert signal.message_id == "alert-123"
    assert signal.parser_version == "webhook-json-v1"


def test_parse_without_message_id_leaves_it_none():
    source = WebhookSource(on_signal=None)
    signal = source.parse({"symbol": "BTCUSDT", "side": "buy"})
    assert signal.message_id is None


@pytest.mark.asyncio
async def test_ingest_emits_source_event_original_when_handler_wired():
    received = []
    events = []

    async def on_signal(signal):
        received.append(signal)

    async def on_source_event(event):
        events.append(event)

    source = WebhookSource(on_signal=on_signal, on_source_event=on_source_event)
    await source.ingest({"symbol": "ETHUSDT", "side": "sell", "message_id": "evt-1"})

    assert len(events) == 1
    from app.models import SourceEventKind

    assert events[0].kind == SourceEventKind.ORIGINAL
    assert events[0].message_id == "evt-1"
    assert events[0].signal is received[0]


@pytest.mark.asyncio
async def test_ingest_without_source_event_handler_is_a_safe_no_op():
    received = []

    async def on_signal(signal):
        received.append(signal)

    source = WebhookSource(on_signal=on_signal)
    await source.ingest({"symbol": "ETHUSDT", "side": "sell"})

    assert len(received) == 1


# -- Mutation resistance: critical boundary conditions and guard logic --------


def test_empty_symbol_raises():
    """Empty string symbol must be rejected, not treated as falsy-but-present."""
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "", "side": "buy"})


def test_empty_side_raises():
    """Empty string side must be rejected, not treated as falsy-but-present."""
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT", "side": ""})


def test_invalid_asset_class_raises():
    """Invalid asset_class must raise, not silently default."""
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({"symbol": "BTCUSDT", "side": "buy", "asset_class": "invalid_class"})


def test_parse_reads_alert_id_fallback_for_message_id():
    """'alert_id' should be read as fallback to 'message_id'."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({"symbol": "BTCUSDT", "side": "buy", "alert_id": "alert-456"})
    assert signal.message_id == "alert-456"


def test_parse_reads_id_fallback_for_message_id():
    """'id' should be read as final fallback to 'message_id'."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({"symbol": "BTCUSDT", "side": "buy", "id": "id-789"})
    assert signal.message_id == "id-789"


def test_parse_message_id_priority_over_alert_id():
    """'message_id' should take priority over 'alert_id' when both present."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({
        "symbol": "BTCUSDT",
        "side": "buy",
        "message_id": "msg-123",
        "alert_id": "alert-456"
    })
    assert signal.message_id == "msg-123"


def test_parse_alert_id_priority_over_id():
    """'alert_id' should take priority over 'id' when both present."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({
        "symbol": "BTCUSDT",
        "side": "buy",
        "alert_id": "alert-456",
        "id": "id-789"
    })
    assert signal.message_id == "alert-456"


def test_target_fraction_boundary_exactly_one():
    """Target fraction of exactly 1.0 should be accepted."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({
        "symbol": "BTCUSDT",
        "side": "buy",
        "targets": [{"price": 70000, "fraction": 1.0}]
    })
    assert signal.targets[0].fraction == 1.0


def test_target_with_zero_fraction_raises():
    """Target fraction of 0 should be rejected (must be > 0)."""
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({
            "symbol": "BTCUSDT",
            "side": "buy",
            "targets": [{"price": 70000, "fraction": 0}]
        })


def test_target_with_negative_fraction_raises():
    """Target fraction of negative value should be rejected."""
    source = WebhookSource(on_signal=None)
    with pytest.raises(SignalValidationError):
        source.parse({
            "symbol": "BTCUSDT",
            "side": "buy",
            "targets": [{"price": 70000, "fraction": -0.5}]
        })


def test_target_quantity_optional():
    """Target quantity should be optional (None when not provided)."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({
        "symbol": "BTCUSDT",
        "side": "buy",
        "targets": [{"price": 70000}]
    })
    assert signal.targets[0].quantity is None


def test_target_label_optional():
    """Target label should be optional (None when not provided)."""
    source = WebhookSource(on_signal=None)
    signal = source.parse({
        "symbol": "BTCUSDT",
        "side": "buy",
        "targets": [{"price": 70000}]
    })
    assert signal.targets[0].label is None


def test_case_insensitive_side():
    """Side parsing should be case-insensitive (buy, BUY, Buy, etc.)."""
    source = WebhookSource(on_signal=None)
    for side_str in ["buy", "BUY", "Buy", "bUy"]:
        signal = source.parse({"symbol": "BTCUSDT", "side": side_str})
        assert signal.side == Side.BUY

    for side_str in ["sell", "SELL", "Sell", "sELl"]:
        signal = source.parse({"symbol": "BTCUSDT", "side": side_str})
        assert signal.side == Side.SELL

    for side_str in ["close", "CLOSE", "Close", "cLOsE"]:
        signal = source.parse({"symbol": "BTCUSDT", "side": side_str})
        assert signal.side == Side.CLOSE


def test_case_insensitive_asset_class():
    """Asset class parsing should be case-insensitive."""
    source = WebhookSource(on_signal=None)
    for ac_str in ["crypto", "CRYPTO", "Crypto", "cRYPTO"]:
        signal = source.parse({"symbol": "BTCUSDT", "side": "buy", "asset_class": ac_str})
        assert signal.asset_class == AssetClass.CRYPTO

    for ac_str in ["equity", "EQUITY", "Equity", "eQUITY"]:
        signal = source.parse({"symbol": "AAPL", "side": "buy", "asset_class": ac_str})
        assert signal.asset_class == AssetClass.EQUITY


@pytest.mark.asyncio
async def test_ingest_returns_signal():
    """Ingest should return the parsed signal."""
    async def on_signal(signal):
        pass

    source = WebhookSource(on_signal=on_signal)
    signal = await source.ingest({"symbol": "ETHUSDT", "side": "sell"})
    assert signal.symbol == "ETHUSDT"
    assert signal.side == Side.SELL


def test_parse_populates_raw_fields():
    """Parse should populate both 'raw' and 'raw_source_event' with the payload."""
    source = WebhookSource(on_signal=None)
    payload = {"symbol": "BTCUSDT", "side": "buy", "quantity": 1.5}
    signal = source.parse(payload)
    assert signal.raw == payload
    assert signal.raw_source_event == payload
