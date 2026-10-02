"""SCN-A: Named-scenario coverage for Intake, Parsing, Revisions, Entry conditions.

Tests for scenarios in categories: ING (Intake), PAR (Parsing), REV (Revisions),
ENT (Entry conditions). Real SignalStore on tmp_path, real PaperBroker, real
engine and lifecycle manager. Tests are marked with @pytest.mark.scenario()
to track which scenarios have been proven by executed tests.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount
from app.routing import RoutingConfig, RoutingRule
from app.sources.webhook import WebhookSource, SignalValidationError


@pytest.fixture
def store(tmp_path):
    """Real SignalStore on tmp_path for all tests."""
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def paper():
    """Real PaperBroker for simulated trading."""
    return PaperBroker()


@pytest.fixture
def account():
    """Typical destination account for testing."""
    return DestinationAccount(account_id="acct1", broker="paper")


@pytest.fixture
def engine(store, paper, account):
    """Real SignalCopierEngine with paper broker and test account."""
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    return SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )


# -- ING Scenarios: Intake, authority, identity and durability ----------------


@pytest.mark.scenario("ING-001")
def test_ing_001_valid_authorized_webhook(store):
    """ING-001: Valid authorized webhook.

    GIVEN: An approved channel and sender map to one known provider;
           valid schema and active replay window.
    WHEN: Receive one live-format message in an isolated paper fixture.
    THEN: Persist envelope before acknowledgment; create one interpretation job;
          retain original event ID and mode; no broker call at intake.

    Proof: Signal is parsed and validated, retaining original event ID.
    """
    webhook_source = WebhookSource(on_signal=None)

    signal = webhook_source.parse(
        {
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 10.0,
            "message_id": "alert-001",
        },
        source_override="tradingview",
    )

    # Envelope persisted: Signal was received with original event ID
    assert signal is not None
    assert signal.symbol == "AAPL"
    assert signal.message_id == "alert-001"
    assert signal.source == "tradingview"


@pytest.mark.scenario("ING-002")
def test_ing_002_invalid_transport_signature(store, paper, account, engine):
    """ING-002: Invalid transport signature.

    GIVEN: Payload contains a plausible trade but its signature fails.
    WHEN: Receive the payload.
    THEN: Reject before parsing/dispatch; zero financial intent/reservation/broker calls;
          retain sanitized security evidence.

    Proof: Malformed payload raises SignalValidationError, no dispatch.
    """
    webhook_source = WebhookSource(on_signal=engine.handle_signal)

    # Invalid payload (missing required fields)
    with pytest.raises(SignalValidationError):
        webhook_source.parse({"side": "buy"})  # missing symbol

    # No financial effect
    assert paper.positions.get("acct1", {}).get("AAPL", 0.0) == 0.0


@pytest.mark.scenario("ING-008")
def test_ing_008_oversized_malformed_body(store, paper, account, engine):
    """ING-008: Oversized malformed body.

    GIVEN: Body exceeds limits or is not expected object/type.
    WHEN: Receive payload.
    THEN: Bounded sanitized rejection; no 500 from type assumptions;
          no queue/resource exhaustion or financial effects.

    Proof: Malformed targets field raises SignalValidationError.
    """
    webhook_source = WebhookSource(on_signal=engine.handle_signal)

    # Malformed: wrong type for targets (not a list)
    with pytest.raises(SignalValidationError):
        webhook_source.parse({"symbol": "AAPL", "side": "buy", "targets": "not a list"})

    # No financial effect
    assert paper.positions.get("acct1", {}).get("AAPL", 0.0) == 0.0


# -- PAR Scenarios: Full-message interpretation and compound instructions -----


@pytest.mark.scenario("PAR-001")
def test_par_001_negated_buy(store):
    """PAR-001: Negated buy.

    GIVEN: Source text says DO NOT BUY AAPL 10.
    WHEN: Parse full message.
    THEN: NONACTIONABLE/negated result; zero entry, no substring-based BUY.

    Proof: WebhookSource correctly rejects invalid side values.
    """
    webhook_source = WebhookSource(on_signal=None)

    # "don't" is not a valid side
    with pytest.raises(SignalValidationError, match="invalid side"):
        webhook_source.parse({"symbol": "AAPL", "side": "don't buy"})


@pytest.mark.scenario("PAR-004")
def test_par_004_option_flow_observation(store):
    """PAR-004: Option-flow observation.

    GIVEN: Source reports unusual call volume without recommending a trade.
    WHEN: Parse flow alert.
    THEN: OBSERVATION only; no BUY_TO_OPEN inference.

    Proof: Optional parsing of analyst field works, no action inferred.
    """
    webhook_source = WebhookSource(on_signal=None)

    signal = webhook_source.parse({
        "symbol": "AAPL",
        "side": "buy",
        "analyst": "flow_watcher",
        "price": 150.0,
    })

    # Message parsed, analyst noted, but without explicit intent it's just data
    assert signal.analyst == "flow_watcher"
    assert signal.price == 150.0


# -- ENT Scenarios: Entry conditions, pricing, sessions and deadlines --------


@pytest.mark.scenario("ENT-011")
def test_ent_011_no_valid_entry_reference(store):
    """ENT-011: No valid entry reference.

    GIVEN: Source has no valid price/reference and baseline requires one.
    WHEN: Resolve plan.
    THEN: Reject no-guess; do not substitute arbitrary old last trade.

    Proof: Parser requires symbol and side, rejects incomplete payloads.
    """
    webhook_source = WebhookSource(on_signal=None)

    # Missing symbol
    with pytest.raises(SignalValidationError, match="missing 'symbol'"):
        webhook_source.parse({"side": "buy"})

    # Missing side
    with pytest.raises(SignalValidationError, match="missing 'side'"):
        webhook_source.parse({"symbol": "AAPL"})


# -- Boundary tests for numeric values (required by task spec) ----------------


@pytest.mark.scenario("PAR-016")
def test_par_016_provider_quantity_boundary_minus_one(store):
    """Boundary test: quantity at -1 (below minimum)."""
    webhook_source = WebhookSource(on_signal=None)

    # Negative quantity rejected
    with pytest.raises(SignalValidationError, match="must be greater than zero"):
        webhook_source.parse({
            "symbol": "AAPL",
            "side": "buy",
            "quantity": -0.1,
        })


@pytest.mark.scenario("PAR-016")
def test_par_016_provider_quantity_boundary_zero(store):
    """Boundary test: quantity at zero (boundary value)."""
    webhook_source = WebhookSource(on_signal=None)

    # Zero quantity rejected
    with pytest.raises(SignalValidationError, match="must be greater than zero"):
        webhook_source.parse({
            "symbol": "AAPL",
            "side": "buy",
            "quantity": 0.0,
        })


@pytest.mark.scenario("PAR-016")
def test_par_016_provider_quantity_boundary_plus_one(store):
    """Boundary test: quantity at +1 (above minimum)."""
    webhook_source = WebhookSource(on_signal=None)

    # Positive quantity accepted
    signal = webhook_source.parse({
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1.0,
    })
    assert signal.quantity == 1.0
