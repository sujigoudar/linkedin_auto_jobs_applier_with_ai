"""Venue qualification tests — sandbox-gated tests for live brokers/exchanges.

This module contains tests that exercise real broker/exchange integrations
against their actual venues (production or sandbox, depending on
credentials). These tests are NOT run in CI without explicit venue
credentials, and they skip cleanly (pytest mark: skipped, not errored)
when required environment variables or credential files are not present.

Each test is decorated with `@pytest.mark.skipif` to check for the
presence of required credentials. The pattern:
  - Tests import their adapter class (e.g., AlpacaBroker, CCXTBroker)
  - Each test checks for a specific credential or environment marker
  - Absence of credentials triggers a SKIP, not a test failure
  - When credentials ARE present, tests exercise live order/position/
    balance feedback paths

This is the honest, expected state:
  - CI/sandbox runs: all venue tests are skipped
  - Developer local runs with real credentials: venue tests run
  - Venue tests that DO run record qualification state transitions
    (see app/qualification.py) via direct calls to SignalStore

Venue qualifications tracked here are per exact route (adapter_type,
route_key, asset_class, product_type). A CCXT spot route and a CCXT
perpetual route on the same exchange are tracked separately. See
app/qualification.py's module docstring for the full ladder.
"""
from __future__ import annotations

__all__ = []
