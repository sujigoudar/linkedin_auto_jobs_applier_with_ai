"""Track 5: the persistent Telegram collector registry -- CRUD,
qualification-evidence recording, checkpoint persistence, and the
private-ingestion / commercial-redistribution isolation point 10
requires."""
from pathlib import Path

import pytest

from app.db import SignalStore
from app.telegram_collectors import (
    AllowedUse,
    CollectorHealth,
    ConnectionMode,
    TelegramCollectorError,
    validate_registration,
)


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


def test_register_collector_defaults_to_unqualified_and_private_trading_only(store):
    row = store.register_telegram_collector(
        collector_id="buyalerts",
        connection_mode="user_account",
        identity_ref="+15551234567",
        credential_env_var="TELEGRAM_USER_BUYALERTS_SESSION_PATH",
        chat_id="-100123",
        provider_name="buyalerts",
    )
    assert row["id"] == "buyalerts"
    assert row["connection_mode"] == "user_account"
    assert row["allowed_uses"] == ["private_trading"]
    assert row["health_state"] == "unqualified"
    assert row["checkpoint_message_id"] is None
    assert row["noforwards"] is None


def test_register_rejects_unknown_connection_mode(store):
    with pytest.raises(TelegramCollectorError):
        store.register_telegram_collector(
            collector_id="x",
            connection_mode="carrier_pigeon",
            identity_ref="x",
            credential_env_var="TELEGRAM_USER_X_SESSION_PATH",
            chat_id="1",
            provider_name="x",
        )


def test_register_rejects_credential_env_var_that_looks_like_a_secret_value(store):
    """A bare env-var name is UPPER_SNAKE_CASE; something that looks like
    an actual token/session string must be refused rather than silently
    stored (point 3/4: this table never stores a credential value)."""
    with pytest.raises(TelegramCollectorError):
        store.register_telegram_collector(
            collector_id="x",
            connection_mode="user_account",
            identity_ref="x",
            credential_env_var="1BVtsOKh4pQ1EX4mRO2Rk9V=some.session.string",
            chat_id="1",
            provider_name="x",
        )


def test_register_rejects_unknown_allowed_use(store):
    with pytest.raises(TelegramCollectorError):
        store.register_telegram_collector(
            collector_id="x",
            connection_mode="bot",
            identity_ref="@somebot",
            credential_env_var="TELEGRAM_BOT_TOKEN",
            chat_id="1",
            provider_name="x",
            allowed_uses=["global_domination"],
        )


def test_reregistering_same_id_updates_identity_but_preserves_qualification_and_checkpoint(store):
    store.register_telegram_collector(
        collector_id="buyalerts",
        connection_mode="user_account",
        identity_ref="+15551234567",
        credential_env_var="TELEGRAM_USER_BUYALERTS_SESSION_PATH",
        chat_id="-100123",
        provider_name="buyalerts",
    )
    store.record_telegram_collector_qualification_evidence(
        "buyalerts", evidence={"observed_message_id": "1", "method": "live_new_message_event"}
    )
    store.advance_telegram_collector_checkpoint("buyalerts", 1)

    # Re-register with a rotated session-file env var (a real operational
    # scenario) -- identity/connection fields change, evidence/checkpoint
    # must not silently reset.
    row = store.register_telegram_collector(
        collector_id="buyalerts",
        connection_mode="user_account",
        identity_ref="+15551234567",
        credential_env_var="TELEGRAM_USER_BUYALERTS_SESSION_PATH_V2",
        chat_id="-100123",
        provider_name="buyalerts",
    )
    assert row["credential_env_var"] == "TELEGRAM_USER_BUYALERTS_SESSION_PATH_V2"
    assert row["health_state"] == "healthy_qualified"
    assert row["checkpoint_message_id"] == 1


def test_get_and_list_collectors(store):
    store.register_telegram_collector(
        collector_id="a", connection_mode="bot", identity_ref="@a", credential_env_var="TELEGRAM_BOT_TOKEN",
        chat_id="1", provider_name="a",
    )
    store.register_telegram_collector(
        collector_id="b", connection_mode="user_account", identity_ref="+1", credential_env_var="TELEGRAM_USER_B_SESSION_PATH",
        chat_id="2", provider_name="b",
    )
    assert store.get_telegram_collector("does-not-exist") is None
    ids = [c["id"] for c in store.list_telegram_collectors()]
    assert ids == ["a", "b"]


def test_update_health_state_rejects_unrecognized_state(store):
    store.register_telegram_collector(
        collector_id="a", connection_mode="bot", identity_ref="@a", credential_env_var="TELEGRAM_BOT_TOKEN",
        chat_id="1", provider_name="a",
    )
    with pytest.raises(ValueError):
        store.update_telegram_collector_health("a", "vibes_are_off")


def test_update_health_state_unknown_collector_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.update_telegram_collector_health("nonexistent", "no_channel_access")


@pytest.mark.parametrize(
    "state",
    [
        CollectorHealth.MISSING_CREDENTIALS,
        CollectorHealth.NO_CHANNEL_ACCESS,
        CollectorHealth.NO_MESSAGES_OBSERVED,
        CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
        CollectorHealth.PROTECTED_CONTENT_RESTRICTED,
        CollectorHealth.PARSER_FAILURE,
        CollectorHealth.HEALTHY_QUALIFIED,
    ],
)
def test_every_documented_health_state_is_settable_and_persists(store, state):
    """Point 8: each of the explicit, documented incident/health states
    must be a real, persistable value -- never a state a dashboard could
    render but this registry can't actually store."""
    store.register_telegram_collector(
        collector_id="a", connection_mode="bot", identity_ref="@a", credential_env_var="TELEGRAM_BOT_TOKEN",
        chat_id="1", provider_name="a",
    )
    store.update_telegram_collector_health("a", state.value, detail="test detail")
    row = store.get_telegram_collector("a")
    assert row["health_state"] == state.value
    assert row["health_detail"] == "test detail"


def test_qualification_evidence_with_noforwards_sets_protected_content_restricted_not_green(store):
    """Point 5/8: a chat with protected content ON must never render
    identically to an unrestricted, fully-healthy collector."""
    store.register_telegram_collector(
        collector_id="a", connection_mode="user_account", identity_ref="+1",
        credential_env_var="TELEGRAM_USER_A_SESSION_PATH", chat_id="1", provider_name="a",
    )
    store.record_telegram_collector_qualification_evidence(
        "a", evidence={"chat_noforwards_observed": True}, noforwards=True
    )
    row = store.get_telegram_collector("a")
    assert row["noforwards"] is True
    assert row["health_state"] == "protected_content_restricted"


def test_qualification_evidence_without_noforwards_flag_set_marks_healthy_qualified(store):
    store.register_telegram_collector(
        collector_id="a", connection_mode="user_account", identity_ref="+1",
        credential_env_var="TELEGRAM_USER_A_SESSION_PATH", chat_id="1", provider_name="a",
    )
    store.record_telegram_collector_qualification_evidence(
        "a", evidence={"chat_noforwards_observed": False}, noforwards=False
    )
    row = store.get_telegram_collector("a")
    assert row["noforwards"] is False
    assert row["health_state"] == "healthy_qualified"


def test_checkpoint_is_none_until_first_advance_and_then_monotonic(store):
    store.register_telegram_collector(
        collector_id="a", connection_mode="user_account", identity_ref="+1",
        credential_env_var="TELEGRAM_USER_A_SESSION_PATH", chat_id="1", provider_name="a",
    )
    assert store.get_telegram_collector_checkpoint("a") is None
    store.advance_telegram_collector_checkpoint("a", 10)
    assert store.get_telegram_collector_checkpoint("a") == 10
    # An out-of-order/lower message id must never move the checkpoint backward.
    store.advance_telegram_collector_checkpoint("a", 5)
    assert store.get_telegram_collector_checkpoint("a") == 10
    store.advance_telegram_collector_checkpoint("a", 20)
    assert store.get_telegram_collector_checkpoint("a") == 20


def test_checkpoint_unknown_collector_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.get_telegram_collector_checkpoint("nonexistent")
    with pytest.raises(KeyError):
        store.advance_telegram_collector_checkpoint("nonexistent", 1)


# -- Point 10: private-ingestion / commercial-redistribution isolation ------


def test_default_allowed_uses_never_includes_commercial_redistribution(store):
    row = store.register_telegram_collector(
        collector_id="a", connection_mode="user_account", identity_ref="+1",
        credential_env_var="TELEGRAM_USER_A_SESSION_PATH", chat_id="1", provider_name="a",
    )
    assert AllowedUse.COMMERCIAL_REDISTRIBUTION.value not in row["allowed_uses"]
    assert row["allowed_uses"] == [AllowedUse.PRIVATE_TRADING.value]


def test_registering_a_collector_never_touches_commercial_rights(store):
    """Registering (or fully qualifying) a Telegram collector must never
    call into, or fabricate a record resembling, signal-portfolio-
    commercial's own RightsGrant/publication-eligibility model -- that
    lives entirely in a separate service/database this module has no
    import of at all (grep-verified below) and no code path here ever
    calls out to."""
    import app.telegram_collectors as module

    source = Path(module.__file__).read_text()
    # Documentation is allowed to (and does) NAME the other service's
    # model for context -- what must never appear is an actual import of
    # or call into it.
    assert "import" not in "\n".join(
        line for line in source.splitlines() if "rights" in line.lower() or "portfolio" in line.lower()
    )
    assert "from signal_portfolio_commercial" not in source
    assert "signal_portfolio_commercial" not in source

    store.register_telegram_collector(
        collector_id="a", connection_mode="user_account", identity_ref="+1",
        credential_env_var="TELEGRAM_USER_A_SESSION_PATH", chat_id="1", provider_name="a",
    )
    store.record_telegram_collector_qualification_evidence(
        "a", evidence={"observed_message_id": "1"}, noforwards=False
    )
    # Nothing about registration/qualification writes any table other
    # than telegram_collectors -- in particular no route_qualifications
    # row (that ladder is a SEPARATE, broker-route concept -- see
    # app/engine.py's _check_route_qualified) is created as a side effect.
    assert store.list_route_qualifications() == []


def test_allowed_uses_can_record_commercial_redistribution_as_a_named_value_but_nothing_reads_it_as_a_grant(store):
    """AllowedUse.COMMERCIAL_REDISTRIBUTION is a real, named value an
    operator COULD record for their own bookkeeping -- but recording it
    here is not itself eligibility for anything; this test documents
    that the value round-trips as plain data with no other observable
    effect."""
    row = store.register_telegram_collector(
        collector_id="a", connection_mode="bot", identity_ref="@a", credential_env_var="TELEGRAM_BOT_TOKEN",
        chat_id="1", provider_name="a",
        allowed_uses=["private_trading", "commercial_redistribution"],
    )
    assert set(row["allowed_uses"]) == {"private_trading", "commercial_redistribution"}
    # Still no live-routing or rights-adjacent side effect from this alone.
    assert store.list_route_qualifications() == []


def test_validate_registration_helper_directly():
    mode, uses = validate_registration(
        collector_id="a",
        connection_mode="bot",
        identity_ref="@a",
        credential_env_var="TELEGRAM_BOT_TOKEN",
        chat_id="1",
        provider_name="a",
        allowed_uses=None,
    )
    assert mode == ConnectionMode.BOT
    assert uses == ["private_trading"]
