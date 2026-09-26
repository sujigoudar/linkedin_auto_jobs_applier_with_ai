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

## Managed-lifecycle capabilities (app/lifecycle/)

For accounts that opt into the managed-lifecycle path instead (see
app/lifecycle/manager.py), this implements the real endpoints:

- `place_protective_stop` — a standalone `type: "stop"` order (not attached
  to anything else).
- `cancel_order` — `DELETE /v2/orders/{id}`; Alpaca returns 204 on success,
  404 if the order is already filled/gone (treated as "can't confirm
  cancellation," per this class's contract — see BrokerAdapter.cancel_order).
- `replace_stop_quantity` — `PATCH /v2/orders/{id}`. Alpaca's replace
  **cancels the original order and creates a new one** with a new id; the
  returned `OrderResult.broker_order_id` carries that new id, and
  callers (app/lifecycle/manager.py) must track it instead of the old one.
- `get_broker_position` — `GET /v2/positions/{symbol}`; a 404 means flat
  (returns 0.0), not an error.
"""
from __future__ import annotations

import os

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"
    supports_native_bracket = True  # bracket/OTO order_class, see place_order below
    # This module's own docstring: "this adapter sends plain equity market
    # orders and doesn't attempt options-specific order shaping" — despite
    # Alpaca-the-broker supporting options, this adapter's place_order does
    # not, so routing an OPTION signal here would submit a malformed/wrong
    # order rather than an options contract. See BrokerAdapter.supported_asset_classes.
    supported_asset_classes = frozenset({AssetClass.EQUITY})

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
        filled_qty = order.get("filled_qty")

        if status == "filled":
            new_status = OrderStatus.FILLED
        elif status in ("canceled", "rejected", "expired"):
            new_status = OrderStatus.REJECTED
        elif status == "partially_filled" and filled_qty:
            # Still open, but with real fill progress worth reporting -- e.g.
            # so a managed-lifecycle entry can be protected for what's
            # actually confirmed owned so far, not just once the whole order
            # is done (see PositionLifecycleManager.resolve_pending_entry).
            # Stays PENDING (not a new terminal status) since more may yet
            # fill or the remainder may still be canceled.
            new_status = OrderStatus.PENDING
        else:
            return None  # still open/pending with nothing new to report
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

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side: Side
    ) -> OrderResult | None:
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError as exc:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id="", message=str(exc))

        try:
            response = await self._client.post(
                f"{base_url}/v2/orders",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
                json={
                    "symbol": symbol,
                    "qty": str(quantity),
                    "side": exit_side.value,
                    "type": "stop",
                    "stop_price": str(stop_price),
                    "time_in_force": "gtc",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id="", message=f"Alpaca stop order request failed: {exc}")

        order = response.json()
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=order.get("id"),
            message=f"Alpaca stop resting (status: {order.get('status')})",
        )

    async def cancel_order(self, account: DestinationAccount, broker_order_id: str) -> bool:
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError:
            return False

        try:
            response = await self._client.delete(
                f"{base_url}/v2/orders/{broker_order_id}",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            )
        except httpx.HTTPError:
            return False

        # 204: cancelled. Anything else (404 = already filled/gone, 422 = can't be
        # cancelled in its current state, ...) is NOT a confirmed cancellation.
        return response.status_code == 204

    async def replace_stop_quantity(
        self,
        account: DestinationAccount,
        broker_order_id: str,
        new_quantity: float,
        new_price: float | None = None,
    ) -> OrderResult | None:
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError:
            return None

        payload = {"qty": str(new_quantity)}
        if new_price is not None:
            payload["stop_price"] = str(new_price)

        try:
            response = await self._client.patch(
                f"{base_url}/v2/orders/{broker_order_id}",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None  # no confirmed replace — caller falls back to cancel + resubmit

        order = response.json()
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            # Alpaca's replace creates a new order id — see module docstring.
            broker_order_id=order.get("id"),
            message=f"Alpaca stop replaced (status: {order.get('status')})",
        )

    async def get_broker_position(self, account: DestinationAccount, symbol: str) -> float | None:
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError:
            return None

        try:
            response = await self._client.get(
                f"{base_url}/v2/positions/{symbol}",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            )
        except httpx.HTTPError:
            return None

        if response.status_code == 404:
            return 0.0  # flat — no open position for this symbol
        if response.status_code != 200:
            return None

        position = response.json()
        qty = position.get("qty")
        return float(qty) if qty is not None else None

    async def close(self) -> None:
        await self._client.aclose()
