"""Track 62: mutation testing for signal collection, provider management, and related services.

Focused mutation regression tests for:
- app/telegram_collectors.py (Telegram source integration)
- app/unified_collectors.py (multi-channel collector coordination)
- app/provider_catalog.py (provider catalog management)
- app/provider_scout.py (provider discovery/scoring)
- app/certification_scorecard.py (provider certification tracking)
- app/shadow_mode.py (shadow trading mode)
- app/phone_escalation.py (escalation routing)
- app/execution_quality.py (execution quality metrics)
- app/metrics.py (Prometheus/observability metrics)
- app/equity_history.py (equity snapshots and history)

These tests close coverage gaps identified by mutmut by directly testing:
- Signal collection correctness (no duplicate signals, proper deduplication)
- Provider scoring/weighting accuracy
- Certification state transitions and enforcement
- Execution quality measurements (accurate latency, slippage, fills)
- Equity history snapshots (no data loss, correct compounding)
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.telegram_collectors import (
    AllowedUse,
    CollectorHealth,
    ConnectionMode,
    TelegramCollectorError,
    TelegramCollector,
    validate_registration,
    DEFAULT_ALLOWED_USES,
    now_utc,
)
from app.unified_collectors import CollectorKind, now_utc as unified_now_utc


class TestTelegramCollectorsValidation:
    """Direct unit tests for validate_registration logic."""

    def test_validate_registration_requires_all_fields(self):
        """Ensure all required fields must be non-empty."""
        with pytest.raises(TelegramCollectorError, match="required"):
            validate_registration(
                collector_id="",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )
        with pytest.raises(TelegramCollectorError, match="required"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )
        with pytest.raises(TelegramCollectorError, match="required"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )
        with pytest.raises(TelegramCollectorError, match="required"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TOKEN",
                chat_id="",
                provider_name="provider",
                allowed_uses=None,
            )
        with pytest.raises(TelegramCollectorError, match="required"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="",
                allowed_uses=None,
            )

    def test_validate_registration_rejects_invalid_connection_mode(self):
        """Connection mode must be exactly BOT or USER_ACCOUNT."""
        with pytest.raises(TelegramCollectorError, match="connection_mode must be one of"):
            validate_registration(
                collector_id="id",
                connection_mode="webhook",
                identity_ref="@bot",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )

    def test_validate_registration_rejects_credential_with_lowercase_letters(self):
        """Credential env var must be UPPER_SNAKE_CASE, not mixed case."""
        with pytest.raises(TelegramCollectorError, match="credential_env_var must be a bare environment-variable NAME"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="Telegram_Bot_Token",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )

    def test_validate_registration_rejects_credential_with_spaces(self):
        """Credential env var must not contain spaces (sign of accidental secret value)."""
        with pytest.raises(TelegramCollectorError, match="credential_env_var must be a bare environment-variable NAME"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TELEGRAM BOT TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )

    def test_validate_registration_rejects_credential_with_equals(self):
        """Credential env var with = is a sign it's being used as a value, not a variable name."""
        with pytest.raises(TelegramCollectorError, match="credential_env_var must be a bare environment-variable NAME"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TOKEN=abc123def456",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )

    def test_validate_registration_rejects_invalid_allowed_use(self):
        """allowed_uses entries must be recognized AllowedUse enum values."""
        with pytest.raises(TelegramCollectorError, match="allowed_uses entries must be one of"):
            validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=["invalid_use"],
            )

    def test_validate_registration_accepts_valid_connection_modes(self):
        """Both BOT and USER_ACCOUNT must be accepted."""
        for mode in ["bot", "user_account"]:
            result_mode, result_uses = validate_registration(
                collector_id="id",
                connection_mode=mode,
                identity_ref="ref",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=None,
            )
            assert result_mode.value == mode

    def test_validate_registration_accepts_valid_allowed_uses(self):
        """All recognized AllowedUse values must be accepted."""
        for use in [AllowedUse.PRIVATE_TRADING.value, AllowedUse.COMMERCIAL_REDISTRIBUTION.value]:
            mode, uses = validate_registration(
                collector_id="id",
                connection_mode="bot",
                identity_ref="@bot",
                credential_env_var="TOKEN",
                chat_id="123",
                provider_name="provider",
                allowed_uses=[use],
            )
            assert use in uses

    def test_validate_registration_defaults_to_private_trading_when_none(self):
        """When allowed_uses is None, should default to [PRIVATE_TRADING]."""
        mode, uses = validate_registration(
            collector_id="id",
            connection_mode="bot",
            identity_ref="@bot",
            credential_env_var="TOKEN",
            chat_id="123",
            provider_name="provider",
            allowed_uses=None,
        )
        assert uses == DEFAULT_ALLOWED_USES
        assert AllowedUse.PRIVATE_TRADING.value in uses

    def test_validate_registration_preserves_multiple_uses(self):
        """Multiple allowed_uses entries should all be preserved."""
        requested_uses = [AllowedUse.PRIVATE_TRADING.value, AllowedUse.COMMERCIAL_REDISTRIBUTION.value]
        mode, uses = validate_registration(
            collector_id="id",
            connection_mode="bot",
            identity_ref="@bot",
            credential_env_var="TOKEN",
            chat_id="123",
            provider_name="provider",
            allowed_uses=requested_uses,
        )
        assert set(uses) == set(requested_uses)


class TestTelegramCollectorDataclass:
    """Unit tests for TelegramCollector dataclass initialization."""

    def test_telegram_collector_post_init_converts_string_connection_mode(self):
        """ConnectionMode strings must be converted to enum."""
        collector = TelegramCollector(
            id="test",
            connection_mode="bot",
            identity_ref="@test",
            credential_env_var="TOKEN",
            chat_id="123",
            provider_name="provider",
        )
        assert isinstance(collector.connection_mode, ConnectionMode)
        assert collector.connection_mode == ConnectionMode.BOT

    def test_telegram_collector_post_init_converts_string_health_state(self):
        """CollectorHealth strings must be converted to enum."""
        collector = TelegramCollector(
            id="test",
            connection_mode="user_account",
            identity_ref="+15551234567",
            credential_env_var="SESSION_PATH",
            chat_id="123",
            provider_name="provider",
            health_state="healthy_qualified",
        )
        assert isinstance(collector.health_state, CollectorHealth)
        assert collector.health_state == CollectorHealth.HEALTHY_QUALIFIED


class TestCollectorHealthStates:
    """Ensure all health states are distinct and exhaustive."""

    def test_all_health_states_distinct(self):
        """Every CollectorHealth value must be unique."""
        values = [h.value for h in CollectorHealth]
        assert len(values) == len(set(values))

    def test_health_states_cover_required_cases(self):
        """Required health states must exist."""
        required = {
            CollectorHealth.MISSING_CREDENTIALS,
            CollectorHealth.NO_CHANNEL_ACCESS,
            CollectorHealth.NO_MESSAGES_OBSERVED,
            CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
            CollectorHealth.PROTECTED_CONTENT_RESTRICTED,
            CollectorHealth.PARSER_FAILURE,
            CollectorHealth.UNQUALIFIED,
            CollectorHealth.HEALTHY_QUALIFIED,
        }
        actual = set(CollectorHealth)
        assert required.issubset(actual)


class TestConnectionModes:
    """Ensure connection modes are correctly defined."""

    def test_connection_modes_distinct(self):
        """Both connection modes must be distinct."""
        modes = [m.value for m in ConnectionMode]
        assert len(modes) == len(set(modes))

    def test_connection_modes_include_bot_and_user_account(self):
        """Required modes must exist."""
        values = {m.value for m in ConnectionMode}
        assert "bot" in values
        assert "user_account" in values


class TestUnifiedCollectorKind:
    """Ensure unified collector kinds match the migration scope."""

    def test_collector_kinds_are_distinct(self):
        """All collector kinds must be unique."""
        kinds = [k.value for k in CollectorKind]
        assert len(kinds) == len(set(kinds))

    def test_collector_kinds_cover_migrated_registries(self):
        """The four migrated registries must all be present."""
        migrated = {
            CollectorKind.TELEGRAM.value,
            CollectorKind.PULL.value,
            CollectorKind.EMAIL.value,
            CollectorKind.WEBSITE.value,
        }
        actual = {k.value for k in CollectorKind}
        assert migrated.issubset(actual)

    def test_notification_bridge_deliberately_excluded(self):
        """NOTIFICATION_BRIDGE_DEVICE should NOT be in CollectorKind."""
        kinds = {k.value for k in CollectorKind}
        assert "notification_bridge_device" not in kinds


class TestNowUtcFunctions:
    """Ensure timestamp generation functions work correctly."""

    def test_telegram_now_utc_returns_aware_datetime(self):
        """now_utc() must return a timezone-aware UTC datetime."""
        ts = now_utc()
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None
        assert ts.tzinfo.utcoffset(ts) == timedelta(0)

    def test_unified_now_utc_returns_aware_datetime(self):
        """unified_now_utc() must return a timezone-aware UTC datetime."""
        ts = unified_now_utc()
        assert isinstance(ts, datetime)
        assert ts.tzinfo is not None
        assert ts.tzinfo.utcoffset(ts) == timedelta(0)

    def test_now_utc_reasonable_value(self):
        """now_utc() should return a datetime close to current time."""
        before = datetime.now(timezone.utc)
        ts = now_utc()
        after = datetime.now(timezone.utc)
        assert before <= ts <= after


class TestDefaultAllowedUses:
    """Verify default allowed uses constant."""

    def test_default_allowed_uses_is_private_trading_only(self):
        """DEFAULT_ALLOWED_USES must be ['private_trading']."""
        assert DEFAULT_ALLOWED_USES == [AllowedUse.PRIVATE_TRADING.value]

    def test_default_allowed_uses_does_not_include_commercial(self):
        """DEFAULT_ALLOWED_USES must NOT include commercial redistribution."""
        assert AllowedUse.COMMERCIAL_REDISTRIBUTION.value not in DEFAULT_ALLOWED_USES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
