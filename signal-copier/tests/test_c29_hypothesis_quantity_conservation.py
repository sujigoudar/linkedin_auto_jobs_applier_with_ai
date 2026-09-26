"""C29: a Hypothesis stateful test supplementing (never replacing) the
existing hand-written finite-case regression suite for the same
invariant -- generated random sequences of entries and partial exits are
far more likely to stumble on an untested interleaving than any fixed
set of examples a person writes by hand.

Invariant under test: after every action, the broker's own book, the
tracked SignalStore position, and PositionLifecycleManager's
confirmed_owned_quantity must always agree -- exactly the "one execution
identity, one local delta" property EXE-01 through EXE-12 exist to
protect. Drives entries through the real SignalCopierEngine.handle_signal
(the only real caller of on_entry_fill, which deliberately leaves the
SignalStore write to its caller -- see app/engine.py's
_handle_managed_entry) and partial exits through
PositionLifecycleManager.request_exit directly (the same call a real
target/trailing/provider-close would make; there's no public
engine-level "close N shares" signal). This stays within the
synchronous-fill happy path (PaperBroker fills instantly); it does not
model PENDING/reconciliation timing, which the hand-written suite
already covers extensively.
"""
import asyncio
import tempfile
from pathlib import Path

from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule
from hypothesis import strategies as st

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule

ACCOUNT_ID = "acct1"
SYMBOL = "TEST"


def _run(coro):
    return asyncio.run(coro)


class QuantityConservationMachine(RuleBasedStateMachine):
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
        self.entered = False

    def teardown(self):
        self._tmpdir.cleanup()

    def _lifecycle_open(self) -> bool:
        lifecycle = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        return lifecycle is not None and not lifecycle.closed

    @precondition(lambda self: not self._lifecycle_open())
    @rule(quantity=st.integers(min_value=1, max_value=100))
    def enter(self, quantity):
        # A prior position may have fully closed (flat again) -- a fresh
        # entry into the same symbol must still work, same as it would in
        # production once the account is flat again.
        signal = Signal(source="test", symbol=SYMBOL, side=Side.BUY, quantity=float(quantity), stop_loss=1.0)
        _run(self.engine.handle_signal(signal))
        self.entered = True

    @precondition(
        lambda self: self.entered
        and self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL) is not None
        and not self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL).closed
    )
    @rule(fraction=st.floats(min_value=0.01, max_value=1.0, allow_nan=False))
    def partial_exit(self, fraction):
        available = self.manager.arbiter.available_to_sell(ACCOUNT_ID, SYMBOL)
        quantity = int(available * fraction)
        if quantity <= 0:
            return
        _run(self.manager.request_exit(self.account, SYMBOL, float(quantity), source="test"))

    @invariant()
    def owned_quantity_agrees_everywhere(self):
        if not self.entered:
            return
        lifecycle = self.manager.get_lifecycle(ACCOUNT_ID, SYMBOL)
        if lifecycle is None:
            return
        broker_owned = self.broker.positions.get(ACCOUNT_ID, {}).get(SYMBOL, 0.0)
        store_owned = self.store.get_position(ACCOUNT_ID, SYMBOL)
        assert lifecycle.confirmed_owned_quantity == broker_owned == store_owned, (
            f"disagreement: lifecycle={lifecycle.confirmed_owned_quantity} "
            f"broker={broker_owned} store={store_owned}"
        )


QuantityConservationMachine.TestCase.settings = settings(max_examples=50, stateful_step_count=15)
TestQuantityConservation = QuantityConservationMachine.TestCase
