"""EXE-08: some brokers report PENDING for a still-open order but already
carry a confirmed partial fill in that SAME synchronous response (e.g.
filled_quantity=30 on an order for 100). Discarding that and waiting for
the next reconciliation poll to "discover" it leaves a real, already-known
fill unprotected for however long that poll interval is."""
import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


class _ImmediatePartialFillBroker(BrokerAdapter):
    name = "partial-broker"

    def __init__(self):
        self.stop_calls = []

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="broker-order-1",
            filled_quantity=30.0,  # confirmed partial, in the SAME response
            message="accepted, 30 of 100 filled so far",
        )

    async def place_protective_stop(self, account, symbol, quantity, stop_price, exit_side):
        self.stop_calls.append(quantity)
        return OrderResult(
            account_id=account.account_id, status=OrderStatus.PENDING, signal_id="", broker_order_id="stop-1"
        )

    def can_protect_a_managed_position(self) -> bool:
        return True


def _force_release_approved(store, *, adapter_type, route_key, asset_class, product_type="default"):
    """Track 1b: this fixture's own test broker has no real order-status/
    position/balance feedback override at all (only `place_order` is
    overridden), so `SignalStore.record_route_qualification` can never
    honestly record `account_entitled`-or-above for it (see
    app/qualification.py's `FEEDBACK_DEPENDENT_FLOOR`). This writes
    directly to `route_qualifications`, bypassing that write path's own
    ladder/feedback validation, ONLY to isolate this file's own subject
    (EXE-08 immediate-partial-fill handling) from Track 1b's separate
    live-routing qualification gate. `record_route_qualification` itself
    is untouched and still fails closed for every real caller."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        for state in [
            "implemented", "configured", "authenticated", "account_entitled", "protocol_tested", "venue_tested",
            "release_approved",
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO route_qualifications "
                "(adapter_type, route_key, asset_class, product_type, state, recorded_at, recorded_by, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (adapter_type, route_key, asset_class, product_type, state, now, "test-fixture", "test-only bypass"),
            )


@pytest.mark.asyncio
async def test_a_confirmed_partial_fill_in_the_initial_pending_response_is_protected_immediately(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _ImmediatePartialFillBroker()
    account = DestinationAccount(account_id="acct1", broker="partial-broker", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="test", destinations=["acct1"])], accounts={"acct1": account})
    manager = PositionLifecycleManager(brokers={"partial-broker": broker}, store=store)
    engine = SignalCopierEngine(routing=routing, brokers={"partial-broker": broker}, store=store, lifecycle_manager=manager)
    _force_release_approved(store, adapter_type="partial-broker", route_key="acct1", asset_class="crypto")

    results = await engine.handle_signal(
        Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=90.0)
    )

    assert results[0].status == OrderStatus.PENDING
    # Protected immediately -- not left waiting for the next reconciliation poll.
    assert broker.stop_calls == [30.0]
    assert store.get_position("acct1", "AAPL") == 30.0
    lifecycle = manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle.confirmed_owned_quantity == 30.0
    assert lifecycle.stop.protected_quantity == 30.0
