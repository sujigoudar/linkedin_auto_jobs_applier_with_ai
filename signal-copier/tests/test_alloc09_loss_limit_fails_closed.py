"""ALLOC-09: with a REAL SignalStore, a configured daily-loss limit or
min-equity threshold must reject (fail closed), never raise out of signal
handling. Existing tests mock the store attributes this code reads
(`get_daily_pnl`, `_broker_adapters`), which hid that a real store lacks
them."""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def _engine(tmp_path, account):
    store = SignalStore(tmp_path / "t.db")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=[account.account_id])], accounts={account.account_id: account}
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": PaperBroker()}, store=store)


@pytest.mark.asyncio
async def test_configured_daily_loss_limit_rejects_instead_of_raising(tmp_path):
    account = DestinationAccount(account_id="a1", broker="paper", daily_loss_limit_percent=2.0)
    results = await _engine(tmp_path, account).handle_signal(
        Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=1.0, price=10.0)
    )
    assert results[0].status == OrderStatus.REJECTED
    assert "Daily loss limit check failed" in results[0].message


@pytest.mark.asyncio
async def test_configured_min_equity_rejects_instead_of_raising(tmp_path):
    account = DestinationAccount(account_id="a1", broker="paper", min_equity_threshold=1.0)
    results = await _engine(tmp_path, account).handle_signal(
        Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=1.0, price=10.0)
    )
    assert results[0].status == OrderStatus.REJECTED
    assert "Min equity check failed" in results[0].message


@pytest.mark.asyncio
async def test_unconfigured_limits_do_not_block(tmp_path):
    account = DestinationAccount(account_id="a1", broker="paper")
    results = await _engine(tmp_path, account).handle_signal(
        Signal(source="tv", symbol="AAA", side=Side.BUY, quantity=1.0, price=10.0)
    )
    assert results[0].status == OrderStatus.FILLED
