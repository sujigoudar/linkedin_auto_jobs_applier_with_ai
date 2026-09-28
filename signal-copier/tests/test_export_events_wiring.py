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
the account's own stream an EXECUTION_APPLIED uses, so these two event
types never share an export_sequence counter; tests below that care
specifically about the EXECUTION_APPLIED (or the per-account sequence)
filter `list_undelivered_export_events()` down to that event_type/
stream rather than assuming it's the only thing in the outbox."""
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

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=2.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.FILLED

    undelivered = store.list_undelivered_export_events()
    executions = _by_type(undelivered, EventType.EXECUTION_APPLIED)
    receipts = _by_type(undelivered, EventType.SOURCE_RECEIPT)
    assert len(executions) == 1
    assert len(receipts) == 1  # the signal's own SOURCE_RECEIPT, exported before routing

    envelope = executions[0]
    assert envelope.source_stream == "signal-copier:acct1"
    assert envelope.evidence_class == EvidenceClass.INTERNAL_PAPER
    assert envelope.payload["side"] == "buy"
    assert envelope.payload["filled_quantity"] == "2.0"
    assert envelope.payload["broker_order_id"] == results[0].broker_order_id

    assert receipts[0].source_stream == "signal-copier:source:tradingview"
    assert receipts[0].payload["quantity"] == "2.0"


@pytest.mark.asyncio
async def test_a_rejected_order_produces_no_execution_applied_event(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper", enabled=False)}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"], symbol_filter=["OTHER"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)
    assert results == []
    undelivered = store.list_undelivered_export_events()
    assert _by_type(undelivered, EventType.EXECUTION_APPLIED) == []
    # The signal's own SOURCE_RECEIPT still exports -- it records what
    # was recommended, independent of whether routing ever sent it
    # anywhere.
    assert len(_by_type(undelivered, EventType.SOURCE_RECEIPT)) == 1


@pytest.mark.asyncio
async def test_a_missing_broker_error_produces_no_execution_applied_event(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="nonexistent")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)
    assert results[0].status == OrderStatus.ERROR
    undelivered = store.list_undelivered_export_events()
    assert _by_type(undelivered, EventType.EXECUTION_APPLIED) == []
    assert len(_by_type(undelivered, EventType.SOURCE_RECEIPT)) == 1


@pytest.mark.asyncio
async def test_two_fills_on_the_same_account_get_increasing_export_sequences(store):
    paper = PaperBroker()
    accounts = {"acct1": DestinationAccount(account_id="acct1", broker="paper")}
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])],
        accounts=accounts,
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": paper}, store=store)

    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0))
    await engine.handle_signal(Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0))

    undelivered = store.list_undelivered_export_events()
    executions = _by_type(undelivered, EventType.EXECUTION_APPLIED)
    receipts = _by_type(undelivered, EventType.SOURCE_RECEIPT)
    assert sorted(e.export_sequence for e in executions) == [0, 1]  # own stream: signal-copier:acct1
    assert sorted(e.export_sequence for e in receipts) == [0, 1]  # own stream: signal-copier:source:tradingview


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
    signal = Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)
    expected_execution_event_id = f"execution-applied:acct1:{results[0].broker_order_id}"
    expected_receipt_event_id = f"source-receipt:{signal.id}"

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            # One result per event in the batch -- the outbox now holds
            # both this signal's own SOURCE_RECEIPT and the resulting
            # EXECUTION_APPLIED (see this module's own docstring).
            return {
                "results": [
                    {"status": "applied", "event_id": expected_execution_event_id},
                    {"status": "applied", "event_id": expected_receipt_event_id},
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

    assert set(outcome.delivered_event_ids) == {expected_execution_event_id, expected_receipt_event_id}
    assert b"execution_applied" in captured["content"]
    assert b"source_receipt" in captured["content"]
    assert store.list_undelivered_export_events() == []
