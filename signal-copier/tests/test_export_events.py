"""app/export_events.py's own `build_execution_applied_envelope` --
unit tests isolating its guard conditions directly (not through the
full engine), since a REJECTED/ERROR OrderResult in this codebase's own
current adapters also happens to leave broker_order_id/filled_quantity/
filled_price unset, which would make the FILLED-only check look
redundant if only exercised end-to-end through the engine."""
import pytest
from signal_platform_contracts import Environment, EvidenceClass

from app.export_events import build_execution_applied_envelope
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side


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
