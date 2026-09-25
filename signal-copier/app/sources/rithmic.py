"""Rithmic signal source, via the `async_rithmic` library
(https://github.com/rundef/async_rithmic — MIT licensed, actively
maintained; verified against its source/docs rather than written blind).

Setup:
    pip install async_rithmic
    1. Get Rithmic API credentials from your broker (this is a licensed
       service — there is no free/self-serve signup). You'll receive a
       user, password, system_name, and a gateway url.
    2. Set RITHMIC_USER / RITHMIC_PASSWORD / RITHMIC_SYSTEM_NAME /
       RITHMIC_GATEWAY_URL, and construct RithmicSource with them.
    3. Optionally restrict to one account with `account_id` (Rithmic account
       id, e.g. from `client.list_accounts()`); otherwise every fill on the
       login is copied.

This subscribes to `client.on_exchange_order_notification` and turns each
FILL notification into a Signal, using the exact field names from
`exchange_order_notification.proto` (symbol, transaction_type, fill_size,
fill_price).
"""
from __future__ import annotations

from app.models import AssetClass, Signal, Side
from app.sources.base import SourceAdapter


class RithmicSource(SourceAdapter):
    name = "rithmic"

    def __init__(
        self,
        on_signal,
        user: str,
        password: str,
        system_name: str,
        gateway_url: str,
        app_name: str = "signal-copier",
        app_version: str = "1.0",
        account_id: str | None = None,
    ):
        super().__init__(on_signal)
        self.user = user
        self.password = password
        self.system_name = system_name
        self.gateway_url = gateway_url
        self.app_name = app_name
        self.app_version = app_version
        self.account_id = account_id
        self._client = None

    async def start(self) -> None:
        try:
            from async_rithmic import ExchangeOrderNotificationType, RithmicClient, TransactionType
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("async_rithmic is not installed; run `pip install async_rithmic`") from exc

        client = RithmicClient(
            user=self.user,
            password=self.password,
            system_name=self.system_name,
            app_name=self.app_name,
            app_version=self.app_version,
            url=self.gateway_url,
        )

        async def on_notification(notification) -> None:
            if notification.notify_type != ExchangeOrderNotificationType.FILL:
                return
            if self.account_id and notification.account_id != self.account_id:
                return

            side = Side.BUY if notification.transaction_type == TransactionType.BUY else Side.SELL
            price = notification.fill_price or notification.avg_fill_price or None

            signal = Signal(
                source=self.name,
                symbol=notification.symbol,
                side=side,
                asset_class=AssetClass.FUTURE,
                quantity=float(notification.fill_size) if notification.fill_size else None,
                price=float(price) if price else None,
                raw={"account_id": notification.account_id, "exchange": notification.exchange},
            )
            await self.on_signal(signal)

        client.on_exchange_order_notification += on_notification
        await client.connect()
        self._client = client

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
