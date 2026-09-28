"""POST /catalog/providers/{source}/fit-simulation -- the real, bounded,
non-owner path signal-portfolio-commercial's own backend calls on behalf
of an anonymous public-catalog visitor. See app/main.py's own docstring
on `run_catalog_fit_simulation` and app/services/catalog_fit_sim_auth.py
for the design this exercises.
"""
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SignalStore
from app.models import DestinationAccount, Signal, Side
from app.rate_limit import limiter
from app.services.catalog_fit_sim_auth import sign_catalog_fit_sim_request


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(app_config, "CATALOG_FIT_SIM_SIGNING_SECRET", "test-catalog-fit-sim-secret")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    main_module.routing_config.accounts.clear()
    main_module.routing_config.accounts["acct1"] = DestinationAccount(account_id="acct1", broker="paper")
    return TestClient(main_module.app)


def _signal(**overrides):
    defaults = dict(
        source="alerts_guy",
        symbol="AAPL",
        side=Side.BUY,
        quantity=100.0,
        price=100.0,
        received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _body(csv_path, **overrides):
    payload = dict(
        source="alerts_guy",
        account_size=25000.0,
        max_per_trade=2500.0,
        lookback_days=3000.0,
        csv_paths={"AAPL": str(csv_path)},
    )
    payload.update(overrides)
    return json.dumps(payload).encode()


def _post(client, body, *, secret="test-catalog-fit-sim-secret", timestamp=None, source="alerts_guy"):
    header = sign_catalog_fit_sim_request(body, secret, timestamp=timestamp)
    return client.post(
        f"/catalog/providers/{source}/fit-simulation",
        content=body,
        headers={"x-catalog-fit-sim-signature": header, "content-type": "application/json"},
    )


def _write_csv(tmp_path):
    csv_path = tmp_path / "AAPL.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close\n"
        "2024-01-02T00:00:00+00:00,101,111,100,110\n"  # target-ish move so a trade resolves
    )
    return csv_path


# --- the real, correctly-authenticated path works end to end ---


def test_a_correctly_signed_request_runs_a_real_simulation_and_returns_only_fit_sim_fields(client, store, tmp_path):
    store.save_signal(_signal())
    csv_path = _write_csv(tmp_path)

    response = _post(client, _body(csv_path))

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"summary", "equity_curve", "trades"}
    assert body["summary"]["source"] == "alerts_guy"
    assert body["summary"]["account_size"] == 25000.0
    assert body["trades"][0]["symbol"] == "AAPL"
    # Rescaled to the caller's own max_per_trade, exactly like the owner
    # route's own already-tested rescaling behavior.
    assert body["trades"][0]["simulated_quantity"] == pytest.approx(25.0)
    # No owner-account/position/balance field anywhere in this response --
    # everything present is either the caller's own request echoed back
    # (summary) or that source's own historical-signal replay (trades).
    disallowed_markers = ("account_id", "balance", "broker", "position")
    flat = json.dumps(body)
    for marker in disallowed_markers:
        assert marker not in flat


def test_mismatched_path_and_body_source_is_rejected(client, store, tmp_path):
    csv_path = _write_csv(tmp_path)
    response = _post(client, _body(csv_path, source="someone_else"))
    assert response.status_code == 422


# --- signature/auth boundary ---


def test_a_missing_signature_is_rejected(client, tmp_path):
    csv_path = _write_csv(tmp_path)
    body = _body(csv_path)
    response = client.post(
        "/catalog/providers/alerts_guy/fit-simulation",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 401


def test_a_wrong_secret_is_rejected(client, tmp_path):
    csv_path = _write_csv(tmp_path)
    response = _post(client, _body(csv_path), secret="wrong-secret")
    assert response.status_code == 401


def test_an_expired_timestamp_is_rejected(client, tmp_path):
    csv_path = _write_csv(tmp_path)
    response = _post(client, _body(csv_path), timestamp=1)  # 1970 -- wildly outside the tolerance window
    assert response.status_code == 401


def test_a_tampered_body_is_rejected(client, tmp_path):
    csv_path = _write_csv(tmp_path)
    body = _body(csv_path)
    header = sign_catalog_fit_sim_request(body, "test-catalog-fit-sim-secret")
    tampered = body.replace(b"25000.0", b"999999999.0")
    response = client.post(
        "/catalog/providers/alerts_guy/fit-simulation",
        content=tampered,
        headers={"x-catalog-fit-sim-signature": header, "content-type": "application/json"},
    )
    assert response.status_code == 401


def test_the_route_is_unconfigured_and_fails_closed_when_the_secret_is_blank(client, tmp_path, monkeypatch):
    from app import config as app_config

    monkeypatch.setattr(app_config, "CATALOG_FIT_SIM_SIGNING_SECRET", "")
    csv_path = _write_csv(tmp_path)
    response = _post(client, _body(csv_path), secret="")
    assert response.status_code == 503


def test_too_many_symbols_is_rejected_by_the_ceiling(client, tmp_path):
    csv_path = _write_csv(tmp_path)
    too_many = {f"SYM{i}": str(csv_path) for i in range(26)}
    response = _post(client, _body(csv_path, csv_paths=too_many))
    assert response.status_code == 422


def test_exceeding_the_route_specific_rate_limit_returns_429(client, store, tmp_path):
    store.save_signal(_signal())
    csv_path = _write_csv(tmp_path)
    responses = [_post(client, _body(csv_path)) for _ in range(21)]
    statuses = [r.status_code for r in responses]
    assert statuses[:20] == [200] * 20
    assert statuses[20] == 429


# --- LOAD-BEARING: a valid catalog-fit-sim signature must never
# authenticate to an owner-gated route ---


def test_a_valid_catalog_fit_sim_signature_cannot_authenticate_to_an_owner_gated_route(client, tmp_path):
    """The single most safety-critical invariant of this whole slice: a
    caller holding only a valid catalog-fit-sim token (no owner cookie
    session, no CSRF token) must be refused by every OTHER owner-gated
    route -- this signature scheme authenticates to exactly one endpoint
    and nothing else in this service."""
    csv_path = _write_csv(tmp_path)
    body = _body(csv_path)
    header = sign_catalog_fit_sim_request(body, "test-catalog-fit-sim-secret")

    # Not accepted as an owner session on a real owner-gated route --
    # neither as a cookie nor as any header require_owner reads from.
    response = client.get(
        "/accounts",
        headers={"x-catalog-fit-sim-signature": header},
    )
    assert response.status_code == 401

    # Nor does it satisfy the OWNER-GATED version of this exact same
    # capability (`/providers/{source}/fit-simulation`, cookie/CSRF-only).
    response = client.post(
        "/providers/alerts_guy/fit-simulation",
        content=body,
        headers={"x-catalog-fit-sim-signature": header, "content-type": "application/json"},
    )
    assert response.status_code == 401
