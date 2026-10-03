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

from app.models import AssetClass, Intent, Signal, Side
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
            # A-07: Set intent to EXIT for SELL fills (exits closing a long position).
            # Since Rithmic doesn't provide explicit position tracking, SELL fills are
            # exits (the opposite of the usual Side.SELL entry interpretation).
            intent = Intent.EXIT if notification.transaction_type == TransactionType.SELL else None

            # RISK-FC-02: `x or y or None` treats a genuinely-reported 0.0
            # the same as "not reported at all" and silently substitutes
            # something else (or None) for it. `signal.price` feeds
            # app/engine.py's `_try_reserve_capital`, which SKIPS the
            # account's notional-exposure ceiling entirely whenever
            # `order_signal.price is None` -- so a fill price that
            # collapses to None here isn't just a display gap, it's a
            # silent admission-check bypass. Use `fill_price` only when
            # Rithmic actually reported it (not None), falling back to
            # avg_fill_price only when it didn't, and never coercing a
            # real reported value via truthiness.
            price = notification.fill_price if notification.fill_price is not None else notification.avg_fill_price

            signal = Signal(
                source=self.name,
                symbol=notification.symbol,
                side=side,
                asset_class=AssetClass.FUTURE,
                quantity=float(notification.fill_size) if notification.fill_size is not None else None,
                price=float(price) if price is not None else None,
                intent=intent,
                raw={"account_id": notification.account_id, "exchange": notification.exchange},
            )
            await self.on_signal(signal)

        client.on_exchange_order_notification += on_notification
        await client.connect()
        self._client = client

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
