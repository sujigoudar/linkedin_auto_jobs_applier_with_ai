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
import asyncio
from types import SimpleNamespace

import pytest

from app.brokers.mt4_mt5 import MT5Broker
from app.models import DestinationAccount, OrderStatus, Side, Signal


def _stub_broker(place_order_result: dict) -> MT5Broker:
    broker = object.__new__(MT5Broker)
    broker._mt5 = SimpleNamespace(TRADE_RETCODE_DONE=10009)
    broker._place_order_sync = lambda *a: place_order_result
    # CONC-01: place_order serializes _place_order_sync through this lock
    # (see app/brokers/mt4_mt5.py's MT5Broker.__init__) -- built here too
    # since this stub bypasses __init__.
    broker._lock = asyncio.Lock()
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
async def test_requote_is_ambiguous_error_not_rejection():
    """REQUOTE (10004) is an ambiguous/transient error (price may have
    changed, retry might work) -- must be ERROR, not REJECTED. Definite
    rejections use other retcodes."""
    broker = _stub_broker(
        {"retcode": 10004, "order": 0, "price": 0, "volume": 0, "comment": "REQUOTE"}
    )

    result = await broker.place_order(
        Signal("s", "EURUSD", Side.BUY), DestinationAccount("a", "mt4_mt5"), 1, "EURUSD"
    )

    assert result.status == OrderStatus.ERROR


@pytest.mark.asyncio
async def test_genuine_rejection_is_still_rejected():
    """Definite rejections (not ambiguous retcodes) must be reported as
    REJECTED -- the fix must not swallow real rejections along with
    genuine partial fills. Use a retcode that's not in the ambiguous set."""
    # Using a hypothetical definite rejection (any code not in the ambiguous set)
    # For this test, we use 10015 (hypothetical definite rejection)
    broker = _stub_broker(
        {"retcode": 10015, "order": 0, "price": 0, "volume": 0, "comment": "REJECTED"}
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


@pytest.mark.asyncio
async def test_concurrent_orders_do_not_interleave_across_accounts():
    """CONC-01 regression: one MT5Broker instance serves every account
    configured with `broker: mt4_mt5` (app/main.py's broker registry), but
    the `MetaTrader5` package's initialize()/order_send()/shutdown() act on
    ONE process-global terminal login, not per-call state. Without
    serializing place_order, two concurrent orders for two different
    accounts could interleave their initialize() calls and have order A
    actually submit under account B's now-current login.

    This drives two concurrent place_order calls through a
    `_place_order_sync` stub that real-sleeps (a real OS thread, via
    `asyncio.to_thread`, exactly like the real `_place_order_sync`) between
    its own start and end markers, and asserts one call's start/end pair is
    never split by the other call's start landing in between -- which is
    exactly what would happen without the lock: the second call's
    `asyncio.to_thread` would be scheduled onto a second worker thread while
    the first is still mid-sleep."""
    import time

    events: list[str] = []

    def make_place_order_sync(label: str):
        def _sync(signal, account, quantity, symbol):
            events.append(f"{label}:start")
            time.sleep(0.05)
            events.append(f"{label}:end")
            return {"retcode": 10009, "order": 1, "price": 100, "volume": 1.0, "comment": "DONE"}
        return _sync

    broker = object.__new__(MT5Broker)
    broker._mt5 = SimpleNamespace(TRADE_RETCODE_DONE=10009)
    broker._lock = asyncio.Lock()

    async def run_one(label: str):
        broker._place_order_sync = make_place_order_sync(label)
        return await broker.place_order(
            Signal("s", "EURUSD", Side.BUY), DestinationAccount("a", "mt4_mt5"), 1, "EURUSD"
        )

    await asyncio.gather(run_one("A"), run_one("B"))

    assert events in (["A:start", "A:end", "B:start", "B:end"], ["B:start", "B:end", "A:start", "A:end"])
