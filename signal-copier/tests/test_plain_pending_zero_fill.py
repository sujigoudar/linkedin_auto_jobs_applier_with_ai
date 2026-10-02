"""EXE-04: `result.filled_quantity or quantity` treats an explicit, reported
zero fill (0.0, falsy in Python) the same as "nothing reported yet" (None),
silently applying the full requested quantity to a position that's actually
still flat. A genuinely unknown fill (None) is the only case that should
fall back to the optimistic full-quantity guess."""
import pytest

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


class _ZeroFillPendingBroker(BrokerAdapter):
    name = "zero-fill"

    async def place_order(self, signal, account, quantity, symbol):
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="broker-order-1",
            filled_quantity=0.0,  # explicit, reported zero -- not "unknown"
            message="accepted, nothing filled yet",
        )


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _force_release_approved(store, *, adapter_type, route_key, asset_class, product_type="default"):
    """Track 1b: `_ZeroFillPendingBroker` has no real order-status/
    position/balance feedback override at all, so `record_route_
    qualification` can never honestly record `account_entitled`-or-above
    for it (app/qualification.py's `FEEDBACK_DEPENDENT_FLOOR`). Writes
    directly to `route_qualifications`, bypassing that write path's own
    validation, only to isolate THIS file's own subject (EXE-04 explicit-
    zero-fill handling) from Track 1b's separate live-routing
    qualification gate. `record_route_qualification` itself is untouched
    and still fails closed for every real caller."""
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
async def test_explicit_zero_fill_on_a_pending_entry_does_not_apply_the_full_quantity(store):
    broker = _ZeroFillPendingBroker()
    # WP-32: broker has no verified balance capability, so add max_notional_exposure ceiling
    account = DestinationAccount(account_id="acct1", broker="zero-fill", max_notional_exposure=50000.0)
    routing = RoutingConfig(rules=[RoutingRule(source="test", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"zero-fill": broker}, store=store)
    _force_release_approved(store, adapter_type="zero-fill", route_key="acct1", asset_class="crypto")

    results = await engine.handle_signal(Signal(source="test", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0))

    assert results[0].status == OrderStatus.PENDING
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_explicit_zero_fill_on_a_plain_close_does_not_apply_the_full_quantity(store):
    broker = _ZeroFillPendingBroker()
    # P0-5: this broker has no get_broker_position implementation at all, so
    # a plain close now requires this account to be explicitly
    # exclusive_writer_qualified before it's allowed to proceed on this
    # service's own tracked position alone -- see
    # SignalCopierEngine._reconcile_before_plain_close. This test is about
    # zero-fill quantity handling, not reconciliation, so it opts in
    # deliberately rather than have the close blocked before it ever
    # reaches the code under test.
    account = DestinationAccount(account_id="acct1", broker="zero-fill", exclusive_writer_qualified=True)
    routing = RoutingConfig(rules=[RoutingRule(source="test", destinations=["acct1"])], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={"zero-fill": broker}, store=store)
    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)  # pretend a prior fill left us long 10

    result = await engine.close_position(account, "AAPL")

    assert result.status == OrderStatus.PENDING
    # A confirmed zero fill on the closing order must not be treated as if
    # the whole 10 sold -- position should still show the original 10.
    assert store.get_position("acct1", "AAPL") == 10.0
