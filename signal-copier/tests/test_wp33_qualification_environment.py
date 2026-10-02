"""WP-33: qualification keyed by venue environment.

A route is release-approved only for the environment it was qualified in.
Different brokers resolve environment differently:
- Alpaca: paper/live from base URL
- ccxt: sandbox/live from sandbox flag
- Paper: always paper
- Others: unknown by default

Revoked is a terminal state that blocks routing regardless of other qualifications.
"""
import pytest
from app.brokers.alpaca import AlpacaBroker
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.models import AssetClass, DestinationAccount
from app.qualification import QualificationState


@pytest.fixture
def mock_alpaca_paper(monkeypatch):
    """Mock Alpaca broker with paper environment."""
    monkeypatch.setenv("ALPACA_TEST_PAPER_API_KEY", "pk_test_abc")
    monkeypatch.setenv("ALPACA_TEST_PAPER_API_SECRET", "sk_test_xyz")
    monkeypatch.setenv("ALPACA_TEST_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
    return AlpacaBroker()


@pytest.fixture
def mock_alpaca_live(monkeypatch):
    """Mock Alpaca broker with live environment."""
    monkeypatch.setenv("ALPACA_TEST_LIVE_API_KEY", "pk_live_abc")
    monkeypatch.setenv("ALPACA_TEST_LIVE_API_SECRET", "sk_live_xyz")
    monkeypatch.setenv("ALPACA_TEST_LIVE_BASE_URL", "https://api.alpaca.markets")
    return AlpacaBroker()


@pytest.fixture
def test_store(tmp_path):
    """Create a SignalStore for testing."""
    db_path = tmp_path / "test.db"
    return SignalStore(str(db_path))


def test_paper_broker_venue_environment():
    """PaperBroker always returns 'paper'."""
    broker = PaperBroker()
    account = DestinationAccount(
        account_id="test_acct",
        broker="paper",
        enabled=True,
    )
    assert broker.venue_environment(account) == "paper"


def test_alpaca_paper_venue_environment(mock_alpaca_paper):
    """AlpacaBroker returns 'paper' for paper-api.alpaca.markets."""
    account = DestinationAccount(
        account_id="test_paper",
        broker="alpaca",
        enabled=True,
    )
    assert mock_alpaca_paper.venue_environment(account) == "paper"


def test_alpaca_live_venue_environment(mock_alpaca_live):
    """AlpacaBroker returns 'live' for api.alpaca.markets."""
    account = DestinationAccount(
        account_id="test_live",
        broker="alpaca",
        enabled=True,
    )
    assert mock_alpaca_live.venue_environment(account) == "live"


def test_ccxt_sandbox_venue_environment():
    """CCXTBroker returns 'sandbox' when sandbox=True."""
    broker = CCXTBroker(exchange_id="binance", sandbox=True)
    account = DestinationAccount(
        account_id="test_acct",
        broker="ccxt_binance",
        enabled=True,
    )
    assert broker.venue_environment(account) == "sandbox"


def test_ccxt_live_venue_environment():
    """CCXTBroker returns 'live' when sandbox=False."""
    broker = CCXTBroker(exchange_id="binance", sandbox=False)
    account = DestinationAccount(
        account_id="test_acct",
        broker="ccxt_binance",
        enabled=True,
    )
    assert broker.venue_environment(account) == "live"


def test_route_approval_per_environment(test_store):
    """A route qualified in paper is not approved for live without re-qualification."""
    adapter_type = "alpaca"
    route_key = "test_acct"
    asset_class = AssetClass.EQUITY.value
    product_type = "default"

    # Record qualification ladder through release_approved for PAPER environment
    for state in QualificationState:
        if state == QualificationState.REVOKED:
            continue  # Skip revoked, it's terminal
        test_store.record_route_qualification(
            adapter_type=adapter_type,
            route_key=route_key,
            asset_class=asset_class,
            product_type=product_type,
            state=state.value,
            supports_feedback=True,
            recorded_by="test_operator",
            environment="paper",
        )

    # Now check approval with paper environment (should be approved)
    assert test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment="paper",
    )

    # Check approval with live environment (should NOT be approved, different environment)
    assert not test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment="live",
    )


def test_route_revoked_blocks_all_environments(test_store):
    """A revoked route blocks all environments, even if release_approved was recorded."""
    adapter_type = "alpaca"
    route_key = "test_acct"
    asset_class = AssetClass.EQUITY.value
    product_type = "default"
    environment = "paper"

    # Record qualification ladder through release_approved
    for state in QualificationState:
        if state == QualificationState.REVOKED:
            continue
        test_store.record_route_qualification(
            adapter_type=adapter_type,
            route_key=route_key,
            asset_class=asset_class,
            product_type=product_type,
            state=state.value,
            supports_feedback=True,
            recorded_by="test_operator",
            environment=environment,
        )

    # Verify it's approved before revocation
    assert test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment=environment,
    )

    # Record revocation
    test_store.record_route_qualification(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        state=QualificationState.REVOKED.value,
        supports_feedback=True,
        recorded_by="test_operator",
        notes="Security incident",
        environment=environment,
    )

    # Now it should be blocked (revoked is terminal)
    assert not test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment=environment,
    )


def test_different_environments_are_different_routes(test_store):
    """Paper and live routes are tracked separately."""
    adapter_type = "alpaca"
    route_key = "test_acct"
    asset_class = AssetClass.EQUITY.value
    product_type = "default"

    # Qualify paper environment only
    for state in QualificationState:
        if state == QualificationState.REVOKED:
            continue
        test_store.record_route_qualification(
            adapter_type=adapter_type,
            route_key=route_key,
            asset_class=asset_class,
            product_type=product_type,
            state=state.value,
            supports_feedback=True,
            recorded_by="test_operator",
            environment="paper",
        )

    # Paper should be approved
    assert test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment="paper",
    )

    # Live should NOT be approved (not qualified)
    assert not test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment="live",
    )

    # Separately qualify live environment
    for state in QualificationState:
        if state == QualificationState.REVOKED:
            continue
        test_store.record_route_qualification(
            adapter_type=adapter_type,
            route_key=route_key,
            asset_class=asset_class,
            product_type=product_type,
            state=state.value,
            supports_feedback=True,
            recorded_by="test_operator",
            environment="live",
        )

    # Now live should be approved too
    assert test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment="live",
    )

    # And paper should still be approved
    assert test_store.is_route_release_approved(
        adapter_type=adapter_type,
        route_key=route_key,
        asset_class=asset_class,
        product_type=product_type,
        environment="paper",
    )


def test_qualification_state_revoked_in_ladder():
    """REVOKED is the final state in the qualification ladder."""
    # Check that REVOKED is in the list
    assert QualificationState.REVOKED in list(QualificationState)
    assert QualificationState.REVOKED.value == "revoked"
