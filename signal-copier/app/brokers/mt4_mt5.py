"""MT4/MT5 execution destinations. Two independent implementations live in
this file — pick whichever fits your setup:

- `MT5Broker`: the official `MetaTrader5` Python package, same-host only,
  MT5 only. Simplest if this service already runs on the MT5 terminal's
  own Windows box.
- `MetaApiBroker`: the MetaApi cloud SDK (see app/sources/mt4_mt5.py's
  docstring), works for both MT4 and MT5, and needs no local terminal at
  all since MetaApi hosts the terminal connection in the cloud. Prefer
  this unless you specifically want to avoid a third-party cloud
  dependency and already have a same-host MT5 setup.

## MT5Broker setup:
    pip install MetaTrader5
    1. This service must run on the same Windows host as the MT5 terminal
       (the package talks to it via local IPC — it cannot reach a remote
       terminal). If this service normally runs elsewhere, you need a
       Windows box/VPS running both.
    2. One MT5 terminal instance == one logged-in account. Multiple
       accounts need multiple terminal instances (separate install
       directories), each with its own MetaTraderBroker(terminal_path=...)
       registered under a distinct broker name if used simultaneously — the
       `MetaTrader5` package's `initialize()` call targets one terminal
       process per Python interpreter.
    3. Login credentials go in accounts.yaml only as the *env var name
       prefix* pattern used elsewhere: set `MT5_{ACCOUNT_ID}_LOGIN` /
       `..._PASSWORD` / `..._SERVER` (and optionally `..._TERMINAL_PATH` if
       not using the default installed terminal).

MT4 has no equivalent official Python package — trading on MT4 needs an EA
bridge (e.g. ZeroMQ) on the terminal, which is out of scope for this file
(see app/sources/mt4_mt5.py's docstring for the same caveat on the source
side).

The `MetaTrader5` package's calls are blocking (local IPC, not network
async), so they're run via `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import os

from app.brokers.base import BrokerAdapter
from app.models import DestinationAccount, OrderResult, OrderStatus, Signal


class MT5Broker(BrokerAdapter):
    name = "mt4_mt5"
    supports_native_bracket = True  # sl/tp are fields on the same order_send request

    def __init__(self):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "MetaTrader5 package is not installed (Windows only); run `pip install MetaTrader5`"
            ) from exc
        self._mt5 = mt5

    def _credentials_for(self, account: DestinationAccount) -> tuple[int, str, str]:
        prefix = f"MT5_{account.account_id.upper()}"
        login = os.getenv(f"{prefix}_LOGIN")
        password = os.getenv(f"{prefix}_PASSWORD")
        server = os.getenv(f"{prefix}_SERVER")
        if not login or not password or not server:
            raise RuntimeError(
                f"missing {prefix}_LOGIN / {prefix}_PASSWORD / {prefix}_SERVER "
                f"environment variables for account '{account.account_id}'"
            )
        return int(login), password, server

    def _place_order_sync(self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str) -> dict:
        mt5 = self._mt5
        login, password, server = self._credentials_for(account)

        if not mt5.initialize(login=login, password=password, server=server):
            raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                raise RuntimeError(f"MT5 has no tick data for symbol '{symbol}' (check it's visible/enabled)")

            order_type = mt5.ORDER_TYPE_BUY if signal.side.value == "buy" else mt5.ORDER_TYPE_SELL
            price = tick.ask if signal.side.value == "buy" else tick.bid

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": quantity,
                "type": order_type,
                "price": price,
                "deviation": 20,
                "magic": 20260101,
                "comment": f"signal-copier:{signal.source}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            if signal.stop_loss:
                request["sl"] = signal.stop_loss
            if signal.take_profit:
                request["tp"] = signal.take_profit

            result = mt5.order_send(request)
            return {
                "retcode": result.retcode,
                "order": result.order,
                "price": result.price,
                "volume": result.volume,
                "comment": result.comment,
            }
        finally:
            mt5.shutdown()

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
            result = await asyncio.to_thread(self._place_order_sync, signal, account, quantity, symbol)
        except RuntimeError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        if result["retcode"] != self._mt5.TRADE_RETCODE_DONE:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.REJECTED,
                signal_id=signal.id,
                message=f"MT5 rejected order: retcode={result['retcode']} ({result['comment']})",
            )

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=str(result["order"]),
            filled_quantity=result["volume"],
            filled_price=result["price"],
            message="filled by MT5",
        )


class MetaApiBroker(BrokerAdapter):
    """MT4/MT5 execution via the MetaApi cloud SDK. See this file's module
    docstring and app/sources/mt4_mt5.py for setup steps and API notes.

    Per-account config, read from env vars:
        MT4_MT5_METAAPI_TOKEN               # shared across accounts
        MT4_MT5_METAAPI_{ACCOUNT_ID}_ID      # this account's MetaApi account id
    """

    name = "mt4_mt5_metaapi"
    supports_native_bracket = True  # stop_loss/take_profit params on the same order call

    def __init__(self):
        try:
            import metaapi_cloud_sdk  # noqa: F401 - import check only; MetaApi() itself needs a running event loop
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "metaapi-cloud-sdk is not installed; run `pip install metaapi-cloud-sdk`"
            ) from exc

        self._token = os.getenv("MT4_MT5_METAAPI_TOKEN")
        if not self._token:
            raise RuntimeError("missing MT4_MT5_METAAPI_TOKEN environment variable")

        # MetaApi() schedules an internal background task on construction, so it
        # can only be built once there's a running event loop (i.e. not here in
        # __init__, which runs at module import time) — built lazily below instead.
        self._api = None
        self._connections: dict[str, object] = {}

    async def _connection_for(self, account: DestinationAccount):
        if account.account_id in self._connections:
            return self._connections[account.account_id]

        if self._api is None:
            from metaapi_cloud_sdk import MetaApi

            self._api = MetaApi(self._token)

        metaapi_account_id = os.getenv(f"MT4_MT5_METAAPI_{account.account_id.upper()}_ID")
        if not metaapi_account_id:
            raise RuntimeError(
                f"missing MT4_MT5_METAAPI_{account.account_id.upper()}_ID environment variable "
                f"for account '{account.account_id}'"
            )

        metaapi_account = await self._api.metatrader_account_api.get_account(metaapi_account_id)
        if metaapi_account.state != "DEPLOYED":
            await metaapi_account.deploy()
        if metaapi_account.connection_status != "CONNECTED":
            await metaapi_account.wait_connected()

        connection = metaapi_account.get_streaming_connection()
        await connection.connect()
        await connection.wait_synchronized()
        self._connections[account.account_id] = connection
        return connection

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        try:
            connection = await self._connection_for(account)
        except RuntimeError as exc:
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        sl_tp_kwargs = {}
        if signal.stop_loss:
            sl_tp_kwargs["stop_loss"] = signal.stop_loss
        if signal.take_profit:
            sl_tp_kwargs["take_profit"] = signal.take_profit

        try:
            if signal.side.value == "buy":
                result = await connection.create_market_buy_order(symbol=symbol, volume=quantity, **sl_tp_kwargs)
            elif signal.side.value == "sell":
                result = await connection.create_market_sell_order(symbol=symbol, volume=quantity, **sl_tp_kwargs)
            else:
                return OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.REJECTED,
                    signal_id=signal.id,
                    message="'close' side reached the broker directly without engine-level resolution (see SignalCopierEngine._resolve_close); this broker only accepts buy/sell",
                )
        except Exception as exc:  # noqa: BLE001 - surface any MetaApi error as a failed order
            return OrderResult(
                account_id=account.account_id,
                status=OrderStatus.ERROR,
                signal_id=signal.id,
                message=str(exc),
            )

        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED if result.get("stringCode") == "TRADE_RETCODE_DONE" else OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id=str(result.get("orderId") or result.get("positionId") or ""),
            message=f"MetaApi order result: {result.get('stringCode')}",
        )

    async def close(self) -> None:
        for connection in self._connections.values():
            await connection.close()
