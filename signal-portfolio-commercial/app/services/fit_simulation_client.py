"""PU-03's "Try our fit simulator" -- this service's own backend calling
signal-copier's real `POST /catalog/providers/{source}/fit-simulation`
on behalf of an anonymous public-catalog visitor. See that route's own
docstring (signal-copier's app/main.py, `run_catalog_fit_simulation`)
and app/services/catalog_fit_sim_auth.py there for the auth scheme this
module's own `_sign_request` mirrors.

## Why the browser never talks to signal-copier directly

signal-copier is the OWNER's own private execution engine -- its URL and
signing secret are never sent to a visitor's browser. This module is the
ONLY caller: the dashboard route (app/api/dashboard_routes.py) calls
`run_fit_simulation` server-side; the browser only ever sees this
service's own rendered HTML.

## Why a result can be genuinely unavailable, and why that's the honest
## default today

Running a real simulation needs THREE things this service must actually
have, not assume:
1. `config.SIGNAL_COPIER_BASE_URL` and `config.CATALOG_FIT_SIM_SIGNING_SECRET`
   -- the cross-service wiring itself.
2. A real mapping from this product's own slug to the signal-copier
   `source` name whose historical signals back it
   (`config.FIT_SIM_CATALOG_CONFIG_JSON`).
3. Real historical CSV price paths for that source's own traded
   symbol(s) -- the SAME disclosed requirement signal-copier's own
   `/backtest` and fit-simulation endpoints have always had (no market-
   data vendor is connected anywhere in this project; see that service's
   own README "Signal Backtester" section).

As of this module's own introduction, #2 and #3 are empty
(`FIT_SIM_CATALOG_CONFIG_JSON` defaults to `"{}"`) for every real
product in this build -- there is no product-to-source mapping and no
real historical price CSV anywhere in this repository. `run_fit_simulation`
therefore returns `available=False` with an honest, specific reason for
every slug today, never a fabricated report. The wiring itself (signing,
the HTTP call, response parsing) is real and tested end to end with the
signal-copier HTTP call mocked at the boundary (the same way signal-
copier's own relay worker tests mock its outbound POST) -- once an
operator populates real source/CSV-path configuration for a real
product, the exact same code path returns a real, computed
`ProviderFitReport`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app import config

_AUDIENCE = "catalog-fit-sim-v1"


class FitSimUnavailableReason:
    """Not an enum -- a small set of named string constants, so a
    template/test can match on a stable value without importing an enum
    type across a Jinja boundary. Each one is a genuinely different,
    honestly reported gap; never conflated into one generic "unavailable"."""

    NOT_CONFIGURED = "SERVICE_NOT_CONFIGURED"
    NO_SOURCE_MAPPED = "NO_SOURCE_MAPPED"
    NO_PRICE_DATA = "NO_PRICE_DATA"
    SERVICE_UNREACHABLE = "SERVICE_UNREACHABLE"
    SIMULATION_FAILED = "SIMULATION_FAILED"


@dataclass(frozen=True)
class FitSimulationOutcome:
    available: bool
    #: One of the `FitSimUnavailableReason` constants when `available` is
    #: False; always None when it's True.
    reason: str | None = None
    #: A short, honest, human-readable elaboration for the template to
    #: show (e.g. which config is missing) -- never a fabricated number.
    detail: str | None = None
    #: The signal-copier response body verbatim (its own `summary`/
    #: `equity_curve`/`trades` shape) when `available` is True.
    report: dict[str, Any] | None = None


def _sign_request(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    """The exact "t=<unix ts>,v1=<hex hmac over f'{ts}.{AUDIENCE}.{body}'>"
    scheme signal-copier's own app/services/catalog_fit_sim_auth.py
    verifies -- deliberately a separate, un-shared implementation on each
    side of the trust boundary, same precedent as this service's own
    app/services/relay_auth.py vs signal-copier's app/relay_worker.py."""
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.{_AUDIENCE}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def _load_catalog_config() -> dict[str, dict]:
    """Parses `config.FIT_SIM_CATALOG_CONFIG_JSON`. A malformed value
    (an operator typo, not a visitor-controlled input) is treated as
    empty rather than raised into a request path -- this feature being
    unavailable is always safe; a 500 on every portfolio page from a
    config typo would not be."""
    try:
        parsed = json.loads(config.FIT_SIM_CATALOG_CONFIG_JSON)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def get_fit_sim_availability(slug: str) -> FitSimulationOutcome | None:
    """Cheap, no-network check for whether `slug` has a real source/CSV
    mapping configured at all -- used by the GET route to decide whether
    to show the "Try our fit simulator" form or an honest unavailable
    notice, without waiting for a POST. Returns None when everything
    needed to ATTEMPT a real simulation is present (the form should be
    shown); returns the specific `FitSimulationOutcome` to render instead
    when it isn't."""
    if not config.SIGNAL_COPIER_BASE_URL or not config.CATALOG_FIT_SIM_SIGNING_SECRET:
        return FitSimulationOutcome(
            available=False,
            reason=FitSimUnavailableReason.NOT_CONFIGURED,
            detail="This deployment has not been connected to a signal-copier instance yet.",
        )

    mapping = _load_catalog_config().get(slug)
    if not isinstance(mapping, dict) or not mapping.get("source"):
        return FitSimulationOutcome(
            available=False,
            reason=FitSimUnavailableReason.NO_SOURCE_MAPPED,
            detail="No signal-copier provider source is linked to this product yet.",
        )

    csv_paths = mapping.get("csv_paths")
    if not isinstance(csv_paths, dict) or not csv_paths:
        return FitSimulationOutcome(
            available=False,
            reason=FitSimUnavailableReason.NO_PRICE_DATA,
            detail=(
                "No real historical price data is configured for this product's underlying "
                "instrument(s) yet -- no vendor is connected anywhere in this deployment, and "
                "this feature never substitutes fabricated prices for real ones."
            ),
        )

    return None


def run_fit_simulation(
    slug: str,
    *,
    account_size: float,
    max_per_trade: float,
    lookback_days: float = 90.0,
    http_post=None,
) -> FitSimulationOutcome:
    """Real, end-to-end call when (and only when) `slug` genuinely has a
    source and real CSV price paths configured; an honest
    `FitSimulationOutcome(available=False, ...)` otherwise -- never a
    fabricated report. `http_post` is injectable (defaults to
    `httpx.post`) so tests can mock the cross-service HTTP call at the
    boundary, the same way signal-copier's own relay worker is tested."""
    unavailable = get_fit_sim_availability(slug)
    if unavailable is not None:
        return unavailable

    mapping = _load_catalog_config()[slug]
    source = mapping["source"]
    csv_paths = mapping["csv_paths"]

    body = json.dumps(
        {
            "source": source,
            "account_size": account_size,
            "max_per_trade": max_per_trade,
            "lookback_days": lookback_days,
            "csv_paths": csv_paths,
        }
    ).encode()
    signature = _sign_request(body, config.CATALOG_FIT_SIM_SIGNING_SECRET)
    url = f"{config.SIGNAL_COPIER_BASE_URL.rstrip('/')}/catalog/providers/{source}/fit-simulation"
    post = http_post if http_post is not None else httpx.post

    try:
        response = post(
            url,
            content=body,
            headers={"x-catalog-fit-sim-signature": signature, "content-type": "application/json"},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return FitSimulationOutcome(
            available=False,
            reason=FitSimUnavailableReason.SERVICE_UNREACHABLE,
            detail=f"Could not reach the simulation service: {exc}",
        )

    if response.status_code != 200:
        return FitSimulationOutcome(
            available=False,
            reason=FitSimUnavailableReason.SIMULATION_FAILED,
            detail=f"The simulation service returned {response.status_code}.",
        )

    return FitSimulationOutcome(available=True, report=response.json())
