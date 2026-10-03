"""Tradovate (US futures) execution destination, via Tradovate's own
documented REST API. No official Python SDK exists (confirmed: Tradovate's
own GitHub org, github.com/tradovate, ships JS tutorials only) -- this talks
directly to the same REST endpoints those official tutorials use
(github.com/tradovate/example-api-js), the same way app/brokers/alpaca.py
talks to Alpaca's own REST API directly rather than through a wrapper.

Base URLs (from tutorial/tutorialsURLs.js in that official repo):
    demo: https://demo.tradovateapi.com/v1
    live: https://live.tradovateapi.com/v1
A token minted against one host is only valid against that same host.

One TradovateBroker instance can serve any number of accounts; each
account's credentials come from environment variables named
`TRADOVATE_{ACCOUNT_ID}_*` — never from YAML — so accounts.yaml stays safe
to commit:
    TRADOVATE_{ACCOUNT_ID}_USERNAME
    TRADOVATE_{ACCOUNT_ID}_PASSWORD
    TRADOVATE_{ACCOUNT_ID}_APP_ID
    TRADOVATE_{ACCOUNT_ID}_APP_VERSION   (defaults to "1.0")
    TRADOVATE_{ACCOUNT_ID}_CID
    TRADOVATE_{ACCOUNT_ID}_SECRET
    TRADOVATE_{ACCOUNT_ID}_DEVICE_ID
    TRADOVATE_{ACCOUNT_ID}_ACCOUNT_SPEC  -- the real Tradovate account name
        (what Tradovate calls `accountSpec`, e.g. "DEMO123456") -- this
        service's own `account_id` is a local identifier and is NEVER
        assumed to match it.
    TRADOVATE_{ACCOUNT_ID}_ENV           ("demo" (default) or "live")

## isAutomated -- a real compliance requirement, not a style choice

Tradovate's own official tutorial (tutorial/Access/EX-4a-Place-An-Order/
README.md in that same repo) states this in its own words: "if you were to
place an order impersonally - like via a trading bot or through some other
algorithmic process - you MUST supply `isAutomated` as `true`. The exchange
is _very serious_ about this requirement and _failing to do so could
violate exchange policies_." Every order this adapter places is
algorithmic by construction, so `isAutomated: true` is hardcoded, never a
caller-supplied option.

## Known, disclosed limitations of this slice

- **Order status field mapping is not independently re-verified against a
  live Tradovate response in this session** (network egress to
  api.tradovate.com's own interactive docs was blocked in this sandbox --
  every other Tradovate host confirmed reachable came back as real,
  official GitHub-hosted source, never guessed). `get_order_status` reads
  `/order/item?id=<id>` (a real, documented endpoint -- confirmed via
  TradovatePy's own `order_item`, github.com/antonio-hickey/TradovatePy)
  and maps its `ordStatus` field defensively: known-filled/known-rejected
  token sets, anything else (including a field Tradovate genuinely renamed)
  left as "still pending" (`None`) rather than guessed either way. Confirm
  the real token spelling against a live demo account's response before
  ever relying on this for a real deployment.
- No bracket/OCO/OSO order support (`supports_native_bracket` stays the
  base class's honest default, False) -- Tradovate's own `/order/placeoco`
  and `/order/placeoso` exist and are documented (see TradovatePy's own
  `place_OCO`/`place_OSO`), but this slice only wires the plain
  `/order/placeOrder` path; a stop_loss/take_profit-carrying signal falls
  back to the managed-lifecycle path like any other undeclared-bracket
  broker.
- No `place_protective_stop`/`cancel_order`/`get_broker_position`/
  `get_last_price` -- undeclared here (the base class's own honest
  "not implemented" defaults), not a broken/guessed implementation.
"""
from __future__ import annotations

import os
import time

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal

_DEMO_URL = "https://demo.tradovateapi.com/v1"
_LIVE_URL = "https://live.tradovateapi.com/v1"

#: See this module's own docstring on why these aren't independently
#: re-verified against a live response in this session.
_FILLED_STATUSES = {"filled", "completed"}
_REJECTED_STATUSES = {"rejected", "cancelled", "canceled"}


class TradovateBroker(BrokerAdapter):
    name = "tradovate"
    supported_asset_classes = frozenset({AssetClass.FUTURE})

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        #: account_id -> (access_token, expires_at_epoch_seconds)
        self._tokens: dict[str, tuple[str, float]] = {}
        #: account_id -> Tradovate's own numeric accountId, resolved once
        #: from TRADOVATE_{ACCOUNT_ID}_ACCOUNT_SPEC via /account/list.
        self._tradovate_account_ids: dict[str, int] = {}

    def _credentials_for(self, account: DestinationAccount) -> dict[str, str]:
        prefix = f"TRADOVATE_{account.account_id.upper()}"
        required = ["USERNAME", "PASSWORD", "APP_ID", "CID", "SECRET", "DEVICE_ID", "ACCOUNT_SPEC"]
        values = {key: os.getenv(f"{prefix}_{key}") for key in required}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                f"missing {', '.join(f'{prefix}_{key}' for key in missing)} "
                f"environment variable(s) for account '{account.account_id}'"
            )
        values["APP_VERSION"] = os.getenv(f"{prefix}_APP_VERSION", "1.0")
        values["ENV"] = os.getenv(f"{prefix}_ENV", "demo")
        return values  # type: ignore[return-value]

    def _base_url_for(self, env: str) -> str:
        if env == "live":
            return _LIVE_URL
        return _DEMO_URL

    async def _access_token_for(self, account: DestinationAccount, creds: dict[str, str]) -> str:
        cached = self._tokens.get(account.account_id)
        if cached is not None and cached[1] > time.time():
            return cached[0]

        base_url = self._base_url_for(creds["ENV"])
        response = await self._client.post(
            f"{base_url}/auth/accesstokenrequest",
            json={
                "name": creds["USERNAME"],
                "password": creds["PASSWORD"],
                "appId": creds["APP_ID"],
                "appVersion": creds["APP_VERSION"],
                "cid": creds["CID"],
                "sec": creds["SECRET"],
                "deviceId": creds["DEVICE_ID"],
            },
        )
        response.raise_for_status()
        body = response.json()
        if body.get("errorText"):
            raise RuntimeError(f"Tradovate auth failed: {body['errorText']}")
        token = body["accessToken"]
        # expirationTime is an ISO8601 string; a fixed 15-minute local TTL
        # is used instead of parsing it -- Tradovate's own tokens are
        # short-lived (minutes, not hours), and re-requesting one early is
        # harmless, unlike using one past its real expiry.
        self._tokens[account.account_id] = (token, time.time() + 15 * 60)
        return token

    async def _tradovate_account_id_for(self, account: DestinationAccount, creds: dict[str, str], token: str) -> int:
        cached = self._tradovate_account_ids.get(account.account_id)
        if cached is not None:
            return cached

        base_url = self._base_url_for(creds["ENV"])
        response = await self._client.get(
            f"{base_url}/account/list", headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        for item in response.json():
            if item.get("name") == creds["ACCOUNT_SPEC"]:
                self._tradovate_account_ids[account.account_id] = item["id"]
                return item["id"]
        raise RuntimeError(
            f"no Tradovate account named '{creds['ACCOUNT_SPEC']}' found in /account/list "
            f"for signal-copier account '{account.account_id}'"
        )

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
            if not float(quantity).is_integer():
                raise RuntimeError(
                    f"Tradovate requires a whole-number contract quantity; refusing to silently "
                    f"truncate {quantity!r}"
                )
        except RuntimeError as exc:
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id, message=str(exc))

        try:
            token = await self._access_token_for(account, creds)
            tradovate_account_id = await self._tradovate_account_id_for(account, creds, token)
        except (httpx.HTTPError, RuntimeError, KeyError) as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Tradovate auth/account lookup failed: {exc}",
            )

        base_url = self._base_url_for(creds["ENV"])
        try:
            response = await self._client.post(
                f"{base_url}/order/placeOrder",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "accountSpec": creds["ACCOUNT_SPEC"],
                    "accountId": tradovate_account_id,
                    "action": "Buy" if signal.side == Side.BUY else "Sell",
                    "symbol": symbol,
                    "orderQty": int(quantity),
                    "orderType": "Market",
                    # See this module's own docstring's "isAutomated" section --
                    # a real exchange-policy compliance requirement, not optional.
                    "isAutomated": True,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Definite 4xx validation rejections -> REJECTED (can't fix by retrying).
            # Ambiguous 4xx (408, 429) and 5xx -> ERROR (might succeed on retry).
            status_code = exc.response.status_code
            if 400 <= status_code < 500 and status_code not in (408, 429):
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                    message=f"Tradovate order rejected (HTTP {status_code}): {exc.response.text}",
                )
            else:
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                    message=f"Tradovate order request failed (HTTP {status_code}): {exc}",
                )
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Tradovate order request failed: {exc}",
            )

        body = response.json()
        if body.get("failureText"):
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                message=f"Tradovate order rejected: {body['failureText']}",
            )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=str(body.get("orderId")),
            message="submitted to Tradovate",
        )

    async def get_order_status(self, account: DestinationAccount, broker_order_id: str) -> OrderResult | None:
        try:
            creds = self._credentials_for(account)
            token = await self._access_token_for(account, creds)
        except (RuntimeError, httpx.HTTPError):
            return None

        base_url = self._base_url_for(creds["ENV"])
        try:
            response = await self._client.get(
                f"{base_url}/order/item",
                params={"id": broker_order_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        order = response.json()
        ord_status = str(order.get("ordStatus", "")).lower()

        if ord_status in _FILLED_STATUSES:
            new_status = OrderStatus.FILLED
        elif ord_status in _REJECTED_STATUSES:
            new_status = OrderStatus.REJECTED
        else:
            return None  # still working, or a status token this slice doesn't recognize -- see module docstring

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            message=f"Tradovate order status: {ord_status}",
        )

    async def close(self) -> None:
        await self._client.aclose()
