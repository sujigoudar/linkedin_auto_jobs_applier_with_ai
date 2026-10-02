"""app/engine.py's own wiring of app/export_events.py -- the slice
named in slice 5's own commit message as "not yet built": a genuine
FILLED order from the engine must produce a real EXECUTION_APPLIED
export event in the outbox, ready for app/relay_worker.py to pick up,
and a REJECTED/ERROR result must not.

Since S12 step 5 "Portfolio Lab source feed" (a later slice than the
one this file's own docstring above describes), `_handle_signal` ALSO
exports a SOURCE_RECEIPT for every non-CLOSE signal, unconditionally,
right after `save_signal` -- BEFORE routing is even resolved. That
event lives on its own stream (`signal-copier:source:<source>`), never
the account's own stream an EXECUTION_APPLIED uses, so those two event
types never share an export_sequence counter; tests below that care
specifically about the EXECUTION_APPLIED (or the per-account sequence)
filter `list_undelivered_export_events()` down to that event_type/
stream rather than assuming it's the only thing in the outbox.

INT-027 "All permitted source outcomes reach research": `_handle_signal`
ALSO exports a real `ROUTING_ADMISSION_OUTCOME` for every real signal/
account outcome it reaches, correlated back to that same signal's own
SOURCE_RECEIPT via `RoutingAdmissionOutcomePayload.
originating_source_event_id`. It lives on the SAME
`signal-copier:source:<source>` stream the SOURCE_RECEIPT itself uses
(both are about the source's own recommendation, never a private
per-account ledger) -- so the two DO interleave on that one stream's
own export_sequence counter, unlike EXECUTION_APPLIED's separate
per-account stream."""
import pytest
from signal_platform_contracts import EventType, EvidenceClass

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _by_type(envelopes, event_type):
    return [e for e in envelopes if e.event_type == event_type]


@pytest.mark.asyncio
async def test_a_real_fill_produces_a_real_undelivered_export_event(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper", multiplier=1.0)}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=2.0, price=100.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    undelivered = store.list_undelivered_export_events()
    executions = _by_type(undelivered, EventType.EXECUTION_APPLIED)
    receipts = _by_type(undelivered, EventType.SOURCE_RECEIPT)
    outcomes = _by_type(undelivered, EventType.ROUTING_ADMISSION_OUTCOME)
    assert len(executions) == 1
    assert len(receipts) == 1  # the signal's own SOURCE_RECEIPT, exported before routing
    assert len(outcomes) == 1  # INT-027: the real routing outcome, exported once routing resolves

    envelope = executions[0]
    assert envelope.source_stream == "signal-copier:acct1"
    assert envelope.evidence_class == EvidenceClass.INTERNAL_PAPER
    assert envelope.payload["side"] == "buy"
    assert envelope.payload["filled_quantity"] == "2.0"
    assert envelope.payload["broker_order_id"] == results[0].broker_order_id

    assert receipts[0].source_stream == "signal-copier:source:tradingview"
    assert receipts[0].payload["quantity"] == "2.0"

    outcome_envelope = outcomes[0]
    assert outcome_envelope.source_stream == "signal-copier:source:tradingview"
    assert outcome_envelope.payload["outcome"] == "admitted_filled"
    assert outcome_envelope.payload["originating_source_event_id"] == receipts[0].event_id
    assert outcome_envelope.payload["account"]["account_id"] == "acct1"


@pytest.mark.asyncio
async def test_a_rejected_order_produces_no_execution_applied_event(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper", enabled=False)}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"], symbol_filter=["OTHER"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0)
    results = await engine.handle_signal(signal)
    assert results == []
    undelivered = store.list_undelivered_export_events()
    assert _by_type(undelivered, EventType.EXECUTION_APPLIED) == []
    # The signal's own SOURCE_RECEIPT still exports -- it records what
    # was recommended, independent of whether routing ever sent it
    # anywhere.
    assert len(_by_type(undelivered, EventType.SOURCE_RECEIPT)) == 1
    # INT-027: no destination even matched this symbol's routing rule --
    # a real, honest "not_routed" outcome, still correlated to the
    # receipt above.
    outcomes = _by_type(undelivered, EventType.ROUTING_ADMISSION_OUTCOME)
    assert len(outcomes) == 1
    assert outcomes[0].payload["outcome"] == "not_routed"
    assert outcomes[0].payload["account"] is None


@pytest.mark.asyncio
async def test_a_missing_broker_error_produces_no_execution_applied_event(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="nonexistent")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.ERROR
    undelivered = store.list_undelivered_export_events()
    assert _by_type(undelivered, EventType.EXECUTION_APPLIED) == []
    assert len(_by_type(undelivered, EventType.SOURCE_RECEIPT)) == 1
    outcomes = _by_type(undelivered, EventType.ROUTING_ADMISSION_OUTCOME)
    assert len(outcomes) == 1
    assert outcomes[0].payload["outcome"] == "error"
    assert outcomes[0].payload["account"]["account_id"] == "acct1"


@pytest.mark.asyncio
async def test_two_fills_on_the_same_account_get_increasing_export_sequences(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0))
    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0))

    undelivered = store.list_undelivered_export_events()
    executions = _by_type(undelivered, EventType.EXECUTION_APPLIED)
    receipts = _by_type(undelivered, EventType.SOURCE_RECEIPT)
    outcomes = _by_type(undelivered, EventType.ROUTING_ADMISSION_OUTCOME)
    assert sorted(e.export_sequence for e in executions) == [0, 1]  # own stream: signal-copier:acct1
    # SOURCE_RECEIPT and ROUTING_ADMISSION_OUTCOME share ONE stream
    # (signal-copier:source:tradingview) and so share ONE sequence
    # counter -- two signals each producing one of each interleaves to
    # [0, 1, 2, 3] across the four rows, never [0, 1] for either alone.
    assert sorted(e.export_sequence for e in receipts + outcomes) == [0, 1, 2, 3]
    assert len(receipts) == 2
    assert len(outcomes) == 2


@pytest.mark.asyncio
async def test_the_produced_envelope_is_accepted_end_to_end_by_the_real_relay_worker(store):
    """Full pipeline proof: engine fill -> outbox -> relay_worker.run_once
    -> (simulated) commercial ingress -> marked delivered. Uses a fake
    http_post that itself calls the real
    signal-portfolio-commercial ingest path is out of scope for this repo
    boundary (separate package); this proves signal-copier's own half
    produces a batch the documented ingress contract actually accepts."""
    from app.relay_worker import run_once

    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)
    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0)
    results = await engine.handle_signal(signal)
    expected_execution_event_id = f"execution-applied:acct1:{results[0].broker_order_id}"
    expected_receipt_event_id = f"source-receipt:{signal.id}"
    expected_outcome_event_id = f"routing-outcome:{signal.id}:acct1"

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            # One result per event in the batch -- the outbox now holds
            # this signal's own SOURCE_RECEIPT, the resulting
            # EXECUTION_APPLIED, and its own ROUTING_ADMISSION_OUTCOME
            # (see this module's own docstring).
            return {
                "results": [
                    {"status": "applied", "event_id": expected_execution_event_id},
                    {"status": "applied", "event_id": expected_receipt_event_id},
                    {"status": "applied", "event_id": expected_outcome_event_id},
                ]
            }

    captured = {}

    def fake_post(url, *, content, headers):
        captured["content"] = content
        return _FakeResponse()

    outcome = run_once(
        store, ingress_url="https://commercial.example/internal/relay/ingest-batch",
        signing_secret="s", http_post=fake_post,
    )

    assert set(outcome.delivered_event_ids) == {
        expected_execution_event_id, expected_receipt_event_id, expected_outcome_event_id,
    }
    assert b"execution_applied" in captured["content"]
    assert b"source_receipt" in captured["content"]
    assert b"routing_admission_outcome" in captured["content"]
    assert store.list_undelivered_export_events() == []
