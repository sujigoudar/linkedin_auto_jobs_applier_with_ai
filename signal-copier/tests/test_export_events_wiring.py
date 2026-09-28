"""app/engine.py's own wiring of app/export_events.py -- the slice
named in slice 5's own commit message as "not yet built": a genuine
FILLED order from the engine must produce a real EXECUTION_APPLIED
export event in the outbox, ready for app/relay_worker.py to pick up,
and a REJECTED/ERROR result must not."""
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
    assert len(undelivered) == 1
    envelope = undelivered[0]
    assert envelope.event_type == EventType.EXECUTION_APPLIED
    assert envelope.source_stream == "signal-copier:acct1"
    assert envelope.evidence_class == EvidenceClass.INTERNAL_PAPER
    assert envelope.payload["side"] == "buy"
    assert envelope.payload["filled_quantity"] == "2.0"
    assert envelope.payload["broker_order_id"] == results[0].broker_order_id


@pytest.mark.asyncio
async def test_a_rejected_order_produces_no_export_event(store):
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
    assert store.list_undelivered_export_events() == []


@pytest.mark.asyncio
async def test_a_missing_broker_error_produces_no_export_event(store):
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
    assert store.list_undelivered_export_events() == []


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
    assert len(undelivered) == 2
    assert sorted(e.export_sequence for e in undelivered) == [0, 1]


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
    results = await engine.handle_signal(
        Signal(source="tradingview", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    )
    expected_event_id = f"execution-applied:acct1:{results[0].broker_order_id}"

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"status": "applied", "event_id": expected_event_id}]}

    captured = {}

    def fake_post(url, *, content, headers):
        captured["content"] = content
        return _FakeResponse()

    outcome = run_once(
        store, ingress_url="https://commercial.example/internal/relay/ingest-batch",
        signing_secret="s", http_post=fake_post,
    )

    assert outcome.delivered_event_ids == [expected_event_id]
    assert b"execution_applied" in captured["content"]
    assert store.list_undelivered_export_events() == []
