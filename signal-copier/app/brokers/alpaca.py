"""Alpaca (US stocks/options) execution destination — STUB.

To implement:
    pip install alpaca-py
    1. Get API key/secret from an Alpaca account (paper trading keys work
       for testing without real money).
    2. In `place_order()`, build an alpaca.trading.client.TradingClient with
       credentials read from env vars `ALPACA_{ACCOUNT_ID}_API_KEY` /
       `ALPACA_{ACCOUNT_ID}_API_SECRET` (same env-var-per-account pattern as
       app/brokers/ccxt_broker.py), submit a MarketOrderRequest, and map the
       response into an OrderResult.
    3. Options support requires Alpaca's options trading enabled on the
       account and using their options-specific order types.
"""
from __future__ import annotations

from app.models import DestinationAccount, OrderResult, Signal
from app.brokers.base import BrokerAdapter


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        raise NotImplementedError(
            "AlpacaBroker is a stub. See module docstring: install alpaca-py, read "
            "ALPACA_{ACCOUNT_ID}_API_KEY/SECRET from env, submit a MarketOrderRequest."
        )
