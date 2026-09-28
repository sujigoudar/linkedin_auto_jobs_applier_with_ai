"""OANDA (forex/CFD) execution destination, via OANDA's own official v20
REST API (developer.oanda.com/rest-live-v20). No official Python SDK is
published by OANDA itself, but the community-maintained `oandapyV20`
(github.com/hootnot/oanda-api-v20 -- widely used, still actively
referenced by OANDA's own community) embeds real request/response
examples straight from OANDA's own docs; this module talks directly to
the same documented REST endpoints those examples use, the same way
AlpacaBroker/TradovateBroker talk to their own brokers' REST APIs
directly rather than through a wrapper.

Base URLs (from oandapyV20's own `TRADING_ENVIRONMENTS`, itself taken
from OANDA's own docs):
    practice: https://api-fxpractice.oanda.com
    live:     https://api-fxtrade.oanda.com

One OANDABroker instance can serve any number of accounts; each account's
credentials come from environment variables named `OANDA_{ACCOUNT_ID}_*`
— never from YAML — so accounts.yaml stays safe to commit:
    OANDA_{ACCOUNT_ID}_TOKEN        -- OANDA API access token
    OANDA_{ACCOUNT_ID}_ACCOUNT_ID   -- OANDA's own v20 account id
        (e.g. "101-004-1435156-001") -- this service's own `account_id`
        is a local identifier and is never assumed to match it.
    OANDA_{ACCOUNT_ID}_ENV          -- "practice" (default) or "live"

## Units, not quantity+side

OANDA's own order model uses one SIGNED `units` field (positive = buy/
long, negative = sell/short) rather than a separate side flag -- this is
OANDA's own real, documented convention (confirmed via oandapyV20's own
`MarketOrderRequest`, which documents exactly this: "units: integer
(required). If positive the order results in a LONG order. If negative
the order results in a SHORT order"), not a design choice made here.

## Order placement is synchronous for a MARKET order

A MARKET order (`timeInForce: "FOK"`, the only mode this adapter sends)
either fills or is rejected in the SAME response -- confirmed via
oandapyV20's own documented example response, which shows
`orderFillTransaction` (a genuine immediate fill, carrying the real fill
price) appearing alongside `orderCreateTransaction` in one response body,
not a separate later poll. `place_order` reports FILLED directly from
that same response when `orderFillTransaction` is present, matching this
codebase's own OrderResult contract more precisely than Alpaca's (which
always reports PENDING even for a synchronously-filled market order) --
`get_order_status` still exists as a real, working fallback for the rare
case a create response reports neither a fill nor a definite rejection.

## Known, disclosed limitations of this slice

- No bracket/stop/take-profit wiring (`supports_native_bracket` stays the
  base class's honest default, False) -- OANDA's own order model
  genuinely supports `stopLossOnFill`/`takeProfitOnFill` attached to the
  same create request (see oandapyV20's own `MarketOrderRequest`), but
  this slice only wires the plain market-order path; a stop_loss/
  take_profit-carrying signal falls back to the managed-lifecycle path
  like any other undeclared-bracket broker.
- No `place_protective_stop`/`cancel_order`/`get_broker_position`/
  `get_last_price` -- undeclared here (the base class's own honest
  "not implemented" defaults), not a broken/guessed implementation.
"""
from __future__ import annotations

import os

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal

_PRACTICE_URL = "https://api-fxpractice.oanda.com"
_LIVE_URL = "https://api-fxtrade.oanda.com"

#: OANDA's own real Order.state values (confirmed via oandapyV20's own
#: documented /orders/{orderID}/details response example).
_FILLED_STATES = {"filled"}
_REJECTED_STATES = {"cancelled"}


class OANDABroker(BrokerAdapter):
    name = "oanda"
    supported_asset_classes = frozenset({AssetClass.FOREX})

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)

    def _credentials_for(self, account: DestinationAccount) -> tuple[str, str, str]:
        prefix = f"OANDA_{account.account_id.upper()}"
        token = os.getenv(f"{prefix}_TOKEN")
        oanda_account_id = os.getenv(f"{prefix}_ACCOUNT_ID")
        env = os.getenv(f"{prefix}_ENV", "practice")
        if not token or not oanda_account_id:
            raise RuntimeError(
                f"missing {prefix}_TOKEN / {prefix}_ACCOUNT_ID environment variables "
                f"for account '{account.account_id}'"
            )
        base_url = _LIVE_URL if env == "live" else _PRACTICE_URL
        return token, oanda_account_id, base_url

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
            token, oanda_account_id, base_url = self._credentials_for(account)
        except RuntimeError as exc:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc))

        # See this module's own docstring's "Units, not quantity+side"
        # section -- OANDA's own signed-units convention, not derived here.
        units = quantity if signal.side == Side.BUY else -quantity
        try:
            response = await self._client.post(
                f"{base_url}/v3/accounts/{oanda_account_id}/orders",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "order": {
                        "type": "MARKET",
                        "instrument": symbol,
                        "units": str(units),
                        "timeInForce": "FOK",
                        "positionFill": "DEFAULT",
                    }
                },
            )
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"OANDA order request failed: {exc}",
            )

        body = response.json()
        # See this module's own docstring's "Order placement is
        # synchronous for a MARKET order" section.
        fill = body.get("orderFillTransaction")
        if fill is not None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.FILLED,
                signal_id=signal.id,
                broker_order_id=fill.get("orderID"),
                filled_quantity=abs(float(fill["units"])),
                filled_price=float(fill["price"]),
                message="filled by OANDA",
            )

        reject = body.get("orderCancelTransaction") or body.get("orderRejectTransaction")
        if reject is not None:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                message=f"OANDA order not filled: {reject.get('reason', 'unknown reason')}",
            )

        create = body.get("orderCreateTransaction")
        if create is not None:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.PENDING,
                signal_id=signal.id,
                broker_order_id=create.get("id"),
                message="submitted to OANDA",
            )

        return OrderResult(
            account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
            message=f"OANDA order response had neither a fill, a rejection, nor a create transaction: {body}",
        )

    async def get_order_status(self, account: DestinationAccount, broker_order_id: str) -> OrderResult | None:
        try:
            token, oanda_account_id, base_url = self._credentials_for(account)
        except RuntimeError:
            return None

        try:
            response = await self._client.get(
                f"{base_url}/v3/accounts/{oanda_account_id}/orders/{broker_order_id}/details",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        order = response.json().get("order", {})
        state = str(order.get("state", "")).lower()

        if state in _FILLED_STATES:
            new_status = OrderStatus.FILLED
        elif state in _REJECTED_STATES:
            new_status = OrderStatus.REJECTED
        else:
            return None  # still PENDING/TRIGGERED -- nothing new to report

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            message=f"OANDA order state: {state}",
        )

    async def close(self) -> None:
        await self._client.aclose()
