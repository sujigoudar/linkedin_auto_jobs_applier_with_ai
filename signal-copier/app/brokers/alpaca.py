"""Alpaca (US stocks/options) execution destination, via Alpaca's plain REST API.

One ALpacaBroker instance can serve any number of accounts; each account's
API key/secret pair and paper-vs-live base URL come from environment
variables named `ALPACA_{ACCOUNT_ID}_API_KEY` / `..._API_SECRET` /
`..._BASE_URL` — never from YAML — so accounts.yaml stays safe to commit.

`..._BASE_URL` defaults to Alpaca's paper trading endpoint
(https://paper-api.alpaca.markets) so an account is never accidentally
live-traded without explicitly setting it to
https://api.alpaca.markets.

Options trading needs Alpaca's options-specific contract symbols and
requires options trading enabled on the account; this adapter sends plain
equity market orders and doesn't attempt options-specific order shaping.
"""
from __future__ import annotations

import os

import httpx

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)

    def _credentials_for(self, account: DestinationAccount) -> tuple[str, str, str]:
        prefix = f"ALPACA_{account.account_id.upper()}"
        api_key = os.getenv(f"{prefix}_API_KEY")
        api_secret = os.getenv(f"{prefix}_API_SECRET")
        base_url = os.getenv(f"{prefix}_BASE_URL", "https://paper-api.alpaca.markets")
        if not api_key or not api_secret:
            raise RuntimeError(
                f"missing {prefix}_API_KEY / {prefix}_API_SECRET environment variables "
                f"for account '{account.account_id}'"
            )
        return api_key, api_secret, base_url

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        if signal.side.value == "close":
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="'close' side reached the broker directly without engine-level resolution (see SignalCopierEngine._resolve_close); this broker only accepts buy/sell",
            )

        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        try:
            response = await self._client.post(
                f"{base_url}/v2/orders",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
                json={
                    "symbol": symbol,
                    "qty": str(quantity),
                    "side": signal.side.value,
                    "type": "market",
                    "time_in_force": "day",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=f"Alpaca order request failed: {exc}",
            )

        order = response.json()
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=order.get("id"),
            message=f"submitted to Alpaca (status: {order.get('status')})",
        )

    async def close(self) -> None:
        await self._client.aclose()
