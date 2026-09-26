"""SIG-01: three distinct ways a duplicate/replayed signal used to submit
duplicate orders, with no protection against any of them:

1. Two routing rules for the same source both listing the same
   destination account routed a single signal to that account twice.
2. handle_signal() had no memory of a signal id it already processed --
   calling it twice with the same Signal.id (e.g. a caller retrying after
   a timeout) submitted to every destination a second time.
3. The webhook and Twilio SMS ingress routes had no replay protection at
   all: a redelivered HTTP request (TradingView/Twilio both redeliver on
   timeout) produced a brand new Signal.id each time and was processed as
   an entirely new, distinct signal.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def test_two_rules_naming_the_same_account_do_not_duplicate_it():
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[
            RoutingRule(source="telegram", destinations=["acct1"]),
            RoutingRule(source="telegram", destinations=["acct1"], symbol_filter=["BTCUSDT"]),
        ],
        accounts={"acct1": account},
    )
    destinations = routing.destinations_for("telegram", "BTCUSDT")
    assert destinations == [account]


def test_unrelated_accounts_from_different_rules_still_both_route():
    acct1 = DestinationAccount(account_id="acct1", broker="paper")
    acct2 = DestinationAccount(account_id="acct2", broker="paper")
    routing = RoutingConfig(
        rules=[
            RoutingRule(source="telegram", destinations=["acct1"]),
            RoutingRule(source="telegram", destinations=["acct2"]),
        ],
        accounts={"acct1": acct1, "acct2": acct2},
    )
    destinations = routing.destinations_for("telegram", "BTCUSDT")
    assert destinations == [acct1, acct2]


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.mark.asyncio
async def test_handle_signal_replays_cached_results_for_an_already_processed_signal_id(store):
    from app.brokers.paper import PaperBroker
    from app.engine import SignalCopierEngine
    from app.routing import RoutingConfig, RoutingRule

    broker = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts={"acct1": DestinationAccount(account_id="acct1", broker="paper")},
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)

    first = await engine.handle_signal(signal)
    assert len(first) == 1
    assert first[0].status == OrderStatus.FILLED

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    second = await engine.handle_signal(signal)  # SAME Signal object/id

    assert place_order_calls == []  # never resubmitted to the broker
    assert len(second) == 1
    assert second[0].broker_order_id == first[0].broker_order_id
    assert second[0].filled_quantity == first[0].filled_quantity


@pytest.mark.asyncio
async def test_a_different_signal_id_for_the_same_symbol_still_processes_normally(store):
    from app.brokers.paper import PaperBroker
    from app.engine import SignalCopierEngine
    from app.routing import RoutingConfig, RoutingRule

    broker = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts={"acct1": DestinationAccount(account_id="acct1", broker="paper")},
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    first = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0))
    second = await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0))

    assert first[0].broker_order_id != second[0].broker_order_id


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

    test_client.post("/accounts", json={"account_id": "acct1", "broker": "paper"})
    test_client.post("/routing-rules", json={"source": "tradingview", "destinations": ["acct1"]})
    return test_client


def test_webhook_replay_with_the_same_idempotency_key_does_not_resubmit(client):
    with client:
        payload = {"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0}
        headers = {"Idempotency-Key": "alert-12345"}

        first = client.post("/webhook/tradingview", json=payload, headers=headers)
        assert first.status_code == 200
        first_orders = first.json()["orders"]
        assert first_orders[0]["status"] == "filled"

        second = client.post("/webhook/tradingview", json=payload, headers=headers)
        assert second.status_code == 200
        assert second.json() == first.json()

        # a genuinely new alert (different key) still processes normally
        third = client.post(
            "/webhook/tradingview", json=payload, headers={"Idempotency-Key": "alert-67890"}
        )
        assert third.status_code == 200
        assert third.json()["signal_id"] != first.json()["signal_id"]


def test_webhook_without_idempotency_key_still_works_unchanged(client):
    with client:
        response = client.post(
            "/webhook/tradingview", json={"symbol": "BTCUSDT", "side": "buy", "quantity": 1.0}
        )
    assert response.status_code == 200
    assert response.json()["orders"][0]["status"] == "filled"


@pytest.fixture
def sms_client(monkeypatch):
    twilio_validator = pytest.importorskip("twilio.request_validator")
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    monkeypatch.setattr(app_config, "TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(app_config, "TWILIO_WEBHOOK_URL", "https://example.test/sms/twilio")
    monkeypatch.setattr(app_config, "TWILIO_ALLOWED_FROM_NUMBERS", ["+15550001111"])
    monkeypatch.setattr(twilio_validator.RequestValidator, "validate", lambda self, url, params, sig: True)
    return TestClient(main_module.app)


def test_twilio_redelivery_of_the_same_message_sid_does_not_resubmit(sms_client, tmp_path, monkeypatch):
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.rules.clear()
    from app.models import DestinationAccount

    from app.routing import RoutingRule

    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    main_module.routing_config.rules.append(RoutingRule(source="sms_twilio", destinations=["acct1"]))

    with sms_client:
        payload = {"Body": "AAPL buy 5", "From": "+15550001111", "MessageSid": "SM123456"}
        headers = {"X-Twilio-Signature": "irrelevant-mocked-valid"}

        first = sms_client.post("/sms/twilio", data=payload, headers=headers)
        assert first.status_code == 200

        second = sms_client.post("/sms/twilio", data=payload, headers=headers)  # Twilio redelivery
        assert second.status_code == 200
        assert second.json() == first.json()

        third = sms_client.post(
            "/sms/twilio",
            data={"Body": "AAPL buy 5", "From": "+15550001111", "MessageSid": "SM999999"},
            headers=headers,
        )
        assert third.status_code == 200
        assert third.json() != first.json()
