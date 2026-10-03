"""Protective levels on the wrong side of the entry price must be rejected.

A long's stop above its entry (or target below it) triggers the moment the
position opens; the mirror holds for shorts. The engine must refuse these before
any sizing, reservation or broker call.
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import AssetClass, DestinationAccount, Intent, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def _engine(tmp_path, *, allow_short: bool = True):
    account = DestinationAccount(account_id="a1", broker="paper", allow_short=allow_short)
    routing = RoutingConfig(
        rules=[RoutingRule(source="s", destinations=["a1"])], accounts={"a1": account}
    )
    store = SignalStore(tmp_path / "pl.db")
    return SignalCopierEngine(routing=routing, brokers={"paper": PaperBroker()}, store=store)


def _signal(name, side, intent, price, stop=None, target=None):
    return Signal(
        id=f"sig_{name}",
        source="s",
        symbol="AAPL",
        side=side,
        asset_class=AssetClass.EQUITY,
        quantity=10,
        price=price,
        stop_loss=stop,
        take_profit=target,
        intent=intent,
    )


@pytest.mark.parametrize(
    "name,side,intent,price,stop,target,fragment",
    [
        ("long_stop_above", Side.BUY, Intent.ENTRY_LONG, 100.0, 105.0, None, "must be below the entry price"),
        ("long_stop_equal", Side.BUY, Intent.ENTRY_LONG, 100.0, 100.0, None, "must be below the entry price"),
        ("long_target_below", Side.BUY, Intent.ENTRY_LONG, 100.0, 95.0, 90.0, "must be above the entry price"),
        ("long_target_equal", Side.BUY, Intent.ENTRY_LONG, 100.0, 95.0, 100.0, "must be above the entry price"),
        ("add_stop_above", Side.BUY, Intent.ADD, 100.0, 110.0, None, "must be below the entry price"),
        ("short_stop_below", Side.SELL, Intent.ENTRY_SHORT, 100.0, 95.0, None, "must be above the entry price"),
        ("short_target_above", Side.SELL, Intent.ENTRY_SHORT, 100.0, 105.0, 110.0, "must be below the entry price"),
    ],
)
@pytest.mark.asyncio
async def test_wrong_side_protective_levels_are_rejected(tmp_path, name, side, intent, price, stop, target, fragment):
    engine = _engine(tmp_path)
    results = await engine.handle_signal(_signal(name, side, intent, price, stop, target), dry_run=True)
    assert results, "the signal must produce an explicit result, not vanish"
    assert all(r.status == OrderStatus.REJECTED for r in results)
    assert all("invalid protective levels" in r.message and fragment in r.message for r in results)


@pytest.mark.parametrize(
    "name,side,intent,price,stop,target",
    [
        ("long_ok", Side.BUY, Intent.ENTRY_LONG, 100.0, 95.0, 110.0),
        ("long_stop_only", Side.BUY, Intent.ENTRY_LONG, 100.0, 95.0, None),
        ("long_no_levels", Side.BUY, Intent.ENTRY_LONG, 100.0, None, None),
        ("short_ok", Side.SELL, Intent.ENTRY_SHORT, 100.0, 105.0, 90.0),
    ],
)
@pytest.mark.asyncio
async def test_correct_side_protective_levels_still_admitted(tmp_path, name, side, intent, price, stop, target):
    engine = _engine(tmp_path)
    results = await engine.handle_signal(_signal(name, side, intent, price, stop, target), dry_run=True)
    assert results
    assert all(r.status == OrderStatus.PENDING for r in results), [r.message for r in results]
