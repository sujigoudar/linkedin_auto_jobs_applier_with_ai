"""Frankfurter (https://frankfurter.dev) — fully keyless daily reference FX
rates, sourced from the ECB. No account, no API key, no rate-limit header
to manage.

This is a *reference* rate (ECB's daily fixing), not an executable
bid/ask and not a substitute for whatever price basis an actual FX/CFD
broker adapter uses for a real order — see this project's broker adapters
for that. Use this for reporting/context only.
"""
from __future__ import annotations

import httpx

_BASE_URL = "https://api.frankfurter.dev/v1"


async def get_latest_rate(base: str, quote: str) -> dict:
    """Latest daily ECB reference rate for one currency pair."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{_BASE_URL}/latest", params={"base": base.upper(), "symbols": quote.upper()})
        response.raise_for_status()
        return response.json()


async def get_historical_rate(date: str, base: str, quote: str) -> dict:
    """ECB reference rate for one currency pair on a specific date
    ('YYYY-MM-DD'). Frankfurter returns the nearest earlier business day's
    rate if the exact date wasn't a trading day, per its own documented
    behavior."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{_BASE_URL}/{date}", params={"base": base.upper(), "symbols": quote.upper()})
        response.raise_for_status()
        return response.json()
