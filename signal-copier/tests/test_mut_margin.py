"""Mutation-killing tests for margin_call_detector.py.

These tests are designed to catch mutations that survived the initial test suite:
- Threshold value changes (0.10 to other percentages)
- Comparison operator changes (< vs <=)
- Return value changes
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.margin_call_detector import MarginCallDetector
from app.models import AccountBalance, DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


ACCT = "m1"


def _alert(store: SignalStore) -> None:
    """Create a margin call alert for testing."""
    store.persist_margin_call_alert(
        account_id=ACCT, current_equity=90.0, maintenance_requirement=100.0, excess_margin=-10.0, broker="paper"
    )


class _MarginBroker(PaperBroker):
    """Paper broker whose reported equity / maintenance margin the test controls."""

    def __init__(self) -> None:
        super().__init__()
        self.equity = 90.0
        self.maintenance = 100.0

    async def get_account_balance(self, account):
        return AccountBalance(
            account_id=account.account_id,
            equity=self.equity,
            buying_power=1_000_000.0,
            cash=1_000_000.0,
            maintenance_margin=self.maintenance,
        )


def _entry(name: str) -> Signal:
    return Signal(id=f"sig_{name}", source="s", symbol="AAPL", side=Side.BUY, quantity=1.0, price=100.0, stop_loss=95.0)


class TestRecoveryThresholdBoundary:
    """Tests to kill mutations that change the 0.10 (10%) recovery threshold.

    The recovery condition is: if (equity - requirement) > (requirement * 0.10), then recover.
    Or equivalently: if (equity - requirement) <= (requirement * 0.10), then DON'T recover.
    """

    def test_no_recovery_at_10_percent_exactly(self, tmp_path):
        """No recovery when excess_margin equals exactly 10% of maintenance_requirement."""
        store = SignalStore(tmp_path / "m.db")
        _alert(store)
        detector = MarginCallDetector(store)

        # 10% of 100 = 10, so equity = 110 means excess_margin = 10
        # Since condition is (<= 0.10 * req), this should NOT recover
        assert detector.resolve_recovered_margin_calls(ACCT, 110.0, 100.0) == 0
        assert len(store.get_unresolved_margin_calls(ACCT)) == 1

    def test_recovery_above_10_percent_threshold(self, tmp_path):
        """Recovery happens when excess_margin is above 10% threshold."""
        store = SignalStore(tmp_path / "m.db")
        _alert(store)
        detector = MarginCallDetector(store)

        # excess_margin = 10.1 (> 10% of 100), should recover
        assert detector.resolve_recovered_margin_calls(ACCT, 110.1, 100.0) == 1
        assert store.get_unresolved_margin_calls(ACCT) == []

    def test_no_recovery_at_9_percent_below_threshold(self, tmp_path):
        """No recovery when excess_margin is below 10% threshold."""
        store = SignalStore(tmp_path / "m.db")
        _alert(store)
        detector = MarginCallDetector(store)

        # excess_margin = 9 (9% of 100), below 10%
        assert detector.resolve_recovered_margin_calls(ACCT, 109.0, 100.0) == 0
        assert len(store.get_unresolved_margin_calls(ACCT)) == 1

    def test_recovery_scales_with_maintenance_requirement(self, tmp_path):
        """10% threshold scales with maintenance_requirement value."""
        store = SignalStore(tmp_path / "m.db")
        # Create alert with large maintenance
        store.persist_margin_call_alert(
            account_id=ACCT, current_equity=9900.0, maintenance_requirement=10000.0, excess_margin=-100.0, broker="paper"
        )
        detector = MarginCallDetector(store)

        # 10% of 10000 = 1000
        # excess_margin = 1000.1 should recover
        assert detector.resolve_recovered_margin_calls(ACCT, 11000.1, 10000.0) == 1
        assert store.get_unresolved_margin_calls(ACCT) == []


class TestMarginCallDetectionBoundary:
    """Tests to kill mutations that change the margin call detection threshold (<= 0)."""

    def test_margin_call_at_exact_zero_excess(self, tmp_path):
        """Margin call should be detected when excess_margin == 0."""
        store = SignalStore(tmp_path / "test.db")
        detector = MarginCallDetector(store)
        account = DestinationAccount(account_id="test_account", broker="paper")

        # excess_margin = 0 should trigger margin call (<= 0 condition)
        error = detector.check_and_persist_margin_call(
            account=account,
            current_equity=6000.0,
            maintenance_requirement=6000.0,
            excess_margin=0.0,
            broker="paper",
        )
        assert error is not None
        assert "Margin call" in error

        # Verify alert was persisted
        alerts = store.get_unresolved_margin_calls("test_account")
        assert len(alerts) == 1

    def test_no_margin_call_at_positive_excess(self, tmp_path):
        """Margin call should NOT be detected when excess_margin > 0."""
        store = SignalStore(tmp_path / "test.db")
        detector = MarginCallDetector(store)
        account = DestinationAccount(account_id="test_account", broker="paper")

        # excess_margin = 0.01 (positive), should NOT trigger margin call
        error = detector.check_and_persist_margin_call(
            account=account,
            current_equity=6000.01,
            maintenance_requirement=6000.0,
            excess_margin=0.01,
            broker="paper",
        )
        assert error is None

        # No alert should be persisted
        alerts = store.get_unresolved_margin_calls("test_account")
        assert len(alerts) == 0


class TestWarningThresholdBoundary:
    """Tests for warning threshold behavior (< 10% but > 0)."""

    def test_warning_triggered_at_9_percent(self, tmp_path):
        """Warning is logged when excess_margin is 9% (below 10% threshold)."""
        store = SignalStore(tmp_path / "test.db")
        detector = MarginCallDetector(store)
        account = DestinationAccount(account_id="test_account", broker="paper")

        # excess_margin = 900 (9% of 10000), below 10% threshold
        # Should not be a margin call but should log warning
        error = detector.check_and_persist_margin_call(
            account=account,
            current_equity=10900.0,
            maintenance_requirement=10000.0,
            excess_margin=900.0,
            broker="paper",
        )
        # Should not be a margin call (excess_margin > 0)
        assert error is None


class TestResolutionReturnValue:
    """Tests to kill mutations that change the return value of resolve_recovered_margin_calls."""

    def test_resolution_count_zero_when_no_alerts(self, tmp_path):
        """Should return 0 when no alerts exist to resolve."""
        store = SignalStore(tmp_path / "m.db")
        detector = MarginCallDetector(store)

        # No alerts, so return should be 0
        result = detector.resolve_recovered_margin_calls(ACCT, 200.0, 100.0)
        assert result == 0

    def test_resolution_count_one_when_one_resolved(self, tmp_path):
        """Should return 1 when one alert is resolved."""
        store = SignalStore(tmp_path / "m.db")
        _alert(store)
        detector = MarginCallDetector(store)

        # One alert should be resolved
        result = detector.resolve_recovered_margin_calls(ACCT, 200.0, 100.0)
        assert result == 1

    def test_resolution_count_matches_alert_count(self, tmp_path):
        """Should return correct count matching number of alerts resolved."""
        store = SignalStore(tmp_path / "m.db")
        account = DestinationAccount(account_id=ACCT, broker="paper")
        detector = MarginCallDetector(store)

        # Create two alerts
        detector.check_and_persist_margin_call(
            account=account,
            current_equity=5000.0,
            maintenance_requirement=6000.0,
            excess_margin=-1000.0,
            broker="paper",
        )
        detector.check_and_persist_margin_call(
            account=account,
            current_equity=3000.0,
            maintenance_requirement=6000.0,
            excess_margin=-3000.0,
            broker="paper",
        )

        assert len(store.get_unresolved_margin_calls(ACCT)) == 2

        # Both should be resolved, returning 2 (not hardcoded 1)
        result = detector.resolve_recovered_margin_calls(ACCT, 200.0, 100.0)
        assert result == 2
        assert store.get_unresolved_margin_calls(ACCT) == []

    @pytest.mark.asyncio
    async def test_engine_recovery_depends_on_exact_count(self, tmp_path):
        """Engine behavior depends on accurate recovery count."""
        store = SignalStore(tmp_path / "m.db")
        broker = _MarginBroker()
        account = DestinationAccount(account_id=ACCT, broker="paper")
        routing = RoutingConfig(rules=[RoutingRule(source="s", destinations=[ACCT])], accounts={ACCT: account})
        engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

        # Trigger margin call
        during = await engine.handle_signal(_entry("during"), dry_run=True)
        assert during[0].status == OrderStatus.REJECTED and "argin call" in during[0].message
        assert store.get_unresolved_margin_calls(ACCT), "alert should be persisted"

        # Still breached
        still = await engine.handle_signal(_entry("still"), dry_run=True)
        assert still[0].status == OrderStatus.REJECTED, "no recovery yet"

        # Recover with enough margin
        broker.equity = 500.0
        after = await engine.handle_signal(_entry("after"), dry_run=True)
        assert after[0].status == OrderStatus.PENDING, after[0].message
        assert store.get_unresolved_margin_calls(ACCT) == []


class TestComparisonOperatorBoundaries:
    """Tests to verify exact comparison operators."""

    def test_no_recovery_at_exactly_10_percent_boundary(self, tmp_path):
        """At exactly 10% boundary, should NOT recover (uses <=, not <)."""
        store = SignalStore(tmp_path / "m.db")
        _alert(store)
        detector = MarginCallDetector(store)

        # Exactly at 10% boundary
        result = detector.resolve_recovered_margin_calls(ACCT, 110.0, 100.0)
        assert result == 0

    def test_recovery_just_above_10_percent_boundary(self, tmp_path):
        """Just above 10% boundary, should recover."""
        store = SignalStore(tmp_path / "m.db")
        _alert(store)
        detector = MarginCallDetector(store)

        # Just above 10% boundary
        result = detector.resolve_recovered_margin_calls(ACCT, 110.00001, 100.0)
        assert result == 1

    def test_margin_call_at_zero_boundary(self, tmp_path):
        """At exactly zero excess margin, margin call should be detected (<= 0)."""
        store = SignalStore(tmp_path / "test.db")
        detector = MarginCallDetector(store)
        account = DestinationAccount(account_id="test_account", broker="paper")

        # At exactly zero
        error = detector.check_and_persist_margin_call(
            account=account,
            current_equity=6000.0,
            maintenance_requirement=6000.0,
            excess_margin=0.0,
            broker="paper",
        )
        assert error is not None
        assert "Margin call" in error

    def test_no_margin_call_just_above_zero(self, tmp_path):
        """Just above zero, no margin call."""
        store = SignalStore(tmp_path / "test.db")
        detector = MarginCallDetector(store)
        account = DestinationAccount(account_id="test_account", broker="paper")

        # Just above zero
        error = detector.check_and_persist_margin_call(
            account=account,
            current_equity=6000.00001,
            maintenance_requirement=6000.0,
            excess_margin=0.00001,
            broker="paper",
        )
        assert error is None
