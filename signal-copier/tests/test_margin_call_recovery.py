"""A margin-call alert must release once margin has really recovered, and only then.

Previously the alert row latched: every later entry was rejected by the
'unresolved alert' check and nothing in production ever resolved it.
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.margin_call_detector import MarginCallDetector
from app.models import AccountBalance, DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

ACCT = "m1"


def _alert(store: SignalStore) -> None:
    store.persist_margin_call_alert(
        account_id=ACCT, current_equity=90.0, maintenance_requirement=100.0, excess_margin=-10.0, broker="paper"
    )


def test_recovered_margin_resolves_open_alerts(tmp_path):
    store = SignalStore(tmp_path / "m.db")
    _alert(store)
    detector = MarginCallDetector(store)
    assert detector.resolve_recovered_margin_calls(ACCT, 200.0, 100.0) == 1
    assert store.get_unresolved_margin_calls(ACCT) == []


@pytest.mark.parametrize(
    "equity,maintenance",
    [
        (100.5, 100.0),  # positive but inside the 10% warning buffer: not recovered
        (100.0, 100.0),  # exactly at the requirement
        (90.0, 100.0),  # still short
        (None, 100.0),  # unreadable
        (200.0, None),  # unreadable
    ],
)
def test_unrecovered_or_unreadable_margin_keeps_alerts_open(tmp_path, equity, maintenance):
    store = SignalStore(tmp_path / "m.db")
    _alert(store)
    assert MarginCallDetector(store).resolve_recovered_margin_calls(ACCT, equity, maintenance) == 0
    assert len(store.get_unresolved_margin_calls(ACCT)) == 1


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


@pytest.mark.asyncio
async def test_engine_blocks_during_a_margin_call_then_unblocks_when_it_recovers(tmp_path):
    store = SignalStore(tmp_path / "m.db")
    broker = _MarginBroker()
    account = DestinationAccount(account_id=ACCT, broker="paper")
    routing = RoutingConfig(rules=[RoutingRule(source="s", destinations=[ACCT])], accounts={ACCT: account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    during = await engine.handle_signal(_entry("during"), dry_run=True)
    assert during[0].status == OrderStatus.REJECTED and "argin call" in during[0].message
    assert store.get_unresolved_margin_calls(ACCT), "the breach must leave an alert behind"

    still = await engine.handle_signal(_entry("still"), dry_run=True)
    assert still[0].status == OrderStatus.REJECTED, "no recovery yet: must stay blocked"

    broker.equity = 500.0  # comfortably above the requirement
    after = await engine.handle_signal(_entry("after"), dry_run=True)
    assert after[0].status == OrderStatus.PENDING, after[0].message
    assert store.get_unresolved_margin_calls(ACCT) == []
