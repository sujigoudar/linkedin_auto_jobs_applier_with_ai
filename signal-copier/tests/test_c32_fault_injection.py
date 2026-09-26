"""C32 (bounded): fault injection for real network-level faults, not just
handled HTTP status codes.

A real Toxiproxy deployment sits a TCP proxy in front of a dependency and
injects faults at the transport layer -- connection resets, dropped
packets, added latency past a client's timeout -- to prove an application
degrades correctly under genuine network failure, not just the tidy
handled-error-response cases its own unit tests usually cover. This
sandbox has no infrastructure to run a real Toxiproxy instance, so this
uses `httpx.MockTransport` (already the pattern this codebase uses for
broker-response fault injection -- see
tests/test_adp04_alpaca_cancel_replace_confirmation.py) to raise genuine
httpx transport exceptions (`ConnectError`, `ReadTimeout`) from inside the
real httpx client stack a broker actually uses, rather than monkeypatching
around it.

This closes a real, previously-untested gap: AlpacaBroker.get_order_status
(app/brokers/alpaca.py) has an `except httpx.HTTPError: return None`
around its GET call, but nothing before this exercised it with an actual
connection-level fault (only HTTP status-code-derived failures are the
same exception family, and even those weren't covered) -- so this had
never actually been proven to survive the fault it claims to handle.
It also proves, at the OrderReconciler level, the documented
"one broker's failure must not block the rest" contract
(app/reconciliation.py's per-order `except Exception: continue` and the
background loop's per-pass `except Exception: logger.exception(...)`)
against a fault type NEITHER AlpacaBroker NOR the reconciler's own code
specifically anticipated -- a corrupted/truncated response body
(`response.json()` raising `json.JSONDecodeError`, which lives outside
AlpacaBroker's own `except httpx.HTTPError` block entirely) -- rather
than a connection fault that broker code already swallows before it
ever reaches the reconciler.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.reconciliation import OrderReconciler


@pytest.fixture(autouse=True)
def _alpaca_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT_A_API_KEY", "test-a")
    monkeypatch.setenv("ALPACA_ACCT_A_API_SECRET", "test-a")
    monkeypatch.setenv("ALPACA_ACCT_B_API_KEY", "test-b")
    monkeypatch.setenv("ALPACA_ACCT_B_API_SECRET", "test-b")


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _faulty_alpaca_broker(fault: Exception) -> AlpacaBroker:
    """A real AlpacaBroker whose transport raises `fault` on every request --
    a genuine connection-level failure, not a crafted HTTP response."""
    broker = AlpacaBroker()

    def transport(request: httpx.Request) -> httpx.Response:
        raise fault

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return broker


def _healthy_alpaca_broker(*, status: str, filled_qty: str = "0") -> AlpacaBroker:
    broker = AlpacaBroker()

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "1", "status": status, "filled_qty": filled_qty}, request=request)

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return broker


def _corrupted_response_alpaca_broker() -> AlpacaBroker:
    """A real AlpacaBroker whose GET returns HTTP 200 (so
    `response.raise_for_status()` doesn't trip its `except httpx.HTTPError`
    at all) but with a truncated/corrupted body -- the kind of byte-level
    corruption a real Toxiproxy `slicer`/`limit_data` toxic produces.
    `response.json()` (app/brokers/alpaca.py, just past that try/except)
    raises `json.JSONDecodeError` on this, a fault type AlpacaBroker itself
    does NOT catch -- it must reach OrderReconciler's own per-order
    `except Exception: continue` to be survived at all."""
    broker = AlpacaBroker()

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"id": "1", "status": "fil', request=request)

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return broker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")],
    ids=["connect-error", "read-timeout", "connect-timeout"],
)
async def test_alpaca_get_order_status_survives_real_transport_faults(fault):
    broker = _faulty_alpaca_broker(fault)
    try:
        result = await broker.get_order_status(DestinationAccount("acct_a", "alpaca"), "order-1")
    finally:
        await broker.close()

    # The documented contract is "nothing new to report", not a raised
    # exception -- the reconciler leaves a PENDING order alone on this
    # exact return value (see app/reconciliation.py).
    assert result is None


def _seed_pending_order(store, *, broker: str, account_id: str, broker_order_id: str):
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=1.0)
    store.save_signal(signal)
    store.record_fill(account_id, "AAPL", Side.BUY, 1.0)
    store.save_order_result(
        OrderResult(
            account_id=account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=broker_order_id,
            filled_quantity=1.0,
            message="submitted",
        ),
        broker=broker,
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=1.0,
    )


@pytest.mark.asyncio
async def test_one_brokers_corrupted_response_does_not_block_reconciling_the_other(store):
    _seed_pending_order(store, broker="alpaca_a", account_id="acct_a", broker_order_id="order-a")
    _seed_pending_order(store, broker="alpaca_b", account_id="acct_b", broker_order_id="order-b")

    faulty = _corrupted_response_alpaca_broker()
    healthy = _healthy_alpaca_broker(status="filled", filled_qty="1")
    reconciler = OrderReconciler(store, {"alpaca_a": faulty, "alpaca_b": healthy})

    try:
        corrected = await reconciler.reconcile_once()
    finally:
        await faulty.close()
        await healthy.close()

    # The faulty broker's own order can't be corrected (its response body
    # was corrupted) and stays PENDING -- but that must NOT raise out of
    # reconcile_once and prevent the healthy broker's order, processed in
    # the very same pass, from being corrected.
    assert corrected == 1
    orders = {o["broker_order_id"]: o for o in store.list_recent_orders()}
    assert orders["order-a"]["status"] == "pending"
    assert orders["order-b"]["status"] == "filled"


@pytest.mark.asyncio
async def test_background_loop_survives_an_unanticipated_exception_and_keeps_running(store, monkeypatch):
    reconciler = OrderReconciler(store, brokers={}, interval_seconds=0.05)

    calls = {"n": 0}
    real_reconcile_once = reconciler.reconcile_once

    async def flaky_reconcile_once():
        calls["n"] += 1
        if calls["n"] == 1:
            # A fault type reconcile_once's own per-order try/except never
            # anticipated (e.g. a bug, not a broker call) -- proving the
            # background loop's *own* outer guard (app/reconciliation.py's
            # `_loop`) survives it too, independent of any per-order guard.
            raise RuntimeError("unanticipated failure during a reconciliation pass")
        return await real_reconcile_once()

    monkeypatch.setattr(reconciler, "reconcile_once", flaky_reconcile_once)

    await reconciler.start()
    try:
        for _ in range(50):
            if reconciler.last_success_at is not None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("background loop never recovered after the first pass's exception")
    finally:
        await reconciler.stop()

    assert calls["n"] >= 2  # it kept looping past the first, failed pass
