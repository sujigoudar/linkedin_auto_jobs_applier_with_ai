"""MT5 execution destination, via the official `MetaTrader5` Python package
(Windows only, same host as a running MT5 terminal).

Setup:
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
                message="'close' side requires position-aware close logic; not yet implemented",
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
