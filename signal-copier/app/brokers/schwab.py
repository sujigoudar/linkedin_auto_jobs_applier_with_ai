"""Charles Schwab (US equities/options) execution destination, via
Schwab's own real REST API. **There is no official Schwab trading API
client, and -- unlike every other broker this codebase talks to --
Schwab has NO SANDBOX/PAPER environment at all.** Every request this
adapter makes, including your very first test of it, goes against a
real account with real money. This slice exists because it was
explicitly requested with that risk accepted; read this whole docstring
before ever wiring real credentials to it.

No official Python SDK is published; this talks directly to the same
documented endpoints the well-established, actively-maintained
community wrapper github.com/alexgolec/schwab-py uses -- its own
source was read directly in this session to confirm the real request/
response shapes (base URL, the OAuth2 token endpoint, the order-leg
JSON schema, and -- critically -- that a successful `placeOrder` call
returns *no JSON body at all*, only a `Location` response header
containing the new order's URL, confirmed via that wrapper's own
`Utils.extract_order_id`), the same way AlpacaBroker/TradovateBroker/
OANDABroker/TradeStationBroker/TastytradeBroker talk to their own
brokers' REST APIs directly rather than through a wrapper.

Base URL (from that wrapper's own client modules -- there is only ONE,
confirmed by reading every request-issuing method in schwab-py's
`schwab/client/*.py`, none of which reference any other host):
    https://api.schwabapi.com

Auth is a standard OAuth2 (RFC 6749) `refresh_token` grant against
`https://api.schwabapi.com/v1/oauth/token`, HTTP Basic-authenticated
with your Schwab developer app's (client_id, client_secret) -- this
part of schwab-py delegates to the standard `requests_oauthlib` library
rather than hand-rolling the request, so this module implements the
standard RFC 6749 shape directly instead of guessing a Schwab-specific
one. Getting the FIRST refresh_token requires a one-time interactive
OAuth2 authorization-code consent step through Schwab's own developer
portal that this codebase does not automate (see this module's own
`.env.example` entry).

One SchwabBroker instance can serve any number of accounts; each
account's credentials come from environment variables named
`SCHWAB_{ACCOUNT_ID}_*` — never from YAML — so accounts.yaml stays safe
to commit:
    SCHWAB_{ACCOUNT_ID}_CLIENT_ID       -- Schwab developer app key
    SCHWAB_{ACCOUNT_ID}_CLIENT_SECRET
    SCHWAB_{ACCOUNT_ID}_REFRESH_TOKEN
    SCHWAB_{ACCOUNT_ID}_ACCOUNT_HASH    -- Schwab's own encrypted account
        hash (from `GET /trader/v1/accounts/accountNumbers`) -- this
        service's own `account_id` is a local identifier and is never
        assumed to match it.

## Order status codes -- PARTIALLY, not fully, independently confirmed

Only `"FILLED"` and `"WORKING"` were independently confirmed as real
Schwab status strings in this session (read directly from a real test
fixture and a real docstring example in a second community wrapper,
github.com/jaycollett/SchwabPy). `"REJECTED"`, `"CANCELED"`, and
`"EXPIRED"` below are treated as terminal/rejected-like based on well-
established public documentation of Schwab's trader API, but were NOT
independently re-verified against a live response in this session
(network egress to Schwab's own interactive API docs was blocked in
this sandbox). Confirm the exact real token spelling against a live
account's response before ever relying on this for a real deployment --
anything not in the two confirmed-filled/confirmed-rejected sets is
left as "still working" (`None`), never guessed either way.

## Known, disclosed limitations of this slice

- No bracket/stop/take-profit wiring (`supports_native_bracket` stays the
  base class's honest default, False).
- No `place_protective_stop`/`cancel_order`/`get_broker_position`/
  `get_last_price` -- undeclared here (the base class's own honest
  "not implemented" defaults), not a broken/guessed implementation.
- Equity orders only (`EquityInstruction`'s plain BUY/SELL) -- options
  use a different to-open/to-close instruction set this slice doesn't
  wire.
"""
from __future__ import annotations

import os
import re
import time

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal

_BASE_URL = "https://api.schwabapi.com"
_TOKEN_URL = f"{_BASE_URL}/v1/oauth/token"
#: Matches the real Location header shape confirmed via schwab-py's own
#: Utils.extract_order_id: "https://api.schwabapi.com/trader/v1/accounts/{hash}/orders/{id}"
_ORDER_LOCATION_RE = re.compile(r"/orders/(\d+)\s*$")

#: See this module's own docstring's "Order status codes" section.
_FILLED_STATUSES = {"filled"}
_REJECTED_STATUSES = {"rejected", "canceled", "expired"}


class SchwabBroker(BrokerAdapter):
    name = "schwab"
    supported_asset_classes = frozenset({AssetClass.EQUITY})

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        #: account_id -> (access_token, expires_at_epoch_seconds)
        self._tokens: dict[str, tuple[str, float]] = {}

    def _credentials_for(self, account: DestinationAccount) -> dict[str, str]:
        prefix = f"SCHWAB_{account.account_id.upper()}"
        required = ["CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "ACCOUNT_HASH"]
        values = {key: os.getenv(f"{prefix}_{key}") for key in required}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                f"missing {', '.join(f'{prefix}_{key}' for key in missing)} "
                f"environment variable(s) for account '{account.account_id}'"
            )
        return values  # type: ignore[return-value]

    async def _access_token_for(self, account: DestinationAccount, creds: dict[str, str]) -> str:
        cached = self._tokens.get(account.account_id)
        if cached is not None and cached[1] > time.time():
            return cached[0]

        response = await self._client.post(
            _TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": creds["REFRESH_TOKEN"]},
            auth=(creds["CLIENT_ID"], creds["CLIENT_SECRET"]),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        body = response.json()
        token = body["access_token"]
        expires_in = float(body.get("expires_in", 1800))
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
                message=f"Schwab auth failed: {exc}",
            )

        try:
            response = await self._client.post(
                f"{_BASE_URL}/trader/v1/accounts/{creds['ACCOUNT_HASH']}/orders",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "orderType": "MARKET",
                    "session": "NORMAL",
                    "duration": "DAY",
                    "orderStrategyType": "SINGLE",
                    "orderLegCollection": [
                        {
                            "instruction": "BUY" if signal.side == Side.BUY else "SELL",
                            "instrument": {"assetType": "EQUITY", "symbol": symbol},
                            "quantity": quantity,
                        }
                    ],
                },
            )
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Schwab order request failed: {exc}",
            )

        # Definite 4xx validation rejections -> REJECTED (can't fix by retrying).
        # Ambiguous 4xx (408, 429) and 5xx -> ERROR (might succeed on retry).
        if response.status_code >= 400:
            if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                    message=f"Schwab order rejected (status {response.status_code}): {response.text}",
                )
            else:
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                    message=f"Schwab order request failed (status {response.status_code}): {response.text}",
                )

        # See this module's own docstring: a successful placeOrder response
        # carries NO JSON body -- the order id is only in the Location header.
        location = response.headers.get("Location", "")
        match = _ORDER_LOCATION_RE.search(location)
        if match is None:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Schwab order accepted (status {response.status_code}) but no order id in Location header: {location!r}",
            )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=match.group(1),
            message="submitted to Schwab",
        )

    async def get_order_status(self, account: DestinationAccount, broker_order_id: str) -> OrderResult | None:
        try:
            creds = self._credentials_for(account)
            token = await self._access_token_for(account, creds)
        except (RuntimeError, httpx.HTTPError, KeyError):
            return None

        try:
            response = await self._client.get(
                f"{_BASE_URL}/trader/v1/accounts/{creds['ACCOUNT_HASH']}/orders/{broker_order_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        order = response.json()
        status = str(order.get("status", "")).lower()

        if status in _FILLED_STATUSES:
            new_status = OrderStatus.FILLED
        elif status in _REJECTED_STATUSES:
            new_status = OrderStatus.REJECTED
        else:
            return None  # still working -- nothing new to report

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            message=f"Schwab order status: {status}",
        )

    async def close(self) -> None:
        await self._client.aclose()
