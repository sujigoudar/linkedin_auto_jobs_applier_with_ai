"""The interface every execution destination (broker/exchange/platform) must implement."""
from __future__ import annotations

import abc

from app.models import AssetClass, DestinationAccount, OrderResult, Side, Signal


class BrokerAdapter(abc.ABC):
    #: Must match the `broker` field used for accounts in accounts.yaml.
    name: str

    #: True only if this broker can submit entry + stop + take-profit as one
    #: atomic bracket/OCO order (verified — see each broker module's
    #: docstring). False is the honest default: everything below is optional
    #: and defaults to "unsupported" rather than pretending a capability
    #: exists. app/lifecycle/manager.py uses this to decide whether an
    #: account needs the managed-lifecycle fallback at all.
    supports_native_bracket: bool = False

    #: Which `AssetClass` values this adapter's `place_order` actually
    #: knows how to submit — set ONLY where the adapter's own code enforces
    #: or is fundamentally limited to a subset (e.g. AlpacaBroker sends
    #: plain equity orders and explicitly does not attempt options-specific
    #: order shaping even though Alpaca-the-broker supports options; ccxt
    #: is inherently exchange/crypto-only). `None` — the default — means
    #: "not declared," NOT "supports everything": app/engine.py only
    #: refuses a signal when this is explicitly set and doesn't contain the
    #: signal's asset_class, so an undeclared adapter's existing behavior
    #: is unaffected. Declaring a false restriction here would silently
    #: break a real, working route, which is worse than not declaring at
    #: all — see each broker module's docstring for what's actually
    #: verified before adding an entry.
    supported_asset_classes: frozenset[AssetClass] | None = None

    def can_trade_asset_class(self, asset_class: AssetClass) -> bool:
        if self.supported_asset_classes is None:
            return True
        return asset_class in self.supported_asset_classes

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

    # --- Managed-lifecycle capabilities (app/lifecycle/) ---
    #
    # These back the fallback path for brokers/accounts that can't submit a
    # true atomic bracket (`supports_native_bracket = False`). Every one of
    # them defaults to "not supported" — returning None/False — rather than
    # raising, so PositionLifecycleManager can check a capability and degrade
    # gracefully instead of assuming a broker can do something it can't. A
    # subclass overrides only the ones it has a verified, real implementation
    # for (see each broker module's docstring for what's been checked).

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side: Side
    ) -> OrderResult | None:
        """Submit a standalone stop order sized to `quantity` (not attached to
        any other order). `exit_side` is the side of the STOP order itself
        (opposite of the position being protected — SELL for a long, BUY for
        a short); the caller already knows this and passes it explicitly
        rather than every broker having to re-derive it from a position
        query (which several brokers can't do reliably — e.g. ccxt spot
        markets have no "position" concept at all). Return None if this
        broker has no verified way to place a standalone stop — the caller
        must not treat the position as protected."""
        return None

    async def cancel_order(self, account: DestinationAccount, broker_order_id: str) -> bool:
        """Cancel a previously placed order (e.g. an existing protective stop,
        before replacing it). Return False if cancellation isn't supported or
        confirmed — the caller must not assume it worked."""
        return False

    async def replace_stop_quantity(
        self,
        account: DestinationAccount,
        broker_order_id: str,
        new_quantity: float,
        new_price: float | None = None,
    ) -> OrderResult | None:
        """Resize (and optionally reprice) an existing stop order in place.
        Return None if this broker has no verified in-place replace — the
        caller falls back to cancel-then-resubmit instead."""
        return None

    async def get_broker_position(self, account: DestinationAccount, symbol: str) -> float | None:
        """Query the broker's own record of the current position size for this
        symbol (positive = long, negative = short, 0 = flat). Return None if
        this broker has no verified way to read that back — the caller must
        not treat None as zero."""
        return None

    async def get_last_price(self, account: DestinationAccount, symbol: str) -> float | None:
        """Best-effort current market price for `symbol` on this account's
        venue — what app/pricing.py's `PriceMonitor` polls to drive
        `PositionLifecycleManager.on_price_update()` (targets, trailing,
        stop resizing) continuously in production. Return None if this
        broker has no verified way to fetch one; the caller must skip this
        poll for this position, never treat None as "price unchanged" or
        stop monitoring the position entirely."""
        return None

    # --- Capability introspection (computed, not declared) ---
    #
    # These answer "does this adapter have a REAL implementation of X" by
    # checking whether the subclass actually overrides the base no-op —
    # not from a separately maintained boolean flag. A flag nobody updates
    # when a method changes is just as false as a missing method: "method
    # existence is not capability evidence" cuts both ways. Used to gate
    # live admission (app/engine.py, app/lifecycle/manager.py) and to
    # report real capability to a caller (GET /brokers in app/main.py)
    # instead of a broker name/class being treated as proof of anything.

    @property
    def has_protective_stop_capability(self) -> bool:
        return type(self).place_protective_stop is not BrokerAdapter.place_protective_stop

    @property
    def has_cancel_capability(self) -> bool:
        return type(self).cancel_order is not BrokerAdapter.cancel_order

    @property
    def has_replace_stop_capability(self) -> bool:
        return type(self).replace_stop_quantity is not BrokerAdapter.replace_stop_quantity

    @property
    def has_position_readback_capability(self) -> bool:
        return type(self).get_broker_position is not BrokerAdapter.get_broker_position

    @property
    def has_order_status_capability(self) -> bool:
        return type(self).get_order_status is not BrokerAdapter.get_order_status

    @property
    def has_last_price_capability(self) -> bool:
        return type(self).get_last_price is not BrokerAdapter.get_last_price

    def can_protect_a_managed_position(self) -> bool:
        """Whether `PositionLifecycleManager` can actually keep a position
        protected on this broker.

        ADP-06: `supports_native_bracket` alone used to count here, but it
        can't -- a managed-lifecycle entry deliberately STRIPS stop_loss/
        take_profit before ever calling `place_order` (see
        app/engine.py's `_handle_managed_entry`: protection is meant to be
        owned entirely by the lifecycle manager's own logical
        targets/trailing/stop machinery, never embedded in the broker
        order). A broker whose only claimed capability is an atomic
        entry-time bracket has no way to protect a position through THIS
        path at all, regardless of what `supports_native_bracket` says --
        only a real, verified standalone `place_protective_stop`
        (submitted after the fact, once the actual fill is known) can. A
        broker with neither must not be admitted into managed-lifecycle
        live trading — see app/lifecycle/manager.py's `validate_plan`."""
        return self.has_protective_stop_capability
