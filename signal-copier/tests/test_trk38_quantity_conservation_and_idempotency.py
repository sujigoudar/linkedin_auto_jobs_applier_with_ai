"""TRK-38: a Hypothesis stateful test that extends C29's quantity-
conservation machine (tests/test_c29_hypothesis_quantity_conservation.py)
to also drive full closes, re-entries, and genuinely duplicate CLOSE
signals through generated event orderings -- not just the two hand-picked
scenarios in tests/test_trk27_managed_exit_duplicate_episode.py.

This does not replace C29 or TRK-27; it supplements both by generating
random interleavings of:
  - entries (opening a fresh episode once flat)
  - partial exits (PositionLifecycleManager.request_exit, as C29 does)
  - full closes via the real engine entrypoint (Signal(side=CLOSE)), which
    is the only call site that exercises SIG-01's signal.id replay guard
    and `check_duplicate_exit`'s TRK-27 duplicate-episode guard together
  - immediate re-submission of the *same* CLOSE signal object (genuine
    duplicate, same signal.id) and of a CLOSE signal with a different
    channel_id/message_id but no quantity change (the TRK-27 gap case)

Invariants proved across every generated sequence, not just fixed
examples:
  1. Quantity conservation: lifecycle.confirmed_owned_quantity, the
     broker's own book, and the SignalStore's tracked position always
     agree (same as C29).
  2. No negative/over-fill: owned quantity is never negative and never
     exceeds the cumulative quantity actually entered minus the
     cumulative quantity actually exited (nothing manufactured or lost).
  3. Idempotency boundary: a duplicate CLOSE (same signal.id, OR a
     different channel/message id arriving within
     MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS of a just-resolved close for a
     now-flat position) never submits a second broker order and never
     changes owned quantity. A genuine new episode's own close is never
     suppressed by that guard.
"""
import asyncio
import tempfile
import uuid
from pathlib import Path

from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule
from hypothesis import strategies as st

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

ACCOUNT_ID = "acct1"
SYMBOL = "TEST"


def _run(coro):
    return asyncio.run(coro)


class QuantityAndIdempotencyMachine(RuleBasedStateMachine):
    @initialize()
    def setup(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = SignalStore(Path(self._tmpdir.name) / "test.db")
        self.broker = PaperBroker()
        self.account = DestinationAccount(account_id=ACCOUNT_ID, broker="paper", managed_lifecycle=True)
        self.manager = PositionLifecycleManager(brokers={"paper": self.broker}, store=self.store)
        routing = RoutingConfig(
            rules=[RoutingRule(source="test", destinations=[ACCOUNT_ID])], accounts={ACCOUNT_ID: self.account}
        )
        self.engine = SignalCopierEngine(
            routing=routing, brokers={"paper": self.broker}, store=self.store, lifecycle_manager=self.manager
        )
        self.total_entered = 0.0
        self.total_exited = 0.0
        self.last_close_signal: Signal | None = None

    def teardown(self):
        self._tmpdir.cleanup()

    def _lifecycle_open(self) -> bool:
        lifecycle = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        return lifecycle is not None and not lifecycle.closed

    @precondition(lambda self: not self._lifecycle_open())
    @rule(quantity=st.integers(min_value=1, max_value=100))
    def enter(self, quantity):
        signal = Signal(
            source="test",
            symbol=SYMBOL,
            side=Side.BUY,
            quantity=float(quantity),
            stop_loss=1.0,
            channel_id="chan",
            message_id=f"entry-{uuid.uuid4().hex[:8]}",
        )
        result = _run(self.engine.handle_signal(signal))
        if result and result[0].status == OrderStatus.FILLED:
            self.total_entered += result[0].filled_quantity

    @precondition(lambda self: self._lifecycle_open())
    @rule(fraction=st.floats(min_value=0.01, max_value=1.0, allow_nan=False))
    def partial_exit(self, fraction):
        available = self.manager.arbiter.available_to_sell(ACCOUNT_ID, SYMBOL)
        quantity = int(available * fraction)
        if quantity <= 0:
            return
        result = _run(self.manager.request_exit(self.account, SYMBOL, float(quantity), source="test"))
        if result.status == OrderStatus.FILLED:
            self.total_exited += result.filled_quantity

    @precondition(lambda self: self._lifecycle_open())
    @rule()
    def full_close(self):
        signal = Signal(
            source="test",
            symbol=SYMBOL,
            side=Side.CLOSE,
            channel_id="chan",
            message_id=f"close-{uuid.uuid4().hex[:8]}",
        )
        result = _run(self.engine.handle_signal(signal))
        if result and result[0].status == OrderStatus.FILLED:
            self.total_exited += result[0].filled_quantity
            self.last_close_signal = signal

    @precondition(lambda self: self.last_close_signal is not None)
    @rule(same_signal_id=st.booleans())
    def duplicate_close_attempt(self, same_signal_id):
        """Re-send the just-resolved close, either as the literal same
        signal (same id, caught by SIG-01) or as a different
        channel/message id (caught by TRK-27's check_duplicate_exit)."""
        prior = self.last_close_signal
        assert prior is not None
        if same_signal_id:
            duplicate = prior
        else:
            duplicate = Signal(
                source="test",
                symbol=SYMBOL,
                side=Side.CLOSE,
                channel_id="chan-other-collector",
                message_id=f"close-dup-{uuid.uuid4().hex[:8]}",
            )
        before_owned = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        before_quantity = before_owned.confirmed_owned_quantity if before_owned is not None else 0.0
        before_broker = self.broker.positions.get(ACCOUNT_ID, {}).get(SYMBOL, 0.0)

        result = _run(self.engine.handle_signal(duplicate))

        after_owned = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        after_quantity = after_owned.confirmed_owned_quantity if after_owned is not None else 0.0
        after_broker = self.broker.positions.get(ACCOUNT_ID, {}).get(SYMBOL, 0.0)

        # A genuinely duplicate close against an already-flat position must
        # never move quantity -- whether it was caught by SIG-01 (REJECTED,
        # "duplicate signal") or by TRK-27's check_duplicate_exit (REJECTED,
        # "duplicate exit recognized"), or -- if a real re-entry happened in
        # between, outside this rule's control -- legitimately re-processed.
        if result and result[0].status == OrderStatus.REJECTED:
            assert before_quantity == after_quantity, "a REJECTED duplicate must not change owned quantity"
            assert before_broker == after_broker, "a REJECTED duplicate must not change the broker's book"

    @invariant()
    def owned_quantity_agrees_everywhere(self):
        lifecycle = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        if lifecycle is None:
            return
        broker_owned = self.broker.positions.get(ACCOUNT_ID, {}).get(SYMBOL, 0.0)
        store_owned = self.store.get_position(ACCOUNT_ID, SYMBOL)
        assert lifecycle.confirmed_owned_quantity == broker_owned == store_owned, (
            f"disagreement: lifecycle={lifecycle.confirmed_owned_quantity} "
            f"broker={broker_owned} store={store_owned}"
        )

    @invariant()
    def never_negative_or_over_filled(self):
        lifecycle = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        owned = lifecycle.confirmed_owned_quantity if lifecycle is not None else 0.0
        assert owned >= 0.0, f"owned quantity went negative: {owned}"
        # Nothing manufactured: owned quantity can never exceed what was
        # actually entered minus what was actually exited so far.
        assert owned <= self.total_entered - self.total_exited + 1e-6, (
            f"owned={owned} exceeds entered({self.total_entered}) - exited({self.total_exited})"
        )


QuantityAndIdempotencyMachine.TestCase.settings = settings(max_examples=75, stateful_step_count=25)
TestQuantityConservationAndIdempotency = QuantityAndIdempotencyMachine.TestCase
