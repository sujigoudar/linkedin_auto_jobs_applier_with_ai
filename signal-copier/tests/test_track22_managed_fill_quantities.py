"""TRK-22: `_handle_managed_entry`/`_handle_managed_close` each submit a
real broker order but, before this task, never threaded AUD-01's
distinct-field quantity model (`applied_quantity`/`confirmed_cumulative_
fill`/`applied_execution_delta`/`outstanding_possible_fill`/
`acknowledged_quantity`) back to their shared caller's `save_order_result`
call -- so `orders.applied_execution_delta` was permanently NULL for
every managed-lifecycle order, leaving Track 16's `/positions/{symbol}/
provider-allocations` (and Track 18's `get_provider_position_ownership`)
blind to managed-lifecycle activity. This directly asserts the fix: a
managed entry that fills, a managed entry that's REJECTED (fields stay
honestly None/0.0, never fabricated), a managed entry that's PENDING with
a synchronous partial fill, and a managed close that fills, all now
populate `orders.applied_execution_delta`/`confirmed_cumulative_fill`/
`outstanding_possible_fill` correctly -- and that `get_provider_position_
ownership` (Track 16/18's own shared computation) now sees managed-
lifecycle activity it previously could not.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.lifecycle.manager import PositionLifecycleManager
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _managed_engine(store, broker, account, sources):
    routing = RoutingConfig(
        rules=[RoutingRule(source=src, destinations=[account.account_id]) for src in sources],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


def _order_row(store: SignalStore, signal_id: str) -> dict:
    """Reads the full `orders` row (including the AUD-01 quantity columns
    `list_orders_for_signal` doesn't project) for one signal id -- there's
    exactly one order per signal in every test below."""
    conn = sqlite3.connect(store.db_path)
    try:
        cursor = conn.execute(
            "SELECT status, filled_quantity, confirmed_cumulative_fill, applied_execution_delta, "
            "outstanding_possible_fill, acknowledged_quantity FROM orders WHERE signal_id = ? ORDER BY id DESC",
            (signal_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    assert row is not None, f"no order row found for signal_id={signal_id}"
    return {
        "status": row[0],
        "filled_quantity": row[1],
        "confirmed_cumulative_fill": row[2],
        "applied_execution_delta": row[3],
        "outstanding_possible_fill": row[4],
        "acknowledged_quantity": row[5],
    }


class _SyncPartialFillBroker(PaperBroker):
    """A broker whose place_order synchronously reports PENDING with a
    real, non-zero `filled_quantity` on its FIRST response -- the exact
    shape `_handle_managed_entry`'s own "initial synchronous response can
    already carry a confirmed partial fill" branch (EXE-08) handles."""

    def __init__(self, filled_quantity: float):
        super().__init__()
        self._filled_quantity = filled_quantity

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="order-sync-partial",
            filled_quantity=self._filled_quantity,
            message="partially filled synchronously",
        )


# --- (a) a managed entry that fills ------------------------------------


@pytest.mark.asyncio
async def test_managed_entry_filled_populates_applied_execution_delta(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    results = await engine.handle_signal(entry)

    assert results[0].status == OrderStatus.FILLED
    assert store.get_position("acct1", "AAPL") == 100.0

    row = _order_row(store, entry.id)
    assert row["status"] == "filled"
    assert row["confirmed_cumulative_fill"] == 100.0
    assert row["applied_execution_delta"] == 100.0
    assert row["outstanding_possible_fill"] == 0.0
    assert row["acknowledged_quantity"] == 100.0


# --- (b) a managed entry that's REJECTED -- fields stay honestly None/0.0


@pytest.mark.asyncio
async def test_managed_entry_rejected_never_fabricates_a_fill(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    # No stop_loss at all -- `validate_plan` refuses this before any
    # broker call is ever reached (see PositionLifecycleManager.
    # validate_plan's own docstring, "no stop-loss resolved").
    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0)
    results = await engine.handle_signal(entry)

    assert results[0].status == OrderStatus.REJECTED
    assert store.get_position("acct1", "AAPL") == 0.0

    row = _order_row(store, entry.id)
    assert row["status"] == "rejected"
    assert row["filled_quantity"] is None
    assert row["confirmed_cumulative_fill"] is None
    # Never reached a broker at all -- honestly None, not a fabricated 0.0,
    # matching `_handle_signal`'s own established convention for its
    # equivalent pre-submission rejections.
    assert row["applied_execution_delta"] is None
    assert row["outstanding_possible_fill"] is None
    assert row["acknowledged_quantity"] is None


# --- (c) a managed entry that's PENDING with a partial fill -------------


@pytest.mark.asyncio
async def test_managed_entry_pending_partial_fill_reports_the_real_partial_quantity(store):
    broker = _SyncPartialFillBroker(filled_quantity=30.0)
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    results = await engine.handle_signal(entry)

    assert results[0].status == OrderStatus.PENDING
    # The confirmed partial is applied immediately (EXE-08) -- not the
    # full requested 100.
    assert store.get_position("acct1", "AAPL") == 30.0

    row = _order_row(store, entry.id)
    assert row["status"] == "pending"
    assert row["confirmed_cumulative_fill"] == 30.0
    assert row["applied_execution_delta"] == 30.0
    # 70 of the original 100 remains genuine uncertain exposure.
    assert row["outstanding_possible_fill"] == 70.0
    # A real broker_order_id was returned -- fully acknowledged.
    assert row["acknowledged_quantity"] == 100.0


# --- (d) a managed close that fills -------------------------------------


@pytest.mark.asyncio
async def test_managed_close_filled_populates_applied_execution_delta(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    await engine.handle_signal(entry)
    assert store.get_position("acct1", "AAPL") == 100.0

    close = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    close_results = await engine.handle_signal(close)

    assert close_results[0].status == OrderStatus.FILLED
    assert store.get_position("acct1", "AAPL") == 0.0

    # DB-01: unlike `close_position`, `_handle_signal`'s managed-close
    # branch does NOT rewrite `result.signal_id` back to the originating
    # CLOSE signal's own id -- the saved row's real `signal_id` is
    # whatever `PositionLifecycleManager._submit_exit_order` generated for
    # its own internal `exit_signal` (a pre-existing, documented gap, not
    # introduced by this task -- see that method's own DB-01 comment).
    row = _order_row(store, close_results[0].signal_id)
    assert row["status"] == "filled"
    assert row["confirmed_cumulative_fill"] == 100.0
    assert row["applied_execution_delta"] == 100.0
    assert row["outstanding_possible_fill"] == 0.0
    assert row["acknowledged_quantity"] == 100.0


# --- (e) close_position (manual dashboard flatten) also threads it ------


@pytest.mark.asyncio
async def test_close_position_managed_flatten_populates_applied_execution_delta(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    await engine.handle_signal(entry)
    assert store.get_position("acct1", "AAPL") == 100.0

    result = await engine.close_position(account, "AAPL", reason="manual_exit")
    assert result.status == OrderStatus.FILLED
    assert store.get_position("acct1", "AAPL") == 0.0

    row = _order_row(store, result.signal_id)
    assert row["status"] == "filled"
    assert row["confirmed_cumulative_fill"] == 100.0
    assert row["applied_execution_delta"] == 100.0
    assert row["outstanding_possible_fill"] == 0.0
    assert row["acknowledged_quantity"] == 100.0


# --- (f) Track 16/18's shared computation now sees managed activity -----


@pytest.mark.asyncio
async def test_provider_position_ownership_now_sees_managed_lifecycle_activity(store):
    """Before this task, `get_provider_position_ownership` (the SAME
    computation Track 16's `/positions/{symbol}/provider-allocations`
    visibility endpoint uses -- see app/db.py's own docstring on
    `_provider_signed_deltas`) was blind to managed-lifecycle orders,
    since `orders.applied_execution_delta` was never populated for them.
    This directly proves that gap is closed -- a managed account's own
    entry fill is now attributed to its real provider."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    await engine.handle_signal(entry)

    this_provider, total = store.get_provider_position_ownership("acct1", "AAPL", "tv")
    assert this_provider == 100.0
    assert total == 100.0

    allocations = store.get_position_provider_allocations("AAPL")
    acct_row = next(a for a in allocations["accounts"] if a["account_id"] == "acct1")
    assert acct_row["total_quantity"] == 100.0
    assert acct_row["provider_allocations"] == [{"provider": "tv", "attributable_quantity": 100.0}]
    assert acct_row["unattributed_quantity"] == 0.0
