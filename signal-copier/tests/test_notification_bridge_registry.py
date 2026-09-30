"""Track 10: the persistent notification-bridge device registry -- CRUD,
pairing-token hashing, heartbeat/health-state transitions, and the
content-completeness rolling-window degraded heuristic. Mirrors
tests/test_telegram_collectors_registry.py's own structure."""
from pathlib import Path

import pytest

from app.db import SignalStore
from app.notification_bridge import (
    NotificationBridgeError,
    content_fingerprint,
    generate_pairing_token,
    hash_pairing_token,
    validate_content_completeness,
    validate_device_registration,
    verify_pairing_token,
)


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


# -- Pairing-token hashing (never store the raw token) -----------------


def test_generated_token_is_high_entropy_and_never_equal_to_its_own_hash():
    token = generate_pairing_token()
    assert len(token) >= 32
    hashed = hash_pairing_token(token)
    assert hashed != token
    # argon2id hashes are self-describing ($argon2id$...) -- confirms this
    # isn't accidentally a reversible encoding of the raw token.
    assert hashed.startswith("$argon2")


def test_verify_pairing_token_accepts_correct_and_rejects_wrong_token():
    token = generate_pairing_token()
    hashed = hash_pairing_token(token)
    assert verify_pairing_token(token, hashed) is True
    assert verify_pairing_token("wrong-token", hashed) is False


def test_verify_pairing_token_fails_closed_for_garbage_hash():
    assert verify_pairing_token("anything", "not-a-real-argon2-hash") is False
    assert verify_pairing_token("", "") is False
    assert verify_pairing_token("token", "") is False


# -- Registration validation --------------------------------------------


def test_validate_device_registration_requires_device_id():
    with pytest.raises(NotificationBridgeError):
        validate_device_registration(device_id="", app_packages=["com.example.app"], provider_mapping=None)


def test_validate_device_registration_requires_at_least_one_app_package():
    with pytest.raises(NotificationBridgeError):
        validate_device_registration(device_id="phone-1", app_packages=[], provider_mapping=None)


def test_validate_device_registration_rejects_provider_mapping_for_unlisted_package():
    with pytest.raises(NotificationBridgeError):
        validate_device_registration(
            device_id="phone-1",
            app_packages=["com.example.app"],
            provider_mapping={"com.other.app": {"provider_name": "x"}},
        )


def test_validate_content_completeness_rejects_unknown_value():
    with pytest.raises(NotificationBridgeError):
        validate_content_completeness("mostly_complete_i_guess")
    assert validate_content_completeness("complete").value == "complete"


# -- Registry CRUD --------------------------------------------------------


def test_register_device_defaults_to_never_paired(store):
    row = store.register_notification_bridge_device(
        device_id="phone-1",
        pairing_token_hash=hash_pairing_token("token"),
        app_packages=["com.example.tradingapp"],
    )
    assert row["device_id"] == "phone-1"
    assert row["app_packages"] == ["com.example.tradingapp"]
    assert row["health_state"] == "never_paired"
    assert row["last_heartbeat_at"] is None
    assert row["recent_completeness"] == []


def test_register_rejects_invalid_registration(store):
    with pytest.raises(NotificationBridgeError):
        store.register_notification_bridge_device(
            device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=[]
        )


def test_reregistering_preserves_heartbeat_and_health_but_replaces_token_and_packages(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("old-token"), app_packages=["com.a"]
    )
    store.record_notification_bridge_heartbeat("phone-1")
    reregistered = store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("new-token"), app_packages=["com.a", "com.b"]
    )
    assert reregistered["app_packages"] == ["com.a", "com.b"]
    assert verify_pairing_token("new-token", reregistered["pairing_token_hash"])
    assert not verify_pairing_token("old-token", reregistered["pairing_token_hash"])
    # heartbeat-derived state survives re-registration -- an operator
    # re-describing packages must not silently reset already-observed
    # evidence (same precedent as register_telegram_collector).
    assert reregistered["last_heartbeat_at"] is not None


def test_get_and_list_unknown_device(store):
    assert store.get_notification_bridge_device("nope") is None
    assert store.list_notification_bridge_devices() == []


# -- Heartbeat / health-state transitions --------------------------------


def test_heartbeat_promotes_never_paired_to_no_notifications_observed(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    store.record_notification_bridge_heartbeat("phone-1")
    row = store.get_notification_bridge_device("phone-1")
    assert row["health_state"] == "no_notifications_observed"
    assert row["last_heartbeat_at"] is not None


def test_heartbeat_on_unregistered_device_raises(store):
    with pytest.raises(KeyError):
        store.record_notification_bridge_heartbeat("nope")


def test_update_health_rejects_unknown_state(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    with pytest.raises(ValueError):
        store.update_notification_bridge_device_health("phone-1", "vibes_are_off")


def test_update_health_on_unregistered_device_raises(store):
    with pytest.raises(KeyError):
        store.update_notification_bridge_device_health("nope", "healthy_qualified")


def test_no_heartbeat_recently_is_a_read_time_override(store, monkeypatch):
    """Point 8: a device must render as stale RIGHT NOW even though the
    last thing actually WRITTEN to health_state was healthy_qualified --
    see app/notification_bridge.py's own docstring for why this is
    computed at read time, never persisted as such."""
    import app.notification_bridge as nb_module

    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    store.record_notification_bridge_heartbeat("phone-1")
    store.update_notification_bridge_device_health("phone-1", "healthy_qualified")

    # Fresh heartbeat -- not stale yet.
    fresh = store.get_notification_bridge_device("phone-1")
    assert fresh["health_state"] == "healthy_qualified"
    assert fresh["stored_health_state"] == "healthy_qualified"

    # Simulate the heartbeat threshold having elapsed.
    monkeypatch.setattr(nb_module, "DEFAULT_HEARTBEAT_STALE_SECONDS", -1)
    stale = store.get_notification_bridge_device("phone-1")
    assert stale["health_state"] == "no_heartbeat_recently"
    # The underlying write is untouched -- only the read-time view changed.
    assert stale["stored_health_state"] == "healthy_qualified"


def test_never_paired_device_is_not_overridden_to_no_heartbeat(store):
    """A device that has literally never heartbeated at all should read
    as never_paired, not no_heartbeat_recently -- those are distinct,
    honest states (see DeviceHealth's own docstring)."""
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    row = store.get_notification_bridge_device("phone-1")
    assert row["health_state"] == "never_paired"


# -- Content-completeness rolling-window degraded heuristic --------------


def test_single_incomplete_event_does_not_trigger_degraded(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    degraded = store.record_notification_bridge_completeness("phone-1", complete=False)
    assert degraded is False


def test_sustained_incomplete_pattern_triggers_degraded(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    results = [store.record_notification_bridge_completeness("phone-1", complete=False) for _ in range(5)]
    assert results[-1] is True


def test_mostly_complete_window_does_not_trigger_degraded(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    pattern = [True, True, True, True, False]  # 20% incomplete, below the 50% threshold
    results = [store.record_notification_bridge_completeness("phone-1", complete=c) for c in pattern]
    assert results[-1] is False


def test_completeness_window_is_bounded_and_recovers_when_old_bad_samples_age_out(store):
    from app.notification_bridge import COMPLETENESS_WINDOW_SIZE

    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    for _ in range(COMPLETENESS_WINDOW_SIZE):
        store.record_notification_bridge_completeness("phone-1", complete=False)
    row = store.get_notification_bridge_device("phone-1")
    assert len(row["recent_completeness"]) == COMPLETENESS_WINDOW_SIZE
    # Now flood with COMPLETE samples -- the old incomplete ones age out of
    # the bounded window and the verdict recovers.
    last = None
    for _ in range(COMPLETENESS_WINDOW_SIZE):
        last = store.record_notification_bridge_completeness("phone-1", complete=True)
    assert last is False


# -- Event dedup ledger ----------------------------------------------------


def test_find_notification_bridge_event_returns_none_when_unseen(store):
    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    assert store.find_notification_bridge_event("phone-1", "key-1") is None


def test_save_and_find_notification_bridge_event(store):
    from datetime import datetime, timezone

    store.register_notification_bridge_device(
        device_id="phone-1", pairing_token_hash=hash_pairing_token("t"), app_packages=["com.a"]
    )
    now = datetime.now(timezone.utc)
    event_id = store.save_notification_bridge_event(
        device_id="phone-1",
        app_package="com.a",
        notification_key="key-1",
        content_hash=content_fingerprint("title", "text", None),
        revision_seq=1,
        content_completeness="complete",
        posted_at=now,
        received_at=now,
        classification="live",
        signal_id="sig-1",
    )
    assert event_id
    found = store.find_notification_bridge_event("phone-1", "key-1")
    assert found["signal_id"] == "sig-1"
    assert found["revision_seq"] == 1

    events = store.list_notification_bridge_events("phone-1")
    assert len(events) == 1
    assert events[0]["id"] == event_id


def test_content_fingerprint_changes_when_content_changes():
    original = content_fingerprint("New trade", "BUY BTC at 50000", None)
    edited = content_fingerprint("New trade", "BUY BTC at 51000", None)
    retried = content_fingerprint("New trade", "BUY BTC at 50000", None)
    assert original != edited
    assert original == retried
