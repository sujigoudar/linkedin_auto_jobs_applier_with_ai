"""app/export_events.py's own `build_execution_applied_envelope` and
`build_source_receipt_envelope` -- unit tests isolating each builder's
guard conditions directly (not through the full engine), since a
REJECTED/ERROR OrderResult in this codebase's own current adapters also
happens to leave broker_order_id/filled_quantity/filled_price unset,
which would make the FILLED-only check look redundant if only
exercised end-to-end through the engine."""
from datetime import datetime, timezone

import pytest
from signal_platform_contracts import EventType, Environment, EvidenceClass

from app.export_events import build_execution_applied_envelope, build_source_event_envelope, build_source_receipt_envelope
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, ProfitTarget, Side, Signal, SourceEvent, SourceEventKind


def _account():
    return DestinationAccount(account_id="acct1", broker="paper")


def _build(**overrides):
    fields = {
        "account_id": "acct1",
        "status": OrderStatus.FILLED,
        "signal_id": "sig-1",
        "broker_order_id": "paper-1",
        "filled_quantity": 2.0,
        "filled_price": 150.0,
    }
    fields.update(overrides)
    result = OrderResult(**fields)
    return build_execution_applied_envelope(
        result,
        account=_account(),
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )


def test_a_genuinely_filled_result_produces_an_envelope():
    envelope = _build()
    assert envelope is not None
    assert envelope.payload["broker_order_id"] == "paper-1"


def test_a_pending_result_with_every_field_populated_still_produces_nothing():
    """The load-bearing case: a hypothetical broker that reports
    broker_order_id/filled_quantity/filled_price on a still-PENDING
    result (optimistic values, not a confirmed fill) must not produce an
    EXECUTION_APPLIED envelope -- only a genuinely FILLED status may."""
    envelope = _build(status=OrderStatus.PENDING)
    assert envelope is None


def test_a_rejected_result_produces_nothing():
    envelope = _build(status=OrderStatus.REJECTED, broker_order_id=None, filled_quantity=None, filled_price=None)
    assert envelope is None


def test_a_filled_result_missing_broker_order_id_produces_nothing():
    envelope = _build(broker_order_id=None)
    assert envelope is None


def test_a_filled_result_missing_filled_quantity_produces_nothing():
    envelope = _build(filled_quantity=None)
    assert envelope is None


def test_side_close_is_refused_outright():
    result = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="sig-1",
        broker_order_id="paper-1", filled_quantity=2.0, filled_price=150.0,
    )
    with pytest.raises(ValueError, match="Side.CLOSE"):
        build_execution_applied_envelope(
            result, account=_account(), symbol="AAPL", side=Side.CLOSE, asset_class=AssetClass.EQUITY,
            source_stream="signal-copier:acct1", export_sequence=0, producer_id="test-producer",
            evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
        )


def test_currency_resolves_from_a_slash_pair_symbol():
    result = OrderResult(
        account_id="acct1", status=OrderStatus.FILLED, signal_id="sig-1",
        broker_order_id="cx-1", filled_quantity=1.0, filled_price=50000.0,
    )
    envelope = build_execution_applied_envelope(
        result, account=_account(), symbol="BTC/USDT", side=Side.BUY, asset_class=AssetClass.CRYPTO,
        source_stream="signal-copier:acct1", export_sequence=0, producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.payload["instrument"]["currency"] == "USDT"


def test_currency_defaults_to_usd_for_a_plain_symbol():
    envelope = _build()
    assert envelope.payload["instrument"]["currency"] == "USD"


def _build_receipt(**overrides):
    fields = {
        "source": "tradingview",
        "symbol": "AAPL",
        "side": Side.BUY,
        "asset_class": AssetClass.EQUITY,
        "analyst": None,
        "quantity": 10.0,
        "price": 150.0,
    }
    fields.update(overrides)
    signal = Signal(**fields)
    return build_source_receipt_envelope(
        signal,
        source_stream=f"signal-copier:source:{signal.source}",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    ), signal


def test_a_real_signal_produces_a_source_receipt_envelope():
    envelope, signal = _build_receipt()
    assert envelope is not None
    assert envelope.event_type == EventType.SOURCE_RECEIPT
    assert envelope.event_id == f"source-receipt:{signal.id}"
    assert envelope.payload["source"]["source_provider_id"] == "tradingview"
    assert envelope.payload["quantity"] == "10.0"
    assert envelope.payload["price"] == "150.0"


def test_a_close_signal_produces_nothing():
    """A close's real direction depends on whatever position is open at
    execution time -- not a property of the recommendation itself, and
    SourceReceiptPayload.side only accepts buy/sell -- see this
    builder's own docstring."""
    envelope, _ = _build_receipt(side=Side.CLOSE)
    assert envelope is None


def test_analyst_is_carried_through_to_source_identity():
    envelope, _ = _build_receipt(analyst="alice")
    assert envelope.payload["source"]["analyst_id"] == "alice"


def test_a_signal_with_no_analyst_carries_no_analyst_id():
    envelope, _ = _build_receipt(analyst=None)
    assert envelope.payload["source"].get("analyst_id") is None


def test_unknown_quantity_and_price_are_carried_through_as_none_not_coerced():
    """A source recommendation that carries no quantity/price of its own
    (e.g. a bare "buy AAPL" alert) must not have zero or any other
    placeholder value fabricated for it."""
    envelope, _ = _build_receipt(quantity=None, price=None)
    assert "quantity" not in envelope.payload or envelope.payload["quantity"] is None
    assert "price" not in envelope.payload or envelope.payload["price"] is None


def test_the_event_id_is_stable_for_the_same_signal_id_enabling_idempotent_redelivery():
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0)
    first = build_source_receipt_envelope(
        signal, source_stream="signal-copier:source:tradingview", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    second = build_source_receipt_envelope(
        signal, source_stream="signal-copier:source:tradingview", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert first.event_id == second.event_id


# -- Multi-provider representability: targets/entry-range/identity on the ---
# -- SOURCE_RECEIPT envelope --------------------------------------------------


def test_ordered_targets_and_entry_range_are_carried_through_to_the_payload():
    signal = Signal(
        source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0,
        price_low=149.0, price_high=151.0, entry_order_type="limit",
        targets=[ProfitTarget(price=155.0, fraction=0.5, label="TP1"), ProfitTarget(price=160.0, label="TP2")],
        channel_id="tradingview", message_id="alert-1", parser_version="webhook-json-v1",
    )
    envelope = build_source_receipt_envelope(
        signal, source_stream="signal-copier:source:tradingview", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert [t["label"] for t in envelope.payload["targets"]] == ["TP1", "TP2"]
    assert envelope.payload["price_low"] == "149.0"
    assert envelope.payload["price_high"] == "151.0"
    assert envelope.payload["entry_order_type"] == "limit"
    assert envelope.payload["source"]["source_channel_id"] == "tradingview"
    assert envelope.payload["source"]["source_event_id"] == "alert-1"
    assert envelope.payload["source"]["parser_version"] == "webhook-json-v1"


def test_a_signal_with_no_new_fields_produces_the_same_shape_as_before():
    """Backward compatibility: a Signal that never sets any of the new
    fields exports exactly the same envelope a pre-existing producer
    always has."""
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0)
    envelope = build_source_receipt_envelope(
        signal, source_stream="signal-copier:source:tradingview", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.payload["targets"] == []
    assert envelope.payload.get("price_low") is None
    assert envelope.payload.get("entry_order_type") is None
    assert envelope.payload["source"]["source_event_id"] == signal.id  # falls back to signal.id, unchanged
    assert envelope.payload["source"].get("source_catalog_id") is None


# -- Track 29: source_catalog_id (Track 14 provider-catalog `sources.id`) ----


def test_a_signal_with_a_known_catalog_source_carries_it_through_to_source_identity():
    """A Signal whose provenance IS traceable to a Track 14 catalog
    `sources.id` (e.g. set by `app/sources/rss_source.py`) must carry
    that id through to the exported SourceIdentity, distinct from
    source_provider_id/source_channel_id."""
    signal = Signal(
        source="momentum_mike", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0,
        channel_id="rss-feed-url", source_catalog_id="catalog-source-42",
    )
    envelope = build_source_receipt_envelope(
        signal, source_stream="signal-copier:source:momentum_mike", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["source"]["source_catalog_id"] == "catalog-source-42"
    assert envelope.payload["source"]["source_provider_id"] == "momentum_mike"
    assert envelope.payload["source"]["source_channel_id"] == "rss-feed-url"


def test_a_signal_with_no_catalog_source_leaves_it_honestly_none_never_fabricated():
    """Every adapter not wired to the Track 14 catalog (every current
    adapter except app/sources/rss_source.py) must never have a catalog
    source id invented for it."""
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0, channel_id="tv-alert-channel")
    envelope = build_source_receipt_envelope(
        signal, source_stream="signal-copier:source:tradingview", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["source"].get("source_catalog_id") is None


def test_source_event_envelope_carries_the_inner_signals_catalog_source_id():
    signal = Signal(
        source="telegram", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=65000.0,
        channel_id="chan-1", message_id="msg-1", parser_version="telegram-text-parser-v1",
        source_catalog_id="catalog-source-7",
    )
    event = SourceEvent(
        source="telegram", kind=SourceEventKind.ORIGINAL, channel_id="chan-1", message_id="msg-1",
        provider_timestamp=datetime.now(timezone.utc), signal=signal,
    )
    envelope = build_source_event_envelope(
        event, source_stream="signal-copier:source:telegram", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.payload["source"]["source_catalog_id"] == "catalog-source-7"


def test_source_event_envelope_with_no_inner_signal_leaves_catalog_source_id_none():
    event = SourceEvent(source="telegram", kind=SourceEventKind.DELETE, channel_id="chan-1", message_id="msg-1")
    envelope = build_source_event_envelope(
        event, source_stream="signal-copier:source:telegram", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.payload["source"].get("source_catalog_id") is None


# -- SOURCE_EVENT source ledger ------------------------------------------------


def test_source_event_envelope_for_an_original_message():
    signal = Signal(
        source="telegram", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=65000.0,
        channel_id="chan-1", message_id="msg-1", parser_version="telegram-text-parser-v1",
    )
    now = datetime.now(timezone.utc)
    event = SourceEvent(
        source="telegram", kind=SourceEventKind.ORIGINAL, channel_id="chan-1", message_id="msg-1",
        provider_timestamp=now, signal=signal,
    )
    envelope = build_source_event_envelope(
        event, source_stream="signal-copier:source:telegram", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.event_type == EventType.SOURCE_EVENT
    assert envelope.payload["kind"] == "original"
    assert envelope.payload["source"]["source_channel_id"] == "chan-1"
    assert envelope.payload["source"]["source_event_id"] == "msg-1"
    assert envelope.payload["signal"]["price"] == "65000.0"


def test_source_event_envelope_for_a_delete_has_no_inner_signal():
    event = SourceEvent(
        source="telegram", kind=SourceEventKind.DELETE, channel_id="chan-1", message_id="msg-1",
        reason="retracted by analyst",
    )
    envelope = build_source_event_envelope(
        event, source_stream="signal-copier:source:telegram", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.payload["kind"] == "delete"
    assert envelope.payload["signal"] is None
    assert envelope.payload["reason"] == "retracted by analyst"


def test_source_event_envelope_for_an_edit_links_revision_to_original():
    signal = Signal(
        source="telegram", symbol="BTCUSDT", side=Side.BUY, price=66000.0,
        channel_id="chan-1", message_id="msg-1", revision_id="msg-1:rev2", original_message_id="msg-1",
    )
    event = SourceEvent(
        source="telegram", kind=SourceEventKind.EDIT, channel_id="chan-1", message_id="msg-1",
        revision_id="msg-1:rev2", original_message_id="msg-1", signal=signal,
    )
    envelope = build_source_event_envelope(
        event, source_stream="signal-copier:source:telegram", export_sequence=0,
        producer_id="test-producer", evidence_class=EvidenceClass.INTERNAL_PAPER, environment=Environment.LOCAL_SIM,
    )
    assert envelope.payload["source"]["revision_id"] == "msg-1:rev2"
    assert envelope.payload["source"]["original_source_event_id"] == "msg-1"
