"""Track 6: the persistent Slack/Twitter user-context collector registry
(app/collector_registry.py, `pull_collectors` table) -- CRUD,
qualification-evidence recording, checkpoint persistence. Mirrors
tests/test_telegram_collectors_registry.py's own pattern."""
from pathlib import Path

import pytest

from app.db import SignalStore
from app.collector_registry import (
    AllowedUse,
    CollectorHealth,
    Provider,
    PullCollectorError,
    validate_registration,
)


@pytest.fixture
def store(tmp_path: Path) -> SignalStore:
    return SignalStore(tmp_path / "test.db")


def test_register_collector_defaults_to_unqualified_and_private_trading_only(store):
    row = store.register_pull_collector(
        collector_id="slack-buyalerts",
        provider="slack",
        auth_mode="oauth_user_token",
        identity_ref="U0123ABC",
        credential_env_var="SLACK_USER_BUYALERTS_TOKEN",
        target_id="C0123ABC",
        provider_name="buyalerts",
    )
    assert row["id"] == "slack-buyalerts"
    assert row["provider"] == "slack"
    assert row["allowed_uses"] == ["private_trading"]
    assert row["health_state"] == "unqualified"
    assert row["checkpoint"] is None


def test_register_rejects_unknown_provider(store):
    with pytest.raises(PullCollectorError):
        store.register_pull_collector(
            collector_id="x",
            provider="carrier_pigeon",
            auth_mode="oauth_user_token",
            identity_ref="x",
            credential_env_var="SLACK_USER_X_TOKEN",
            target_id="1",
            provider_name="x",
        )


def test_register_rejects_credential_env_var_that_looks_like_a_secret_value(store):
    with pytest.raises(PullCollectorError):
        store.register_pull_collector(
            collector_id="x",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="x",
            credential_env_var="xoxp-123456-some-real-token-value",
            target_id="1",
            provider_name="x",
        )


def test_register_rejects_unknown_allowed_use(store):
    with pytest.raises(PullCollectorError):
        store.register_pull_collector(
            collector_id="x",
            provider="twitter",
            auth_mode="oauth2_user_context",
            identity_ref="@x",
            credential_env_var="TWITTER_USER_X_ACCESS_TOKEN",
            target_id="1",
            provider_name="x",
            allowed_uses=["global_domination"],
        )


def test_reregistering_same_id_updates_identity_but_preserves_qualification_and_checkpoint(store):
    store.register_pull_collector(
        collector_id="twitter-somehandle",
        provider="twitter",
        auth_mode="oauth2_user_context",
        identity_ref="@somehandle",
        credential_env_var="TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN",
        target_id="9999",
        provider_name="somehandle",
    )
    store.record_pull_collector_qualification_evidence(
        "twitter-somehandle", evidence={"observed_tweet_id": "1"}
    )
    store.advance_pull_collector_checkpoint("twitter-somehandle", "1")

    row = store.register_pull_collector(
        collector_id="twitter-somehandle",
        provider="twitter",
        auth_mode="oauth2_user_context",
        identity_ref="@somehandle",
        credential_env_var="TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN_V2",
        target_id="9999",
        provider_name="somehandle",
    )
    assert row["credential_env_var"] == "TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN_V2"
    assert row["health_state"] == "healthy_qualified"
    assert row["checkpoint"] == "1"


def test_get_and_list_collectors_filterable_by_provider(store):
    store.register_pull_collector(
        collector_id="slack-a", provider="slack", auth_mode="oauth_user_token", identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN", target_id="C1", provider_name="a",
    )
    store.register_pull_collector(
        collector_id="twitter-b", provider="twitter", auth_mode="oauth2_user_context", identity_ref="@b",
        credential_env_var="TWITTER_USER_B_ACCESS_TOKEN", target_id="2", provider_name="b",
    )
    assert store.get_pull_collector("does-not-exist") is None
    assert [c["id"] for c in store.list_pull_collectors()] == ["slack-a", "twitter-b"]
    assert [c["id"] for c in store.list_pull_collectors(provider="slack")] == ["slack-a"]
    assert [c["id"] for c in store.list_pull_collectors(provider="twitter")] == ["twitter-b"]


def test_update_health_state_rejects_unrecognized_state(store):
    store.register_pull_collector(
        collector_id="a", provider="slack", auth_mode="oauth_user_token", identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN", target_id="C1", provider_name="a",
    )
    with pytest.raises(ValueError):
        store.update_pull_collector_health("a", "vibes_are_off")


def test_update_health_state_unknown_collector_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.update_pull_collector_health("nonexistent", "no_channel_access")


@pytest.mark.parametrize(
    "state",
    [
        CollectorHealth.MISSING_CREDENTIALS,
        CollectorHealth.NO_CHANNEL_ACCESS,
        CollectorHealth.NO_MESSAGES_OBSERVED,
        CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
        CollectorHealth.PARSER_FAILURE,
        CollectorHealth.HEALTHY_QUALIFIED,
    ],
)
def test_every_documented_health_state_is_settable_and_persists(store, state):
    store.register_pull_collector(
        collector_id="a", provider="slack", auth_mode="oauth_user_token", identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN", target_id="C1", provider_name="a",
    )
    store.update_pull_collector_health("a", state.value, detail="test detail")
    row = store.get_pull_collector("a")
    assert row["health_state"] == state.value
    assert row["health_detail"] == "test detail"


def test_qualification_evidence_always_marks_healthy_qualified(store):
    """Unlike Telegram's registry, there is no verified noforwards-style
    downstream restriction flag to branch on here -- see
    app/collector_registry.py's module docstring."""
    store.register_pull_collector(
        collector_id="a", provider="twitter", auth_mode="oauth2_user_context", identity_ref="@a",
        credential_env_var="TWITTER_USER_A_ACCESS_TOKEN", target_id="1", provider_name="a",
    )
    store.record_pull_collector_qualification_evidence("a", evidence={"observed_tweet_id": "42"})
    row = store.get_pull_collector("a")
    assert row["health_state"] == "healthy_qualified"
    assert row["qualification_evidence"] == {"observed_tweet_id": "42"}


def test_checkpoint_is_none_until_first_advance(store):
    store.register_pull_collector(
        collector_id="a", provider="slack", auth_mode="oauth_user_token", identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN", target_id="C1", provider_name="a",
    )
    assert store.get_pull_collector_checkpoint("a") is None
    store.advance_pull_collector_checkpoint("a", "1699999999.000100")
    assert store.get_pull_collector_checkpoint("a") == "1699999999.000100"
    store.advance_pull_collector_checkpoint("a", "1700000000.000200")
    assert store.get_pull_collector_checkpoint("a") == "1700000000.000200"


def test_checkpoint_unknown_collector_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.get_pull_collector_checkpoint("nonexistent")
    with pytest.raises(KeyError):
        store.advance_pull_collector_checkpoint("nonexistent", "1")


# -- Private-ingestion / commercial-redistribution isolation (reused from
# Track 5's own point 10) -----------------------------------------------


def test_default_allowed_uses_never_includes_commercial_redistribution(store):
    row = store.register_pull_collector(
        collector_id="a", provider="slack", auth_mode="oauth_user_token", identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN", target_id="C1", provider_name="a",
    )
    assert AllowedUse.COMMERCIAL_REDISTRIBUTION.value not in row["allowed_uses"]


def test_registering_a_collector_never_touches_route_qualifications(store):
    store.register_pull_collector(
        collector_id="a", provider="slack", auth_mode="oauth_user_token", identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN", target_id="C1", provider_name="a",
    )
    store.record_pull_collector_qualification_evidence("a", evidence={"observed_ts": "1"})
    assert store.list_route_qualifications() == []


def test_validate_registration_helper_directly():
    provider, uses = validate_registration(
        collector_id="a",
        provider="slack",
        auth_mode="oauth_user_token",
        identity_ref="U1",
        credential_env_var="SLACK_USER_A_TOKEN",
        target_id="C1",
        provider_name="a",
        allowed_uses=None,
    )
    assert provider == Provider.SLACK
    assert uses == ["private_trading"]
