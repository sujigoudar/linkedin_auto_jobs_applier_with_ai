"""The dashboard's "Exit now" (per-position) and "Flatten account" buttons
-- proves both work correctly on a plain account (opposing BUY/SELL
against the tracked SignalStore position) and a managed_lifecycle account
(routed through PositionLifecycleManager.request_exit, respecting
CloseArbiter), and that closing one position doesn't touch a different
symbol or account.
"""
import pytest
from fastapi.testclient import TestClient

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def broker():
    return PaperBroker()


@pytest.fixture
def engine(store, broker):
    accounts = {
        "acct_plain": DestinationAccount(account_id="acct_plain", broker="paper"),
        "acct_managed": DestinationAccount(account_id="acct_managed", broker="paper", managed_lifecycle=True),
    }
    routing = RoutingConfig(rules=[], accounts=accounts)
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker})
    return SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )


@pytest.mark.asyncio
async def test_close_position_flattens_a_plain_account(store, broker, engine):
    account = engine.routing.accounts["acct_plain"]
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 10.0, "AAPL")
    store.record_fill("acct_plain", "AAPL", Side.BUY, 10.0)

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 10.0
    assert store.get_position("acct_plain", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_close_position_with_no_open_position_is_rejected(store, broker, engine):
    account = engine.routing.accounts["acct_plain"]
    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")
    assert result.status == OrderStatus.REJECTED
    assert "no open position" in result.message


@pytest.mark.asyncio
async def test_close_position_flattens_a_managed_lifecycle_account(store, broker, engine):
    account = engine.routing.accounts["acct_managed"]
    lifecycle_manager = engine.lifecycle_manager
    from app.lifecycle.models import PositionPlan

    plan = PositionPlan(
        account_id="acct_managed", symbol="AAPL", side=Side.BUY, planned_quantity=20.0, broker="paper", initial_stop=90.0
    )
    lifecycle_manager.start_plan(plan)
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 20.0, "AAPL")
    await lifecycle_manager.on_entry_fill(account, "AAPL", 20.0)

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 20.0
    lifecycle = lifecycle_manager.get_lifecycle("acct_managed", "AAPL")
    assert lifecycle.closed is True


@pytest.mark.asyncio
async def test_close_position_does_not_touch_a_different_symbol(store, broker, engine):
    account = engine.routing.accounts["acct_plain"]
    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 10.0, "AAPL")
    store.record_fill("acct_plain", "AAPL", Side.BUY, 10.0)
    await broker.place_order(Signal(source="test", symbol="MSFT", side=Side.BUY), account, 5.0, "MSFT")
    store.record_fill("acct_plain", "MSFT", Side.BUY, 5.0)

    await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert store.get_position("acct_plain", "AAPL") == 0.0
    assert store.get_position("acct_plain", "MSFT") == 5.0  # untouched


@pytest.mark.asyncio
async def test_close_position_missing_broker_reports_error(store, broker):
    account = DestinationAccount(account_id="acct1", broker="nonexistent")
    routing = RoutingConfig(rules=[], accounts={"acct1": account})
    engine = SignalCopierEngine(routing=routing, brokers={}, store=store)

    result = await engine.close_position(account, "AAPL")

    assert result.status == OrderStatus.ERROR
    assert "no broker adapter registered" in result.message


# --- HTTP layer (app.main's endpoints) ---


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main_module

    store = SignalStore(tmp_path / "test_http.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    return TestClient(main_module.app), store


def test_close_single_position_endpoint(client):
    test_client, store = client
    import app.main as main_module

    with test_client:
        paper = main_module.brokers["paper"]
        # seed a position via the real broker + store the same way a signal would
        import asyncio

        asyncio.run(
            paper.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), main_module.routing_config.accounts["acct1"], 7.0, "AAPL")
        )
        store.record_fill("acct1", "AAPL", Side.BUY, 7.0)

        response = test_client.post("/positions/acct1/AAPL/close")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "filled"
    assert body["filled_quantity"] == 7.0
    assert store.get_position("acct1", "AAPL") == 0.0


def test_close_single_position_unknown_account_404s(client):
    test_client, _ = client
    with test_client:
        response = test_client.post("/positions/nonexistent/AAPL/close")
    assert response.status_code == 404


def test_flatten_account_endpoint_closes_every_open_symbol(client):
    test_client, store = client
    import app.main as main_module
    import asyncio

    with test_client:
        paper = main_module.brokers["paper"]
        account = main_module.routing_config.accounts["acct1"]

        async def _seed():
            await paper.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 3.0, "AAPL")
            store.record_fill("acct1", "AAPL", Side.BUY, 3.0)
            await paper.place_order(Signal(source="test", symbol="ETHUSDT", side=Side.BUY), account, 2.0, "ETHUSDT")
            store.record_fill("acct1", "ETHUSDT", Side.BUY, 2.0)

        asyncio.run(_seed())

        response = test_client.post("/accounts/acct1/flatten")

    assert response.status_code == 200
    body = response.json()
    assert len(body["closed"]) == 2
    assert all(c["status"] == "filled" for c in body["closed"])
    assert store.get_position("acct1", "AAPL") == 0.0
    assert store.get_position("acct1", "ETHUSDT") == 0.0


def test_flatten_account_with_no_open_positions_closes_nothing(client):
    test_client, _ = client
    with test_client:
        response = test_client.post("/accounts/acct1/flatten")
    assert response.status_code == 200
    assert response.json()["closed"] == []


def test_flatten_unknown_account_404s(client):
    test_client, _ = client
    with test_client:
        response = test_client.post("/accounts/nonexistent/flatten")
    assert response.status_code == 404
