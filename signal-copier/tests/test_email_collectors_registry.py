"""Track 7: the persistent email collector registry -- CRUD,
qualification-evidence recording, checkpoint persistence, and the
private-ingestion / commercial-redistribution isolation point 10
requires. Mirrors tests/test_telegram_collectors_registry.py's own
pattern for the new email_collectors table."""
from pathlib import Path

import pytest

from app.db import SignalStore
from app.email_collectors import (
    AllowedUse,
    CollectorHealth,
    ConnectionMode,
    EmailCollectorError,
    validate_registration,
)


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


def _register(store, **overrides):
    kwargs = dict(
        collector_id="buyalerts",
        connection_mode="imap",
        identity_ref="alerts-watcher@gmail.com",
        credential_env_var="EMAIL_BUYALERTS_APP_PASSWORD",
        imap_host="imap.gmail.com",
        imap_folder="INBOX",
        sender_allowlist=["signals@buyalerts.example"],
        provider_name="buyalerts",
    )
    kwargs.update(overrides)
    return store.register_email_collector(**kwargs)


def test_register_collector_defaults_to_no_messages_observed_and_private_trading_only(store):
    row = _register(store)
    assert row["id"] == "buyalerts"
    assert row["connection_mode"] == "imap"
    assert row["allowed_uses"] == ["private_trading"]
    assert row["health_state"] == "no_messages_observed"
    assert row["checkpoint_uid"] is None
    assert row["sender_allowlist"] == ["signals@buyalerts.example"]
    assert row["imap_port"] == 993
    assert row["poll_interval_seconds"] == 60


def test_register_rejects_unknown_connection_mode(store):
    with pytest.raises(EmailCollectorError):
        _register(store, connection_mode="carrier_pigeon")


def test_register_rejects_credential_env_var_that_looks_like_a_secret_value(store):
    with pytest.raises(EmailCollectorError):
        _register(store, credential_env_var="hunter2-some-app-password")


def test_register_rejects_empty_sender_allowlist(store):
    """Point 4: never parse every message in the inbox indiscriminately."""
    with pytest.raises(EmailCollectorError):
        _register(store, sender_allowlist=[])


def test_register_rejects_unknown_allowed_use(store):
    with pytest.raises(EmailCollectorError):
        _register(store, allowed_uses=["global_domination"])


def test_reregistering_same_id_updates_identity_but_preserves_qualification_and_checkpoint(store):
    _register(store)
    store.record_email_collector_qualification_evidence(
        "buyalerts", evidence={"observed_message_id": "<abc@buyalerts.example>", "observed_uid": 1}
    )
    store.advance_email_collector_checkpoint("buyalerts", 1)

    row = _register(store, credential_env_var="EMAIL_BUYALERTS_APP_PASSWORD_V2")
    assert row["credential_env_var"] == "EMAIL_BUYALERTS_APP_PASSWORD_V2"
    assert row["health_state"] == "healthy_qualified"
    assert row["checkpoint_uid"] == 1


def test_get_and_list_collectors(store):
    _register(store, collector_id="a", sender_allowlist=["x@example.com"])
    _register(store, collector_id="b", sender_allowlist=["y@example.com"])
    assert store.get_email_collector("does-not-exist") is None
    ids = [c["id"] for c in store.list_email_collectors()]
    assert ids == ["a", "b"]


def test_update_health_state_rejects_unrecognized_state(store):
    _register(store)
    with pytest.raises(ValueError):
        store.update_email_collector_health("buyalerts", "vibes_are_off")


def test_update_health_state_unknown_collector_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.update_email_collector_health("nonexistent", "no_mailbox_access")


@pytest.mark.parametrize(
    "state",
    [
        CollectorHealth.MISSING_CREDENTIALS,
        CollectorHealth.NO_MAILBOX_ACCESS,
        CollectorHealth.NO_MESSAGES_OBSERVED,
        CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
        CollectorHealth.PARSER_FAILURE,
        CollectorHealth.HEALTHY_QUALIFIED,
    ],
)
def test_every_documented_health_state_is_settable_and_persists(store, state):
    """Point 6: each of the six explicit, documented incident/health
    states must be a real, persistable value."""
    _register(store)
    store.update_email_collector_health("buyalerts", state.value, detail="test detail")
    row = store.get_email_collector("buyalerts")
    assert row["health_state"] == state.value
    assert row["health_detail"] == "test detail"


def test_qualification_evidence_marks_healthy_qualified(store):
    _register(store)
    store.record_email_collector_qualification_evidence(
        "buyalerts", evidence={"observed_message_id": "<abc@buyalerts.example>"}
    )
    row = store.get_email_collector("buyalerts")
    assert row["health_state"] == "healthy_qualified"
    assert row["last_qualified_at"] is not None


def test_checkpoint_is_none_until_first_advance_and_then_monotonic(store):
    _register(store)
    assert store.get_email_collector_checkpoint("buyalerts") is None
    store.advance_email_collector_checkpoint("buyalerts", 10)
    assert store.get_email_collector_checkpoint("buyalerts") == 10
    # An out-of-order/lower UID must never move the checkpoint backward.
    store.advance_email_collector_checkpoint("buyalerts", 5)
    assert store.get_email_collector_checkpoint("buyalerts") == 10
    store.advance_email_collector_checkpoint("buyalerts", 20)
    assert store.get_email_collector_checkpoint("buyalerts") == 20


def test_checkpoint_unknown_collector_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.get_email_collector_checkpoint("nonexistent")
    with pytest.raises(KeyError):
        store.advance_email_collector_checkpoint("nonexistent", 1)


# -- Point 10: private-ingestion / commercial-redistribution isolation ------


def test_default_allowed_uses_never_includes_commercial_redistribution(store):
    row = _register(store)
    assert AllowedUse.COMMERCIAL_REDISTRIBUTION.value not in row["allowed_uses"]
    assert row["allowed_uses"] == [AllowedUse.PRIVATE_TRADING.value]


def test_registering_a_collector_never_touches_commercial_rights(store):
    import app.email_collectors as module

    source = Path(module.__file__).read_text()
    assert "import" not in "\n".join(
        line for line in source.splitlines() if "rights" in line.lower() or "portfolio" in line.lower()
    )
    assert "from signal_portfolio_commercial" not in source
    assert "signal_portfolio_commercial" not in source

    _register(store)
    store.record_email_collector_qualification_evidence("buyalerts", evidence={"observed_message_id": "1"})
    assert store.list_route_qualifications() == []


def test_validate_registration_helper_directly():
    mode, uses = validate_registration(
        collector_id="a",
        connection_mode="imap",
        identity_ref="a@example.com",
        credential_env_var="EMAIL_A_APP_PASSWORD",
        imap_host="imap.example.com",
        imap_folder="INBOX",
        sender_allowlist=["signals@example.com"],
        provider_name="a",
        allowed_uses=None,
    )
    assert mode == ConnectionMode.IMAP
    assert uses == ["private_trading"]
