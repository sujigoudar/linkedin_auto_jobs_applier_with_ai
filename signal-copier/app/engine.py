"""The copier engine: wires a Signal from any source to every configured
destination account, sized and symbol-mapped per account.

Sources and brokers know nothing about each other — this is the only place
that does.
"""
from __future__ import annotations

import logging

from app.brokers.base import BrokerAdapter
from app.db import SignalStore
from app.models import OrderResult, OrderStatus, Signal
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
            else:
                quantity = size_for_account(signal, account)
                symbol = symbol_for_account(signal, account)
                try:
                    result = await broker.place_order(signal, account, quantity, symbol)
                except Exception as exc:  # noqa: BLE001 - one account's failure must not block others
                    logger.exception("order failed for account=%s", account.account_id)
                    result = OrderResult(
                        account_id=account.account_id,
                        status=OrderStatus.ERROR,
                        signal_id=signal.id,
                        message=str(exc),
                    )

            self.store.save_order_result(result)
            results.append(result)

        return results
