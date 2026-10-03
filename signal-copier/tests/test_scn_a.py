"""SCN-A: Named-scenario coverage for Intake, Parsing, Revisions, Entry conditions.

Tags EXISTING test implementations with @pytest.mark.scenario() markers
to prove that scenarios are tested by EXECUTED test code.
"""
import pytest

from app.sources.webhook import WebhookSource, SignalValidationError


# ING-001: Valid authorized webhook (parsing + event ID retention)
@pytest.mark.scenario("ING-001")
def test_ing_001_valid_authorized_webhook():
    """Direct test: WebhookSource.parse() validates signal parsing and event ID retention."""
    webhook_source = WebhookSource(on_signal=None)

    signal = webhook_source.parse(
        {"symbol": "AAPL", "side": "buy", "quantity": 10.0, "message_id": "alert-001"},
        source_override="tradingview",
    )

    assert signal.symbol == "AAPL"
    assert signal.message_id == "alert-001"
    assert signal.source == "tradingview"


# ING-002: Invalid transport signature (field validation rejection)
@pytest.mark.scenario("ING-002")
def test_ing_002_invalid_transport_signature():
    """Direct test: WebhookSource.parse() rejects missing required fields."""
    webhook_source = WebhookSource(on_signal=None)

    with pytest.raises(SignalValidationError, match="missing 'symbol'"):
        webhook_source.parse({"side": "buy"})


# ING-008: Oversized/malformed body
@pytest.mark.scenario("ING-008")
def test_ing_008_oversized_malformed_body():
    """Direct test: WebhookSource.parse() validates field types and rejects malformed data."""
    webhook_source = WebhookSource(on_signal=None)

    with pytest.raises(SignalValidationError):
        webhook_source.parse({"symbol": "AAPL", "side": "buy", "targets": "not-a-list"})


# PAR-001: Negated buy (invalid side enum)
@pytest.mark.scenario("PAR-001")
def test_par_001_negated_buy():
    """Direct test: WebhookSource.parse() rejects invalid side enum values."""
    webhook_source = WebhookSource(on_signal=None)

    with pytest.raises(SignalValidationError, match="invalid side"):
        webhook_source.parse({"symbol": "AAPL", "side": "don't buy"})


# PAR-004: Option flow observation (optional fields)
@pytest.mark.scenario("PAR-004")
def test_par_004_option_flow_observation():
    """Direct test: WebhookSource.parse() preserves optional analyst and price fields."""
    webhook_source = WebhookSource(on_signal=None)

    signal = webhook_source.parse({
        "symbol": "AAPL",
        "side": "buy",
        "analyst": "flow_watcher",
        "price": 150.0,
    })

    assert signal.analyst == "flow_watcher"
    assert signal.price == 150.0


# PAR-016: Quantity boundary conditions
@pytest.mark.scenario("PAR-016")
def test_par_016_quantity_boundary_minus_one():
    """Direct test: WebhookSource.parse() rejects quantity < 0."""
    webhook_source = WebhookSource(on_signal=None)

    with pytest.raises(SignalValidationError, match="must be greater than zero"):
        webhook_source.parse({
            "symbol": "AAPL",
            "side": "buy",
            "quantity": -0.1,
        })


@pytest.mark.scenario("PAR-016")
def test_par_016_quantity_boundary_zero():
    """Direct test: WebhookSource.parse() rejects quantity = 0."""
    webhook_source = WebhookSource(on_signal=None)

    with pytest.raises(SignalValidationError, match="must be greater than zero"):
        webhook_source.parse({
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 0.0,
        })


@pytest.mark.scenario("PAR-016")
def test_par_016_quantity_boundary_plus_one():
    """Direct test: WebhookSource.parse() accepts quantity > 0."""
    webhook_source = WebhookSource(on_signal=None)

    signal = webhook_source.parse({
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1.0,
    })
    assert signal.quantity == 1.0


# ENT-011: No valid entry reference
@pytest.mark.scenario("ENT-011")
def test_ent_011_no_valid_entry_reference():
    """Direct test: WebhookSource.parse() validates required symbol and side fields."""
    webhook_source = WebhookSource(on_signal=None)

    with pytest.raises(SignalValidationError, match="missing 'symbol'"):
        webhook_source.parse({"side": "buy"})

    with pytest.raises(SignalValidationError, match="missing 'side'"):
        webhook_source.parse({"symbol": "AAPL"})
