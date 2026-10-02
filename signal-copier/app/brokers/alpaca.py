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
- `get_account_balance` — `GET /v2/account`; reports cash, equity,
  buying_power and maintenance_margin exactly as Alpaca's own account
  object reports them (never derived from this service's own tracked
  positions). Deliberately not read by app/capital_allocator.py's
  notional ceiling -- see that module's and `get_account_balance`'s own
  docstrings for why those are two intentionally separate checks.
"""
from __future__ import annotations

import os

import httpx

from app.brokers.base import BrokerAdapter
from app.models import AccountBalance, AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal


def _coerce_broker_order_id(raw_id: object) -> str | None:
    """Track 40 (fault-injection fuzzing): every `OrderResult.broker_
    order_id=order.get("id")` call site in this file used to pass
    Alpaca's own JSON `"id"` field through completely unvalidated --
    `OrderResult.broker_order_id` is declared `Optional[str]`
    (app/models.py), but a plain `@dataclass` never enforces that at
    construction. A genuinely malformed response body (this adapter's
    own API contract drifting out from under it, or a corrupted
    response surviving `response.raise_for_status()`/`response.json()`
    without raising) whose `"id"` is some other JSON type (a nested
    object was reproduced by this track's own fault-injection test,
    tests/test_c36_broker_submission_fault_injection.py) used to reach
    `app/db.py`'s `save_order_result` completely unchanged, far past
    this adapter's own boundary, and crash there instead with an opaque
    `sqlite3.ProgrammingError: type 'dict' is not supported` -- a real,
    reproduced unhandled exception, NOT wrapped by
    `app/engine.py`'s own per-account `except Exception` (that one only
    wraps the `broker.place_order` call itself, not everything the
    engine does afterward with its result), so it could crash the
    entire `handle_signal` call, not just this one account's own entry.

    Same `str(x) if x is not None else None` discipline this codebase
    already uses for exactly this kind of externally-sourced optional
    identity field (see `app/sources/webhook.py`'s own `message_id`
    handling) -- never silently drops or guesses a real id, only
    normalizes whatever JSON type it happened to arrive as into the
    honest string this field's own type always claimed it would be."""
    return str(raw_id) if raw_id is not None else None


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

        order_payload: dict[str, object] = {
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
        except httpx.HTTPStatusError as exc:
            # Definite 4xx validation rejections (400, 403, 422, etc.) -> REJECTED (can't fix by retrying).
            # Ambiguous 4xx (408, 429) and 5xx -> ERROR (might succeed on retry).
            status_code = exc.response.status_code
            if 400 <= status_code < 500 and status_code not in (408, 429):
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message=f"Alpaca order rejected (HTTP {status_code}): {exc.response.text}",
                )
            else:
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"Alpaca order request failed (HTTP {status_code}): {exc}",
                )
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
            broker_order_id=_coerce_broker_order_id(order.get("id")),
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
            broker_order_id=_coerce_broker_order_id(order.get("id")),
            message=f"Alpaca stop resting (status: {order.get('status')})",
        )

    #: Alpaca order states that actually mean nothing more of this order can
    #: ever execute. `pending_cancel` (returned by DELETE's 204 acceptance
    #: itself being merely a request, not a confirmed outcome) is
    #: deliberately NOT here -- the order can still fill before the venue
    #: finishes cancelling it.
    _TERMINAL_CANCELLED_STATUSES = frozenset({"canceled", "expired"})

    async def cancel_order(self, account: DestinationAccount, broker_order_id: str) -> bool:
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError:
            return False
        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}

        try:
            response = await self._client.delete(f"{base_url}/v2/orders/{broker_order_id}", headers=headers)
        except httpx.HTTPError:
            return False

        # ADP-04: Alpaca's 204 from DELETE means "the cancel REQUEST was
        # accepted," not "the order is now actually cancelled" -- the order
        # can sit in `pending_cancel` and still fill before the venue
        # finishes tearing it down. Anything other than 204 here (404 =
        # already filled/gone, 422 = can't be cancelled in its current
        # state, ...) was never even accepted, so that's still an
        # unconfirmed cancellation. A confirmed one additionally requires a
        # follow-up read showing the order actually reached a terminal
        # cancelled state.
        if response.status_code != 204:
            return False

        try:
            status_response = await self._client.get(f"{base_url}/v2/orders/{broker_order_id}", headers=headers)
            status_response.raise_for_status()
        except httpx.HTTPError:
            return False

        return status_response.json().get("status") in self._TERMINAL_CANCELLED_STATUSES

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
        order_status = order.get("status")
        if order_status in ("rejected", "canceled", "expired"):
            # ADP-04: an HTTP 200 here only means Alpaca's API layer accepted
            # the PATCH request -- the order-management system can still
            # reject the replace itself, reported in the response BODY's own
            # `status`, not the HTTP status code. Reporting this as PENDING
            # would make the caller believe a replacement is resting when
            # nothing actually is (see docs.alpaca.markets/reference/
            # patchorderbyorderid-1's own note that a 200 does not guarantee
            # the replace happened).
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id="",
                broker_order_id=_coerce_broker_order_id(order.get("id")),
                message=f"Alpaca stop replace was not accepted (status: {order_status})",
            )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            # Alpaca's replace creates a new order id — see module docstring.
            broker_order_id=_coerce_broker_order_id(order.get("id")),
            message=f"Alpaca stop replaced (status: {order_status})",
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

    async def get_last_price(self, account: DestinationAccount, symbol: str) -> float | None:
        # ADP-07: this is what actually drives on_price_update() for a
        # managed-lifecycle position's targets/trailing/stop resizing (see
        # app/pricing.py) — without it, Alpaca positions were never polled
        # at all despite the lifecycle logic supporting them. Alpaca's
        # market data lives on a separate host from trading/account data
        # (data.alpaca.markets vs. the paper/live api host), so this can't
        # reuse `_credentials_for`'s base_url.
        try:
            api_key, api_secret, _ = self._credentials_for(account)
        except RuntimeError:
            return None

        try:
            response = await self._client.get(
                f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        price = response.json().get("trade", {}).get("p")
        return float(price) if price is not None else None

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        # ADP-08: `GET /v2/account` -- cash/equity/buying_power/
        # maintenance_margin are all real fields on Alpaca's account
        # object (docs.alpaca.markets/reference/getaccount), not guessed
        # or derived from anything else this adapter tracks.
        try:
            api_key, api_secret, base_url = self._credentials_for(account)
        except RuntimeError:
            return None

        try:
            response = await self._client.get(
                f"{base_url}/v2/account",
                headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        data = response.json()

        def _float_or_none(key: str) -> float | None:
            value = data.get(key)
            return float(value) if value is not None else None

        return AccountBalance(
            account_id=account.account_id,
            cash=_float_or_none("cash"),
            equity=_float_or_none("equity"),
            buying_power=_float_or_none("buying_power"),
            maintenance_margin=_float_or_none("maintenance_margin"),
        )

    async def close(self) -> None:
        await self._client.aclose()
