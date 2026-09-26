"""app/context/sec_edgar.py is keyless (no API key), but SEC's fair-access
policy requires an identifying User-Agent on every request -- these tests
check both the fail-closed behavior when that's unset and the actual
request shape/parsing when it is."""
import httpx
import pytest

from app.context import sec_edgar


@pytest.fixture(autouse=True)
def _reset_ticker_cache():
    sec_edgar._ticker_to_cik_cache = None
    yield
    sec_edgar._ticker_to_cik_cache = None


@pytest.mark.asyncio
async def test_missing_user_agent_fails_closed(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SEC_EDGAR_USER_AGENT", "")

    with pytest.raises(sec_edgar.NotConfigured):
        await sec_edgar.get_company_submissions("AAPL")


@pytest.mark.asyncio
async def test_get_company_submissions_sends_identifying_user_agent_and_resolves_cik(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SEC_EDGAR_USER_AGENT", "TestCo test@example.com")
    captured_headers = []

    async def fake_get(self, url, headers=None, params=None):
        captured_headers.append(headers)
        request = httpx.Request("GET", url)
        if "company_tickers.json" in url:
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}, request=request)
        assert "0000320193" in url  # zero-padded CIK
        return httpx.Response(200, json={"cik": 320193, "filings": {"recent": {}}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    result = await sec_edgar.get_company_submissions("aapl")

    assert result == {"cik": 320193, "filings": {"recent": {}}}
    assert all(h["User-Agent"] == "TestCo test@example.com" for h in captured_headers)


@pytest.mark.asyncio
async def test_unknown_ticker_returns_none_not_an_error(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SEC_EDGAR_USER_AGENT", "TestCo test@example.com")

    async def fake_get(self, url, headers=None, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    assert await sec_edgar.cik_for_ticker("NOTAREALTICKER") is None
    assert await sec_edgar.get_company_submissions("NOTAREALTICKER") is None
    assert await sec_edgar.get_company_facts("NOTAREALTICKER") is None


@pytest.mark.asyncio
async def test_ticker_map_is_cached_across_calls(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "SEC_EDGAR_USER_AGENT", "TestCo test@example.com")
    tickers_fetched = 0

    async def fake_get(self, url, headers=None, params=None):
        nonlocal tickers_fetched
        request = httpx.Request("GET", url)
        if "company_tickers.json" in url:
            tickers_fetched += 1
            return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}, request=request)
        return httpx.Response(200, json={"cik": 320193}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await sec_edgar.get_company_submissions("AAPL")
    await sec_edgar.get_company_submissions("AAPL")

    assert tickers_fetched == 1  # the ticker->CIK map is fetched once, not per call
