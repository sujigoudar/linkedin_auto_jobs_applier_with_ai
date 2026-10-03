"""TradeStation (US equities/options/futures) execution destination, via
TradeStation's own official REST API v3 (no official Python SDK is
published by TradeStation itself, but its request/response shapes are
directly and completely documented, including real field-level type
definitions, in the well-established community wrapper
github.com/mxcoppell/tradestation-api-python). This module talks
directly to the same documented endpoints that wrapper uses, the same
way AlpacaBroker/TradovateBroker/OANDABroker talk to their own brokers'
REST APIs directly rather than through a wrapper.

Base URLs (from that wrapper's own `HttpClient`):
    sim (simulated/paper): https://sim.api.tradestation.com
    live:                  https://api.tradestation.com

Auth is OAuth2 (RFC 6749) `refresh_token` grant against
`https://signin.tradestation.com/oauth/token` -- a real TradeStation
developer application (client_id, optionally client_secret for a
confidential client) plus a refresh_token obtained via a one-time
authorization-code flow this codebase does not automate (TradeStation
requires an interactive user consent step to mint the FIRST refresh
token; there is no way around that for a real account -- see this
module's own `.env.example` entry for exactly what to do).

One TradeStationBroker instance can serve any number of accounts; each
account's credentials come from environment variables named
`TRADESTATION_{ACCOUNT_ID}_*` — never from YAML — so accounts.yaml stays
safe to commit:
    TRADESTATION_{ACCOUNT_ID}_CLIENT_ID
    TRADESTATION_{ACCOUNT_ID}_CLIENT_SECRET  (optional -- confidential
        clients only, per the wrapper's own "conditionally include" note)
    TRADESTATION_{ACCOUNT_ID}_REFRESH_TOKEN
    TRADESTATION_{ACCOUNT_ID}_TS_ACCOUNT_ID  -- TradeStation's own
        account id string -- this service's own `account_id` is a local
        identifier and is never assumed to match it.
    TRADESTATION_{ACCOUNT_ID}_ENV            ("sim" (default) or "live")

## Order status codes

`get_order_status` maps TradeStation's own real order-status code set
(confirmed via that wrapper's own `HistoricalOrderStatus`/`OrderStatus`
type literal, which enumerates every code TradeStation's API actually
returns) -- "FLL"/"FLP" (filled/partial-fill-UROut) as filled, "REJ"
(rejected), "CAN"/"EXP"/"OUT" (canceled/expired/UROut) as rejected-like
terminal states, anything else left as "still working" (`None`).

## Known, disclosed limitations of this slice

- No bracket/stop/take-profit wiring (`supports_native_bracket` stays the
  base class's honest default, False) -- a stop_loss/take_profit-carrying
  signal falls back to the managed-lifecycle path like any other
  undeclared-bracket broker.
- No `place_protective_stop`/`cancel_order`/`get_broker_position`/
  `get_last_price` -- undeclared here (the base class's own honest
  "not implemented" defaults), not a broken/guessed implementation.
- `Route` is hardcoded to `"Intelligent"` (TradeStation's own smart-order
  routing default, used directly in that wrapper's own official
  example -- examples/OrderExecution/place_and_cancel_order.py) rather
  than made configurable; a real deployment needing a specific
  destination route would need this extended.
"""
from __future__ import annotations

import os
import time

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal

_SIM_URL = "https://sim.api.tradestation.com"
_LIVE_URL = "https://api.tradestation.com"
_TOKEN_URL = "https://signin.tradestation.com/oauth/token"

#: See this module's own docstring's "Order status codes" section.
_FILLED_CODES = {"fll", "flp"}
_REJECTED_CODES = {"rej", "can", "exp", "out"}


class TradeStationBroker(BrokerAdapter):
    name = "tradestation"
    supported_asset_classes = frozenset({AssetClass.EQUITY, AssetClass.FUTURE})

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        #: account_id -> (access_token, expires_at_epoch_seconds)
        self._tokens: dict[str, tuple[str, float]] = {}

    def _credentials_for(self, account: DestinationAccount) -> dict[str, str]:
        prefix = f"TRADESTATION_{account.account_id.upper()}"
        required = ["CLIENT_ID", "REFRESH_TOKEN", "TS_ACCOUNT_ID"]
        values = {key: os.getenv(f"{prefix}_{key}") for key in required}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                f"missing {', '.join(f'{prefix}_{key}' for key in missing)} "
                f"environment variable(s) for account '{account.account_id}'"
            )
        values["CLIENT_SECRET"] = os.getenv(f"{prefix}_CLIENT_SECRET", "")
        values["ENV"] = os.getenv(f"{prefix}_ENV", "sim")
        return values  # type: ignore[return-value]

    def _base_url_for(self, env: str) -> str:
        return _LIVE_URL if env == "live" else _SIM_URL

    async def _access_token_for(self, account: DestinationAccount, creds: dict[str, str]) -> str:
        cached = self._tokens.get(account.account_id)
        if cached is not None and cached[1] > time.time():
            return cached[0]

        data = {
            "grant_type": "refresh_token",
            "client_id": creds["CLIENT_ID"],
            "refresh_token": creds["REFRESH_TOKEN"],
        }
        if creds["CLIENT_SECRET"]:
            data["client_secret"] = creds["CLIENT_SECRET"]

        response = await self._client.post(
            _TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        response.raise_for_status()
        body = response.json()
        token = body["access_token"]
        expires_in = float(body.get("expires_in", 1200))
        self._tokens[account.account_id] = (token, time.time() + expires_in - 60)
        return token

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
            creds = self._credentials_for(account)
        except RuntimeError as exc:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc))

        try:
            token = await self._access_token_for(account, creds)
        except (httpx.HTTPError, KeyError) as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"TradeStation auth failed: {exc}",
            )

        base_url = self._base_url_for(creds["ENV"])
        try:
            response = await self._client.post(
                f"{base_url}/v3/orderexecution/orders",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "AccountID": creds["TS_ACCOUNT_ID"],
                    "Symbol": symbol,
                    "Quantity": str(quantity),
                    "OrderType": "Market",
                    "TradeAction": "BUY" if signal.side == Side.BUY else "SELL",
                    "TimeInForce": {"Duration": "DAY"},
                    "Route": "Intelligent",
                },
            )
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"TradeStation order request failed: {exc}",
            )

        body = response.json()
        errors = body.get("Errors") or []
        if errors:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                message=f"TradeStation order rejected: {errors[0].get('Message', errors[0])}",
            )

        orders = body.get("Orders") or []
        if not orders:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"TradeStation order response had neither Orders nor Errors: {body}",
            )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=orders[0]["OrderID"],
            message="submitted to TradeStation",
        )

    async def get_order_status(self, account: DestinationAccount, broker_order_id: str) -> OrderResult | None:
        """C-13: Get order status including filled quantity for partial fills.

        TradeStation's FLP (partial fill then UROut) must report the actual
        filled quantity, not just the terminal status code.
        """
        try:
            creds = self._credentials_for(account)
            token = await self._access_token_for(account, creds)
        except (RuntimeError, httpx.HTTPError, KeyError):
            return None

        base_url = self._base_url_for(creds["ENV"])
        try:
            response = await self._client.get(
                f"{base_url}/v3/brokerage/accounts/{creds['TS_ACCOUNT_ID']}/orders/{broker_order_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        orders = response.json().get("Orders") or []
        if not orders:
            return None
        order = orders[0]
        code = str(order.get("Status", "")).lower()

        if code in _FILLED_CODES:
            new_status = OrderStatus.FILLED
        elif code in _REJECTED_CODES:
            new_status = OrderStatus.REJECTED
        else:
            return None  # still working -- nothing new to report

        # C-13: Read filled quantity from the order response (FilledQuantity or ExecQuantity)
        filled_qty = order.get("FilledQuantity") or order.get("ExecQuantity")
        filled_quantity = float(filled_qty) if filled_qty is not None else None

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            filled_quantity=filled_quantity,
            message=f"TradeStation order status: {code}",
        )

    async def close(self) -> None:
        await self._client.aclose()
