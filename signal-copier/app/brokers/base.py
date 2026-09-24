"""The interface every execution destination (broker/exchange/platform) must implement."""
from __future__ import annotations

import abc

from app.models import DestinationAccount, OrderResult, Signal


class BrokerAdapter(abc.ABC):
    #: Must match the `broker` field used for accounts in accounts.yaml.
    name: str

    @abc.abstractmethod
    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        """Execute the sized, symbol-mapped signal on this account.

        `quantity` and `symbol` are already resolved by app/risk.py — this
        method's only job is to talk to the broker/exchange and report what
        happened.
        """
