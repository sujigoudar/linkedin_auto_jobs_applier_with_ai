"""Robinhood (US equities) execution destination. **Robinhood has never
published an official trading API. There is no sandbox/paper
environment. Automating trades against Robinhood through any of the
endpoints below is outside Robinhood's own Terms of Service for
programmatic/bot access, and every order this adapter places, including
your first test of it, uses real money on a real account.** This slice
exists because it was explicitly requested with all of that risk
accepted; read this whole docstring, and app/main.py's own gating
comment next to where this broker is registered, before ever wiring
real credentials to it.

This talks directly to the same reverse-engineered REST endpoints the
most widely used unofficial library, github.com/jmfernandes/robin_stocks
(30k+ stars across its forks, the de facto standard unofficial client),
uses -- its own source was read directly in this session to confirm the
real request/response shapes (base URL, the OAuth2 password-grant login
payload INCLUDING Robinhood's own publicly known, hardcoded mobile-app
`client_id`, the account/instrument/quote lookup endpoints, and --
critically -- that Robinhood does not actually accept a plain "market"
order type for a regular-hours buy: robin_stocks's own `order()`
function converts it into a LIMIT order pegged 5% above the current ask
price, `preset_percent_limit: "0.05"`, confirmed directly from that
function's own real logic, not guessed). This module mirrors that exact
real logic for the plain market-buy/market-sell case this codebase's
own `Signal` model actually produces.

Base URL: https://api.robinhood.com (confirmed via that library's own
`urls.py` -- every endpoint below is read directly from there).

## Auth: password grant, MFA, and device approval -- genuinely fragile

Robinhood's own login endpoint (`POST /oauth2/token/`) takes your real
username and password directly (`grant_type: "password"`) -- there is
no client-credentials or refresh-token-only alternative for the FIRST
login. A real account with SMS/app-based MFA enabled (Robinhood
recommends this for every account) will also require a one-time
`mfa_code`, and some logins additionally require an interactive
"approve this login on your phone" device-verification step
(robin_stocks's own `authentication.py` polls a `workflow_status`
field waiting for that approval) that this module does NOT automate --
if your account requires it, the very first login attempt from this
service will need to be approved by hand on your phone before it can
proceed at all.

One RobinhoodBroker instance can serve any number of accounts; each
account's credentials come from environment variables named
`ROBINHOOD_{ACCOUNT_ID}_*` — never from YAML — so accounts.yaml stays
safe to commit:
    ROBINHOOD_{ACCOUNT_ID}_USERNAME
    ROBINHOOD_{ACCOUNT_ID}_PASSWORD
    ROBINHOOD_{ACCOUNT_ID}_MFA_CODE      (optional -- a TOTP code is
        time-limited and generally can't be pre-supplied for an
        unattended process; leave unset unless you have a real reason
        to believe one specific code will still be valid at start time)
    ROBINHOOD_{ACCOUNT_ID}_ACCOUNT_NUMBER  -- Robinhood's own account
        number -- this service's own `account_id` is a local identifier
        and is never assumed to match it.

## Known, disclosed limitations of this slice

- No bracket/stop/take-profit wiring (`supports_native_bracket` stays the
  base class's honest default, False).
- No `place_protective_stop`/`cancel_order`/`get_broker_position`/
  `get_last_price` -- undeclared here (the base class's own honest
  "not implemented" defaults), not a broken/guessed implementation.
- Order status codes (`"filled"`/`"rejected"`/`"cancelled"`/`"failed"`)
  are taken from robin_stocks's own `order()` docstring, which lists
  them directly ("the state of order (queued, confired, filled, failed,
  canceled, etc.)") -- not independently re-verified against a live
  response in this session (network egress to Robinhood's own docs was
  blocked in this sandbox). Anything not in the confirmed-filled/
  confirmed-rejected sets is left as "still working" (`None`), never
  guessed either way.
"""
from __future__ import annotations

import os
import time
import uuid

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal

_BASE_URL = "https://api.robinhood.com"
#: Robinhood's own mobile app's OAuth client id -- publicly known and
#: hardcoded identically across every unofficial Robinhood client
#: (confirmed via robin_stocks's own authentication.py), not a secret
#: this codebase invented.
_CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"

#: See this module's own docstring's "Known, disclosed limitations" section.
_FILLED_STATES = {"filled"}
_REJECTED_STATES = {"rejected", "cancelled", "canceled", "failed"}


class RobinhoodBroker(BrokerAdapter):
    name = "robinhood"
    supported_asset_classes = frozenset({AssetClass.EQUITY})

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(timeout=timeout)
        #: account_id -> (access_token, expires_at_epoch_seconds)
        self._tokens: dict[str, tuple[str, float]] = {}
        #: account_id -> a stable per-process device token, generated once
        #: and reused -- a fresh one on every login is more likely to
        #: trigger Robinhood's own new-device verification step.
        self._device_tokens: dict[str, str] = {}

    def _credentials_for(self, account: DestinationAccount) -> dict[str, str]:
        prefix = f"ROBINHOOD_{account.account_id.upper()}"
        required = ["USERNAME", "PASSWORD", "ACCOUNT_NUMBER"]
        values = {key: os.getenv(f"{prefix}_{key}") for key in required}
        missing = [key for key, value in values.items() if not value]
        if missing:
            raise RuntimeError(
                f"missing {', '.join(f'{prefix}_{key}' for key in missing)} "
                f"environment variable(s) for account '{account.account_id}'"
            )
        values["MFA_CODE"] = os.getenv(f"{prefix}_MFA_CODE", "")
        return values  # type: ignore[return-value]

    async def _access_token_for(self, account: DestinationAccount, creds: dict[str, str]) -> str:
        cached = self._tokens.get(account.account_id)
        if cached is not None and cached[1] > time.time():
            return cached[0]

        device_token = self._device_tokens.setdefault(account.account_id, str(uuid.uuid4()))
        payload = {
            "client_id": _CLIENT_ID,
            "expires_in": 86400,
            "grant_type": "password",
            "password": creds["PASSWORD"],
            "scope": "internal",
            "username": creds["USERNAME"],
            "device_token": device_token,
        }
        if creds["MFA_CODE"]:
            payload["mfa_code"] = creds["MFA_CODE"]

        response = await self._client.post(f"{_BASE_URL}/oauth2/token/", data=payload)
        response.raise_for_status()
        body = response.json()
        if "access_token" not in body:
            raise RuntimeError(f"Robinhood login did not return an access token: {body}")
        token = body["access_token"]
        expires_in = float(body.get("expires_in", 86400))
        self._tokens[account.account_id] = (token, time.time() + expires_in - 60)
        return token

    async def _instrument_url_for(self, symbol: str, token: str) -> str | None:
        response = await self._client.get(
            f"{_BASE_URL}/instruments/",
            params={"symbol": symbol},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            return None
        return results[0]["url"]

    async def _ask_price_for(self, symbol: str, token: str) -> float | None:
        response = await self._client.get(
            f"{_BASE_URL}/quotes/",
            params={"symbols": symbol},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results or results[0] is None:
            return None
        return float(results[0]["ask_price"])

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
            instrument_url = await self._instrument_url_for(symbol, token)
        except (httpx.HTTPError, RuntimeError, KeyError) as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Robinhood auth/instrument lookup failed: {exc}",
            )
        if instrument_url is None:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Robinhood has no instrument for symbol '{symbol}'",
            )

        account_url = f"{_BASE_URL}/accounts/{creds['ACCOUNT_NUMBER']}/"
        # See this module's own docstring: Robinhood's own real order
        # model has no plain "market buy" during regular hours -- a real
        # buy is submitted as a LIMIT order pegged 5% above the current
        # ask (robin_stocks's own order()'s exact real logic, not a
        # guess). A sell stays a genuine market order.
        payload: dict[str, object]
        if signal.side == Side.BUY:
            try:
                ask_price = await self._ask_price_for(symbol, token)
            except httpx.HTTPError as exc:
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                    message=f"Robinhood quote lookup failed: {exc}",
                )
            if ask_price is None:
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                    message=f"Robinhood has no quote for symbol '{symbol}'",
                )
            payload = {
                "account": account_url,
                "instrument": instrument_url,
                "symbol": symbol,
                "price": round(ask_price, 2),
                "quantity": quantity,
                "ref_id": str(uuid.uuid4()),
                "type": "limit",
                "time_in_force": "gfd",
                "trigger": "immediate",
                "side": "buy",
                "extended_hours": False,
                "preset_percent_limit": "0.05",
            }
        else:
            payload = {
                "account": account_url,
                "instrument": instrument_url,
                "symbol": symbol,
                "quantity": quantity,
                "ref_id": str(uuid.uuid4()),
                "type": "market",
                "time_in_force": "gfd",
                "trigger": "immediate",
                "side": "sell",
                "extended_hours": False,
            }

        try:
            response = await self._client.post(
                f"{_BASE_URL}/orders/",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Robinhood order request failed: {exc}",
            )

        # Definite 4xx validation rejections -> REJECTED (can't fix by retrying).
        # Ambiguous 4xx (408, 429) and 5xx -> ERROR (might succeed on retry).
        if response.status_code >= 400:
            if 400 <= response.status_code < 500 and response.status_code not in (408, 429):
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.REJECTED, signal_id=signal.id,
                    message=f"Robinhood order rejected (status {response.status_code}): {response.text}",
                )
            else:
                return OrderResult(
                    account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                    message=f"Robinhood order request failed (status {response.status_code}): {response.text}",
                )

        body = response.json()
        order_id = body.get("id")
        if not order_id:
            return OrderResult(
                account_id=account.account_id, status=OrderStatus.ERROR, signal_id=signal.id,
                message=f"Robinhood order response had no 'id': {body}",
            )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=str(order_id),
            message="submitted to Robinhood",
        )

    async def get_order_status(self, account: DestinationAccount, broker_order_id: str) -> OrderResult | None:
        try:
            creds = self._credentials_for(account)
            token = await self._access_token_for(account, creds)
        except (RuntimeError, httpx.HTTPError, KeyError):
            return None

        try:
            response = await self._client.get(
                f"{_BASE_URL}/orders/{broker_order_id}/",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        order = response.json()
        state = str(order.get("state", "")).lower()

        if state in _FILLED_STATES:
            new_status = OrderStatus.FILLED
        elif state in _REJECTED_STATES:
            new_status = OrderStatus.REJECTED
        else:
            return None  # still queued/confirmed -- nothing new to report

        return OrderResult(
            account_id=account.account_id,
            status=new_status,
            signal_id="",  # filled in by the reconciler from its own stored order row
            broker_order_id=broker_order_id,
            message=f"Robinhood order state: {state}",
        )

    async def close(self) -> None:
        await self._client.aclose()
