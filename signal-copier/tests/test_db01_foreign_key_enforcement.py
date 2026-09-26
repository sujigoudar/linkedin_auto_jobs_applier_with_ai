"""DB-01: orders.signal_id is DECLARED as a foreign key into signals.id in
the schema, but SQLite disables foreign key enforcement by default on
every new connection regardless of a schema's own FOREIGN KEY clause --
this was never turned on, so an order row could point at a signal id that
was never persisted, silently.

Reproduces the audit's exact case
(test_adapter_research_audit::test_database_enforces_declared_foreign_keys),
plus the real call paths that would otherwise violate it once enforcement
is actually on.
"""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_audits_exact_case_foreign_keys_are_enabled(store):
    with store._connect() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_order_referencing_an_unsaved_signal_is_rejected(store):
    with pytest.raises(Exception):
        store.save_order_result(OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id="never-saved"))


def test_order_referencing_a_saved_signal_succeeds(store):
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    store.save_signal(signal)
    store.save_order_result(OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id))
    assert len(store.list_recent_orders()) == 1


def _engine(store, broker, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager)


@pytest.mark.asyncio
async def test_manual_close_of_a_plain_account_does_not_violate_the_fk(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, broker, account)
    await engine.handle_signal(Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0))

    result = await engine.close_position(account, "AAPL")

    assert result.status == OrderStatus.FILLED
    assert len(store.list_recent_orders(account_id="acct1")) == 2  # entry + close


@pytest.mark.asyncio
async def test_manual_close_of_a_managed_lifecycle_account_does_not_violate_the_fk(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine = _engine(store, broker, account)
    await engine.handle_signal(
        Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    )

    result = await engine.close_position(account, "AAPL")

    assert result.status == OrderStatus.FILLED
    orders = store.list_recent_orders(account_id="acct1")
    assert len(orders) == 2  # entry + close, each with its own valid signal_id
    assert all(o["signal_id"] for o in orders)


@pytest.mark.asyncio
async def test_manual_close_still_works_when_the_lifecycle_manager_has_no_store():
    """Mirrors a real construction shape (e.g. a manager built without a
    store wired in) -- the order row's signal_id must still resolve to a
    real, saved signal via close_position's own save, not rely on
    _submit_exit_order having persisted its internal one."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account})
    from app.db import SignalStore as _Store
    import tempfile

    tmp_store = _Store(tempfile.mktemp())
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker})  # no store=
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=tmp_store, lifecycle_manager=lifecycle_manager)
    await engine.handle_signal(
        Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    )

    result = await engine.close_position(account, "AAPL")

    assert result.status == OrderStatus.FILLED
    assert len(tmp_store.list_recent_orders(account_id="acct1")) == 2  # entry + close
