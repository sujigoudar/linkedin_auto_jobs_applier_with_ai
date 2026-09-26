"""RISK-01: financial inputs must be strictly validated before they can have
any effect -- a negative/zero/boolean/non-finite quantity or price must be
rejected at the webhook boundary, an invalid resolved stop must not pass
plan validation, and a malformed request body must 400, not crash into a
500."""
import math

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.brokers.paper import PaperBroker
from app.errors import SignalValidationError
from app.lifecycle.manager import PositionLifecycleManager
from app.lifecycle.models import PositionPlan
from app.models import Side
from app.sources.webhook import WebhookSource


@pytest.fixture
def webhook_source():
    return WebhookSource(on_signal=None)


@pytest.mark.parametrize("field", ["quantity", "price", "stop_loss", "take_profit"])
@pytest.mark.parametrize("bad_value", [-5, 0, -0.01])
def test_negative_or_zero_financial_field_is_rejected(webhook_source, field, bad_value):
    payload = {"symbol": "BTCUSDT", "side": "buy", field: bad_value}
    with pytest.raises(SignalValidationError):
        webhook_source.parse(payload)


@pytest.mark.parametrize("field", ["quantity", "price", "stop_loss", "take_profit"])
def test_boolean_financial_field_is_rejected(webhook_source, field):
    """float(True) == 1.0 -- Python would silently accept a boolean as a
    valid quantity unless explicitly rejected."""
    payload = {"symbol": "BTCUSDT", "side": "buy", field: True}
    with pytest.raises(SignalValidationError):
        webhook_source.parse(payload)


@pytest.mark.parametrize("field", ["quantity", "price", "stop_loss", "take_profit"])
@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_non_finite_financial_field_is_rejected(webhook_source, field, bad_value):
    payload = {"symbol": "BTCUSDT", "side": "buy", field: bad_value}
    with pytest.raises(SignalValidationError):
        webhook_source.parse(payload)


def test_non_numeric_financial_field_raises_validation_error_not_crash(webhook_source):
    """Previously `float("abc")` raised an unhandled ValueError here, which
    the webhook route did not catch -- it must surface as a clean
    SignalValidationError (-> HTTP 400), not propagate as a 500."""
    payload = {"symbol": "BTCUSDT", "side": "buy", "quantity": "abc"}
    with pytest.raises(SignalValidationError):
        webhook_source.parse(payload)


def test_valid_positive_financial_fields_still_accepted(webhook_source):
    signal = webhook_source.parse(
        {
            "symbol": "BTCUSDT",
            "side": "buy",
            "quantity": 0.01,
            "price": 65000.0,
            "stop_loss": 63000.0,
            "take_profit": 70000.0,
        }
    )
    assert signal.quantity == 0.01
    assert signal.price == 65000.0
    assert signal.stop_loss == 63000.0
    assert signal.take_profit == 70000.0


def test_omitted_financial_fields_still_parse_as_none(webhook_source):
    signal = webhook_source.parse({"symbol": "BTCUSDT", "side": "buy"})
    assert signal.quantity is None
    assert signal.price is None
    assert signal.stop_loss is None
    assert signal.take_profit is None


@pytest.fixture
def manager():
    return PositionLifecycleManager(brokers={"paper": PaperBroker()})


def _plan(**overrides) -> PositionPlan:
    defaults = dict(
        account_id="acct1",
        symbol="AAPL",
        side=Side.BUY,
        planned_quantity=100.0,
        broker="paper",
        initial_stop=48.50,
    )
    defaults.update(overrides)
    return PositionPlan(**defaults)


@pytest.mark.parametrize("bad_stop", [0.0, -1.0, math.nan, math.inf, -math.inf, True])
def test_validate_plan_refuses_invalid_resolved_stop(manager, bad_stop):
    """A resolved stop of 0/negative/NaN/inf/boolean is not None, so the
    pre-existing `plan.initial_stop is None` check alone would let it
    through as if a real stop were set."""
    plan = _plan(initial_stop=bad_stop)
    error = manager.validate_plan(plan)
    assert error is not None
    assert "not a valid positive price" in error


def test_validate_plan_still_accepts_a_valid_stop(manager):
    plan = _plan(initial_stop=48.50)
    assert manager.validate_plan(plan) is None


@pytest.fixture
def webhook_client(monkeypatch):
    monkeypatch.setattr(app_config, "WEBHOOK_SHARED_SECRET", "test-webhook-secret")
    return TestClient(main_module.app)


def test_malformed_json_body_400s_not_500s(webhook_client):
    """Previously an unparseable body propagated the JSONDecodeError straight
    out of `await request.json()`, past the try/except that only wrapped
    the parse() call -- an unhandled exception, surfaced as a 500."""
    response = webhook_client.post(
        "/webhook/tradingview",
        content=b"not json at all {{{",
        headers={
            "X-Webhook-Secret": "test-webhook-secret",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 400


def test_non_object_json_body_400s(webhook_client):
    response = webhook_client.post(
        "/webhook/tradingview",
        json=["not", "an", "object"],
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert response.status_code == 400


def test_invalid_financial_field_via_http_400s(webhook_client):
    response = webhook_client.post(
        "/webhook/tradingview",
        json={"symbol": "BTCUSDT", "side": "buy", "quantity": -5},
        headers={"X-Webhook-Secret": "test-webhook-secret"},
    )
    assert response.status_code == 400
