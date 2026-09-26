import httpx
import pytest

from app.context import fred as fred_context
from app.context import fx as fx_context


@pytest.mark.asyncio
async def test_fred_missing_api_key_fails_closed(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "FRED_API_KEY", "")

    with pytest.raises(fred_context.NotConfigured):
        await fred_context.get_series_observations("DGS10")


@pytest.mark.asyncio
async def test_fred_sends_api_key_and_returns_observations(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "FRED_API_KEY", "test-key-123")
    captured = {}

    async def fake_get(self, url, params=None):
        captured["url"] = url
        captured["params"] = params
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={"observations": [{"date": "2024-01-01", "value": "4.5"}]},
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await fred_context.get_series_observations("DGS10", limit=10)

    assert result["observations"][0]["value"] == "4.5"
    assert captured["params"]["api_key"] == "test-key-123"
    assert captured["params"]["series_id"] == "DGS10"
    assert captured["params"]["file_type"] == "json"


@pytest.mark.asyncio
async def test_fred_vintage_params_pass_through(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "FRED_API_KEY", "test-key-123")
    captured = {}

    async def fake_get(self, url, params=None):
        captured["params"] = params
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"observations": []}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await fred_context.get_series_observations("CPIAUCSL", realtime_start="2020-01-01", realtime_end="2020-01-01")

    assert captured["params"]["realtime_start"] == "2020-01-01"
    assert captured["params"]["realtime_end"] == "2020-01-01"


@pytest.mark.asyncio
async def test_fx_latest_rate_is_keyless(monkeypatch):
    captured = {}

    async def fake_get(self, url, params=None):
        captured["url"] = url
        captured["params"] = params
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"base": "USD", "rates": {"EUR": 0.92}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await fx_context.get_latest_rate("usd", "eur")

    assert result == {"base": "USD", "rates": {"EUR": 0.92}}
    assert captured["params"] == {"base": "USD", "symbols": "EUR"}
    assert "latest" in captured["url"]


@pytest.mark.asyncio
async def test_fx_historical_rate_uses_the_date_path(monkeypatch):
    captured = {}

    async def fake_get(self, url, params=None):
        captured["url"] = url
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"base": "USD", "date": "2023-01-01", "rates": {"EUR": 0.94}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await fx_context.get_historical_rate("2023-01-01", "usd", "eur")

    assert result["rates"]["EUR"] == 0.94
    assert captured["url"].endswith("/2023-01-01")
