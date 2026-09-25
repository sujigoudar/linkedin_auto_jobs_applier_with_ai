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

A Signal carrying `stop_loss` and/or `take_profit` is sent as an Alpaca
bracket (`order_class: "bracket"`, both legs) or one-triggers-other
(`order_class: "oto"`, a single leg) order — Alpaca manages the exit once
the parent fills; this service doesn't need to watch for it separately.
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

        order_payload = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": signal.side.value,
            "type": "market",
            "time_in_force": "day",
        }
        if signal.stop_loss and signal.take_profit:
            # Both legs present: OTOCO bracket order.
            order_payload["order_class"] = "bracket"
            order_payload["take_profit"] = {"limit_price": signal.take_profit}
            order_payload["stop_loss"] = {"stop_price": signal.stop_loss}
        elif signal.take_profit:
            # One-triggers-other: a single exit leg attached to the parent.
            order_payload["order_class"] = "oto"
            order_payload["take_profit"] = {"limit_price": signal.take_profit}
        elif signal.stop_loss:
            order_payload["order_class"] = "oto"
            order_payload["stop_loss"] = {"stop_price": signal.stop_loss}

        try:
            response = await self._client.post(
                f"{base_url}/v2/orders",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
                json=order_payload,
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

    async def get_order_status(
        self, account: DestinationAccount, broker_order_id: str
    ) -> OrderResult | None:
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError:
            return None

        try:
            response = await self._client.get(
                f"{base_url}/v2/orders/{broker_order_id}",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        order = response.json()
        status = order.get("status")

        if status == "filled":
            new_status = OrderStatus.FILLED
        elif status in ("canceled", "rejected", "expired"):
            new_status = OrderStatus.REJECTED
        else:
            return None  # still open/pending — nothing new to report

        filled_qty = order.get("filled_qty")
        filled_price = order.get("filled_avg_price")
        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            filled_quantity=float(filled_qty) if filled_qty else None,
            filled_price=float(filled_price) if filled_price else None,
            message=f"Alpaca order status: {status}",
        )

    async def close(self) -> None:
        await self._client.aclose()
