"""ADP-08: MT5Broker.place_order treated any retcode other than
TRADE_RETCODE_DONE as a rejection, including TRADE_RETCODE_DONE_PARTIAL
(10010) -- the venue's own signal for "filled part of the requested
volume and stopped there," a real, terminal partial fill. That silently
dropped a confirmed position: nothing downstream (lifecycle tracking,
protective stops) ever learned the account actually holds anything.

Reproduces the audit's exact case (test_adapter_research_audit.py::
test_mt5_partial_success_preserves_fills). `MT5Broker` is built via
`object.__new__` (bypassing `__init__`, which imports the Windows-only
`MetaTrader5` package) with a stub `_mt5` and `_place_order_sync`, exactly
as the audit's own test does.
"""
from types import SimpleNamespace

import pytest

from app.brokers.mt4_mt5 import MT5Broker
from app.models import DestinationAccount, OrderStatus, Side, Signal


def _stub_broker(place_order_result: dict) -> MT5Broker:
    broker = object.__new__(MT5Broker)
    broker._mt5 = SimpleNamespace(TRADE_RETCODE_DONE=10009)
    broker._place_order_sync = lambda *a: place_order_result
    return broker


@pytest.mark.asyncio
async def test_audits_exact_case_partial_success_preserves_fills():
    broker = _stub_broker(
        {"retcode": 10010, "order": 1, "price": 100, "volume": 0.3, "comment": "DONE_PARTIAL"}
    )

    result = await broker.place_order(
        Signal("s", "EURUSD", Side.BUY), DestinationAccount("a", "mt4_mt5"), 1, "EURUSD"
    )

    assert result.filled_quantity == 0.3
    assert result.status != OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_partial_fill_reports_filled_not_pending():
    """A DONE_PARTIAL result is terminal (the venue stopped trying, it
    isn't still resting) -- it must report FILLED with the partial
    quantity, not linger as PENDING waiting for more."""
    broker = _stub_broker(
        {"retcode": 10010, "order": 1, "price": 100, "volume": 0.3, "comment": "DONE_PARTIAL"}
    )

    result = await broker.place_order(
        Signal("s", "EURUSD", Side.BUY), DestinationAccount("a", "mt4_mt5"), 1, "EURUSD"
    )

    assert result.status == OrderStatus.FILLED
    assert result.filled_price == 100
    assert result.broker_order_id == "1"


@pytest.mark.asyncio
async def test_genuine_rejection_is_still_rejected():
    """A real reject retcode (anything besides DONE/DONE_PARTIAL) must
    still be reported as REJECTED -- the fix must not swallow real
    rejections along with genuine partial fills."""
    broker = _stub_broker(
        {"retcode": 10004, "order": 0, "price": 0, "volume": 0, "comment": "REQUOTE"}
    )

    result = await broker.place_order(
        Signal("s", "EURUSD", Side.BUY), DestinationAccount("a", "mt4_mt5"), 1, "EURUSD"
    )

    assert result.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_full_fill_still_reports_filled():
    broker = _stub_broker(
        {"retcode": 10009, "order": 2, "price": 100, "volume": 1.0, "comment": "DONE"}
    )

    result = await broker.place_order(
        Signal("s", "EURUSD", Side.BUY), DestinationAccount("a", "mt4_mt5"), 1, "EURUSD"
    )

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 1.0
