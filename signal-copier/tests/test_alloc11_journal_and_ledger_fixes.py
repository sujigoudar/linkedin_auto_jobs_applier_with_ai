"""R0 fixes from docs/audit/SOLUTION_GAP_ANALYSIS.md:

E-01  a managed provider CLOSE is journaled with its resolved exit side, so
      the P&L replay, episodes and the capital gate see the exit;
E-04  lifecycle- and operator-initiated exits are attributed to the ENTRY's
      source (via family_id), so strategy exposure decreases after a stop-out
      or a manual close;
D-13 / F-05  unresolved command-ledger rows are visible and resolvable through
      the API.
"""
import pytest
from fastapi.testclient import TestClient

from app.brokers.paper import PaperBroker
from app.capital_allocator import confirmed_open_notional, confirmed_strategy_notional
from app.db import SignalStore
from app.economics import compute_account_economics
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def _managed(tmp_path, **account_kwargs):
    store = SignalStore(tmp_path / "t.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="m1", broker="paper", managed_lifecycle=True, **account_kwargs)
    manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=RoutingConfig(rules=[RoutingRule(source="tv", destinations=["m1"])], accounts={"m1": account}),
        brokers={"paper": broker},
        store=store,
        lifecycle_manager=manager,
    )
    return store, broker, account, manager, engine


@pytest.mark.asyncio
async def test_managed_provider_close_is_journaled_with_resolved_side_and_unlocks_the_gate(tmp_path):
    store, broker, account, _manager, engine = _managed(tmp_path, max_notional_exposure=100_000.0)
    entry = await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10, price=50.0, stop_loss=45.0))
    assert entry[0].status == OrderStatus.FILLED
    close = await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.CLOSE, price=60.0))
    assert close[0].status == OrderStatus.FILLED
    assert broker.positions["m1"]["AAPL"] == 0.0

    rows = store.list_orders_for_signal(close[0].signal_id)
    assert [r["side"] for r in rows] == ["sell"]  # never 'close'

    economics = compute_account_economics(store, "m1")
    assert economics.incomplete_symbols == []
    assert economics.per_symbol["AAPL"].open_quantity == 0.0
    assert confirmed_open_notional(store, "m1").notional == 0.0
    assert confirmed_strategy_notional(store, "tv").notional == 0.0

    # the account is flat: a new entry must be admitted, not rejected as "unresolved exposure"
    nxt = await engine.handle_signal(Signal(source="tv", symbol="MSFT", side=Side.BUY, quantity=1, price=10.0, stop_loss=9.0))
    assert nxt[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_stop_out_reduces_strategy_exposure(tmp_path):
    store, broker, account, manager, engine = _managed(tmp_path)
    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10, price=50.0, stop_loss=45.0))
    assert confirmed_strategy_notional(store, "tv").notional == 500.0

    fills = broker.simulate_price("AAPL", 44.0)
    assert len(fills) == 1
    await manager.on_stop_filled(account, "AAPL", filled_quantity=10.0, filled_price=44.0)

    assert broker.positions["m1"]["AAPL"] == 0.0
    assert compute_account_economics(store, "m1").incomplete_symbols == []
    assert confirmed_strategy_notional(store, "tv").notional == 0.0
    assert store.list_accounts_with_fills_for_source("tv") == ["m1"]


@pytest.mark.asyncio
async def test_manual_close_reduces_strategy_exposure(tmp_path):
    store, broker, account, _manager, engine = _managed(tmp_path)
    await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=10, price=50.0, stop_loss=45.0))
    result = await engine.close_position(account, "AAPL", reason="manual_exit")
    assert result.status == OrderStatus.FILLED
    assert confirmed_strategy_notional(store, "tv").notional == 0.0
    assert compute_account_economics(store, "m1", source="tv").per_symbol["AAPL"].open_quantity == 0.0


# ---- D-13 / F-05: operator visibility and resolution of unresolved commands ----


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    monkeypatch.setattr(main_module.engine.capital_allocator, "store", store)
    # the module-level PaperBroker is shared by every API test in the run; a
    # fresh instance keeps cash/positions left by earlier tests from changing
    # the admission outcome here (the engine, lifecycle manager and
    # reconciler all hold the same `brokers` dict, so setitem reaches them)
    monkeypatch.setitem(main_module.brokers, "paper", PaperBroker())
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()
    c = TestClient(main_module.app)
    login = c.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    c.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    c.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return c


def test_unresolved_ledger_rows_are_listed_and_resolvable(client, monkeypatch):
    import app.main as main_module

    async def lost_response(*_args, **_kwargs):
        raise TimeoutError("response lost")

    monkeypatch.setattr(main_module.brokers["paper"], "place_order", lost_response)
    with client:
        assert client.post("/accounts", json={"account_id": "a1", "broker": "paper", "max_notional_exposure": 100000}).status_code == 200
        assert client.post("/routing-rules", json={"source": "tradingview", "destinations": ["a1"]}).status_code == 200
        r = client.post("/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 0.5, "price": 65000})
        assert r.status_code == 200 and r.json()["orders"][0]["status"] == "error", r.json()

        listed = client.get("/command-ledger/unresolved").json()["unresolved"]
        assert len(listed) == 1
        assert listed[0]["uncertainty_state"] == "unknown_ambiguous"
        assert listed[0]["account_id"] == "a1"
        key = listed[0]["idempotency_key"]
        assert main_module.engine.capital_allocator.pending_reservation("a1") == 32500.0

        bad = client.post("/command-ledger/resolve", json={"idempotency_key": key, "outcome": "filled", "evidence": "x"})
        assert bad.status_code == 422

        ok = client.post(
            "/command-ledger/resolve",
            json={"idempotency_key": key, "outcome": "not_placed", "evidence": "venue order blotter shows nothing"},
        )
        assert ok.status_code == 200
        assert client.get("/command-ledger/unresolved").json()["unresolved"] == []
        assert main_module.engine.capital_allocator.pending_reservation("a1") == 0.0

        again = client.post(
            "/command-ledger/resolve",
            json={"idempotency_key": key, "outcome": "not_placed", "evidence": "second attempt"},
        )
        assert again.status_code == 409


def test_ledger_endpoints_require_owner_auth(client):
    anon = TestClient(client.app)
    assert anon.get("/command-ledger/unresolved").status_code in (401, 403)
    assert anon.post("/command-ledger/resolve", json={"idempotency_key": "x", "outcome": "not_placed", "evidence": "abc"}).status_code in (401, 403)
