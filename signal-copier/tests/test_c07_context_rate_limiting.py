"""C07: outbound quota limiter (aiolimiter) for app/context/* -- proves
each module's HTTP call actually goes through its `_rate_limiter`, not
just that the modules import aiolimiter. Uses a tiny, artificial
capacity/window (monkeypatched onto each module's real limiter instance)
so the test can observe real throttling in well under a second, rather
than waiting out the real 1s/60s windows these modules use in production.
"""
import asyncio
import time

import httpx
import pytest
from aiolimiter import AsyncLimiter

from app import config
from app.context import fred as fred_context
from app.context import fx as fx_context
from app.context import sec_edgar


async def _instant_ok_get(self, url, headers=None, params=None):
    request = httpx.Request("GET", url)
    return httpx.Response(200, json={"observations": [], "0": {}}, request=request)


@pytest.mark.asyncio
async def test_sec_edgar_throttles_bursts_through_its_rate_limiter(monkeypatch):
    monkeypatch.setattr(config, "SEC_EDGAR_USER_AGENT", "TestCo test@example.com")
    monkeypatch.setattr(sec_edgar, "_ticker_to_cik_cache", {"AAPL": 320193})
    monkeypatch.setattr(sec_edgar, "_rate_limiter", AsyncLimiter(1, 0.2))  # 1 call per 200ms
    monkeypatch.setattr(httpx.AsyncClient, "get", _instant_ok_get)

    start = time.monotonic()
    for _ in range(3):
        await sec_edgar.get_company_submissions("AAPL")
    elapsed = time.monotonic() - start

    # 3 calls at 1/200ms capacity must take at least ~2 refill intervals.
    assert elapsed >= 0.3


@pytest.mark.asyncio
async def test_fred_throttles_bursts_through_its_rate_limiter(monkeypatch):
    monkeypatch.setattr(config, "FRED_API_KEY", "test-key")
    monkeypatch.setattr(fred_context, "_rate_limiter", AsyncLimiter(1, 0.2))
    monkeypatch.setattr(httpx.AsyncClient, "get", _instant_ok_get)

    start = time.monotonic()
    for _ in range(3):
        await fred_context.get_series_observations("DGS10")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.3


@pytest.mark.asyncio
async def test_fx_throttles_bursts_through_its_rate_limiter(monkeypatch):
    monkeypatch.setattr(fx_context, "_rate_limiter", AsyncLimiter(1, 0.2))
    monkeypatch.setattr(httpx.AsyncClient, "get", _instant_ok_get)

    start = time.monotonic()
    for _ in range(3):
        await fx_context.get_latest_rate("USD", "EUR")
    elapsed = time.monotonic() - start

    assert elapsed >= 0.3


@pytest.mark.asyncio
async def test_calls_within_capacity_are_not_delayed(monkeypatch):
    """Sanity check on the other direction: staying under capacity must
    not introduce any artificial delay."""
    monkeypatch.setattr(fx_context, "_rate_limiter", AsyncLimiter(10, 60))
    monkeypatch.setattr(httpx.AsyncClient, "get", _instant_ok_get)

    start = time.monotonic()
    await fx_context.get_latest_rate("USD", "EUR")
    elapsed = time.monotonic() - start

    assert elapsed < 0.1
