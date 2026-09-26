"""RISK-02: PositionPlan.max_risk is stored but nothing in this codebase
enforces it -- no entry/reference price to compute (entry - stop) *
quantity against, no reservation ledger, no account/portfolio cap. A plan
naming a risk bound that can't be enforced must not be silently admitted
as if that bound were honored.

Reproduces the audit's exact case
(test_state_and_input_audit::test_unresolvable_risk_bound_cannot_be_admitted).
"""
import pytest

from app.brokers.paper import PaperBroker
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import Side


@pytest.fixture
def manager():
    return PositionLifecycleManager(brokers={"paper": PaperBroker()})


def test_audits_exact_case_max_risk_plan_is_refused(manager):
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=10000,
        broker="paper",
        initial_stop=90,
        max_risk=1,
    )
    error = manager.validate_plan(plan)
    assert error is not None
    assert "max_risk" in error


def test_plan_without_max_risk_is_unaffected(manager):
    plan = PositionPlan(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=100,
        broker="paper",
        initial_stop=90,
    )
    assert manager.validate_plan(plan) is None
