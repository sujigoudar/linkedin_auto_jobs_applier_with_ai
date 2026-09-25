"""The copier engine: wires a Signal from any source to every configured
destination account, sized and symbol-mapped per account.

Sources and brokers know nothing about each other — this is the only place
that does.

## Close signals

A `Side.CLOSE` signal doesn't say how much to close — that depends on
what's currently open on that specific destination account, which the
account's `multiplier`/`fixed_quantity` sizing doesn't know either (closing
is about flattening a position, not scaling a trade). So the engine
resolves it here, per account, before calling the broker at all:

    1. Look up this account's tracked net position for the mapped symbol
       (`SignalStore.get_position` — this service's own record of what it
       has sent, not a live read of the broker's book).
    2. If flat (zero), there's nothing to close: report REJECTED without
       calling the broker.
    3. Otherwise resolve to the opposing BUY/SELL at the full open
       quantity, and call `place_order` with that — brokers never see
       `Side.CLOSE` from the engine; they only need to implement BUY/SELL.
       (Each broker's own `Side.CLOSE` handling, where present, is a
       defensive fallback for direct/standalone use, not something the
       engine relies on.)

Position tracking itself updates from `OrderResult.filled_quantity` when a
broker confirms FILLED, or optimistically from the requested quantity when
a broker only reports PENDING (SignalStack, Alpaca, IBKR, NinjaTrader,
Rithmic all confirm fills asynchronously, outside this call). That means
tracked positions on those brokers can drift from the real book if an
order is later rejected or partially filled after reporting PENDING — this
is a known limitation of not having a fill-confirmation feedback path from
those brokers back into this service yet.
"""
from __future__ import annotations

import logging

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.risk import size_for_account, symbol_for_account
from app.routing import RoutingConfig

logger = logging.getLogger(__name__)


class SignalCopierEngine:
    def __init__(self, routing: RoutingConfig, brokers: dict[str, BrokerAdapter], store: SignalStore):
        self.routing = routing
        self.brokers = brokers
        self.store = store

    async def handle_signal(self, signal: Signal) -> list[OrderResult]:
        self.store.save_signal(signal)

        destinations = self.routing.destinations_for(signal.source, signal.symbol)
        if not destinations:
            logger.info("no destinations configured for source=%s symbol=%s", signal.source, signal.symbol)
            return []

        results: list[OrderResult] = []
        for account in destinations:
            broker = self.brokers.get(account.broker)
            if broker is None:
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=f"no broker adapter registered for '{account.broker}'",
                )
                self.store.save_order_result(result)
                results.append(result)
                continue

            symbol = symbol_for_account(signal, account)

            if signal.side == Side.CLOSE:
                resolved = self._resolve_close(signal, account, symbol)
                if resolved is None:
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.REJECTED,
                        signal_id=signal.id,
                        message="no open position to close",
                    )
                    self.store.save_order_result(result)
                    results.append(result)
                    continue
                order_signal, quantity = resolved
            else:
                order_signal, quantity = signal, size_for_account(signal, account)

            try:
                result = await broker.place_order(order_signal, account, quantity, symbol)
            except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
                logger.exception("order failed for account=%s", account.account_id)
                result = OrderResult(
                    account_id=account.account_id,
                    status=OrderStatus.ERROR,
                    signal_id=signal.id,
                    message=str(exc),
                )

            if result.status in (OrderStatus.FILLED, OrderStatus.PENDING):
                filled_quantity = result.filled_quantity or quantity
                self.store.record_fill(account.account_id, symbol, order_signal.side, filled_quantity)

            self.store.save_order_result(result)
            results.append(result)

        return results

    def _resolve_close(
        self, signal: Signal, account: DestinationAccount, symbol: str
    ) -> tuple[Signal, float] | None:
        position = self.store.get_position(account.account_id, symbol)
        if position == 0:
            return None

        closing_side = Side.SELL if position > 0 else Side.BUY
        quantity = abs(position)
        resolved_signal = Signal(
            source=signal.source,
            symbol=signal.symbol,
            side=closing_side,
            asset_class=signal.asset_class,
            quantity=quantity,
            price=signal.price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            id=signal.id,
            received_at=signal.received_at,
            raw=signal.raw,
        )
        return resolved_signal, quantity
