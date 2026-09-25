"""Crypto exchange execution via ccxt (supports Binance, Bybit, Kraken, OKX, ...).

One CCXTBroker instance talks to exactly one exchange/account, so a user
with sub-accounts on multiple exchanges configures one entry per exchange
account in accounts.yaml, each pointing at its own API key pair.

Credentials are read from environment variables named
`CCXT_{ACCOUNT_ID}_API_KEY` / `CCXT_{ACCOUNT_ID}_API_SECRET` — never from
the YAML config — so accounts.yaml stays safe to commit.

A Signal carrying `stop_loss`/`take_profit` is sent via ccxt's unified
`stopLossPrice`/`takeProfitPrice` order params (verified against ccxt's
source — used by 90+ of its exchange implementations, including Binance
and Bybit, not exchange-specific despite the "unified" API sometimes
varying in practice). If the configured exchange doesn't support it, ccxt
raises `NotSupported`, which is reported as an ERROR result rather than
silently placing the entry without its exit.

## Managed-lifecycle capabilities (app/lifecycle/)

- `place_protective_stop` — a standalone `market` order carrying only
  `stopLossPrice` (the same unified param above, used alone rather than
  paired with `takeProfitPrice` — confirmed against ccxt's source that
  each is checked independently, not required together).
- `cancel_order` — ccxt's unified `cancel_order(id, symbol)`, one of its
  oldest and most broadly implemented methods (unlike the order-type
  params above, this one really is close to universal across exchanges).
- `replace_stop_quantity` — **not implemented.** ccxt's `edit_order` isn't
  consistently supported/verified across exchanges the way `cancel_order`
  is; returning `None` here makes the caller fall back to cancel +
  resubmit, which only needs the two methods above.
- `get_broker_position` — `fetch_positions([symbol])`. This is a
  derivatives/margin concept; on a spot market (this project's example
  config: Binance spot) there is no "position" to fetch, and ccxt raises
  `NotSupported` — caught and reported as `None` (unknown), never `0.0`,
  since spot holdings clearly aren't zero just because "position" doesn't
  apply.
- `get_last_price` — ccxt's unified `fetch_ticker(symbol)`, reading
  `last` (falling back to `close`) — one of ccxt's oldest and most
  broadly implemented methods, same tier as `cancel_order`. This is what
  app/pricing.py's `PriceMonitor` polls to drive
  `PositionLifecycleManager.on_price_update()` continuously in
  production — REST polling on an interval, not a websocket stream
  (ccxt's websocket/"pro" support would need separate per-exchange
  verification and isn't wired up here).
"""
from __future__ import annotations

import os

from app.models import AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.brokers.base import BrokerAdapter


class CCXTBroker(BrokerAdapter):
    name = "ccxt"
    # stopLossPrice/takeProfitPrice ride on the same create_order call as the
    # entry — genuinely atomic where the exchange supports it (see module
    # docstring: ~90 of ccxt's implementations do; unverified elsewhere).
    supports_native_bracket = True
    # ccxt talks to crypto exchanges — there is no equity/option/forex/future
    # (in this project's traditional-futures sense) route through it.
    # See BrokerAdapter.supported_asset_classes.
    supported_asset_classes = frozenset({AssetClass.CRYPTO})

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

        params = {}
        if signal.stop_loss:
            params["stopLossPrice"] = signal.stop_loss
        if signal.take_profit:
            params["takeProfitPrice"] = signal.take_profit

        try:
            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=signal.side.value,
                amount=quantity,
                params=params,
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

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side: Side
    ) -> OrderResult | None:
        exchange = self._exchange_for(account)
        try:
            order = await exchange.create_order(
                symbol=symbol,
                type="market",
                side=exit_side.value,
                amount=quantity,
                params={"stopLossPrice": stop_price},
            )
        except Exception as exc:  # noqa: BLE001 - includes ccxt's NotSupported for this exchange
            return OrderResult(account_id=account.account_id, status=OrderStatus.ERROR, signal_id="", message=str(exc))

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=str(order.get("id")),
            message="ccxt stop resting",
        )

    async def cancel_order(self, account: DestinationAccount, broker_order_id: str) -> bool:
        exchange = self._exchange_for(account)
        try:
            await exchange.cancel_order(broker_order_id)
        except Exception:  # noqa: BLE001 - already filled/gone, or genuinely unsupported — either way, not a confirmed cancel
            return False
        return True

    async def get_broker_position(self, account: DestinationAccount, symbol: str) -> float | None:
        exchange = self._exchange_for(account)
        try:
            positions = await exchange.fetch_positions([symbol])
        except Exception:  # noqa: BLE001 - e.g. NotSupported on spot markets — genuinely unknown, not zero
            return None

        for position in positions:
            if position.get("symbol") != symbol:
                continue
            contracts = position.get("contracts")
            if contracts is None:
                continue
            return -contracts if position.get("side") == "short" else contracts
        return 0.0  # no open position found for this symbol

    async def get_last_price(self, account: DestinationAccount, symbol: str) -> float | None:
        exchange = self._exchange_for(account)
        try:
            ticker = await exchange.fetch_ticker(symbol)
        except Exception:  # noqa: BLE001 - network/exchange error — genuinely unknown right now, not "unchanged"
            return None
        price = ticker.get("last")
        if price is None:
            price = ticker.get("close")
        return float(price) if price is not None else None

    async def close(self) -> None:
        for exchange in self._exchanges.values():
            await exchange.close()
