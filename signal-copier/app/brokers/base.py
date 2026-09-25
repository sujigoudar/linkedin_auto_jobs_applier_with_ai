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

    async def get_order_status(
        self, account: DestinationAccount, broker_order_id: str
    ) -> OrderResult | None:
        """Optional: re-check a previously PENDING order's real status.

        Called by app/reconciliation.py to correct this service's tracked
        position when a broker only confirms fills asynchronously (so
        `place_order` had to report PENDING as a best guess). Return `None`
        if the order is still pending with nothing new to report, or if this
        broker doesn't support checking status after submission at all (the
        default here) — either way the reconciler leaves that order alone.
        Return a real `OrderResult` only on a terminal status (filled,
        rejected, etc.) so the reconciler has something to act on.
        """
        return None
