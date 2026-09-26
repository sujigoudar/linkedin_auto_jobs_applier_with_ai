"""Rithmic execution destination, via the `async_rithmic` library
(https://github.com/rundef/async_rithmic — see app/sources/rithmic.py's
docstring for the same setup requirements; this is the trading-side
counterpart).

One RithmicBroker instance holds one Rithmic login (one connection, per
`async_rithmic`'s design) and can route to multiple Rithmic accounts under
that login by account_id. Per-account config, read from env vars:
    RITHMIC_{ACCOUNT_ID}_RITHMIC_ACCOUNT_ID   # Rithmic's own account id
    RITHMIC_{ACCOUNT_ID}_EXCHANGE             # e.g. "CME"
The connection credentials themselves (user/password/system_name/gateway
url) are shared across accounts under the same login and passed to the
constructor, same as app/sources/rithmic.py.

`symbol` here is expected to already be in Rithmic's own contract format
(e.g. "ESZ5") — set that via each account's `symbol_map` in accounts.yaml,
or use `client.get_front_month_contract(root_symbol, exchange)` yourself
if you want automatic front-month rollover (not done here to keep this
adapter's behavior predictable/explicit).
"""
from __future__ import annotations

import asyncio
import os
import uuid

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class RithmicBroker(BrokerAdapter):
    name = "rithmic"

    def __init__(
        self,
        user: str,
        password: str,
        system_name: str,
        gateway_url: str,
        app_name: str = "signal-copier",
        app_version: str = "1.0",
    ):
        try:
            from async_rithmic import OrderType, RithmicClient, TransactionType
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("async_rithmic is not installed; run `pip install async_rithmic`") from exc

        self._OrderType = OrderType
        self._TransactionType = TransactionType
        self._client = RithmicClient(
            user=user,
            password=password,
            system_name=system_name,
            app_name=app_name,
            app_version=app_version,
            url=gateway_url,
        )
        self._connect_lock = asyncio.Lock()
        self._connected = False

    def _rithmic_account_for(self, account: DestinationAccount) -> tuple[str, str]:
        prefix = f"RITHMIC_{account.account_id.upper()}"
        rithmic_account_id = os.getenv(f"{prefix}_RITHMIC_ACCOUNT_ID")
        exchange = os.getenv(f"{prefix}_EXCHANGE")
        if not rithmic_account_id or not exchange:
            raise RuntimeError(
                f"missing {prefix}_RITHMIC_ACCOUNT_ID / {prefix}_EXCHANGE "
                f"environment variables for account '{account.account_id}'"
            )
        return rithmic_account_id, exchange

    async def _ensure_connected(self) -> None:
        if self._connected:
            return
        async with self._connect_lock:
            if not self._connected:
                await self._client.connect()
                self._connected = True

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        try:
            rithmic_account_id, exchange = self._rithmic_account_for(account)
        except RuntimeError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        try:
            await self._ensure_connected()

            if signal.side.value == "close":
                await self._client.exit_position(symbol=symbol, exchange=exchange)
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.PENDING,
                    signal_id=signal.id,
                    message="exit_position submitted to Rithmic",
                )

            order_id = f"sigcopier_{uuid.uuid4().hex[:12]}"
            transaction_type = (
                self._TransactionType.BUY if signal.side.value == "buy" else self._TransactionType.SELL
            )
            await self._client.submit_order(
                order_id,
                symbol,
                exchange,
                qty=int(quantity),
                order_type=self._OrderType.MARKET,
                transaction_type=transaction_type,
                account_id=rithmic_account_id,
            )
        except Exception as exc:  # noqa: BLE001 - surface any connection/protocol error as a failed order
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        # async_rithmic reports fills asynchronously via on_exchange_order_notification
        # (see app/sources/rithmic.py) rather than as a return value here.
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=order_id,
            message="submitted to Rithmic",
        )

    async def close(self) -> None:
        if self._connected:
            await self._client.disconnect()
