"""FRED (Federal Reserve Bank of St. Louis) — free API key required
(register at https://fredaccount.stlouisfed.org/apikeys). Fails closed
(`NotConfigured`) if `FRED_API_KEY` isn't set, same pattern as this
project's other optional integrations, rather than silently returning
nothing.

Supports ALFRED-style vintages via `realtime_start`/`realtime_end`
(https://fred.stlouisfed.org/docs/api/fred/realtime_period.html) — pass
them explicitly when a historical decision needs the value *as it was
known at that time*, not today's since-revised figure. Omitting them
returns the latest vintage for every observation, same as the plain FRED
default.
"""
from __future__ import annotations

import httpx

from app import config

_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"


class NotConfigured(RuntimeError):
    """FRED_API_KEY isn't set — see this module's docstring."""


async def get_series_observations(
    series_id: str,
    *,
    limit: int = 100,
    sort_order: str = "desc",
    realtime_start: str | None = None,
    realtime_end: str | None = None,
) -> dict:
    """Raw FRED observations JSON for one series. `realtime_start`/
    `realtime_end` are 'YYYY-MM-DD' strings selecting an ALFRED vintage
    window; omit both for "latest revision of everything" (FRED's
    default)."""
    if not config.FRED_API_KEY:
        raise NotConfigured(
            "FRED_API_KEY is not set -- register a free key at "
            "https://fredaccount.stlouisfed.org/apikeys"
        )
    params = {
        "series_id": series_id,
        "api_key": config.FRED_API_KEY,
        "file_type": "json",
        "limit": limit,
        "sort_order": sort_order,
    }
    if realtime_start:
        params["realtime_start"] = realtime_start
    if realtime_end:
        params["realtime_end"] = realtime_end

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(_OBSERVATIONS_URL, params=params)
        response.raise_for_status()
        return response.json()
