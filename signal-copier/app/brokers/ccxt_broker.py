"""Crypto exchange execution via ccxt (supports Binance, Bybit, Kraken, OKX, ...).

One CCXTBroker instance talks to exactly one exchange/account, so a user
with sub-accounts on multiple exchanges configures one entry per exchange
account in accounts.yaml, each pointing at its own API key pair.

Credentials are read from environment variables named
`CCXT_{ACCOUNT_ID}_API_KEY` / `CCXT_{ACCOUNT_ID}_API_SECRET` — never from
the YAML config — so accounts.yaml stays safe to commit.
"""
from __future__ import annotations

import os

from app.models import DestinationAccount, OrderResult, OrderStatus, Signal
from app.brokers.base import BrokerAdapter


class CCXTBroker(BrokerAdapter):
    name = "ccxt"

    def __init__(self, exchange_id: str = "binance"):
        try:
            import ccxt.async_support as ccxt  # imported lazily: optional dependency
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "ccxt is not installed; run `pip install ccxt` to use CCXTBroker"
            ) from exc

        self._ccxt = ccxt
        self.exchange_id = exchange_id
        self._exchanges: dict[str, "ccxt.Exchange"] = {}

    def _exchange_for(self, account: DestinationAccount):
        if account.account_id in self._exchanges:
            return self._exchanges[account.account_id]

        prefix = f"CCXT_{account.account_id.upper()}"
        api_key = os.getenv(f"{prefix}_API_KEY")
        api_secret = os.getenv(f"{prefix}_API_SECRET")
        if not api_key or not api_secret:
            raise RuntimeError(
                f"missing {prefix}_API_KEY / {prefix}_API_SECRET environment variables "
                f"for account '{account.account_id}'"
            )

        exchange_class = getattr(self._ccxt, self.exchange_id)
        exchange = exchange_class({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True})
        self._exchanges[account.account_id] = exchange
        return exchange

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        exchange = self._exchange_for(account)

        if signal.side.value == "close":
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message="'close' side reached the broker directly without engine-level resolution (see SignalCopierEngine._resolve_close); this broker only accepts buy/sell",
            )

        try:
            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=signal.side.value,
                amount=quantity,
            )
        except Exception as exc:  # noqa: BLE001 - surface any ccxt/network error as a failed order
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=str(order.get("id")),
            filled_quantity=order.get("filled") or quantity,
            filled_price=order.get("average") or order.get("price"),
            message="filled by ccxt",
        )

    async def close(self) -> None:
        for exchange in self._exchanges.values():
            await exchange.close()
