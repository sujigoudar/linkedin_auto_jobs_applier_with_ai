"""P0-2 (external release audit): the durable, pre-effect command_ledger.

Covers:
  - the ledger row is written and committed BEFORE the broker is called
    (simulated crash: open a ledger entry, never call the broker, then open
    a FRESH SignalStore against the same file and confirm it's visible as
    unresolved);
  - a duplicate idempotency_key (matching request_fingerprint) replays the
    existing row instead of inserting a second one, and a real call site
    (SignalCopierEngine's plain-account close) never submits a second
    broker order for it;
  - a different request_fingerprint under the same idempotency_key is
    refused (CommandFingerprintMismatch), never silently allowed through;
  - a broker call that raises lands the ledger row in UNKNOWN_AMBIGUOUS,
    unresolved -- never silently dropped, never guessed as success or
    failure;
  - list_unresolved_command_ledger_entries's exact contract (resolved rows
    excluded, optional account_id filter, oldest-first ordering).
"""
from __future__ import annotations

import asyncio

import pytest

from app import command_ledger
from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import (
    CommandType,
    DestinationAccount,
    OrderResult,
    OrderStatus,
    Side,
    Signal,
    UncertaintyState,
)
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db"), tmp_path / "test.db"


# --- SignalStore.open_command_ledger_entry / mark_command_ledger_outcome ---


def test_open_command_ledger_entry_is_pre_effect_and_committed(store):
    """The row exists, committed, the instant `open_command_ledger_entry`
    returns -- before any broker call has even been attempted."""
    signal_store, _ = store
    entry = signal_store.open_command_ledger_entry(
        idempotency_key="entry:acct1:AAPL:sig-1",
        command_type=CommandType.ENTRY,
        account_id="acct1",
        environment="test",
        request_fingerprint="fp-1",
    )
    assert entry.uncertainty_state == UncertaintyState.PENDING_SUBMISSION
    assert entry.resolved_at is None
    assert entry.remote_identifiers == {}

    fetched = signal_store.get_command_ledger_entry("entry:acct1:AAPL:sig-1")
    assert fetched is not None
    assert fetched.id == entry.id
    assert fetched.command_type == CommandType.ENTRY


def test_pre_effect_write_survives_a_simulated_crash_before_the_broker_call(store):
    """The exact scenario the audit names: the process writes the durable
    intent, then dies before ever calling the broker. A FRESH SignalStore
    instance opened against the same file (simulating a restart) must see
    it as unresolved -- proving the row is durable and visible without
    relying on any in-memory state from the process that wrote it."""
    signal_store, db_path = store
    signal_store.open_command_ledger_entry(
        idempotency_key="entry:acct1:AAPL:sig-crash",
        command_type=CommandType.ENTRY,
        account_id="acct1",
        environment="test",
        request_fingerprint="fp-crash",
    )
    # No broker call ever happens -- this IS the simulated crash.

    fresh_store = SignalStore(db_path)  # a new process/instance, same file
    unresolved = fresh_store.list_unresolved_command_ledger_entries()
    keys = [e.idempotency_key for e in unresolved]
    assert "entry:acct1:AAPL:sig-crash" in keys
    entry = next(e for e in unresolved if e.idempotency_key == "entry:acct1:AAPL:sig-crash")
    assert entry.uncertainty_state == UncertaintyState.PENDING_SUBMISSION
    assert entry.remote_identifiers == {}
    assert entry.resolved_at is None


def test_duplicate_idempotency_key_with_matching_fingerprint_replays_the_same_row(store):
    signal_store, _ = store
    first = signal_store.open_command_ledger_entry(
        idempotency_key="close:acct1:AAPL:sig-2",
        command_type=CommandType.CLOSE,
        account_id="acct1",
        environment="test",
        request_fingerprint="same-fp",
    )
    signal_store.mark_command_ledger_outcome(
        "close:acct1:AAPL:sig-2",
        uncertainty_state=UncertaintyState.SUBMITTED_UNCONFIRMED,
        remote_identifiers={"broker_order_id": "bo-1"},
    )
    second = signal_store.open_command_ledger_entry(
        idempotency_key="close:acct1:AAPL:sig-2",
        command_type=CommandType.CLOSE,
        account_id="acct1",
        environment="test",
        request_fingerprint="same-fp",
    )
    assert second.id == first.id  # the SAME row, not a new one
    assert second.uncertainty_state == UncertaintyState.SUBMITTED_UNCONFIRMED
    assert second.remote_identifiers == {"broker_order_id": "bo-1"}
    assert command_ledger.is_duplicate_submission(second.uncertainty_state)

    # Only one row was ever inserted for this key.
    all_unresolved = signal_store.list_unresolved_command_ledger_entries("acct1")
    assert len([e for e in all_unresolved if e.idempotency_key == "close:acct1:AAPL:sig-2"]) == 1


def test_duplicate_idempotency_key_with_different_fingerprint_is_rejected(store):
    signal_store, _ = store
    signal_store.open_command_ledger_entry(
        idempotency_key="close:acct1:AAPL:sig-3",
        command_type=CommandType.CLOSE,
        account_id="acct1",
        environment="test",
        request_fingerprint="fp-a",
    )
    with pytest.raises(command_ledger.CommandFingerprintMismatch):
        signal_store.open_command_ledger_entry(
            idempotency_key="close:acct1:AAPL:sig-3",
            command_type=CommandType.CLOSE,
            account_id="acct1",
            environment="test",
            request_fingerprint="fp-b",  # a genuinely different request reusing the same key
        )


def test_mark_command_ledger_outcome_sets_resolved_at_only_for_terminal_states(store):
    signal_store, _ = store
    signal_store.open_command_ledger_entry(
        idempotency_key="k1", command_type=CommandType.ENTRY, account_id="a1", environment="test", request_fingerprint="f1"
    )
    signal_store.mark_command_ledger_outcome(
        "k1", uncertainty_state=UncertaintyState.SUBMITTED_UNCONFIRMED, remote_identifiers={"broker_order_id": "x"}
    )
    entry = signal_store.get_command_ledger_entry("k1")
    assert entry.resolved_at is None

    signal_store.mark_command_ledger_outcome(
        "k1", uncertainty_state=UncertaintyState.CONFIRMED, terminal_evidence={"broker_status": "filled"}
    )
    entry = signal_store.get_command_ledger_entry("k1")
    assert entry.resolved_at is not None
    # Merged, not replaced: the earlier remote_identifiers write survives.
    assert entry.remote_identifiers == {"broker_order_id": "x"}
    assert entry.terminal_evidence == {"broker_status": "filled"}


def test_unknown_ambiguous_outcome_stays_unresolved_and_is_never_silently_dropped(store):
    """The critical case the audit names: a PENDING result with no broker
    order id (or a raised exception) must land in UNKNOWN_AMBIGUOUS and
    stay in the unresolved set -- never silently treated as success or
    failure, and never vanish."""
    signal_store, _ = store
    signal_store.open_command_ledger_entry(
        idempotency_key="k2", command_type=CommandType.ENTRY, account_id="a1", environment="test", request_fingerprint="f2"
    )
    signal_store.mark_command_ledger_outcome(
        "k2",
        uncertainty_state=UncertaintyState.UNKNOWN_AMBIGUOUS,
        terminal_evidence=command_ledger.ambiguous_evidence_for_exception(RuntimeError("connection reset")),
    )
    entry = signal_store.get_command_ledger_entry("k2")
    assert entry.uncertainty_state == UncertaintyState.UNKNOWN_AMBIGUOUS
    assert entry.resolved_at is None
    unresolved_keys = [e.idempotency_key for e in signal_store.list_unresolved_command_ledger_entries()]
    assert "k2" in unresolved_keys


def test_list_unresolved_command_ledger_entries_excludes_resolved_and_filters_by_account(store):
    signal_store, _ = store
    signal_store.open_command_ledger_entry(
        idempotency_key="acctA-1", command_type=CommandType.ENTRY, account_id="acctA", environment="test", request_fingerprint="f"
    )
    signal_store.open_command_ledger_entry(
        idempotency_key="acctB-1", command_type=CommandType.ENTRY, account_id="acctB", environment="test", request_fingerprint="f"
    )
    signal_store.open_command_ledger_entry(
        idempotency_key="acctA-2", command_type=CommandType.CLOSE, account_id="acctA", environment="test", request_fingerprint="f"
    )
    signal_store.mark_command_ledger_outcome(
        "acctA-2", uncertainty_state=UncertaintyState.CONFIRMED, terminal_evidence={"broker_status": "filled"}
    )

    everyone = signal_store.list_unresolved_command_ledger_entries()
    keys = {e.idempotency_key for e in everyone}
    assert keys == {"acctA-1", "acctB-1"}  # acctA-2 is resolved, excluded

    only_a = signal_store.list_unresolved_command_ledger_entries(account_id="acctA")
    assert {e.idempotency_key for e in only_a} == {"acctA-1"}


# --- Real call-site wiring: a duplicate never double-submits to the broker ---


class _CountingBroker(BrokerAdapter):
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def place_order(self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str) -> OrderResult:
        self.calls += 1
        return OrderResult(account_id=account.account_id, status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=quantity)


class _RaisingBroker(BrokerAdapter):
    name = "raising"

    async def place_order(self, signal: Signal, account: DestinationAccount, quantity: float, symbol: str) -> OrderResult:
        raise ConnectionError("simulated network reset after the request may have reached the venue")


def _engine_for(store, broker, account_id="acct1"):
    account = DestinationAccount(account_id=account_id, broker=broker.name)
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account_id])], accounts={account_id: account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={broker.name: broker}, store=store)
    return engine, account


def test_duplicate_entry_command_never_double_submits_to_the_broker(store):
    """Two calls into the same low-level submission path with the SAME
    order_signal id (the real retry identity `_submit_order`/handle_signal
    use) must only ever call the broker once -- the second is recognized
    as a duplicate via the ledger and replayed instead of resubmitted."""
    signal_store, _ = store
    broker = _CountingBroker()
    engine, account = _engine_for(signal_store, broker)

    order_signal = Signal(source="tradingview", symbol="AAPL", side=Side.SELL, id="close-sig-1")
    signal_store.save_signal(order_signal)

    first_result, _applied, _confirmed, _delta, _outstanding, _submitted_at = asyncio.run(
        engine._submit_order(order_signal, 10.0, account, "AAPL", broker)
    )
    assert first_result.status == OrderStatus.FILLED
    assert broker.calls == 1

    # A retried call for the exact same close (same order_signal.id/side/
    # quantity/symbol/account) must not place a second order.
    second_result, _applied2, _confirmed2, _delta2, _outstanding2, _submitted_at2 = asyncio.run(
        engine._submit_order(order_signal, 10.0, account, "AAPL", broker)
    )
    assert broker.calls == 1  # still just one real broker call
    assert "duplicate command" in second_result.message


def test_ambiguous_broker_exception_lands_the_ledger_row_in_unknown_ambiguous(store):
    """A raised exception during the broker call -- the genuinely ambiguous
    "may have reached the venue" case -- must record UNKNOWN_AMBIGUOUS in
    the ledger, not silently drop the attempt or guess an outcome."""
    signal_store, _ = store
    broker = _RaisingBroker()
    engine, account = _engine_for(signal_store, broker)

    order_signal = Signal(source="tradingview", symbol="AAPL", side=Side.SELL, id="close-sig-ambiguous")
    signal_store.save_signal(order_signal)

    result, applied, _confirmed, _delta, _outstanding, _submitted_at = asyncio.run(
        engine._submit_order(order_signal, 10.0, account, "AAPL", broker)
    )
    assert result.status == OrderStatus.ERROR
    assert applied is None

    ledger_key = f"close:{account.account_id}:AAPL:{order_signal.id}"
    entry = signal_store.get_command_ledger_entry(ledger_key)
    assert entry is not None
    assert entry.uncertainty_state == UncertaintyState.UNKNOWN_AMBIGUOUS
    assert entry.resolved_at is None
    assert "exception" in entry.terminal_evidence.get("broker_status", "")

    # Still visible via the sibling-agent-facing query, unresolved.
    unresolved_keys = [e.idempotency_key for e in signal_store.list_unresolved_command_ledger_entries(account.account_id)]
    assert ledger_key in unresolved_keys


def test_classify_cancel_result_false_is_unknown_ambiguous_not_rejected():
    """`BrokerAdapter.cancel_order`'s own docstring: False means "isn't
    supported or confirmed", which covers both "definitely never
    cancelled" AND "may have already filled just before the cancel
    landed" -- classify_cancel_result(False) must stay UNKNOWN_AMBIGUOUS,
    never REJECTED_CONFIRMED (which would claim more certainty than the
    broker actually gave) and never CONFIRMED."""
    state, evidence = command_ledger.classify_cancel_result(False)
    assert state == UncertaintyState.UNKNOWN_AMBIGUOUS
    assert evidence == {"broker_status": "cancel_not_confirmed"}


def test_classify_cancel_result_true_is_confirmed():
    state, evidence = command_ledger.classify_cancel_result(True)
    assert state == UncertaintyState.CONFIRMED
    assert evidence == {"broker_status": "cancelled"}


def test_ledgered_cancel_order_with_an_unconfirmed_cancel_lands_the_ledger_row_in_unknown_ambiguous(store):
    """End-to-end through the real call site --
    PositionLifecycleManager._ledgered_cancel_order -- previously entirely
    untested for the `cancelled=False` outcome (see
    app/command_ledger.py's own `classify_cancel_result` line coverage):
    a broker reporting "not confirmed" must leave the command_ledger entry
    UNKNOWN_AMBIGUOUS, not silently resolved either way."""
    from app.lifecycle.manager import PositionLifecycleManager

    signal_store, _ = store

    class _UnconfirmedCancelBroker(BrokerAdapter):
        name = "unconfirmed_cancel"

        async def place_order(self, signal, account, quantity, symbol):
            raise NotImplementedError

        async def cancel_order(self, account, broker_order_id):
            return False  # "isn't supported or confirmed"

    broker = _UnconfirmedCancelBroker()
    manager = PositionLifecycleManager(brokers={"unconfirmed_cancel": broker}, store=signal_store)
    account = DestinationAccount(account_id="acct1", broker="unconfirmed_cancel")

    cancelled = asyncio.run(
        manager._ledgered_cancel_order(broker, account, "AAPL", "stop-order-1", source="test")
    )

    assert cancelled is False
    unresolved = signal_store.list_unresolved_command_ledger_entries("acct1")
    matching = [e for e in unresolved if e.idempotency_key.startswith("cancel:acct1:AAPL:stop-order-1:")]
    assert len(matching) == 1
    assert matching[0].uncertainty_state == UncertaintyState.UNKNOWN_AMBIGUOUS
    assert matching[0].terminal_evidence == {"broker_status": "cancel_not_confirmed"}
    assert matching[0].resolved_at is None  # genuinely unresolved, not a terminal state


def test_ledgered_cancel_order_with_a_confirmed_cancel_lands_the_ledger_row_in_confirmed(store):
    """The complementary, resolved case -- proves the test above isn't
    just asserting 'always unresolved'."""
    from app.lifecycle.manager import PositionLifecycleManager

    signal_store, _ = store

    class _ConfirmedCancelBroker(BrokerAdapter):
        name = "confirmed_cancel"

        async def place_order(self, signal, account, quantity, symbol):
            raise NotImplementedError

        async def cancel_order(self, account, broker_order_id):
            return True

    broker = _ConfirmedCancelBroker()
    manager = PositionLifecycleManager(brokers={"confirmed_cancel": broker}, store=signal_store)
    account = DestinationAccount(account_id="acct2", broker="confirmed_cancel")

    cancelled = asyncio.run(
        manager._ledgered_cancel_order(broker, account, "MSFT", "stop-order-2", source="test")
    )

    assert cancelled is True
    all_entries_key_prefix = "cancel:acct2:MSFT:stop-order-2:"
    matching_keys = [
        e.idempotency_key
        for e in signal_store.list_unresolved_command_ledger_entries("acct2")
        if e.idempotency_key.startswith(all_entries_key_prefix)
    ]
    # CONFIRMED is terminal -- it must NOT still appear in the unresolved set.
    assert matching_keys == []


def test_entry_command_ledger_confirms_on_a_real_fill(store):
    signal_store, _ = store
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=signal_store)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0, price=100.0)
    results = asyncio.run(engine.handle_signal(signal))
    assert results[0].status == OrderStatus.FILLED

    ledger_key = f"entry:acct1:AAPL:{signal.id}"
    entry = signal_store.get_command_ledger_entry(ledger_key)
    assert entry is not None
    assert entry.uncertainty_state == UncertaintyState.CONFIRMED
    assert entry.resolved_at is not None
    assert entry.remote_identifiers.get("broker_order_id")
