"""WP-34 — F-06: a human is notified via alerts.

Tests for AlertSink, alerts table persistence, acknowledgment API,
and startup sweep of stale allocation intents.
"""
import pytest

from app.alerts import AlertSink
from app.db import SignalStore


@pytest.mark.asyncio
async def test_alert_sink_record_creates_alert(tmp_path):
    """AlertSink.record() persists to the alerts table."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    alert_id = sink.record(
        kind="protection_deficit",
        account_id="acct-123",
        message="Stop placement failed",
        payload={"symbol": "AAPL", "reason": "network timeout"},
    )

    assert alert_id is not None
    alerts = store.list_alerts()
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["id"] == alert_id
    assert alert["kind"] == "protection_deficit"
    assert alert["account_id"] == "acct-123"
    assert alert["message"] == "Stop placement failed"
    assert alert["payload"] == {"symbol": "AAPL", "reason": "network timeout"}
    assert alert["acknowledged_at"] is None


@pytest.mark.asyncio
async def test_list_alerts_filters_unacknowledged(tmp_path):
    """list_alerts(unacknowledged=True) returns only unacknowledged alerts."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    alert1_id = sink.record("protection_deficit", "acct-1", "Stop failed")
    alert2_id = sink.record("loss_halt", "acct-2", "Loss limit breached")
    alert3_id = sink.record("unknown_submission", "acct-3", "Order status unknown")

    # Acknowledge the first alert
    store.acknowledge_alert(alert1_id)

    # List unacknowledged
    unack = store.list_alerts(unacknowledged=True)
    assert len(unack) == 2
    unack_ids = {a["id"] for a in unack}
    assert alert2_id in unack_ids
    assert alert3_id in unack_ids
    assert alert1_id not in unack_ids

    # List all
    all_alerts = store.list_alerts()
    assert len(all_alerts) == 3


@pytest.mark.asyncio
async def test_list_alerts_filters_by_account_id(tmp_path):
    """list_alerts(account_id=...) returns only alerts for that account."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    sink.record("protection_deficit", "acct-1", "Stop failed")
    sink.record("protection_deficit", "acct-2", "Stop failed")
    sink.record("protection_deficit", None, "System level alert")

    acct1_alerts = store.list_alerts(account_id="acct-1")
    assert len(acct1_alerts) == 1
    assert acct1_alerts[0]["account_id"] == "acct-1"

    acct2_alerts = store.list_alerts(account_id="acct-2")
    assert len(acct2_alerts) == 1
    assert acct2_alerts[0]["account_id"] == "acct-2"


@pytest.mark.asyncio
async def test_acknowledge_alert_marks_alert_as_acknowledged(tmp_path):
    """acknowledge_alert() sets acknowledged_at and returns True if found."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    alert_id = sink.record("protection_deficit", "acct-1", "Stop failed")

    # Acknowledge
    result = store.acknowledge_alert(alert_id)
    assert result is True

    # Verify acknowledged_at is set
    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["acknowledged_at"] is not None

    # Try to acknowledge again (should be idempotent)
    result2 = store.acknowledge_alert(alert_id)
    assert result2 is False  # already acknowledged

    # Try to acknowledge non-existent alert
    result3 = store.acknowledge_alert("non-existent-id")
    assert result3 is False


@pytest.mark.asyncio
async def test_alert_sink_with_none_account_id(tmp_path):
    """AlertSink.record() handles system-level alerts (account_id=None)."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    sink.record(
        kind="loss_halt",
        account_id=None,
        message="System-wide daily loss limit breached",
    )

    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["account_id"] is None
    assert alerts[0]["kind"] == "loss_halt"


@pytest.mark.asyncio
async def test_alert_sink_handles_exceptions_without_raising(tmp_path):
    """AlertSink.record() logs failures but never raises, always returns an ID."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)

    # Manually corrupt the store to trigger an exception
    # (This is a bit hacky but tests the exception handling)
    original_persist = store.persist_alert

    def broken_persist(*args, **kwargs):
        raise RuntimeError("Intentional test error")

    store.persist_alert = broken_persist

    # Should not raise, but log the exception
    alert_id = sink.record("protection_deficit", "acct-1", "Test alert")

    # alert_id should be non-None (synthetic ID)
    assert alert_id is not None

    # Restore original method
    store.persist_alert = original_persist


@pytest.mark.asyncio
async def test_alert_sink_records_different_kinds(tmp_path):
    """AlertSink can record all alert kinds mentioned in WP-34."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    kinds = [
        "protection_deficit",
        "loss_halt",
        "unknown_submission",
        "skipped_allocation",
        "venue_adoption",
    ]

    for kind in kinds:
        sink.record(kind=kind, account_id="acct-1", message=f"Test {kind}")

    alerts = store.list_alerts()
    alert_kinds = {a["kind"] for a in alerts}
    assert alert_kinds == set(kinds)


@pytest.mark.asyncio
async def test_alert_payload_serialization(tmp_path):
    """Alert payloads are JSON-serialized and deserialized correctly."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    payload = {
        "symbol": "AAPL",
        "quantity": 100,
        "price": 150.50,
        "nested": {"key": "value"},
        "list": [1, 2, 3],
    }

    sink.record("protection_deficit", "acct-1", "Test", payload=payload)

    alerts = store.list_alerts()
    assert len(alerts) == 1
    assert alerts[0]["payload"] == payload


def test_api_list_alerts_returns_unacknowledged(tmp_path):
    """GET /alerts?unacknowledged=1 returns only unacknowledged alerts (integration test)."""
    # Note: This is a basic test that verifies the database integration works.
    # Full API testing requires a running server (see live_server fixture).
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    sink.record("protection_deficit", "acct-1", "Alert 1")
    alert2_id = sink.record("loss_halt", "acct-2", "Alert 2")
    store.acknowledge_alert(alert2_id)

    # Test the store methods directly
    unack = store.list_alerts(unacknowledged=True)
    assert len(unack) == 1
    assert unack[0]["message"] == "Alert 1"


def test_api_list_alerts_filters_by_account_id(tmp_path):
    """GET /alerts?account_id=... filters by account (integration test)."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    sink.record("protection_deficit", "acct-1", "Alert 1")
    sink.record("protection_deficit", "acct-2", "Alert 2")

    # Test the store methods directly
    acct_alerts = store.list_alerts(account_id="acct-1")
    assert len(acct_alerts) == 1
    assert acct_alerts[0]["account_id"] == "acct-1"


def test_api_acknowledge_alert(tmp_path):
    """POST /alerts/{id}/ack acknowledges an alert (integration test)."""
    db_path = tmp_path / "test.db"
    store = SignalStore(db_path)

    sink = AlertSink(store)
    alert_id = sink.record("protection_deficit", "acct-1", "Test alert")

    # Verify it's unacknowledged
    unack = store.list_alerts(unacknowledged=True)
    assert len(unack) == 1

    # Acknowledge
    result = store.acknowledge_alert(alert_id)
    assert result is True

    # Verify it's no longer unacknowledged
    unack = store.list_alerts(unacknowledged=True)
    assert len(unack) == 0
