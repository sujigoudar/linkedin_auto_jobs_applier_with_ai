"""EXE-10: configuration changes could strand an existing position or
resurrect a deliberately deleted account:
1. Deleting an account with an open tracked position dropped the only
   routing/broker config that could ever manage or close it.
2. Changing an account's broker while it has exposure retargeted it away
   from the broker that actually holds/tracks the position.
3. A one-time YAML seed re-ran whenever config_accounts was empty,
   including after an owner deliberately deleted the only account --
   resurrecting exactly what was removed.
4. An account's enabled=False (meant as an entry pause) also blocked
   CLOSE signals, leaving no way to exit a paused account's existing
   position through the normal signal path.

Reproduces the audit's exact 4 cases (test_http_audit.py,
test_state_and_input_audit.py, test_trading_audit.py).
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.config_admin import seed_from_yaml_if_empty
from app.db import SignalStore
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")

    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    main_module.provider_registry.providers.clear()

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    test_client.headers["X-Webhook-Secret"] = "test-webhook-secret"
    return test_client


def test_audits_exact_case_account_deletion_with_owned_position_is_blocked(client):
    with client:
        client.post("/accounts", json={"account_id": "acct", "broker": "paper"})
        main_module.store.record_fill("acct", "AAPL", Side.BUY, 10)

        response = client.delete("/accounts/acct")

        assert response.status_code == 409
        assert main_module.routing_config.accounts.get("acct") is not None


def test_audits_exact_case_account_broker_change_with_exposure_is_blocked(client):
    with client:
        client.post("/accounts", json={"account_id": "acct", "broker": "paper"})
        main_module.store.record_fill("acct", "AAPL", Side.BUY, 10)

        response = client.post("/accounts", json={"account_id": "acct", "broker": "signalstack", "enabled": True})

        assert response.status_code == 409
        assert main_module.routing_config.accounts["acct"].broker == "paper"


def test_account_without_exposure_can_still_be_deleted_and_broker_changed(client):
    with client:
        client.post("/accounts", json={"account_id": "acct", "broker": "paper"})

        change = client.post("/accounts", json={"account_id": "acct", "broker": "signalstack"})
        assert change.status_code == 200

        delete = client.delete("/accounts/acct")
        assert delete.status_code == 200


def test_audits_exact_case_one_time_seed_does_not_resurrect_a_deleted_account(tmp_path, monkeypatch):
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts:\n  old:\n    broker: paper\n")
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text("rules: []\n")
    providers_path = tmp_path / "providers.yaml"
    providers_path.write_text("providers: {}\n")
    for key, value in {
        "ACCOUNTS_CONFIG_PATH": accounts_path,
        "ROUTING_CONFIG_PATH": routing_path,
        "PROVIDERS_CONFIG_PATH": providers_path,
    }.items():
        monkeypatch.setattr(app_config, key, value)

    store = SignalStore(tmp_path / "db")
    seed_from_yaml_if_empty(store)
    store.delete_config_account("old")

    seed_from_yaml_if_empty(store)

    assert not store.list_config_accounts()


def test_fresh_database_still_seeds_from_yaml_once(tmp_path, monkeypatch):
    accounts_path = tmp_path / "accounts.yaml"
    accounts_path.write_text("accounts:\n  old:\n    broker: paper\n")
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text("rules: []\n")
    providers_path = tmp_path / "providers.yaml"
    providers_path.write_text("providers: {}\n")
    for key, value in {
        "ACCOUNTS_CONFIG_PATH": accounts_path,
        "ROUTING_CONFIG_PATH": routing_path,
        "PROVIDERS_CONFIG_PATH": providers_path,
    }.items():
        monkeypatch.setattr(app_config, key, value)

    store = SignalStore(tmp_path / "db")
    seeded = seed_from_yaml_if_empty(store)

    assert seeded is True
    assert store.list_config_accounts()


@pytest.mark.asyncio
async def test_audits_exact_case_entry_pause_needs_separate_existing_position_exit_handling():
    from app.brokers.paper import PaperBroker
    from app.engine import SignalCopierEngine

    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="review", destinations=["acct1"])], accounts={"acct1": account}
    )
    from app.db import SignalStore as _Store
    import tempfile

    store = _Store(tempfile.mktemp())
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    await engine.handle_signal(Signal(source="review", symbol="AAPL", side=Side.BUY, quantity=10))
    before = len(broker.fills)

    routing.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper", enabled=False)

    await engine.handle_signal(Signal(source="review", symbol="AAPL", side=Side.CLOSE))

    assert len(broker.fills) > before
