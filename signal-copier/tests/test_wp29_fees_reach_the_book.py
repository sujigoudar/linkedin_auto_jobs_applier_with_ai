"""WP-29: E-09/E-15/G-C-14 — fees reach the book.

Audit findings:
- E-09: `fee` is hard-coded to `None` in every `EXECUTION_APPLIED` export
        even when the adapter reported one; no FEE event is ever emitted.
- E-15: Export coverage of fills: four classes never (or wrongly) reach
        the commercial book.
- G-C-14: Net P&L is structurally never available: copier drops the fee
         at export and never emits FEE.

This test package verifies that fee data from broker adapters properly
flows through the export envelope so that the commercial platform's
accounting can compute net P&L.
"""
import pytest
from signal_platform_contracts import EventType, Environment, EvidenceClass

from app.export_events import build_execution_applied_envelope
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side


@pytest.fixture
def account():
    return DestinationAccount(account_id="acct1", broker="paper")


def _build_filled_result(**overrides):
    """Helper to build a FILLED OrderResult with common defaults."""
    fields = {
        "account_id": "acct1",
        "status": OrderStatus.FILLED,
        "signal_id": "sig-1",
        "broker_order_id": "paper-1",
        "filled_quantity": 2.0,
        "filled_price": 150.0,
    }
    fields.update(overrides)
    return OrderResult(**fields)


def test_fee_from_broker_is_exported_in_execution_applied_payload(account):
    """E-09: A filled order with a reported broker fee must export that
    fee in the EXECUTION_APPLIED payload, not hard-code it to None."""
    result = _build_filled_result(fee=1.50, fee_currency="USD")
    envelope = build_execution_applied_envelope(
        result,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["fee"] == "1.5"


def test_zero_fee_is_exported_not_treated_as_no_fee(account):
    """A broker reporting a zero fee (no commission charged) must export
    that as a zero string, not None, to distinguish from 'fee unknown'."""
    result = _build_filled_result(fee=0.0)
    envelope = build_execution_applied_envelope(
        result,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["fee"] == "0.0"


def test_missing_fee_exports_none_not_fabricated(account):
    """A broker that doesn't report fees (or a fill without one) must
    export fee=None to signal 'fee unknown' to the commercial side, not
    a fabricated zero or placeholder value."""
    result = _build_filled_result(fee=None)
    envelope = build_execution_applied_envelope(
        result,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["fee"] is None


def test_fractional_fee_is_correctly_stringified(account):
    """A fee with multiple decimal places (e.g. from a real broker) must
    be stringified consistently with other numeric fields (e.g.
    filled_price)."""
    result = _build_filled_result(fee=1.23456789)
    envelope = build_execution_applied_envelope(
        result,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    # str(1.23456789) should preserve precision
    fee_payload = envelope.payload["fee"]
    assert "1.234" in fee_payload


def test_large_fee_is_exported_correctly(account):
    """Even a large fee (e.g., from a high-volume or illiquid order) must
    be exported without truncation or overflow."""
    result = _build_filled_result(fee=999.99)
    envelope = build_execution_applied_envelope(
        result,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["fee"] == "999.99"


def test_fee_independent_of_other_fill_fields(account):
    """The fee export must not depend on or interfere with other
    critical fill fields (broker_order_id, filled_quantity, filled_price)."""
    result = _build_filled_result(
        fee=1.50,
        broker_order_id="unique-order-123",
        filled_quantity=100.0,
        filled_price=50.0,
    )
    envelope = build_execution_applied_envelope(
        result,
        account=account,
        symbol="TSLA",
        side=Side.SELL,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=1,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope is not None
    assert envelope.payload["fee"] == "1.5"
    assert envelope.payload["broker_order_id"] == "unique-order-123"
    assert envelope.payload["filled_quantity"] == "100.0"
    assert envelope.payload["filled_price"] == "50.0"


def test_envelope_type_is_execution_applied_not_fee_event(account):
    """Until a dedicated FEE event type is implemented, fee data flows
    through EXECUTION_APPLIED; the event_type must remain EXECUTION_APPLIED
    regardless of whether a fee is present."""
    result_with_fee = _build_filled_result(fee=1.50)
    envelope_with_fee = build_execution_applied_envelope(
        result_with_fee,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=0,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    result_without_fee = _build_filled_result(fee=None)
    envelope_without_fee = build_execution_applied_envelope(
        result_without_fee,
        account=account,
        symbol="AAPL",
        side=Side.BUY,
        asset_class=AssetClass.EQUITY,
        source_stream="signal-copier:acct1",
        export_sequence=1,
        producer_id="test-producer",
        evidence_class=EvidenceClass.INTERNAL_PAPER,
        environment=Environment.LOCAL_SIM,
    )
    assert envelope_with_fee.event_type == EventType.EXECUTION_APPLIED
    assert envelope_without_fee.event_type == EventType.EXECUTION_APPLIED
