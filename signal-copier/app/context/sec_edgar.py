"""SEC EDGAR (data.sec.gov) — keyless. SEC's only access requirement is an
identifying User-Agent on every request ("Sample Company Name
admin@example.com" — see
https://www.sec.gov/os/webmaster-faq#developers): no API key, no
registration. This module fails closed if that identity isn't configured
(`SEC_EDGAR_USER_AGENT`), rather than sending an unidentified request SEC's
own fair-access policy asks not to.

Endpoints used:
- https://www.sec.gov/files/company_tickers.json — the full ticker->CIK
  mapping (one file, cached in-process; SEC doesn't offer a per-ticker
  lookup endpoint).
- https://data.sec.gov/submissions/CIK##########.json — a company's recent
  filing history.
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json — a
  company's structured XBRL facts (financial statement line items over
  time, as originally filed/amended).

This is filing metadata and structured facts, not real-time data, and not
investment advice or a signal in itself — a filing's *availability* time
here is whatever SEC's own system reports, not independently verified.
"""
from __future__ import annotations

import asyncio

import httpx

from app import config

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

_ticker_to_cik_cache: dict[str, int] | None = None
_cache_lock = asyncio.Lock()


class NotConfigured(RuntimeError):
    """SEC_EDGAR_USER_AGENT isn't set — see this module's docstring."""


def _headers() -> dict[str, str]:
    if not config.SEC_EDGAR_USER_AGENT:
        raise NotConfigured(
            "SEC_EDGAR_USER_AGENT is not set -- SEC's fair-access policy requires an identifying "
            "User-Agent on every request (e.g. 'YourCompany admin@example.com'); this refuses to "
            "send an unidentified request rather than guess one"
        )
    return {"User-Agent": config.SEC_EDGAR_USER_AGENT}


async def _load_ticker_map(client: httpx.AsyncClient) -> dict[str, int]:
    global _ticker_to_cik_cache
    async with _cache_lock:
        if _ticker_to_cik_cache is not None:
            return _ticker_to_cik_cache
        response = await client.get(_TICKERS_URL, headers=_headers())
        response.raise_for_status()
        raw = response.json()  # {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        _ticker_to_cik_cache = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
        return _ticker_to_cik_cache


async def cik_for_ticker(ticker: str) -> int | None:
    """Returns the numeric CIK for a ticker, or None if not found in SEC's
    own mapping (e.g. an option/crypto symbol, or a ticker SEC doesn't
    track)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        mapping = await _load_ticker_map(client)
    return mapping.get(ticker.upper())


async def get_company_submissions(ticker: str) -> dict | None:
    """Recent filing history for a ticker. Returns None if the ticker
    doesn't map to a CIK in SEC's own directory."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        cik = (await _load_ticker_map(client)).get(ticker.upper())
        if cik is None:
            return None
        response = await client.get(_SUBMISSIONS_URL.format(cik=cik), headers=_headers())
        response.raise_for_status()
        return response.json()


async def get_company_facts(ticker: str) -> dict | None:
    """Structured XBRL facts (financial statement line items over time) for
    a ticker. Returns None if the ticker doesn't map to a CIK, or if SEC
    has no XBRL facts for it (common for companies that don't file
    XBRL-tagged statements)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        cik = (await _load_ticker_map(client)).get(ticker.upper())
        if cik is None:
            return None
        response = await client.get(_COMPANY_FACTS_URL.format(cik=cik), headers=_headers())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()
