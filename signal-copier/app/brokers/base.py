"""The interface every execution destination (broker/exchange/platform) must implement."""
from __future__ import annotations

import abc

from app.models import AccountBalance, AssetClass, DestinationAccount, EntryOrderType, OrderResult, Side, Signal


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

    def can_trade_entry_order_type(self, entry_order_type: EntryOrderType | None) -> bool:
        """Check if this adapter can handle the given entry_order_type.

        Only MARKET orders are universally supported; LIMIT/STOP orders are
        adapter-specific. `None` (unspecified) is treated as MARKET.
        """
        if entry_order_type is None or entry_order_type == EntryOrderType.MARKET:
            return True
        # LIMIT/STOP orders require explicit per-adapter support
        return False

    def normalize_quantity(self, account: DestinationAccount, symbol: str, quantity: float) -> float | None:
        """Normalize quantity to venue precision/lot-step, or return None if unknown.

        Called after risk sizing but before broker submission. Must return the
        normalized quantity (>= 0) or None if this venue's precision is unknown.
        Never raise; return None to signal unknown precision instead.

        Default implementation returns quantity unchanged.
        """
        return quantity

    @abc.abstractmethod
    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        """Execute the sized, symbol-mapped signal on this account.

        `quantity` and `symbol` are already resolved by app/risk.py — this
        method's only job is to talk to the broker/exchange and report what
        happened.
        """
        ...

    async def close(self) -> None:
        """Release any held connection/session (an httpx client, an
        exchange SDK session, a broker API connection). Override only if
        there's actually something to release — the default here is a
        no-op, not a missing capability (app/main.py's lifespan shutdown
        calls this on every registered broker unconditionally)."""
        return None

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

    def get_reference_price(self, symbol: str) -> float | None:
        """Optional: get a reference price for the symbol (e.g., from the last
        fill or a broker quote).

        A-09: Chase guard uses this to validate incoming signal prices against
        broker reference prices. Return the last fill price, broker quote, or
        None if unavailable. The default here returns None (no reference).
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

    async def cancel_order(
        self, account: DestinationAccount, broker_order_id: str, symbol: str | None = None
    ) -> bool:
        """Cancel a previously placed order (e.g. an existing protective stop,
        before replacing it). Return False if cancellation isn't supported or
        confirmed — the caller must not assume it worked. Symbol is optional
        and used by brokers (e.g. ccxt) that require it for order cancellation."""
        return False

    async def replace_stop_quantity(
        self,
        account: DestinationAccount,
        broker_order_id: str,
        new_quantity: float,
        new_price: float | None = None,
        symbol: str | None = None,
    ) -> OrderResult | None:
        """Resize (and optionally reprice) an existing stop order in place.
        Return None if this broker has no verified in-place replace — the
        caller falls back to cancel-then-resubmit instead. Symbol is optional
        and used by brokers (e.g. ccxt) that require it for order replacement."""
        return None

    async def find_order_by_client_id(
        self, account: DestinationAccount, client_order_id: str
    ) -> str | None:
        """Look up an order by its client-assigned id (idempotency key).

        Returns the broker_order_id if found, or None if not found or lookup fails.
        This is optional; brokers that don't implement it return None."""
        return None

    @property
    def has_client_id_lookup_capability(self) -> bool:
        """Check if this broker has overridden find_order_by_client_id."""
        return type(self).find_order_by_client_id is not BrokerAdapter.find_order_by_client_id

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

    async def get_quote(self, symbol: str) -> float | None:
        """Optional: fetch a current market quote for gating purposes
        (e.g. for notional/leverage/buying-power checks before entry).

        B-06: Called by app/engine.py before entry admission to fetch a
        live quote when available, falling back to the message price only
        when this broker has no quote capability. Return None if this
        broker has no verified way to fetch one (the default); the caller
        will use the signal price instead.

        Called at pre-flight time before any order is submitted, not tied to
        a specific account (unlike get_last_price). Brokers that can only
        provide quotes per-account (most) should return None here — the
        engine will fall back to the signal price."""
        return None

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        """Query this account's real, live cash/equity/buying-power/margin
        directly from the broker (see app/models.py's `AccountBalance` for
        what each field means and why every one of them is `None`, never an
        invented 0.0, when this broker/account doesn't report it). Return
        None if this broker has no verified way to read it at all — the
        caller must not treat that as "zero balance."

        Deliberately NOT used by app/capital_allocator.py's notional
        ceiling, which enforces a strictly narrower, already-disclosed
        check against this service's own confirmed-fill replay
        (`confirmed_open_notional`) -- a live broker balance covers
        margin/buying-power the ceiling was never designed to reason
        about, and conflating the two would silently change what
        `max_notional_exposure` means."""
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

    @property
    def has_quote_capability(self) -> bool:
        """B-06: whether this adapter can fetch a live quote for gating purposes."""
        return type(self).get_quote is not BrokerAdapter.get_quote

    @property
    def has_balance_capability(self) -> bool:
        return type(self).get_account_balance is not BrokerAdapter.get_account_balance

    @property
    def has_account_order_position_feedback(self) -> bool:
        """Whether this adapter can verify ANYTHING back about the
        account, an order, or a position after submission -- order-status
        confirmation, position readback, or balance readback (any one of
        the three is enough; a broker doesn't need all three to have SOME
        real feedback channel).

        This is the genuine, structural prerequisite for
        `app/qualification.py`'s `account_entitled` rung and everything
        above it: a route can be `authenticated` (a real auth handshake
        succeeded) on an adapter with none of the three, but it can never
        honestly be verified as account-entitled, protocol-tested or
        venue-tested, because there is no real channel to verify it
        with -- see app/qualification.py's `FEEDBACK_DEPENDENT_FLOOR` and
        `SignalStore.record_route_qualification`, which enforces this as a
        hard write-path rejection, not operator discipline.

        SignalStack is the concrete case this was written for (see its own
        module docstring: it POSTs to a webhook and gets only an HTTP
        accept confirming SignalStack itself received the request --
        never a fill, a position, or a balance figure back from the
        downstream broker it routed to), but this property is adapter-
        generic, not SignalStack-specific -- any adapter with none of
        these three real implementations is capped the same way."""
        return (
            self.has_order_status_capability
            or self.has_position_readback_capability
            or self.has_balance_capability
        )

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

    def entries_admissible(self) -> bool:
        """C-10: Whether live ENTRY signals can be admitted to this adapter's
        routes at all.

        An adapter without `has_account_order_position_feedback` (i.e., no
        order-status confirmation, position readback, or balance readback)
        can never reach `release_approved` qualification state, even after a
        human sign-off -- the qualification ladder's account_entitled rung
        and everything above it require some feedback channel to verify
        that an order was actually executed. Five adapters cannot provide
        this feedback: ninjatrader (fire-and-forget webhook relay),
        rithmic (fire-and-forget relay with no status polling),
        signalstack (webhook relay with no venue feedback), MT4/MT5 (no
        real order tracking after restart), and MetaApi (partial fill
        unpollable, no quantity on FILLED).

        Affected adapters can still route CLOSE signals to close externally-
        opened positions (see _check_route_qualified's logic), but live
        entries are structurally impossible and fail-closed here."""
        return self.has_account_order_position_feedback
    def venue_environment(self, account: DestinationAccount) -> str:
        """Return the venue environment identifier for this account.

        This is used to qualify routes per environment: a route is only
        release-approved for the environment it was qualified in. Different
        adapters resolve this differently:
        - Alpaca: reads base URL env var (paper-api.alpaca.markets = "paper", api.alpaca.markets = "live")
        - ccxt: reads sandbox flag per instance ("sandbox" or "live")
        - Others: return "unknown" by default

        The value becomes part of the route qualification key alongside
        (adapter_type, route_key, asset_class, product_type), so changing
        the environment requires re-qualification."""
        return "unknown"
