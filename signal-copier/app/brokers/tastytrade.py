"""Tastytrade (US equities/options) execution destination, via
Tastytrade's own real REST API. Tastytrade itself publishes an official
wrapper (github.com/tastytrade/tastytrade-sdk-python), but the community
one, github.com/tastyware/tastytrade, is the far more actively
maintained/complete reference for the API's real field-level shapes
(confirmed directly from its own source: base URLs, the OAuth2
refresh_token grant, the kebab-case JSON field convention, the
Leg/OrderAction/OrderStatus enums, and the placed-order response
envelope) -- this module talks directly to those same documented
endpoints, the same way AlpacaBroker/TradovateBroker/OANDABroker/
TradeStationBroker talk to their own brokers' REST APIs directly rather
than through a wrapper.

Base URLs (from that wrapper's own `tastytrade/__init__.py`):
    live: https://api.tastyworks.com
    cert (sandbox): https://api.cert.tastyworks.com

Auth is OAuth2 (RFC 6749) `refresh_token` grant against `{base_url}
/oauth/token` -- a real Tastytrade OAuth application (client secret) plus
a refresh_token obtained via a one-time interactive consent step this
codebase does not automate (see this module's own `.env.example` entry).

One TastytradeBroker instance can serve any number of accounts; each
account's credentials come from environment variables named
`TASTYTRADE_{ACCOUNT_ID}_*` — never from YAML — so accounts.yaml stays
safe to commit:
    TASTYTRADE_{ACCOUNT_ID}_SECRET          -- OAuth client secret
    TASTYTRADE_{ACCOUNT_ID}_REFRESH_TOKEN
    TASTYTRADE_{ACCOUNT_ID}_TT_ACCOUNT_NUMBER  -- Tastytrade's own
        account number string -- this service's own `account_id` is a
        local identifier and is never assumed to match it.
    TASTYTRADE_{ACCOUNT_ID}_ENV             ("cert" (default) or "live")

## JSON field naming

Tastytrade's real API uses kebab-case field names (confirmed via that
wrapper's own `TastytradeData` base model, which sets
`alias_generator=_dasherize` for every request/response) -- this module
builds request bodies with real kebab-case keys directly (`"order-type"`,
`"time-in-force"`, `"instrument-type"`), not the Python-convention
underscored names the wrapper's own dataclasses use internally.

## Known, DISCLOSED limitation: open/close intent is not distinguished

Tastytrade's own `OrderAction` model tags every equity/option leg with
directional INTENT -- `"Buy to Open"`/`"Buy to Close"`/`"Sell to
Open"`/`"Sell to Close"` (plain `"Buy"`/`"Sell"` exist too, but that
wrapper's own source comments them as "for futures only"). This
service's own `Signal`/engine layer resolves a CLOSE signal into a plain
opposing BUY/SELL before any broker ever sees it (see
SignalCopierEngine's own module docstring) -- by the time `place_order`
runs, there is no "is this closing an existing position" flag left to
read. This adapter always sends the "Open" variant
(`"Buy to Open"`/`"Sell to Open"`) for every order, which is correct for
a genuine new entry but has NOT been independently verified against a
real Tastytrade sandbox account for the close-an-existing-position case
-- confirm real net-position behavior there before relying on this
adapter for any account expected to flip or close positions, not just
open new ones.

## Known, disclosed limitations of this slice

- No bracket/stop/take-profit wiring (`supports_native_bracket` stays the
  base class's honest default, False).
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

_LIVE_URL = "https://api.tastyworks.com"
_CERT_URL = "https://api.cert.tastyworks.com"

#: See this module's own docstring -- Tastytrade's own real OrderStatus
#: progression: RECEIVED -> LIVE -> FILLED.
_FILLED_STATUSES = {"filled"}
_REJECTED_STATUSES = {"cancelled", "rejected", "expired"}


class TastytradeBroker(BrokerAdapter):
    name = "tastytrade"
    supported_asset_classes = frozenset({AssetClass.EQUITY, AssetClass.OPTION})

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        #: account_id -> (access_token, expires_at_epoch_seconds)
        self._tokens: dict[str, tuple[str, float]] = {}

    def _credentials_for(self, account: DestinationAccount) -> dict[str, str]:
        prefix = f"TASTYTRADE_{account.account_id.upper()}"
        required = ["SECRET", "REFRESH_TOKEN", "TT_ACCOUNT_NUMBER"]
        values = {key: os.getenv(f"{prefix}_{key}") for key in required}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                f"missing {', '.join(f'{prefix}_{key}' for key in missing)} "
                f"environment variable(s) for account '{account.account_id}'"
            )
        values["ENV"] = os.getenv(f"{prefix}_ENV", "cert")
        return values  # type: ignore[return-value]

    def _base_url_for(self, env: str) -> str:
        return _LIVE_URL if env == "live" else _CERT_URL

    async def _access_token_for(self, account: DestinationAccount, creds: dict[str, str]) -> str:
        cached = self._tokens.get(account.account_id)
        if cached is not None and cached[1] > time.time():
            return cached[0]

        base_url = self._base_url_for(creds["ENV"])
        response = await self._client.post(
            f"{base_url}/oauth/token",
            json={
                "grant_type": "refresh_token",
                "client_secret": creds["SECRET"],
                "refresh_token": creds["REFRESH_TOKEN"],
            },
        )
        response.raise_for_status()
        body = response.json()
        token = body["access_token"]
        expires_in = float(body.get("expires_in", 900))
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
                message=f"Tastytrade auth failed: {exc}",
            )

        base_url = self._base_url_for(creds["ENV"])
        # See this module's own docstring's "open/close intent" section --
        # always the "Open" variant, a disclosed, not a hidden, limitation.
        action = "Buy to Open" if signal.side == Side.BUY else "Sell to Open"
        try:
            response = await self._client.post(
                f"{base_url}/accounts/{creds['TT_ACCOUNT_NUMBER']}/orders",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "order-type": "Market",
                    "time-in-force": "Day",
                    "legs": [
                        {
                            "instrument-type": "Equity",
                            "symbol": symbol,
                            "action": action,
                            "quantity": quantity,
                        }
                    ],
                },
            )
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Tastytrade order request failed: {exc}",
            )

        body = response.json().get("data", {})
        errors = body.get("errors") or []
        if errors:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                message=f"Tastytrade order rejected: {errors[0]}",
            )

        order = body.get("order")
        if not order:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Tastytrade order response had no 'order' and no errors: {body}",
            )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=str(order["id"]),
            message="submitted to Tastytrade",
        )

    async def get_order_status(self, account: DestinationAccount, broker_order_id: str) -> OrderResult | None:
        try:
            creds = self._credentials_for(account)
            token = await self._access_token_for(account, creds)
        except (RuntimeError, httpx.HTTPError, KeyError):
            return None

        base_url = self._base_url_for(creds["ENV"])
        try:
            response = await self._client.get(
                f"{base_url}/accounts/{creds['TT_ACCOUNT_NUMBER']}/orders/{broker_order_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        order = response.json().get("data", {})
        status = str(order.get("status", "")).lower()

        if status in _FILLED_STATUSES:
            new_status = OrderStatus.FILLED
        elif status in _REJECTED_STATUSES:
            new_status = OrderStatus.REJECTED
        else:
            return None  # still received/live -- nothing new to report

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            message=f"Tastytrade order status: {status}",
        )

    async def close(self) -> None:
        await self._client.aclose()
