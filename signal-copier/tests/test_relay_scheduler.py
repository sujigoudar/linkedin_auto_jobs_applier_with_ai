"""app/relay_scheduler.py -- same start/stop lifecycle contract as
PriceMonitor/OrderReconciler/ProviderScout (tests/test_price_monitor.py's
own test_stop_cancels_the_running_loop_task is the template), plus a
real short-interval pass proving it actually calls app/relay_worker.py's
run_once and records last_success_at."""
import asyncio

import pytest
from signal_platform_contracts import (
    Environment,
    EventEnvelope,
    EventType,
    EvidenceClass,
    ExecutionAppliedPayload,
    InstrumentIdentity,
    PrivateAccountIdentity,
    build_subject,
    compute_payload_hash,
)

from app.db import SignalStore
from app.relay_scheduler import RelayScheduler


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _envelope():
    instrument = InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"), instrument=instrument, side="buy",
        filled_quantity="1", filled_price="150.00", broker="paper", broker_order_id="paper-1",
    )
    payload_dict = payload.model_dump(mode="json")
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED, event_id="evt-sched-1", producer_id="test",
        source_stream="signal-copier:acct1", export_sequence=0,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=instrument),
        event_time=now, effective_time=now, availability_time=now, receipt_time=now,
        environment=Environment.LOCAL_SIM, evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict), payload=payload_dict,
    )


@pytest.mark.asyncio
async def test_stop_cancels_the_running_loop_task(store):
    scheduler = RelayScheduler(store=store, interval_seconds=60.0)
    await scheduler.start()
    assert scheduler._task is not None
    await scheduler.stop()
    assert scheduler._task.cancelled() or scheduler._task.done()


@pytest.mark.asyncio
async def test_start_is_idempotent(store):
    scheduler = RelayScheduler(store=store, interval_seconds=60.0)
    await scheduler.start()
    first_task = scheduler._task
    await scheduler.start()
    assert scheduler._task is first_task
    await scheduler.stop()


@pytest.mark.asyncio
async def test_a_real_pass_calls_run_once_and_records_success(store, monkeypatch):
    store.append_export_event(_envelope())

    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"results": [{"status": "applied", "event_id": "evt-sched-1"}]}

        return _Resp()

    monkeypatch.setattr("app.relay_worker.config.RELAY_INGRESS_URL", "https://commercial.example/x")
    monkeypatch.setattr("app.relay_worker.httpx.post", fake_post)

    scheduler = RelayScheduler(store=store, interval_seconds=0.01)
    assert scheduler.last_success_at is None
    await scheduler.start()
    try:
        for _ in range(200):
            if scheduler.last_success_at is not None:
                break
            await asyncio.sleep(0.01)
    finally:
        await scheduler.stop()

    assert scheduler.last_success_at is not None
    assert calls == ["https://commercial.example/x"]
    assert store.list_undelivered_export_events() == []


def test_log_result_logs_distinctly_for_transient_and_terminal_parks(caplog):
    """Track 42: `_log_result`'s own per-pass summary logging for
    `RelayIngestResult.transiently_parked_event_ids`/
    `terminally_parked_event_ids` -- a transient park logs at WARNING
    (normal, expected to resolve on its own), a terminal one at ERROR
    (needs real operator attention, see GET /health's own
    `terminally_parked_export_event_count`)."""
    from app.relay_worker import RelayIngestResult

    result = RelayIngestResult(
        delivered_event_ids=[],
        unregistered_stream_event_ids=[],
        integrity_error_event_ids=[],
        transiently_parked_event_ids=["evt-transient-1"],
        terminally_parked_event_ids=["evt-terminal-1"],
    )
    with caplog.at_level("WARNING", logger="app.relay_scheduler"):
        RelayScheduler._log_result(result)

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert any("transiently parked" in r.message for r in warnings)
    assert any("terminally parked" in r.message for r in errors)
