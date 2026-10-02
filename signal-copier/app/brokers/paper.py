"""In-memory paper/mock broker.

Fills every order instantly at the signal's price (or 0.0 if none given).
Use this for end-to-end testing of the routing/sizing pipeline without
touching a real exchange or broker.

Also the reference implementation of the managed-lifecycle capabilities
(app/brokers/base.py's `place_protective_stop`/`cancel_order`/
`replace_stop_quantity`/`get_broker_position`) — a real broker's stop
orders sit on its servers and fill against real market data; this one
tracks them in memory and fills them only when `simulate_price()` is
called, so app/lifecycle/manager.py's behavior (protect-first, resize
transitions, oversell prevention) can be tested deterministically without
a live broker or feed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import AccountBalance, DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.brokers.base import BrokerAdapter


@dataclass
class _StopOrder:
    order_id: str
    account_id: str
    symbol: str
    side: Side  # the side of the STOP order itself (opposite of the position)
    quantity: float
    stop_price: float


class PaperBroker(BrokerAdapter):
    name = "paper"
    supports_native_bracket = True

    #: DB-0X (bounded, scoped to this broker only): PaperBroker's whole
    #: state is internal to this process -- unlike every other adapter in
    #: this codebase, there's no real external account to under- or
    #: over-report. That makes a genuinely computed (never fabricated)
    #: cash/buying-power figure possible here specifically: a documented
    #: starting simulated cash balance, moved only by this broker's own
    #: real fills (see `_apply_fill_to_cash`), and an explicit, documented
    #: flat simulated fee per fill -- 0.0 by default, i.e. "this simulator
    #: charges no fee," not "fees aren't tracked" (see `get_account_balance`
    #: /`fee_per_fill` below). Every OTHER broker in this codebase must
    #: keep reporting these as genuinely unsupported/not_tracked -- see
    #: this module's own docstring and app/brokers/base.py's
    #: `get_account_balance` default.
    STARTING_CASH = 100_000.0
    FEE_PER_FILL = 0.0

    def __init__(self) -> None:
        # account_id -> symbol -> net position
        self.positions: dict[str, dict[str, float]] = {}
        self.fills: list[OrderResult] = []
        self._stop_orders: dict[str, _StopOrder] = {}
        self._filled_stop_orders: dict[str, OrderResult] = {}  # Track filled orders for status polling
        self._target_exit_orders: dict[str, str] = {}  # D-01: Track take-profit (target exit) orders by order_id
        self._next_stop_id = 1
        #: account_id -> real, genuinely-computed simulated cash balance,
        #: seeded at STARTING_CASH the first time an account is touched
        #: (never fabricated per-account; every account starts from the
        #: same documented baseline since this broker has no real funding
        #: event to read a different one from). Moved only by
        #: `_apply_fill_to_cash`, at the exact same call sites that already
        #: adjust `self.positions` for a real fill.
        self._cash: dict[str, float] = {}
        #: account_id -> next order ID sequence number (WP-38, G-C-24).
        #: Persisted via the DestinationAccount.paper_order_id_sequence field
        #: in the database so order IDs remain unique across restarts.
        #: The engine initializes this from the store at startup and updates
        #: it after each fill.
        self._order_id_sequence: dict[str, int] = {}
        #: Read-only exposure of `fee_per_fill` -- see class docstring.
        self.fee_per_fill = self.FEE_PER_FILL

    def venue_environment(self, account: DestinationAccount) -> str:
        """Return 'paper' for the in-memory simulator."""
        return "paper"

    def _cash_for(self, account_id: str) -> float:
        return self._cash.setdefault(account_id, self.STARTING_CASH)

    def _apply_fill_to_cash(self, account_id: str, side: Side, quantity: float, price: float) -> None:
        """The real (simulated, but genuinely computed) cash effect of one
        fill: a BUY spends cash (price * quantity, plus this broker's own
        documented fee), a SELL/short-cover receives it (less the fee).
        Called from every place `self.positions` already moves for a real
        fill -- `place_order`'s entry/exit fills and `simulate_price`'s
        resting-stop fills alike -- so cash never drifts out of sync with
        the position book both are meant to describe."""
        cash = self._cash_for(account_id)
        notional = quantity * price
        if side == Side.BUY:
            self._cash[account_id] = cash - notional - self.fee_per_fill
        else:  # SELL (Side.CLOSE never reaches here -- see place_order's own resolution)
            self._cash[account_id] = cash + notional - self.fee_per_fill

    async def place_order(
        self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str
    ) -> OrderResult:
        book = self.positions.setdefault(account.account_id, {})
        current = book.get(symbol, 0.0)
        # E-16: managed exits have no price on the signal; return None for
        # unknown price rather than 0.0 (which would be journaled as real).
        price = signal.price

        if signal.side.value == "buy":
            book[symbol] = current + quantity
            self._apply_fill_to_cash(account.account_id, Side.BUY, quantity, price or 0.0)
        elif signal.side.value == "sell":
            book[symbol] = current - quantity
            self._apply_fill_to_cash(account.account_id, Side.SELL, quantity, price or 0.0)
        else:  # close
            # A real cash effect exists here too (closing a position is a
            # real opposing fill), but this branch has no opposing
            # Side.BUY/SELL to size it correctly against `current` (a
            # short close should be a BUY-side cash effect, a long close a
            # SELL-side one) -- app/engine.py never actually sends
            # Side.CLOSE to a broker (it resolves to the opposing side
            # first, see its own "Close signals" docstring), so this
            # branch is a defensive fallback for direct/standalone use, not
            # a real path this cash tracking needs to cover.
            book[symbol] = 0.0

<<<<<<< HEAD
        # WP-38 (G-C-24): Use persistent per-account sequence for order IDs.
        # The engine initializes _order_id_sequence from the database and
        # updates it after each order. Defaults to 1 if not yet set.
        account_id = account.account_id
        seq = self._order_id_sequence.get(account_id, 1)
        self._order_id_sequence[account_id] = seq + 1
        order_id = f"paper-{seq}"
=======
        # D-01: Create simulated child stop orders for bracket entries
        child_order_ids: dict[str, str] = {}
        if signal.side.value in ("buy", "sell") and (signal.stop_loss or signal.take_profit):
            # Determine the exit side for stop/TP (opposite of entry)
            exit_side = Side.SELL if signal.side == Side.BUY else Side.BUY

            if signal.stop_loss:
                stop_order_id = f"paper-stop-{self._next_stop_id}"
                self._next_stop_id += 1
                self._stop_orders[stop_order_id] = _StopOrder(
                    order_id=stop_order_id,
                    account_id=account.account_id,
                    symbol=symbol,
                    side=exit_side,
                    quantity=quantity,
                    stop_price=signal.stop_loss,
                )
                child_order_ids["stop"] = stop_order_id

            if signal.take_profit:
                # Take-profit is a limit order at the target price (opposite side)
                # D-01: Track the order ID so it can be cancelled if a sibling (stop) fills
                tp_order_id = f"paper-tp-{self._next_stop_id}"
                self._next_stop_id += 1
                self._target_exit_orders[tp_order_id] = tp_order_id
                child_order_ids["take_profit"] = tp_order_id
>>>>>>> 21bf5fe (WP-18 (D-01, C-07): Track bracket child legs as reconcilable orders)

        result = OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=order_id,
            filled_quantity=quantity,
            filled_price=price,
            message="filled by paper broker",
            fee=self.fee_per_fill,
            fee_currency="USD",  # Paper broker uses USD convention
            slippage=0.0,  # Paper broker fills exactly at signal price when available
            child_order_ids=child_order_ids,
        )
        self.fills.append(result)
        return result

    def normalize_quantity(self, account: DestinationAccount, symbol: str, quantity: float) -> float | None:
        """Paper broker normalizes to 1e-8 (8 decimal places for crypto compatibility)."""
        import math
        step = 1e-8
        normalized = math.floor(quantity / step) * step
        return max(0.0, normalized)

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        """A real, genuinely-computed simulated cash/buying-power figure --
        see this class's own docstring for why that's honest here
        specifically (a fully-controlled, internal-to-this-process
        broker) where it would be fabrication for a real external broker
        adapter. `equity`/`maintenance_margin` stay `None` (never
        fabricated): this broker tracks no live mark for open positions
        (no continuous price feed, only fill prices and whatever
        `simulate_price` is explicitly called with), so there is no real
        current position value to add to cash for an `equity` figure, and
        it models no margin concept at all."""
        cash = self._cash_for(account.account_id)
        return AccountBalance(account_id=account.account_id, cash=cash, buying_power=cash)

    async def place_protective_stop(
        self, account: DestinationAccount, symbol: str, quantity: float, stop_price: float, exit_side: Side
    ) -> OrderResult | None:
        order_id = f"paper-stop-{self._next_stop_id}"
        self._next_stop_id += 1
        self._stop_orders[order_id] = _StopOrder(
            order_id=order_id,
            account_id=account.account_id,
            symbol=symbol,
            side=exit_side,
            quantity=quantity,
            stop_price=stop_price,
        )
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=order_id,
            message=f"paper stop resting: {quantity} @ {stop_price}",
        )

<<<<<<< HEAD
    async def cancel_order(self, account: DestinationAccount, broker_order_id: str) -> bool:
        # D-01: Handle both stop orders and take-profit (target exit) orders
        stop_found = self._stop_orders.pop(broker_order_id, None) is not None
        target_found = self._target_exit_orders.pop(broker_order_id, None) is not None
        return stop_found or target_found
=======
    async def cancel_order(
        self, account: DestinationAccount, broker_order_id: str, symbol: str | None = None
    ) -> bool:
        return self._stop_orders.pop(broker_order_id, None) is not None
>>>>>>> ece696e (WP-23: recovery edges for lost entries, venue adoption, and partial coverage)

    async def replace_stop_quantity(
        self,
        account: DestinationAccount,
        broker_order_id: str,
        new_quantity: float,
        new_price: float | None = None,
        symbol: str | None = None,
    ) -> OrderResult | None:
        stop = self._stop_orders.get(broker_order_id)
        if stop is None:
            return None
        stop.quantity = new_quantity
        if new_price is not None:
            stop.stop_price = new_price
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.PENDING,
            signal_id="",
            broker_order_id=broker_order_id,
            message=f"paper stop resized: {new_quantity} @ {stop.stop_price}",
        )

    async def get_broker_position(self, account: DestinationAccount, symbol: str) -> float | None:
        return self.positions.setdefault(account.account_id, {}).get(symbol, 0.0)

<<<<<<< HEAD
    def set_order_id_sequence(self, account_id: str, sequence: int) -> None:
        """Initialize the order ID sequence for an account (WP-38, G-C-24).
        Called by the engine at startup to restore the persisted sequence
        from the database."""
        if sequence is not None:
            self._order_id_sequence[account_id] = sequence

    def get_order_id_sequence(self, account_id: str) -> int:
        """Get the current order ID sequence for an account (WP-38, G-C-24).
        Called by the engine after a fill to persist the updated sequence
        back to the database."""
        return self._order_id_sequence.get(account_id, 1)
=======
    async def get_order_status(
        self, account: DestinationAccount, broker_order_id: str
    ) -> OrderResult | None:
        """Poll the status of a resting stop order. Returns None if still
        pending, or an OrderResult with FILLED/REJECTED/ERROR if terminal."""
        # Check if this is a filled stop order
        if broker_order_id in self._filled_stop_orders:
            return self._filled_stop_orders[broker_order_id]

        # Check if this order_id is still resting
        if broker_order_id in self._stop_orders:
            # Still pending - return None to keep polling it later
            return None

        # Unknown order - return None
        return None
>>>>>>> 21bf5fe (WP-18 (D-01, C-07): Track bracket child legs as reconcilable orders)

    def simulate_price(self, symbol: str, price: float) -> list[OrderResult]:
        """Test/simulation hook: check every resting stop order on `symbol`
        against `price` and fill any that trigger. A SELL stop triggers when
        price <= stop_price (protecting a long); a BUY stop triggers when
        price >= stop_price (protecting a short). Returns the fills so the
        caller (normally PositionLifecycleManager) can react to them the same
        way it would react to a real broker's fill notification.
        """
        triggered_ids = []
        for order_id, stop in self._stop_orders.items():
            if stop.symbol != symbol:
                continue
            if stop.side == Side.SELL and price <= stop.stop_price:
                triggered_ids.append(order_id)
            elif stop.side == Side.BUY and price >= stop.stop_price:
                triggered_ids.append(order_id)

        results = []
        for order_id in triggered_ids:
            stop = self._stop_orders.pop(order_id)
            book = self.positions.setdefault(stop.account_id, {})
            current = book.get(stop.symbol, 0.0)
            book[stop.symbol] = current - stop.quantity if stop.side == Side.SELL else current + stop.quantity
            # `stop.side` is the STOP order's own side (opposite the
            # position it protects) -- exactly the real BUY/SELL cash
            # effect this fill has, same call as `place_order`'s.
            self._apply_fill_to_cash(stop.account_id, stop.side, stop.quantity, price)

            result = OrderResult(
                account_id=stop.account_id,
                status=OrderStatus.FILLED,
                signal_id="",
                broker_order_id=order_id,
                filled_quantity=stop.quantity,
                filled_price=price,
                message=f"paper stop filled at simulated price {price}",
            )
            self.fills.append(result)
            self._filled_stop_orders[order_id] = result  # Track filled orders for get_order_status
            results.append(result)

        return results
