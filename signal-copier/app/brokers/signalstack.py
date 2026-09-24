"""SignalStack execution destination.

SignalStack (signalstack.com) is itself an order router: it exposes one
webhook URL per broker/exchange account you connect on their side (IBKR,
Schwab, Alpaca, Tradier, TradeStation, Bybit, Coinbase Pro, Oanda, etc.)
and turns a simple JSON POST into a live order there. That means this
adapter doesn't talk to any broker directly — it just POSTs to whichever
SignalStack webhook URL corresponds to the destination account, and
SignalStack does the actual broker fan-out.

Setup:
    1. In the SignalStack dashboard, connect each broker/exchange account
       you want to trade on, and click "Create Webhook" for it. Each one
       gives you a unique webhook URL (it embeds a secret token — treat it
       like a credential).
    2. For every such account you want this service to route to, add an
       entry to accounts.yaml with `broker: signalstack`, and set the env
       var `SIGNALSTACK_{ACCOUNT_ID}_WEBHOOK_URL` to that account's
       SignalStack webhook URL.
    3. The base payload this adapter sends is `{"symbol", "action",
       "quantity"}`, which covers most brokers per SignalStack's docs. Some
       account types (e.g. options) need extra fields (`limit_price`,
       `class: "option"`, etc.) — see SignalStack's per-broker docs at
       help.signalstack.com for the exact fields your broker needs, and
       extend `_build_payload` below if so.
"""
from __future__ import annotations

import os

import httpx

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class SignalStackBroker(BrokerAdapter):
    name = "signalstack"

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)

    def _webhook_url_for(self, account: DestinationAccount) -> str:
        env_var = f"SIGNALSTACK_{account.account_id.upper()}_WEBHOOK_URL"
        url = os.getenv(env_var)
        if not url:
            raise RuntimeError(
                f"missing {env_var} environment variable for account '{account.account_id}'"
            )
        return url

    def _build_payload(self, signal: Signal, quantity: float, symbol: str) -> dict:
        # SignalStack's minimal schema; extend per-broker as needed (see docstring).
        return {
            "symbol": symbol,
            "action": signal.side.value,
            "quantity": quantity,
        }

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        try:
            url = self._webhook_url_for(account)
        except RuntimeError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        payload = self._build_payload(signal, quantity, symbol)
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=f"SignalStack webhook request failed: {exc}",
            )

        # SignalStack's webhook response only confirms it accepted the order for
        # routing, not that the downstream broker actually filled it — report
        # PENDING rather than FILLED to avoid claiming a fill we haven't seen.
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            message=f"accepted by SignalStack for routing (HTTP {response.status_code}); "
            "fill confirmation not available via webhook response",
        )

    async def close(self) -> None:
        await self._client.aclose()
