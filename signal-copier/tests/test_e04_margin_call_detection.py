"""E04 (bounded): margin call detection and persistence.

Tests for margin call detection with fail-closed pattern: when margin state
cannot be determined, alerts are raised to prevent silent failures.
"""
import pytest

from app.db import SignalStore
from app.margin_call_detector import MarginCallDetector
from app.models import DestinationAccount


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def detector(store: SignalStore) -> MarginCallDetector:
    return MarginCallDetector(store=store)


@pytest.fixture
def sample_account() -> DestinationAccount:
    return DestinationAccount(
        account_id="test_account_001",
        broker="paper",
        enabled=True,
        multiplier=1.0,
    )


def test_margin_call_detector_detects_margin_call(detector: MarginCallDetector, sample_account: DestinationAccount):
    """Margin call should be detected when excess_margin <= 0."""
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="paper",
    )
    assert error is not None
    assert "Margin call" in error
    assert "excess_margin=-1000.00" in error


def test_margin_call_detector_fails_closed_on_missing_equity(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """Fail-closed: missing current_equity should trigger error."""
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=None,
        maintenance_requirement=6000.0,
        excess_margin=None,
        broker="paper",
    )
    assert error is not None
    assert "Cannot determine margin state" in error


def test_margin_call_detector_fails_closed_on_missing_requirement(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """Fail-closed: missing maintenance_requirement should trigger error."""
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=5000.0,
        maintenance_requirement=None,
        excess_margin=None,
        broker="paper",
    )
    assert error is not None
    assert "Cannot determine margin state" in error


def test_margin_call_detector_calculates_excess_margin_when_not_provided(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """Excess margin should be calculated when not explicitly provided."""
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=10000.0,
        maintenance_requirement=8000.0,
        excess_margin=None,  # Not provided; should be calculated
        broker="paper",
    )
    # No margin call (excess_margin = 2000)
    assert error is None


def test_margin_call_detector_accepts_sufficient_margin(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """Should return None when margin is sufficient."""
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=15000.0,
        maintenance_requirement=5000.0,
        excess_margin=10000.0,
        broker="paper",
    )
    assert error is None


def test_margin_call_detector_warns_on_low_margin_but_allows(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """Should allow trading when margin is above zero but below warning threshold (10%)."""
    # Maintenance requirement = 10000, so warning threshold = 1000
    # Excess margin = 500 (below threshold but above zero)
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=10500.0,
        maintenance_requirement=10000.0,
        excess_margin=500.0,
        broker="paper",
    )
    # Should still allow trading (not a margin call yet)
    assert error is None


def test_margin_call_detector_persists_alert(detector: MarginCallDetector, sample_account: DestinationAccount):
    """Margin call alert should be persisted to database."""
    detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="paper",
    )

    # Retrieve unresolved alerts
    alerts = detector.get_unresolved_margin_calls(sample_account.account_id)
    assert len(alerts) == 1
    assert alerts[0]["current_equity"] == 5000.0
    assert alerts[0]["maintenance_requirement"] == 6000.0
    assert alerts[0]["excess_margin"] == -1000.0


def test_margin_call_detector_resolves_alert(detector: MarginCallDetector, sample_account: DestinationAccount):
    """Resolved alerts should not appear in unresolved list."""
    detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="paper",
    )

    alerts = detector.get_unresolved_margin_calls(sample_account.account_id)
    assert len(alerts) == 1
    alert_id = alerts[0]["id"]

    # Resolve the alert
    success = detector.resolve_margin_call(alert_id)
    assert success is True

    # Should not appear in unresolved list
    alerts = detector.get_unresolved_margin_calls(sample_account.account_id)
    assert len(alerts) == 0


def test_margin_call_detector_handles_multiple_alerts(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """Multiple margin call alerts should be tracked separately."""
    # First margin call
    detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=5000.0,
        maintenance_requirement=6000.0,
        excess_margin=-1000.0,
        broker="paper",
    )

    # Second margin call (different equity state)
    detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=3000.0,
        maintenance_requirement=6000.0,
        excess_margin=-3000.0,
        broker="paper",
    )

    alerts = detector.get_unresolved_margin_calls(sample_account.account_id)
    assert len(alerts) == 2


def test_margin_call_detector_skips_check_when_all_data_unavailable(
    detector: MarginCallDetector, sample_account: DestinationAccount
):
    """When all margin data is unavailable (all None), skip the check."""
    error = detector.check_and_persist_margin_call(
        account=sample_account,
        current_equity=None,
        maintenance_requirement=None,
        excess_margin=None,
        broker="paper",
    )
    # Should not error when all data is None (broker doesn't support margin tracking)
    assert error is None
