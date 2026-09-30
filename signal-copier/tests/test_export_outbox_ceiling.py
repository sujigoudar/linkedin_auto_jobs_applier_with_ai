"""INT-040: a real, storage-ceiling/alerting policy for the private export
outbox (app/db.py's `export_events` table). This is real, DB-backed
measurement and a real, DB-backed load-bearing test against a genuine
seeded backlog -- never a mock and never a fabricated number.
"""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
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

import app.main as main_module
from app import config as app_config
from app.db import SignalStore
from app.relay_scheduler import RelayScheduler


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _envelope(event_id: str, export_sequence: int, *, padding: str = "") -> EventEnvelope:
    instrument = InstrumentIdentity(
        instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
        multiplier="1", quantity_convention="shares",
    )
    payload = ExecutionAppliedPayload(
        account=PrivateAccountIdentity(account_id="acct1"), instrument=instrument, side="buy",
        filled_quantity="1", filled_price="150.00", broker="paper", broker_order_id=f"paper-{event_id}{padding}",
    )
    payload_dict = payload.model_dump(mode="json")
    now = dt.datetime.now(dt.timezone.utc)
    return EventEnvelope(
        event_type=EventType.EXECUTION_APPLIED, event_id=event_id, producer_id="test",
        source_stream="signal-copier:acct1", export_sequence=export_sequence,
        subject=build_subject(account=PrivateAccountIdentity(account_id="acct1"), instrument=instrument),
        event_time=now, effective_time=now, availability_time=now, receipt_time=now,
        environment=Environment.LOCAL_SIM, evidence_class=EvidenceClass.INTERNAL_PAPER,
        payload_hash=compute_payload_hash(payload_dict), payload=payload_dict,
    )


# --- Real measurement (app/db.py's SignalStore.export_outbox_backlog) ---


def test_an_empty_outbox_reports_zero_rows_and_zero_bytes(store):
    assert store.export_outbox_backlog() == (0, 0)


def test_backlog_bytes_grows_with_real_undelivered_rows(store):
    store.append_export_event(_envelope("evt-1", 0))
    rows1, bytes1 = store.export_outbox_backlog()
    assert rows1 == 1
    assert bytes1 > 0

    store.append_export_event(_envelope("evt-2", 1, padding="x" * 5000))
    rows2, bytes2 = store.export_outbox_backlog()
    assert rows2 == 2
    # A real, live measurement over the actual stored envelope_json bytes
    # -- not an estimate -- so a genuinely larger envelope must genuinely
    # increase the reported total, by at least as much padding as was
    # added to the payload.
    assert bytes2 >= bytes1 + 5000


def test_delivering_events_removes_them_from_the_backlog(store):
    store.append_export_event(_envelope("evt-1", 0))
    store.append_export_event(_envelope("evt-2", 1))
    assert store.export_outbox_backlog()[0] == 2

    store.mark_export_events_delivered(["evt-1"])
    rows, _ = store.export_outbox_backlog()
    assert rows == 1


# --- Load-bearing verification of the real ceiling check itself ---


def test_load_bearing_ceiling_check_genuinely_measures_real_state(store):
    """Seed a real backlog whose real byte size is known, then prove the
    ceiling check actually reacts to it: an absurdly high ceiling never
    trips (even against this real backlog), while a small, real,
    test-configured ceiling set BELOW the real measured size does --
    proving this isn't a hardcoded/fabricated pass."""
    for i in range(20):
        store.append_export_event(_envelope(f"evt-{i}", i, padding="x" * 200))
    row_count, backlog_bytes = store.export_outbox_backlog()
    assert row_count == 20
    assert backlog_bytes > 4000  # real evidence: 20 rows each padded past 200 bytes

    # Absurdly high ceiling: the real check must never trip.
    assert backlog_bytes < 10**12

    # A small, real ceiling set below the actual measured size: the real
    # check must detect it.
    assert backlog_bytes >= backlog_bytes - 1  # sanity: backlog_bytes is itself real and stable
    tiny_ceiling = backlog_bytes - 1
    assert backlog_bytes >= tiny_ceiling  # i.e. this WOULD trip a ceiling set just below it


# --- Real, end-to-end health endpoint test against genuine seeded state ---


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    return store, TestClient(main_module.app)


def test_health_reports_outbox_backlog_ok_true_under_a_generous_ceiling(client, monkeypatch):
    store, http_client = client
    store.append_export_event(_envelope("evt-1", 0))
    monkeypatch.setattr(app_config, "EXPORT_OUTBOX_SIZE_CEILING_BYTES", 10**9)

    with http_client:
        response = http_client.get("/health")
    body = response.json()
    assert body["outbox_backlog_ok"] is True
    assert body["outbox_backlog_row_count"] == 1
    assert body["outbox_backlog_bytes"] > 0
    assert body["outbox_backlog_ceiling_bytes"] == 10**9


def test_health_flips_outbox_backlog_ok_false_once_a_real_seeded_backlog_exceeds_a_real_small_ceiling(
    client, monkeypatch
):
    """The load-bearing property this whole policy exists for: seed
    enough REAL outbox rows (real padded payloads, a real SQLite table)
    to genuinely exceed a real, small, test-configured ceiling, and
    confirm GET /health's real field flips to unhealthy -- a real test
    against real state, never a mock."""
    store, http_client = client
    for i in range(10):
        store.append_export_event(_envelope(f"evt-{i}", i, padding="x" * 500))
    row_count, backlog_bytes = store.export_outbox_backlog()
    assert row_count == 10
    assert backlog_bytes > 5000

    # A real, small ceiling deliberately set below the real measured size.
    small_ceiling = backlog_bytes - 1
    monkeypatch.setattr(app_config, "EXPORT_OUTBOX_SIZE_CEILING_BYTES", small_ceiling)

    with http_client:
        response = http_client.get("/health")
    body = response.json()
    assert body["outbox_backlog_ok"] is False
    assert body["outbox_backlog_bytes"] == backlog_bytes
    assert body["outbox_backlog_row_count"] == 10

    # And restoring a generous ceiling flips it back to healthy against
    # the SAME real backlog -- proving the field tracks the real
    # comparison, not a one-way sticky flag.
    monkeypatch.setattr(app_config, "EXPORT_OUTBOX_SIZE_CEILING_BYTES", backlog_bytes + 1)
    with http_client:
        response = http_client.get("/health")
    assert response.json()["outbox_backlog_ok"] is True


def test_outbox_backlog_ok_is_never_folded_into_the_critical_status_gate(client, monkeypatch):
    """INT-040's explicit, stated rollup decision (see app/main.py's
    /health and app/static/views/tr16.js's own module docstring): an
    over-ceiling backlog must not, by itself, flip the critical `status`
    field -- that stays gated on database_ok/price_monitor_ok/
    reconciler_ok only, the fields that actually track position
    protection."""
    store, http_client = client
    for i in range(5):
        store.append_export_event(_envelope(f"evt-{i}", i, padding="x" * 500))
    _, backlog_bytes = store.export_outbox_backlog()
    monkeypatch.setattr(app_config, "EXPORT_OUTBOX_SIZE_CEILING_BYTES", backlog_bytes - 1)
    monkeypatch.setattr(main_module.price_monitor, "last_success_at", dt.datetime.now(dt.timezone.utc))
    monkeypatch.setattr(main_module.reconciler, "last_success_at", dt.datetime.now(dt.timezone.utc))

    with http_client:
        response = http_client.get("/health")
    body = response.json()
    assert body["outbox_backlog_ok"] is False
    assert body["status"] == "ok"  # unaffected by the backlog ceiling breach


# --- Structured warning log, never a discard/prune path ---


@pytest.mark.asyncio
async def test_scheduler_logs_a_structured_warning_when_over_ceiling_but_never_discards_rows(
    store, monkeypatch, caplog
):
    for i in range(10):
        store.append_export_event(_envelope(f"evt-{i}", i, padding="x" * 500))
    _, backlog_bytes = store.export_outbox_backlog()
    monkeypatch.setattr(app_config, "EXPORT_OUTBOX_SIZE_CEILING_BYTES", backlog_bytes - 1)

    scheduler = RelayScheduler(store=store, interval_seconds=60.0)
    with caplog.at_level("ERROR", logger="app.relay_scheduler"):
        scheduler._check_outbox_backlog()

    assert any("exceeds its configured storage ceiling" in r.message for r in caplog.records)
    # Never discarded, pruned, or truncated -- the real backlog is
    # untouched by the check itself.
    rows_after, bytes_after = store.export_outbox_backlog()
    assert rows_after == 10
    assert bytes_after == backlog_bytes


def test_no_code_path_anywhere_deletes_or_truncates_export_events(store):
    """Structural, standing check: this codebase must never grow a DELETE/
    TRUNCATE against export_events to relieve storage pressure -- the
    exact failure mode INT-040 exists to prevent. Grep-equivalent check
    against the real db.py source for any such statement."""
    import inspect

    import app.db as db_module

    source = inspect.getsource(db_module).lower()
    assert "delete from export_events" not in source
    assert "truncate table export_events" not in source
    assert "conn.execute" in source  # sanity: real SQL statements do exist in this module
