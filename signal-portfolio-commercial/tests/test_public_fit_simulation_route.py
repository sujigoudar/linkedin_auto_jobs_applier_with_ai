"""PU-03's "Try our fit simulator" -- real HTTP tests against the public
`GET /portfolios/{slug}` and `POST /portfolios/{slug}/fit-simulation`
routes. The signal-copier HTTP call itself is mocked at the boundary
(monkeypatching `httpx.post` in app.services.fit_simulation_client), the
same convention tests/test_relay_routes.py already uses for the relay
worker's own outbound call.
"""
import json

import pytest
from fastapi.testclient import TestClient

from app import config
from app.api.dependencies import get_db_session
from app.main import create_app
from app.models.product import Product, ProductLifecycleState
from app.rate_limit import limiter
from app.services import fit_simulation_client as client_module


@pytest.fixture(autouse=True)
def _reset_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setattr(config, "SIGNAL_COPIER_BASE_URL", "http://signal-copier.internal")
    monkeypatch.setattr(config, "CATALOG_FIT_SIM_SIGNING_SECRET", "test-catalog-fit-sim-secret")
    monkeypatch.setattr(config, "FIT_SIM_CATALOG_CONFIG_JSON", "{}")


def _client(db_session):
    app = create_app()

    def override_get_db_session():
        yield db_session

    app.dependency_overrides[get_db_session] = override_get_db_session
    return TestClient(app)


def _publish(db_session, slug="detail-product"):
    db_session.add(
        Product(
            tenant_id="tenant-a",
            product_name="Detail Product",
            slug=slug,
            cash_bps=500,
            service_modes=["alerts"],
            lifecycle_state=ProductLifecycleState.PUBLISHED,
        )
    )
    db_session.commit()


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


_REPORT_BODY = {
    "summary": {
        "source": "alerts_guy",
        "account_size": 25000.0,
        "max_per_trade": 2500.0,
        "fit_count": 1,
        "fit_percentage": 1.0,
        "simulated_pnl_at_your_size": 250.0,
        "worst_drawdown_at_your_size": 0.0,
        "total_signals": 1,
    },
    "equity_curve": [{"time": "2024-01-02T00:00:00+00:00", "cumulative_pnl": 250.0}],
    "trades": [{"signal_id": "s1", "symbol": "AAPL", "fits": True}],
}


# --- honest, disclosed unavailability (the real default: no product has
# a real source/CSV mapping configured anywhere in this build yet) ---


def test_get_shows_the_fit_simulator_as_unavailable_with_no_mapping_configured(db_session):
    _publish(db_session)
    client = _client(db_session)
    response = client.get("/portfolios/detail-product")
    assert response.status_code == 200
    assert "not available for this portfolio yet" in response.text
    assert "No signal-copier provider source is linked" in response.text


def test_post_also_returns_unavailable_honestly_when_hit_directly_without_a_prior_get(db_session):
    """A crafted POST straight to the route (no GET first) gets the same
    honest outcome, never a code path that only existed because the GET
    route happened to gate the form's visibility."""
    _publish(db_session)
    client = _client(db_session)
    response = client.post("/portfolios/detail-product/fit-simulation", data={"account_size": 1000, "max_per_trade": 100})
    assert response.status_code == 200
    assert "not available for this portfolio yet" in response.text


def test_draft_product_slug_is_a_scoped_404_for_both_routes(db_session):
    db_session.add(
        Product(
            tenant_id="tenant-a",
            product_name="Still Draft",
            slug="still-draft-fitsim",
            cash_bps=500,
            service_modes=["alerts"],
            lifecycle_state=ProductLifecycleState.DRAFT,
        )
    )
    db_session.commit()
    client = _client(db_session)
    assert client.get("/portfolios/still-draft-fitsim").status_code == 404
    assert (
        client.post(
            "/portfolios/still-draft-fitsim/fit-simulation", data={"account_size": 1000, "max_per_trade": 100}
        ).status_code
        == 404
    )


# --- the real, wired path once a product genuinely has a source/CSV mapping ---


def test_post_runs_a_real_simulation_end_to_end_once_configured_and_renders_the_real_report(db_session, monkeypatch):
    _publish(db_session)
    monkeypatch.setattr(
        config,
        "FIT_SIM_CATALOG_CONFIG_JSON",
        json.dumps({"detail-product": {"source": "alerts_guy", "csv_paths": {"AAPL": "/data/AAPL.csv"}}}),
    )
    captured = {}

    def fake_post(url, *, content, headers, timeout):
        captured["url"] = url
        captured["body"] = json.loads(content)
        return _FakeResponse(200, _REPORT_BODY)

    monkeypatch.setattr(client_module.httpx, "post", fake_post)

    client = _client(db_session)
    response = client.post(
        "/portfolios/detail-product/fit-simulation", data={"account_size": 25000, "max_per_trade": 2500}
    )

    assert response.status_code == 200
    assert captured["url"] == "http://signal-copier.internal/catalog/providers/alerts_guy/fit-simulation"
    assert captured["body"]["account_size"] == 25000.0
    assert captured["body"]["max_per_trade"] == 2500.0
    # The real, computed numbers signal-copier returned are rendered --
    # never a fabricated placeholder.
    assert "250.00" in response.text  # simulated_pnl_at_your_size
    assert "not available for this portfolio yet" not in response.text


def test_invalid_account_size_is_rejected_with_no_call_made(db_session, monkeypatch):
    _publish(db_session)
    monkeypatch.setattr(
        config,
        "FIT_SIM_CATALOG_CONFIG_JSON",
        json.dumps({"detail-product": {"source": "alerts_guy", "csv_paths": {"AAPL": "/data/AAPL.csv"}}}),
    )

    def fake_post(*args, **kwargs):
        raise AssertionError("must not be called for an invalid request")

    monkeypatch.setattr(client_module.httpx, "post", fake_post)

    client = _client(db_session)
    response = client.post(
        "/portfolios/detail-product/fit-simulation", data={"account_size": -5, "max_per_trade": 100}
    )
    assert response.status_code == 200
    assert "must both be greater than zero" in response.text


# --- rate limiting ---


def test_exceeding_the_public_rate_limit_returns_429(db_session, monkeypatch):
    _publish(db_session)
    client = _client(db_session)
    responses = [
        client.post("/portfolios/detail-product/fit-simulation", data={"account_size": 1000, "max_per_trade": 100})
        for _ in range(11)
    ]
    statuses = [r.status_code for r in responses]
    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
