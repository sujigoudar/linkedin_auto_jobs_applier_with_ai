"""Balance/margin tracking per broker: a new, optional `BrokerAdapter.
get_account_balance` capability (app/brokers/base.py), following the same
"defaults to unsupported, computed capability-introspection property"
pattern already used for place_protective_stop/cancel_order/get_broker_
position/get_last_price -- and GET /accounts/{account_id}/balance
(app/main.py), which exposes it.

Real implementation: AlpacaBroker (GET /v2/account). Deliberately NOT
implemented for CCXTBroker -- see that module's own docstring on why a
single account-wide balance figure doesn't exist for spot crypto.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.brokers.alpaca import AlpacaBroker
from app.brokers.base import BrokerAdapter
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.models import AccountBalance, DestinationAccount, OrderResult, Signal


class _NoopBroker(BrokerAdapter):
    name = "noop"

    async def place_order(self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str) -> OrderResult:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_base_adapter_reports_no_balance_capability_by_default():
    broker = _NoopBroker()
    assert broker.has_balance_capability is False
    assert await broker.get_account_balance(DestinationAccount(account_id="a", broker="noop")) is None


def test_broker_adapter_cannot_be_instantiated_without_place_order():
    """`place_order` is `@abc.abstractmethod` -- a subclass that doesn't
    implement it (or a bare `BrokerAdapter()`) must fail at instantiation,
    not silently become usable with no real order-submission path."""
    with pytest.raises(TypeError):
        BrokerAdapter()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_base_adapter_cancel_order_defaults_to_false_not_true():
    """`cancel_order`'s own docstring: "Return False if cancellation isn't
    supported or confirmed -- the caller must not assume it worked." A
    broker that doesn't override this must never report a cancel as
    having succeeded."""
    broker = _NoopBroker()
    assert await broker.cancel_order(DestinationAccount(account_id="a", broker="noop"), "some-order-id") is False


@pytest.mark.asyncio
async def test_base_adapter_replace_stop_quantity_defaults_to_none():
    broker = _NoopBroker()
    result = await broker.replace_stop_quantity(
        DestinationAccount(account_id="a", broker="noop"), "some-order-id", new_quantity=5.0
    )
    assert result is None


@pytest.mark.asyncio
async def test_base_adapter_get_broker_position_defaults_to_none():
    broker = _NoopBroker()
    assert await broker.get_broker_position(DestinationAccount(account_id="a", broker="noop"), "AAPL") is None


def test_base_adapter_capability_introspection_defaults_false_for_unoverridden_methods():
    """`has_cancel_capability`/`has_replace_stop_capability`/
    `has_position_readback_capability` compare the subclass's own method
    against `BrokerAdapter`'s base no-op -- a broker that overrides none
    of them must report every one of these as False, not True."""
    broker = _NoopBroker()
    assert broker.has_cancel_capability is False
    assert broker.has_replace_stop_capability is False
    assert broker.has_position_readback_capability is False


def test_paper_broker_capability_introspection_is_true_for_its_real_overrides():
    """PaperBroker is the reference implementation of every managed-
    lifecycle capability (see app/brokers/paper.py's own docstring) --
    each of these must report True, not fall back to the base class's
    False/None defaults."""
    broker = PaperBroker()
    assert broker.has_cancel_capability is True
    assert broker.has_replace_stop_capability is True
    assert broker.has_position_readback_capability is True
    assert broker.has_protective_stop_capability is True
    assert broker.can_protect_a_managed_position() is True


@pytest.mark.asyncio
async def test_alpaca_declares_balance_capability():
    broker = AlpacaBroker()
    try:
        assert broker.has_balance_capability is True
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_ccxt_does_not_declare_balance_capability(monkeypatch):
    pytest.importorskip("ccxt")  # optional dependency -- not installed in CI (see requirements.txt)
    monkeypatch.setenv("CCXT_ACCT1_API_KEY", "key")
    monkeypatch.setenv("CCXT_ACCT1_API_SECRET", "secret")
    broker = CCXTBroker(exchange_id="binance")
    # Deliberate, disclosed non-implementation -- see this module's own
    # docstring on why a single account-wide figure would be invented,
    # not real, for spot crypto.
    assert broker.has_balance_capability is False
    try:
        assert await broker.get_account_balance(DestinationAccount(account_id="acct1", broker="ccxt")) is None
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_alpaca_get_account_balance_reads_real_account_fields(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()
    captured = {}

    async def fake_get(self, url, headers):
        captured["url"] = url
        captured["headers"] = headers
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "cash": "1000.50",
                "equity": "5000.25",
                "buying_power": "2000.00",
                "maintenance_margin": "150.00",
            },
            request=request,
        )

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    balance = await broker.get_account_balance(account)

    assert balance == AccountBalance(
        account_id="acct1", cash=1000.50, equity=5000.25, buying_power=2000.00, maintenance_margin=150.00
    )
    assert captured["url"] == "https://paper-api.alpaca.markets/v2/account"
    assert captured["headers"]["APCA-API-KEY-ID"] == "key123"
    await broker.close()


@pytest.mark.asyncio
async def test_alpaca_get_account_balance_reports_missing_fields_as_none_not_zero(monkeypatch):
    """A non-margin (cash) Alpaca account has no maintenance_margin
    concept -- must stay None, never an invented 0.0 that would look
    identical to "no margin currently in use" for an account that DOES
    have a margin concept."""
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"cash": "1000.0", "equity": "1000.0", "buying_power": "1000.0"}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    balance = await broker.get_account_balance(account)

    assert balance is not None
    assert balance.maintenance_margin is None
    await broker.close()


@pytest.mark.asyncio
async def test_alpaca_get_account_balance_returns_none_on_http_error(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT1_API_KEY", "key123")
    monkeypatch.setenv("ALPACA_ACCT1_API_SECRET", "secret456")
    broker = AlpacaBroker()

    async def fake_get(self, url, headers):
        request = httpx.Request("GET", url)
        return httpx.Response(401, json={"message": "unauthorized"}, request=request)

    broker._client.get = fake_get.__get__(broker._client)
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    assert await broker.get_account_balance(account) is None
    await broker.close()


@pytest.mark.asyncio
async def test_alpaca_get_account_balance_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_ACCT1_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_ACCT1_API_SECRET", raising=False)
    broker = AlpacaBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")

    assert await broker.get_account_balance(account) is None
    await broker.close()


# --- GET /accounts/{account_id}/balance ---


class _FakeBalanceBroker(BrokerAdapter):
    name = "fake"

    def __init__(self, balance: AccountBalance | None):
        self._balance = balance

    async def place_order(self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str) -> OrderResult:
        raise NotImplementedError

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        return self._balance


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_balance_endpoint_returns_404_for_unknown_account(client):
    with client:
        response = client.get("/accounts/does-not-exist/balance")
    assert response.status_code == 404


def test_balance_endpoint_returns_real_balance_when_broker_supports_it(client, monkeypatch):
    account = DestinationAccount(account_id="acct1", broker="fake")
    monkeypatch.setitem(main_module.routing_config.accounts, "acct1", account)
    monkeypatch.setitem(
        main_module.brokers,
        "fake",
        _FakeBalanceBroker(AccountBalance(account_id="acct1", cash=100.0, equity=200.0, buying_power=50.0)),
    )
    with client:
        response = client.get("/accounts/acct1/balance")
    assert response.status_code == 200
    body = response.json()
    assert body == {"account_id": "acct1", "cash": 100.0, "equity": 200.0, "buying_power": 50.0, "maintenance_margin": None, "currency": None}


def test_balance_endpoint_reports_nulls_not_an_error_when_unsupported(client, monkeypatch):
    """A broker with no verified balance capability (e.g. ccxt) must not
    make this endpoint fail -- it reports every field null, exactly what
    `has_balance_capability` on GET /brokers already discloses up front."""
    account = DestinationAccount(account_id="acct1", broker="fake")
    monkeypatch.setitem(main_module.routing_config.accounts, "acct1", account)
    monkeypatch.setitem(main_module.brokers, "fake", _FakeBalanceBroker(None))
    with client:
        response = client.get("/accounts/acct1/balance")
    assert response.status_code == 200
    body = response.json()
    assert body == {"account_id": "acct1", "cash": None, "equity": None, "buying_power": None, "maintenance_margin": None, "currency": None}


def test_brokers_endpoint_reports_balance_capability(client, monkeypatch):
    account = DestinationAccount(account_id="acct1", broker="fake")
    monkeypatch.setitem(main_module.routing_config.accounts, "acct1", account)
    monkeypatch.setitem(main_module.brokers, "fake", _FakeBalanceBroker(None))
    with client:
        response = client.get("/brokers")
    assert response.status_code == 200
    fake_entry = next(b for b in response.json()["brokers"] if b["name"] == "fake")
    assert fake_entry["has_balance_capability"] is True  # _FakeBalanceBroker overrides the method
