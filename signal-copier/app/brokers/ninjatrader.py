"""NinjaTrader execution destination, via the open-source TradeRouter
project's NinjaScript strategy (https://github.com/roydufek/traderouter —
verified against its README rather than reimplementing a NinjaTrader
bridge from scratch).

Setup:
    1. Install TradeRouter's `WebhookOrderStrategy.cs` into NinjaTrader 8:
       copy it to `Documents\\NinjaTrader 8\\bin\\Custom\\Strategies\\`,
       compile it (F5 in the NinjaScript Editor), and enable one instance
       per account you want this service to route to. Each instance listens
       on its own local HTTP port (TradeRouter's own default convention is
       7091, 7092, 7093, ...; any free port works as long as it matches).
    2. This service must be able to reach that port — same host, or a
       tunnel/VPN if not (NinjaTrader itself has no auth on that listener,
       so don't expose it directly to the internet).
    3. Set NT8_{ACCOUNT_ID}_URL to that instance's local URL, e.g.
       `http://127.0.0.1:7091/webhook/`.

Payload format matches WebhookOrderStrategy.cs's documented schema exactly
(`action`/`sentiment`/`quantity`/`price`/`time`), so no NinjaScript changes
are needed on top of the stock TradeRouter strategy file.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class NinjaTraderBroker(BrokerAdapter):
    name = "ninjatrader"

    def __init__(self, timeout: float = 5.0):
        self._client = httpx.AsyncClient(timeout=timeout)

    def _url_for(self, account: DestinationAccount) -> str:
        env_var = f"NT8_{account.account_id.upper()}_URL"
        url = os.getenv(env_var)
        if not url:
            raise RuntimeError(f"missing {env_var} environment variable for account '{account.account_id}'")
        return url

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        try:
            url = self._url_for(account)
        except RuntimeError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        if signal.side.value == "close":
            # TradeRouter's flatten mapping (buy+flat closes a short, sell+flat closes
            # a long) depends on which side is currently open, which this service
            # doesn't track — so "close" is rejected rather than guessed at.
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="'close' side requires position-aware close logic; not yet implemented",
            )

        payload = {
            "action": signal.side.value,
            "sentiment": "long" if signal.side.value == "buy" else "short",
            "quantity": quantity,
            "price": signal.price or 0,
            "time": datetime.now(timezone.utc).isoformat(),
        }

        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=f"NinjaTrader webhook request failed: {exc}",
            )

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            message=f"forwarded to NinjaTrader strategy (HTTP {response.status_code})",
        )

    async def close(self) -> None:
        await self._client.aclose()
