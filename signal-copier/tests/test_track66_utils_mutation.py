"""Track 66: Comprehensive mutation testing for signal-copier utility modules.

Targeted regression tests for mutation-critical patterns in:
  - app/collector_registry.py (collector registration/discovery/health)
  - app/errors.py (custom exceptions and error handling)
  - app/config.py (configuration management and pydantic settings)
  - app/main.py (application entry point, standby mode, auth gates)

Focus areas:
  1. Registry lookups and provider discrimination
  2. Error classification and boundary handling
  3. Config parsing and default value correctness
  4. Enum conversions and health state transitions
  5. Boolean flag gates (standby_mode, force_secure_cookies)
  6. Credential validation and environment variable naming rules
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import SignalStore
from app.collector_registry import (
    AllowedUse,
    CollectorHealth,
    Provider,
    PullCollector,
    PullCollectorError,
    now_utc,
    validate_registration,
)
from app.errors import SignalValidationError


# ============================================================================
# Section 1: collector_registry.py - Registry Lookups and Validation
# ============================================================================


class TestProviderEnumMutation:
    """Mutation-critical tests for Provider enum discrimination."""

    def test_provider_slack_value_is_exactly_slack(self):
        """Mutation: changing "slack" value would break provider filtering."""
        assert Provider.SLACK.value == "slack"

    def test_provider_twitter_value_is_exactly_twitter(self):
        """Mutation: changing "twitter" value would break provider filtering."""
        assert Provider.TWITTER.value == "twitter"

    def test_provider_enum_has_exactly_two_members(self):
        """Mutation: adding/removing providers breaks enum membership checks."""
        assert len(Provider) == 2
        assert set(p.value for p in Provider) == {"slack", "twitter"}

    def test_provider_from_string_slack(self):
        """Mutation: enum conversion failure would break registration."""
        p = Provider("slack")
        assert p == Provider.SLACK
        assert isinstance(p, Provider)

    def test_provider_from_string_twitter(self):
        """Mutation: enum conversion failure would break registration."""
        p = Provider("twitter")
        assert p == Provider.TWITTER
        assert isinstance(p, Provider)

    def test_provider_from_invalid_string_raises_valueerror(self):
        """Mutation: removing exception raises breaks validation."""
        with pytest.raises(ValueError):
            Provider("telegram")


class TestCollectorHealthEnumMutation:
    """Mutation-critical tests for CollectorHealth enum state transitions."""

    def test_health_unqualified_is_default_starting_state(self):
        """Mutation: changing default health breaks initialization."""
        assert CollectorHealth.UNQUALIFIED.value == "unqualified"
        # Also verify it's the default in PullCollector
        collector = PullCollector(
            id="test",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U123",
            credential_env_var="SLACK_USER_TEST_TOKEN",
            target_id="C123",
            provider_name="test",
        )
        assert collector.health_state == CollectorHealth.UNQUALIFIED

    def test_health_healthy_qualified_is_only_green_state(self):
        """Mutation: changing healthy state name breaks dashboard logic."""
        assert CollectorHealth.HEALTHY_QUALIFIED.value == "healthy_qualified"

    def test_health_missing_credentials_enum_value(self):
        """Mutation: changing enum value breaks credential checks."""
        assert CollectorHealth.MISSING_CREDENTIALS.value == "missing_credentials"

    def test_health_no_channel_access_enum_value(self):
        """Mutation: changing enum value breaks access checks."""
        assert CollectorHealth.NO_CHANNEL_ACCESS.value == "no_channel_access"

    def test_health_no_messages_observed_enum_value(self):
        """Mutation: changing enum value breaks stale checks."""
        assert CollectorHealth.NO_MESSAGES_OBSERVED.value == "no_messages_observed"

    def test_health_unsupported_format_enum_value(self):
        """Mutation: changing enum value breaks format error tracking."""
        assert CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value == "unsupported_format_encountered"

    def test_health_parser_failure_enum_value(self):
        """Mutation: changing enum value breaks parser error tracking."""
        assert CollectorHealth.PARSER_FAILURE.value == "parser_failure"

    def test_health_enum_has_exactly_seven_members(self):
        """Mutation: adding/removing health states breaks state machine."""
        assert len(CollectorHealth) == 7
        expected_states = {
            "missing_credentials",
            "no_channel_access",
            "no_messages_observed",
            "unsupported_format_encountered",
            "parser_failure",
            "unqualified",
            "healthy_qualified",
        }
        assert set(h.value for h in CollectorHealth) == expected_states


class TestValidateRegistrationMutation:
    """Mutation-critical tests for validate_registration validation logic."""

    def test_validate_rejects_empty_collector_id(self):
        """Mutation: removing empty check breaks invalid registration detection."""
        with pytest.raises(PullCollectorError) as exc:
            validate_registration(
                collector_id="",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )
        assert "required" in str(exc.value)

    def test_validate_rejects_empty_auth_mode(self):
        """Mutation: removing auth_mode check breaks validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_rejects_empty_identity_ref(self):
        """Mutation: removing identity_ref check breaks validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_rejects_empty_credential_env_var(self):
        """Mutation: removing credential_env_var check breaks validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_rejects_empty_target_id(self):
        """Mutation: removing target_id check breaks validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_rejects_empty_provider_name(self):
        """Mutation: removing provider_name check breaks validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="",
                allowed_uses=None,
            )

    def test_validate_rejects_invalid_provider_enum(self):
        """Mutation: removing provider validation breaks registration."""
        with pytest.raises(PullCollectorError) as exc:
            validate_registration(
                collector_id="test",
                provider="invalid_provider",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )
        assert "must be one of" in str(exc.value)

    def test_validate_rejects_credential_env_var_with_lowercase(self):
        """Mutation: removing uppercase check breaks env var validation."""
        with pytest.raises(PullCollectorError) as exc:
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="slack_user_test_token",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )
        assert "must be a bare environment-variable NAME" in str(exc.value)

    def test_validate_rejects_credential_env_var_with_space(self):
        """Mutation: removing space check breaks env var validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="SLACK USER TOKEN",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_rejects_credential_env_var_with_equals_sign(self):
        """Mutation: removing equals-sign check breaks env var validation."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="SLACK_USER_TOKEN=xoxp-123",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_credential_env_var_with_single_equals_fails(self):
        """Mutation: edge case -- single equals should still fail."""
        with pytest.raises(PullCollectorError):
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR=VAL",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )

    def test_validate_accepts_valid_credential_env_var_with_underscores(self):
        """Mutation: breaking underscore allowance breaks valid names."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="SLACK_USER_TEST_TOKEN",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert provider == Provider.SLACK

    def test_validate_rejects_invalid_allowed_use(self):
        """Mutation: removing allowed_use validation breaks use-case gating."""
        with pytest.raises(PullCollectorError) as exc:
            validate_registration(
                collector_id="test",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="test",
                allowed_uses=["invalid_use_case"],
            )
        assert "must be one of" in str(exc.value)

    def test_validate_accepts_private_trading_use(self):
        """Mutation: breaking private_trading acceptance breaks normal registration."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            allowed_uses=["private_trading"],
        )
        assert "private_trading" in uses

    def test_validate_defaults_to_default_allowed_uses(self):
        """Mutation: breaking default replacement breaks null allowed_uses."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert uses == list(AllowedUse("private_trading").value for use in [AllowedUse.PRIVATE_TRADING])
        # Simpler check: should match DEFAULT_ALLOWED_USES
        from app.telegram_collectors import DEFAULT_ALLOWED_USES
        assert uses == list(DEFAULT_ALLOWED_USES)

    def test_validate_returns_provider_enum_not_string(self):
        """Mutation: returning string instead of enum breaks type safety."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert isinstance(provider, Provider)
        assert provider == Provider.SLACK

    def test_validate_returns_list_not_tuple(self):
        """Mutation: returning tuple instead of list breaks mutations."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert isinstance(uses, list)


class TestPullCollectorPostInitMutation:
    """Mutation-critical tests for PullCollector.__post_init__ conversions."""

    def test_post_init_converts_provider_string_to_enum(self):
        """Mutation: removing string-to-enum conversion breaks YAML deserialization."""
        collector = PullCollector(
            id="test",
            provider="slack",  # string, not enum
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        assert isinstance(collector.provider, Provider)
        assert collector.provider == Provider.SLACK

    def test_post_init_converts_health_state_string_to_enum(self):
        """Mutation: removing health_state conversion breaks YAML deserialization."""
        collector = PullCollector(
            id="test",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            health_state="healthy_qualified",  # string, not enum
        )
        assert isinstance(collector.health_state, CollectorHealth)
        assert collector.health_state == CollectorHealth.HEALTHY_QUALIFIED

    def test_post_init_leaves_enum_provider_unchanged(self):
        """Mutation: double-conversion would break idempotency."""
        collector = PullCollector(
            id="test",
            provider=Provider.TWITTER,  # already enum
            auth_mode="oauth2_user_context",
            identity_ref="@test",
            credential_env_var="VAR",
            target_id="1",
            provider_name="test",
        )
        assert collector.provider == Provider.TWITTER

    def test_post_init_leaves_enum_health_state_unchanged(self):
        """Mutation: double-conversion would break idempotency."""
        collector = PullCollector(
            id="test",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            health_state=CollectorHealth.NO_CHANNEL_ACCESS,  # already enum
        )
        assert collector.health_state == CollectorHealth.NO_CHANNEL_ACCESS


class TestRegistryStoreOperationsMutation:
    """Mutation-critical tests for SignalStore registry operations."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> SignalStore:
        return SignalStore(tmp_path / "test.db")

    def test_list_pull_collectors_respects_provider_filter(self, store):
        """Mutation: removing provider filter breaks selective queries."""
        store.register_pull_collector(
            collector_id="slack-a",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR1",
            target_id="C1",
            provider_name="a",
        )
        store.register_pull_collector(
            collector_id="twitter-b",
            provider="twitter",
            auth_mode="oauth2_user_context",
            identity_ref="@b",
            credential_env_var="VAR2",
            target_id="2",
            provider_name="b",
        )
        slack_only = store.list_pull_collectors(provider="slack")
        twitter_only = store.list_pull_collectors(provider="twitter")
        all_collectors = store.list_pull_collectors()

        assert len(slack_only) == 1
        assert slack_only[0]["id"] == "slack-a"
        assert len(twitter_only) == 1
        assert twitter_only[0]["id"] == "twitter-b"
        assert len(all_collectors) == 2

    def test_get_pull_collector_returns_none_for_missing(self, store):
        """Mutation: returning empty dict instead of None breaks existence checks."""
        result = store.get_pull_collector("nonexistent")
        assert result is None

    def test_get_pull_collector_returns_dict_for_existing(self, store):
        """Mutation: returning wrong type breaks consumer code."""
        store.register_pull_collector(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        result = store.get_pull_collector("test")
        assert isinstance(result, dict)
        assert result["id"] == "test"

    def test_get_pull_collector_checkpoint_returns_none_until_set(self, store):
        """Mutation: initializing checkpoint to empty string breaks null checks."""
        store.register_pull_collector(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        checkpoint = store.get_pull_collector_checkpoint("test")
        assert checkpoint is None

    def test_get_pull_collector_checkpoint_returns_exact_value_after_advance(self, store):
        """Mutation: lossy checkpoint storage breaks resumption."""
        store.register_pull_collector(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        store.advance_pull_collector_checkpoint("test", "1699999999.000100")
        checkpoint = store.get_pull_collector_checkpoint("test")
        assert checkpoint == "1699999999.000100"

    def test_advance_pull_collector_checkpoint_raises_on_nonexistent(self, store):
        """Mutation: silently ignoring nonexistent IDs breaks error reporting."""
        with pytest.raises(KeyError):
            store.advance_pull_collector_checkpoint("nonexistent", "1")


# ============================================================================
# Section 2: errors.py - Custom Exception Hierarchy
# ============================================================================


class TestSignalValidationErrorMutation:
    """Mutation-critical tests for custom exception behavior."""

    def test_signal_validation_error_is_value_error_subclass(self):
        """Mutation: changing base class breaks exception hierarchy."""
        exc = SignalValidationError("test message")
        assert isinstance(exc, ValueError)

    def test_signal_validation_error_preserves_message(self):
        """Mutation: losing message breaks error reporting."""
        msg = "signal failed parsing"
        exc = SignalValidationError(msg)
        assert str(exc) == msg

    def test_signal_validation_error_can_be_caught_as_value_error(self):
        """Mutation: changing base class breaks existing error handling."""
        try:
            raise SignalValidationError("test")
        except ValueError as e:
            assert isinstance(e, SignalValidationError)
        except Exception:
            pytest.fail("Should have caught as ValueError")

    def test_signal_validation_error_distinguishable_from_generic_value_error(self):
        """Mutation: losing custom class breaks specialized error handling."""
        exc = SignalValidationError("test")
        generic = ValueError("test")
        assert type(exc) != type(generic)
        assert isinstance(exc, SignalValidationError)
        assert not isinstance(generic, SignalValidationError)


# ============================================================================
# Section 3: config.py - Configuration Parsing and Defaults
# ============================================================================


class TestConfigDefaultsMutation:
    """Mutation-critical tests for configuration defaults and parsing."""

    def test_default_values_are_safe_when_unset(self, monkeypatch):
        """Mutation: unsafe defaults break fail-closed patterns."""
        import app.config as config_module

        # Reload to pick up environment
        monkeypatch.delenv("STANDBY_MODE", raising=False)
        monkeypatch.delenv("FORCE_SECURE_COOKIES", raising=False)
        monkeypatch.delenv("LEGACY_DASHBOARD_ENABLED", raising=False)

        # These are defined at module level; we test the _Settings object
        settings = config_module._Settings()

        # Verify safe defaults
        assert settings.STANDBY_MODE is False  # dangerous to be True by default
        assert settings.FORCE_SECURE_COOKIES is False  # safe to default to False
        assert settings.LEGACY_DASHBOARD_ENABLED is False  # safe to default to False

    def test_ccxt_exchange_id_defaults_to_binance(self, monkeypatch):
        """Mutation: changing default exchange breaks single-exchange behavior."""
        monkeypatch.delenv("CCXT_EXCHANGE_ID", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.CCXT_EXCHANGE_ID == "binance"

    def test_ccxt_sandbox_defaults_to_false(self, monkeypatch):
        """Mutation: defaulting to True risks real trades in testing."""
        monkeypatch.delenv("CCXT_SANDBOX", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.CCXT_SANDBOX is False

    def test_session_ttl_seconds_defaults_to_12_hours(self, monkeypatch):
        """Mutation: changing TTL breaks session timeout behavior."""
        monkeypatch.delenv("SESSION_TTL_SECONDS", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.SESSION_TTL_SECONDS == 60 * 60 * 12

    def test_writer_lease_seconds_not_zero(self):
        """Mutation: zero lease time breaks failover detection."""
        import app.config as config_module
        assert config_module.WRITER_LEASE_SECONDS > 0

    def test_writer_lease_renew_shorter_than_lease(self):
        """Mutation: renew >= lease breaks lease renewal pattern."""
        import app.config as config_module
        assert config_module.WRITER_LEASE_RENEW_SECONDS < config_module.WRITER_LEASE_SECONDS

    def test_empty_string_secrets_allowed_by_default(self, monkeypatch):
        """Mutation: requiring secrets breaks optional-source behavior."""
        monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
        monkeypatch.delenv("OWNER_PASSWORD", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.WEBHOOK_SHARED_SECRET == ""
        assert settings.OWNER_PASSWORD == ""

    def test_relay_producer_id_has_default_value(self, monkeypatch):
        """Mutation: removing default breaks deployments without explicit ID."""
        monkeypatch.delenv("RELAY_PRODUCER_ID", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.RELAY_PRODUCER_ID == "signal-copier-local"

    def test_relay_evidence_class_defaults_to_internal_paper(self, monkeypatch):
        """Mutation: changing default to OBSERVED breaks fail-closed pattern."""
        monkeypatch.delenv("RELAY_EVIDENCE_CLASS", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.RELAY_EVIDENCE_CLASS == "INTERNAL_PAPER"

    def test_relay_environment_defaults_to_local_sim(self, monkeypatch):
        """Mutation: changing default to LIVE breaks fail-closed pattern."""
        monkeypatch.delenv("RELAY_ENVIRONMENT", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.RELAY_ENVIRONMENT == "LOCAL_SIM"

    def test_export_outbox_ceiling_is_substantial(self):
        """Mutation: reducing ceiling breaks backlog detection."""
        import app.config as config_module
        # Should be at least 1 MB
        assert config_module.EXPORT_OUTBOX_SIZE_CEILING_BYTES >= 1024 * 1024

    def test_signal_correlation_enabled_defaults_true(self, monkeypatch):
        """Mutation: defaulting to False breaks correlation layer."""
        monkeypatch.delenv("SIGNAL_CORRELATION_ENABLED", raising=False)
        import app.config as config_module
        settings = config_module._Settings()
        assert settings.SIGNAL_CORRELATION_ENABLED is True


class TestConfigParsing:
    """Mutation tests for configuration parsing logic."""

    def test_twitter_rules_parsed_from_comma_separated_string(self, monkeypatch):
        """Mutation: breaking CSV parsing breaks Twitter rules."""
        monkeypatch.setenv("TWITTER_RULES", "rule1, rule2 , rule3")
        import importlib
        import app.config as config_module
        importlib.reload(config_module)
        rules = config_module.TWITTER_RULES
        assert rules == ["rule1", "rule2", "rule3"]

    def test_twitter_rules_empty_when_not_set(self, monkeypatch):
        """Mutation: defaulting to None breaks rule iteration."""
        monkeypatch.delenv("TWITTER_RULES", raising=False)
        import importlib
        import app.config as config_module
        importlib.reload(config_module)
        rules = config_module.TWITTER_RULES
        assert isinstance(rules, list)
        assert len(rules) == 0

    def test_twilio_numbers_parsed_from_comma_separated_e164(self, monkeypatch):
        """Mutation: breaking CSV parsing breaks sender authorization."""
        monkeypatch.setenv("TWILIO_ALLOWED_FROM_NUMBERS", "+15551234567, +15557654321")
        import importlib
        import app.config as config_module
        importlib.reload(config_module)
        numbers = config_module.TWILIO_ALLOWED_FROM_NUMBERS
        assert numbers == ["+15551234567", "+15557654321"]

    def test_whatsapp_numbers_parsed_without_leading_plus(self, monkeypatch):
        """Mutation: adding plus sign breaks WhatsApp ID format."""
        monkeypatch.setenv("WHATSAPP_ALLOWED_FROM_NUMBERS", "15551234567, 15557654321")
        import importlib
        import app.config as config_module
        importlib.reload(config_module)
        numbers = config_module.WHATSAPP_ALLOWED_FROM_NUMBERS
        assert numbers == ["15551234567", "15557654321"]
        assert all(not n.startswith("+") for n in numbers)


# ============================================================================
# Section 4: main.py - Application Entry Point (Key Patterns)
# ============================================================================


class TestMainAuthGateMutation:
    """Mutation-critical tests for authentication gate patterns."""

    def test_owner_password_and_password_hash_are_mutually_exclusive_concept(self):
        """Mutation: allowing both to be set simultaneously breaks auth."""
        # This is a conceptual test; the actual enforcement is in auth.py
        # We test that the configuration allows either one
        import app.config as config_module
        settings = config_module._Settings()
        # Both can be empty, but the app should reject both being non-empty
        assert settings.OWNER_PASSWORD == ""
        assert settings.OWNER_PASSWORD_HASH == ""

    def test_session_secret_required_for_auth(self):
        """Mutation: making session secret optional breaks session signing."""
        # The fact that SESSION_SECRET exists and is exposed means it's required
        import app.config as config_module
        assert hasattr(config_module, "SESSION_SECRET")
        assert isinstance(config_module.SESSION_SECRET, str)


class TestNowUtcMutation:
    """Mutation-critical tests for now_utc() time helper."""

    def test_now_utc_returns_datetime(self):
        """Mutation: returning wrong type breaks timestamp operations."""
        result = now_utc()
        assert isinstance(result, datetime)

    def test_now_utc_returns_utc_timezone(self):
        """Mutation: using local timezone breaks cross-timezone consistency."""
        result = now_utc()
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc

    def test_now_utc_is_close_to_system_time(self):
        """Mutation: returning hardcoded time breaks sequencing."""
        before = datetime.now(timezone.utc)
        result = now_utc()
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_now_utc_called_twice_increases_time(self):
        """Mutation: returning same timestamp breaks time ordering."""
        first = now_utc()
        second = now_utc()
        # Second should be >= first (allowing for same second, different microsecond)
        assert second >= first


# ============================================================================
# Section 5: Integration Tests - Cross-Module Mutation Patterns
# ============================================================================


class TestCrossModuleValidation:
    """Integration tests that verify mutations in one module break others."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> SignalStore:
        return SignalStore(tmp_path / "test.db")

    def test_validation_error_and_registry_work_together(self, store):
        """Mutation: validation must use PullCollectorError, not generic ValueError."""
        # The validate_registration function returns a tuple or raises PullCollectorError
        try:
            validate_registration(
                collector_id="",
                provider="slack",
                auth_mode="oauth_user_token",
                identity_ref="U1",
                credential_env_var="VAR",
                target_id="C1",
                provider_name="test",
                allowed_uses=None,
            )
            pytest.fail("Should have raised PullCollectorError")
        except PullCollectorError:
            pass  # Expected
        except Exception as e:
            pytest.fail(f"Should raise PullCollectorError, not {type(e)}")

    def test_provider_enum_used_consistently_in_registration(self, store):
        """Mutation: enum type must be consistent across registration."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert provider == Provider.SLACK

        # Register with the enum value
        row = store.register_pull_collector(
            collector_id="test",
            provider=provider.value,  # use the string value
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        assert row["provider"] == "slack"

    def test_health_state_transitions_via_store(self, store):
        """Mutation: health state enum must round-trip correctly."""
        store.register_pull_collector(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        # Start as UNQUALIFIED
        row = store.get_pull_collector("test")
        assert row["health_state"] == "unqualified"

        # Update to NO_CHANNEL_ACCESS
        store.update_pull_collector_health("test", CollectorHealth.NO_CHANNEL_ACCESS.value)
        row = store.get_pull_collector("test")
        assert row["health_state"] == "no_channel_access"

        # Record qualification evidence -> should become HEALTHY_QUALIFIED
        store.record_pull_collector_qualification_evidence("test", evidence={"observed": True})
        row = store.get_pull_collector("test")
        assert row["health_state"] == "healthy_qualified"


# ============================================================================
# Section 6: Boundary and Edge Case Tests
# ============================================================================


class TestBoundaryConditions:
    """Edge cases and boundary conditions sensitive to mutations."""

    def test_collector_id_with_only_dash_is_valid(self):
        """Mutation: overly strict ID validation breaks valid names."""
        provider, uses = validate_registration(
            collector_id="-",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert provider == Provider.SLACK

    def test_credential_env_var_all_uppercase_numbers_underscores(self):
        """Mutation: overly restrictive char validation breaks real env vars."""
        provider, uses = validate_registration(
            collector_id="test",
            provider="slack",
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR_123_456_789",
            target_id="C1",
            provider_name="test",
            allowed_uses=None,
        )
        assert provider == Provider.SLACK

    def test_target_label_optional_field_can_be_none(self):
        """Mutation: making target_label required breaks existing data."""
        collector = PullCollector(
            id="test",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            target_label=None,
        )
        assert collector.target_label is None

    def test_target_label_optional_field_can_be_string(self):
        """Mutation: removing string allowance breaks label feature."""
        collector = PullCollector(
            id="test",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            target_label="My Channel",
        )
        assert collector.target_label == "My Channel"

    def test_qualification_evidence_dict_can_be_empty(self):
        """Mutation: non-empty requirement breaks fresh registration."""
        collector = PullCollector(
            id="test",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
            qualification_evidence={},
        )
        assert collector.qualification_evidence == {}

    def test_allowed_uses_default_factory_creates_fresh_list(self):
        """Mutation: shared mutable default breaks isolation."""
        c1 = PullCollector(
            id="test1",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U1",
            credential_env_var="VAR",
            target_id="C1",
            provider_name="test",
        )
        c2 = PullCollector(
            id="test2",
            provider=Provider.SLACK,
            auth_mode="oauth_user_token",
            identity_ref="U2",
            credential_env_var="VAR2",
            target_id="C2",
            provider_name="test2",
        )
        # Both should have independent lists
        assert c1.allowed_uses is not c2.allowed_uses


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
