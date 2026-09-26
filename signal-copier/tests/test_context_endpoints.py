"""GET /context/* endpoints -- confirms they require an owner session like
every other read endpoint, and map each module's own failure modes
(NotConfigured, unknown ticker, upstream HTTP error) to the right status
code rather than a raw 500."""
import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config as app_config
from app.context import sec_edgar
from app.db import SignalStore


@pytest.fixture(autouse=True)
def _reset_sec_ticker_cache():
    sec_edgar._ticker_to_cik_cache = None
    yield
    sec_edgar._ticker_to_cik_cache = None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    test_client = TestClient(main_module.app)
    login = test_client.post("/auth/login", json={"password": "test-owner-password"})
    assert login.status_code == 200
    test_client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    return test_client


def test_context_endpoints_require_owner_session(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    unauth_client = TestClient(main_module.app)

    with unauth_client:
        assert unauth_client.get("/context/filings/AAPL").status_code == 401
        assert unauth_client.get("/context/fred/DGS10").status_code == 401
        assert unauth_client.get("/context/fx/USD/EUR").status_code == 401


def test_sec_filings_501s_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(app_config, "SEC_EDGAR_USER_AGENT", "")
    with client:
        response = client.get("/context/filings/AAPL")
    assert response.status_code == 501


def test_sec_filings_returns_data_for_a_known_ticker(client, monkeypatch):
    monkeypatch.setattr(app_config, "SEC_EDGAR_USER_AGENT", "TestCo test@example.com")

    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url)
        if "company_tickers.json" in url:
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}, request=request)
        return httpx.Response(200, json={"cik": 320193, "filings": {}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with client:
        response = client.get("/context/filings/AAPL")

    assert response.status_code == 200
    assert response.json()["cik"] == 320193


def test_sec_filings_404s_for_unknown_ticker(client, monkeypatch):
    monkeypatch.setattr(app_config, "SEC_EDGAR_USER_AGENT", "TestCo test@example.com")

    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with client:
        response = client.get("/context/filings/NOTAREALTICKER")

    assert response.status_code == 404


def test_fred_501s_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(app_config, "FRED_API_KEY", "")
    with client:
        response = client.get("/context/fred/DGS10")
    assert response.status_code == 501


def test_fred_returns_observations(client, monkeypatch):
    monkeypatch.setattr(app_config, "FRED_API_KEY", "test-key")

    async def fake_get(self, url, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"observations": [{"date": "2024-01-01", "value": "4.5"}]}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with client:
        response = client.get("/context/fred/DGS10")

    assert response.status_code == 200
    assert response.json()["observations"][0]["value"] == "4.5"


def test_fx_is_keyless_and_needs_no_extra_config(client):
    async def fake_get(self, url, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"base": "USD", "rates": {"EUR": 0.92}}, request=request)

    import app.context.fx as fx_module

    original = httpx.AsyncClient.get
    httpx.AsyncClient.get = fake_get
    try:
        with client:
            response = client.get("/context/fx/USD/EUR")
    finally:
        httpx.AsyncClient.get = original

    assert response.status_code == 200
    assert response.json()["rates"]["EUR"] == 0.92


def test_upstream_http_error_maps_to_502(client, monkeypatch):
    monkeypatch.setattr(app_config, "FRED_API_KEY", "test-key")

    async def fake_get(self, url, params=None):
        request = httpx.Request("GET", url)
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with client:
        response = client.get("/context/fred/DGS10")

    assert response.status_code == 502


def test_upstream_http_error_redacts_the_api_key_from_the_request_url(client, monkeypatch):
    """SEC-07: httpx's own str(exc) on an HTTPStatusError includes the full
    request URL -- FRED takes its key as a `?api_key=...` query param, so an
    unsanitized error message put the real key directly into this response."""
    monkeypatch.setattr(app_config, "FRED_API_KEY", "super-secret-fred-key")

    async def fake_get(self, url, params=None):
        # Mirror what real httpx.AsyncClient.get(url, params=...) actually
        # sends: params merged into the request's own URL, unlike the
        # control test above. Also use the real raise_for_status() (not a
        # hand-built HTTPStatusError) since ITS generated message is what
        # actually embeds the full URL -- app/context/fred.py calls this
        # same method.
        request = httpx.Request("GET", url, params=params)
        response = httpx.Response(500, request=request)
        response.raise_for_status()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with client:
        response = client.get("/context/fred/DGS10")

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "super-secret-fred-key" not in detail
    assert "REDACTED" in detail
