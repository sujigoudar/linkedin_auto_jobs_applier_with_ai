"""Tests for the multi-provider signal representability additions:
`SourceReceiptPayload`'s new optional fields (ordered profit targets,
entry range/order type/expiration, contract-type details), and the new
`SourceEventPayload`/`SourceEventKind` source ledger."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from signal_platform_contracts import (
    CryptoDerivativeDetails,
    FutureContractDetails,
    FxContractDetails,
    InstrumentIdentity,
    OptionContractDetails,
    ProfitTargetPayload,
    SourceEventKind,
    SourceEventPayload,
    SourceIdentity,
    SourceReceiptPayload,
)


def _now():
    return datetime.now(timezone.utc)


def _source_identity(**overrides):
    fields = dict(
        source_provider_id="telegram", parser_version="v3", source_event_id="src-evt-1",
    )
    fields.update(overrides)
    return SourceIdentity(**fields)


def _instrument_identity(**overrides):
    fields = dict(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )
    fields.update(overrides)
    return InstrumentIdentity(**fields)


# --- Backward compatibility: existing single-target/single-price shape ----


def test_existing_single_target_single_price_payload_still_constructs_unchanged():
    payload = SourceReceiptPayload(
        source=_source_identity(), instrument=_instrument_identity(), side="buy",
        quantity="10", price="150.00", stop_loss="145.00", take_profit="160.00",
    )
    assert payload.targets == []
    assert payload.price_low is None
    assert payload.price_high is None
    assert payload.entry_order_type is None
    assert payload.option is None
    assert payload.future is None
    assert payload.fx is None
    assert payload.crypto_derivative is None
    assert payload.raw_source_event is None


# --- Ordered multiple profit targets ---------------------------------------


def test_ordered_multiple_profit_targets_each_with_own_quantity_or_fraction():
    payload = SourceReceiptPayload(
        source=_source_identity(), instrument=_instrument_identity(), side="buy",
        quantity="100", price="150.00",
        targets=[
            ProfitTargetPayload(price="155.00", fraction="0.5", label="TP1"),
            ProfitTargetPayload(price="160.00", fraction="0.3", label="TP2"),
            ProfitTargetPayload(price="165.00", quantity="20", label="TP3"),
        ],
    )
    assert [t.label for t in payload.targets] == ["TP1", "TP2", "TP3"]
    assert payload.targets[0].fraction == 0.5
    assert payload.targets[2].quantity == 20


def test_profit_target_fraction_must_be_in_unit_range():
    with pytest.raises(ValidationError):
        ProfitTargetPayload(price="100", fraction="1.5")
    with pytest.raises(ValidationError):
        ProfitTargetPayload(price="100", fraction="0")


# --- Entry range / order type / expiration ---------------------------------


def test_entry_price_range_alongside_single_price():
    payload = SourceReceiptPayload(
        source=_source_identity(), instrument=_instrument_identity(), side="buy",
        price_low="149.00", price_high="151.00", entry_order_type="limit",
        entry_expiration=_now(),
    )
    assert payload.price_low == 149
    assert payload.price_high == 151
    assert payload.entry_order_type == "limit"


def test_entry_price_range_rejects_inverted_bounds():
    with pytest.raises(ValidationError, match="must not exceed"):
        SourceReceiptPayload(
            source=_source_identity(), instrument=_instrument_identity(), side="buy",
            price_low="151.00", price_high="149.00",
        )


def test_unknown_entry_order_type_is_rejected():
    with pytest.raises(ValidationError):
        SourceReceiptPayload(
            source=_source_identity(), instrument=_instrument_identity(), side="buy",
            entry_order_type="stop_limit_trailing_iceberg",
        )


def test_entry_expiration_must_be_timezone_aware():
    with pytest.raises(ValidationError, match="timezone-aware"):
        SourceReceiptPayload(
            source=_source_identity(), instrument=_instrument_identity(), side="buy",
            entry_expiration=datetime(2026, 1, 1),
        )


# --- Contract-type-specific details -----------------------------------------


def test_option_contract_details():
    payload = SourceReceiptPayload(
        source=_source_identity(),
        instrument=_instrument_identity(market_type="option"),
        side="buy",
        option=OptionContractDetails(
            underlying="AAPL", expiry="2026-09-18", strike="200.00", right="call",
            multiplier="100", deliverable="100 shares AAPL",
        ),
    )
    assert payload.option.right == "call"
    with pytest.raises(ValidationError):
        OptionContractDetails(
            underlying="AAPL", expiry="2026-09-18", strike="200.00", right="banana", multiplier="100",
        )


def test_future_contract_details():
    future = FutureContractDetails(root="ES", expiry="2025-12-19", multiplier="50", venue="CME")
    assert future.root == "ES"


def test_fx_contract_details():
    fx = FxContractDetails(base_currency="EUR", quote_currency="USD", unit="standard_lot_100000")
    assert fx.base_currency == "EUR"


def test_crypto_derivative_details():
    deriv = CryptoDerivativeDetails(instrument_kind="perpetual", margin_currency="USDT")
    assert deriv.instrument_kind == "perpetual"
    with pytest.raises(ValidationError):
        CryptoDerivativeDetails(instrument_kind="not_a_real_kind")


# --- SOURCE_EVENT source ledger ---------------------------------------------


def test_source_event_original_carries_native_message_identity():
    event = SourceEventPayload(
        kind=SourceEventKind.ORIGINAL,
        source=_source_identity(source_channel_id="chan-1", source_event_id="msg-1"),
        provider_timestamp=_now(),
        local_receipt_timestamp=_now(),
        signal=SourceReceiptPayload(
            source=_source_identity(source_channel_id="chan-1", source_event_id="msg-1"),
            instrument=_instrument_identity(), side="buy", quantity="10", price="150",
        ),
    )
    assert event.source.source_channel_id == "chan-1"
    assert event.source.source_event_id == "msg-1"


def test_source_event_edit_links_revision_to_original():
    event = SourceEventPayload(
        kind=SourceEventKind.EDIT,
        source=_source_identity(
            source_channel_id="chan-1", source_event_id="msg-1", revision_id="msg-1:edit-2",
            original_source_event_id="msg-1",
        ),
        provider_timestamp=_now(),
        local_receipt_timestamp=_now(),
    )
    assert event.source.original_source_event_id == "msg-1"
    assert event.signal is None


def test_source_event_delete_has_no_signal():
    event = SourceEventPayload(
        kind=SourceEventKind.DELETE,
        source=_source_identity(source_channel_id="chan-1", source_event_id="msg-1"),
        provider_timestamp=_now(),
        local_receipt_timestamp=_now(),
        reason="retracted by analyst",
    )
    assert event.signal is None
    assert event.reason == "retracted by analyst"


def test_source_event_reply_names_parent():
    event = SourceEventPayload(
        kind=SourceEventKind.REPLY,
        source=_source_identity(
            source_channel_id="chan-1", source_event_id="msg-2", parent_event_id="msg-1",
        ),
        provider_timestamp=_now(),
        local_receipt_timestamp=_now(),
    )
    assert event.source.parent_event_id == "msg-1"


def test_source_event_timestamps_must_be_timezone_aware():
    with pytest.raises(ValidationError, match="timezone-aware"):
        SourceEventPayload(
            kind=SourceEventKind.ORIGINAL,
            source=_source_identity(),
            provider_timestamp=datetime(2026, 1, 1),
            local_receipt_timestamp=_now(),
        )
