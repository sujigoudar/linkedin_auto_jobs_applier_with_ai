"""DEFAULT_MIN_EQUITY_THRESHOLD must actually apply to accounts that set no threshold.

It used to be parsed and then never read, so an operator who set it believed an
account-liquidation circuit breaker was on when it was off.
"""
from __future__ import annotations

import pytest

from app import config
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def _engine(tmp_path, account: DestinationAccount):
    routing = RoutingConfig(
        rules=[RoutingRule(source="s", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    return SignalCopierEngine(
        routing=routing, brokers={"paper": PaperBroker()}, store=SignalStore(tmp_path / "me.db")
    )


def _entry(name: str) -> Signal:
    return Signal(id=f"sig_{name}", source="s", symbol="AAPL", side=Side.BUY, quantity=1.0, price=100.0, stop_loss=95.0)


@pytest.mark.asyncio
async def test_global_default_threshold_blocks_an_account_with_no_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_MIN_EQUITY_THRESHOLD", 1e12)  # far above any paper equity
    engine = _engine(tmp_path, DestinationAccount(account_id="a1", broker="paper"))
    results = await engine.handle_signal(_entry("blocked"))
    assert len(results) == 1 and results[0].status == OrderStatus.REJECTED
    assert "equity" in results[0].message.lower()


@pytest.mark.asyncio
async def test_unset_default_threshold_does_not_block(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_MIN_EQUITY_THRESHOLD", None)
    engine = _engine(tmp_path, DestinationAccount(account_id="a1", broker="paper"))
    results = await engine.handle_signal(_entry("free"))
    assert len(results) == 1 and "equity" not in (results[0].message or "").lower()


@pytest.mark.asyncio
async def test_account_value_wins_over_the_global_default(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_MIN_EQUITY_THRESHOLD", 1e12)
    account = DestinationAccount(account_id="a1", broker="paper", min_equity_threshold=1.0)
    engine = _engine(tmp_path, account)
    results = await engine.handle_signal(_entry("override"))
    assert len(results) == 1 and "equity" not in (results[0].message or "").lower()
