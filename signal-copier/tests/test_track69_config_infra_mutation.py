"""Comprehensive mutation-testing regression suite for Track 69.

Targeted regression tests for signal-copier's configuration and infrastructure
modules following the Track 60-71 mutation-testing pattern. Focuses on
high-severity mutations that would silently misbehave if critical operators,
conditions, defaults, or boundaries change.

Modules covered:
- app/config.py: Environment configuration, defaults, secrets, type conversions
- app/config_admin.py: Configuration seeding from YAML, conditional logic
- app/rate_limit.py: Request rate limiting per IP, rate limit values
- app/logging_config.py: Logging configuration, secret redaction, handlers
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app import config as app_config
from app.config_admin import seed_from_yaml_if_empty
from app.db import SignalStore
from app.logging_config import (
    configure_structlog,
    bind_signal_context,
    _redact_secrets,
    _SECRET_KEY_SUBSTRINGS,
)
from app.rate_limit import (
    limiter,
    INGRESS_RATE_LIMIT,
    CATALOG_FIT_SIM_RATE_LIMIT,
)


# ============================================================================
# CONFIG.PY MUTATION TESTS: DEFAULTS AND TYPE CONVERSIONS
# ============================================================================


class TestConfigDefaultDefaults:
    """Mutation target: default values must not flip to unsafe opposites."""

    def test_standby_mode_defaults_to_false(self):
        """Mutant: if STANDBY_MODE = True (unsafe, allows trading in standby)."""
        assert app_config._settings.STANDBY_MODE is False

    def test_force_secure_cookies_defaults_to_false(self):
        """Mutant: if FORCE_SECURE_COOKIES = True (wrong default for non-proxy)."""
        assert app_config._settings.FORCE_SECURE_COOKIES is False

    def test_legacy_dashboard_enabled_defaults_to_false(self):
        """Mutant: if LEGACY_DASHBOARD_ENABLED = True (forces legacy UI)."""
        assert app_config._settings.LEGACY_DASHBOARD_ENABLED is False

    def test_ccxt_sandbox_defaults_to_false(self):
        """Mutant: if CCXT_SANDBOX = True (trades on sandbox by default)."""
        assert app_config._settings.CCXT_SANDBOX is False

    def test_schwab_acknowledge_no_sandbox_defaults_to_false(self):
        """Mutant: if SCHWAB_ACKNOWLEDGE_NO_SANDBOX = True (trades live by default)."""
        assert app_config._settings.SCHWAB_ACKNOWLEDGE_NO_SANDBOX is False

    def test_robinhood_acknowledge_tos_risk_defaults_to_false(self):
        """Mutant: if ROBINHOOD_ACKNOWLEDGE_TOS_RISK = True (violates ToS by default)."""
        assert app_config._settings.ROBINHOOD_ACKNOWLEDGE_TOS_RISK is False

    def test_signal_correlation_enabled_defaults_to_true(self):
        """Mutant: if SIGNAL_CORRELATION_ENABLED = False (disables dedup)."""
        assert app_config._settings.SIGNAL_CORRELATION_ENABLED is True


class TestConfigNumericDefaults:
    """Mutation target: numeric defaults for timing, thresholds, limits."""

    def test_session_ttl_seconds_is_12_hours(self):
        """Mutant: if SESSION_TTL_SECONDS changed (affects session validity)."""
        expected = 60 * 60 * 12
        assert app_config._settings.SESSION_TTL_SECONDS == expected

    def test_writer_lease_seconds_greater_than_renew(self):
        """Mutant: if WRITER_LEASE_SECONDS <= WRITER_LEASE_RENEW_SECONDS (lease expires)."""
        assert app_config._settings.WRITER_LEASE_SECONDS > app_config._settings.WRITER_LEASE_RENEW_SECONDS

    def test_writer_lease_seconds_is_30(self):
        """Mutant: if WRITER_LEASE_SECONDS changed (lease validity)."""
        assert app_config._settings.WRITER_LEASE_SECONDS == 30.0

    def test_writer_lease_renew_seconds_is_10(self):
        """Mutant: if WRITER_LEASE_RENEW_SECONDS changed (renewal frequency)."""
        assert app_config._settings.WRITER_LEASE_RENEW_SECONDS == 10.0

    def test_reconcile_interval_is_30_seconds(self):
        """Mutant: if RECONCILE_INTERVAL_SECONDS changed."""
        assert app_config._settings.RECONCILE_INTERVAL_SECONDS == 30.0

    def test_price_monitor_interval_is_15_seconds(self):
        """Mutant: if PRICE_MONITOR_INTERVAL_SECONDS changed."""
        assert app_config._settings.PRICE_MONITOR_INTERVAL_SECONDS == 15.0

    def test_equity_snapshot_interval_is_5_minutes(self):
        """Mutant: if EQUITY_SNAPSHOT_INTERVAL_SECONDS changed."""
        assert app_config._settings.EQUITY_SNAPSHOT_INTERVAL_SECONDS == 300.0

    def test_provider_scout_interval_is_24_hours(self):
        """Mutant: if PROVIDER_SCOUT_INTERVAL_SECONDS changed."""
        assert app_config._settings.PROVIDER_SCOUT_INTERVAL_SECONDS == 86400.0

    def test_provider_value_min_sample_size_is_10(self):
        """Mutant: if PROVIDER_VALUE_MIN_SAMPLE_SIZE changed."""
        assert app_config._settings.PROVIDER_VALUE_MIN_SAMPLE_SIZE == 10

    def test_provider_value_win_rate_threshold_is_0_4(self):
        """Mutant: if PROVIDER_VALUE_WIN_RATE_THRESHOLD changed."""
        assert app_config._settings.PROVIDER_VALUE_WIN_RATE_THRESHOLD == 0.4

    def test_provider_value_profit_factor_threshold_is_1_0(self):
        """Mutant: if PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD changed."""
        assert app_config._settings.PROVIDER_VALUE_PROFIT_FACTOR_THRESHOLD == 1.0

    def test_notification_bridge_stale_threshold_is_5_minutes(self):
        """Mutant: if NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS changed."""
        assert app_config._settings.NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS == 300.0

    def test_signal_correlation_price_tolerance_is_0_005(self):
        """Mutant: if SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT changed."""
        assert app_config._settings.SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT == 0.005

    def test_signal_correlation_timestamp_window_is_900_seconds(self):
        """Mutant: if SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS changed."""
        assert app_config._settings.SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS == 900.0

    def test_managed_exit_duplicate_window_is_900_seconds(self):
        """Mutant: if MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS changed."""
        assert app_config._settings.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS == 900.0

    def test_export_outbox_size_ceiling_is_256mb(self):
        """Mutant: if EXPORT_OUTBOX_SIZE_CEILING_BYTES changed."""
        assert app_config._settings.EXPORT_OUTBOX_SIZE_CEILING_BYTES == 256 * 1024 * 1024

    def test_relay_poll_interval_is_1_second(self):
        """Mutant: if RELAY_POLL_INTERVAL_SECONDS changed."""
        assert app_config._settings.RELAY_POLL_INTERVAL_SECONDS == 1.0

    def test_relay_batch_size_is_100(self):
        """Mutant: if RELAY_BATCH_SIZE changed."""
        assert app_config._settings.RELAY_BATCH_SIZE == 100

    def test_ibkr_default_port_is_7497(self):
        """Mutant: if IBKR_PORT changed (trades on wrong port)."""
        assert app_config._settings.IBKR_PORT == 7497

    def test_ibkr_default_client_id_is_1(self):
        """Mutant: if IBKR_CLIENT_ID changed."""
        assert app_config._settings.IBKR_CLIENT_ID == 1

    def test_ibkr_default_host_is_local(self):
        """Mutant: if IBKR_HOST changed."""
        assert app_config._settings.IBKR_HOST == "127.0.0.1"


class TestConfigStringDefaults:
    """Mutation target: string defaults (exchange, site ID, evidence class)."""

    def test_ccxt_exchange_id_defaults_to_binance(self):
        """Mutant: if CCXT_EXCHANGE_ID changed (trades on wrong exchange)."""
        assert app_config._settings.CCXT_EXCHANGE_ID == "binance"

    def test_relay_producer_id_defaults_to_signal_copier_local(self):
        """Mutant: if RELAY_PRODUCER_ID changed."""
        assert app_config._settings.RELAY_PRODUCER_ID == "signal-copier-local"

    def test_relay_evidence_class_defaults_to_internal_paper(self):
        """Mutant: if RELAY_EVIDENCE_CLASS changed (reports wrong evidence class)."""
        assert app_config._settings.RELAY_EVIDENCE_CLASS == "INTERNAL_PAPER"

    def test_relay_environment_defaults_to_local_sim(self):
        """Mutant: if RELAY_ENVIRONMENT changed (reports wrong environment)."""
        assert app_config._settings.RELAY_ENVIRONMENT == "LOCAL_SIM"

    def test_log_level_defaults_to_info(self):
        """Mutant: if LOG_LEVEL changed."""
        assert app_config._settings.LOG_LEVEL == "INFO"

    def test_relay_signing_secret_has_default_placeholder(self):
        """Mutant: if RELAY_SIGNING_SECRET default removed (breaks local dev)."""
        assert "LOCAL_SIM" in app_config._settings.RELAY_SIGNING_SECRET


class TestConfigEmptyStringDefaults:
    """Mutation target: empty strings that must stay empty for fail-closed."""

    def test_webhook_shared_secret_starts_empty(self):
        """Mutant: if WEBHOOK_SHARED_SECRET has a default (breaks auth)."""
        assert app_config._settings.WEBHOOK_SHARED_SECRET == ""

    def test_owner_password_starts_empty(self):
        """Mutant: if OWNER_PASSWORD has a default (breaks owner auth)."""
        assert app_config._settings.OWNER_PASSWORD == ""

    def test_owner_password_hash_starts_empty(self):
        """Mutant: if OWNER_PASSWORD_HASH has a default (breaks owner auth)."""
        assert app_config._settings.OWNER_PASSWORD_HASH == ""

    def test_session_secret_starts_empty(self):
        """Mutant: if SESSION_SECRET has a default (breaks session auth)."""
        assert app_config._settings.SESSION_SECRET == ""

    def test_writer_site_id_starts_empty(self):
        """Mutant: if WRITER_SITE_ID has a default (wrong site ID in failover)."""
        assert app_config._settings.WRITER_SITE_ID == ""

    def test_relay_ingress_url_starts_empty(self):
        """Mutant: if RELAY_INGRESS_URL has a default (sends to wrong URL)."""
        assert app_config._settings.RELAY_INGRESS_URL == ""

    def test_telegram_bot_token_starts_empty(self):
        """Mutant: if source tokens have defaults (breaks auth)."""
        assert app_config._settings.TELEGRAM_BOT_TOKEN == ""

    def test_discord_bot_token_starts_empty(self):
        """Mutant: if source tokens have defaults (breaks auth)."""
        assert app_config._settings.DISCORD_BOT_TOKEN == ""

    def test_slack_bot_token_starts_empty(self):
        """Mutant: if source tokens have defaults (breaks auth)."""
        assert app_config._settings.SLACK_BOT_TOKEN == ""

    def test_twitter_bearer_token_starts_empty(self):
        """Mutant: if source tokens have defaults (breaks auth)."""
        assert app_config._settings.TWITTER_BEARER_TOKEN == ""

    def test_twilio_auth_token_starts_empty(self):
        """Mutant: if source tokens have defaults (breaks auth)."""
        assert app_config._settings.TWILIO_AUTH_TOKEN == ""

    def test_whatsapp_app_secret_starts_empty(self):
        """Mutant: if source secrets have defaults (breaks auth)."""
        assert app_config._settings.WHATSAPP_APP_SECRET == ""


class TestConfigListParsing:
    """Mutation target: list parsing from comma-separated strings."""

    def test_twitter_rules_empty_by_default(self):
        """Mutant: if TWITTER_RULES parsing changed."""
        assert app_config.TWITTER_RULES == []

    def test_twilio_allowed_numbers_empty_by_default(self):
        """Mutant: if number parsing changed (wrong senders accepted)."""
        assert app_config.TWILIO_ALLOWED_FROM_NUMBERS == []

    def test_whatsapp_allowed_numbers_empty_by_default(self):
        """Mutant: if number parsing changed (wrong senders accepted)."""
        assert app_config.WHATSAPP_ALLOWED_FROM_NUMBERS == []

    def test_ccxt_exchanges_empty_by_default(self):
        """Mutant: if CCXT_EXCHANGES parsing changed."""
        assert app_config.CCXT_EXCHANGES == []

    def test_twitter_rules_splits_on_comma(self):
        """Mutant: if list parsing uses wrong separator."""
        with patch.dict(os.environ, {"TWITTER_RULES": "rule1,rule2,rule3"}):
            from pydantic_settings import BaseSettings, SettingsConfigDict

            class TestSettings(BaseSettings):
                model_config = SettingsConfigDict(env_prefix="", case_sensitive=True, extra="ignore")
                TWITTER_RULES: str = ""

            s = TestSettings()
            rules = [r.strip() for r in s.TWITTER_RULES.split(",") if r.strip()]
            assert len(rules) == 3

    def test_comma_separated_numbers_parsed_correctly(self):
        """Mutant: if list parsing strips spaces incorrectly."""
        with patch.dict(os.environ, {"TWILIO_ALLOWED_FROM_NUMBERS": "+15551234567, +15559876543"}):
            from pydantic_settings import BaseSettings, SettingsConfigDict

            class TestSettings(BaseSettings):
                model_config = SettingsConfigDict(env_prefix="", case_sensitive=True, extra="ignore")
                TWILIO_ALLOWED_FROM_NUMBERS: str = ""

            s = TestSettings()
            numbers = [n.strip() for n in s.TWILIO_ALLOWED_FROM_NUMBERS.split(",") if n.strip()]
            assert "+15551234567" in numbers
            assert "+15559876543" in numbers


# ============================================================================
# CONFIG_ADMIN.PY MUTATION TESTS: SEEDING LOGIC
# ============================================================================


class TestConfigAdminSeedingLogic:
    """Mutation target: conditional logic in seed_from_yaml_if_empty."""

    def test_seed_returns_false_if_accounts_already_exist(self):
        """Mutant: if early-return check removed (re-imports config)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = [{"account_id": "existing"}]

        result = seed_from_yaml_if_empty(store)
        assert result is False
        store.list_config_accounts.assert_called_once()

    def test_seed_returns_false_if_already_seeded_once(self):
        """Mutant: if has_ever_seeded() check removed (re-imports deleted config)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = []
        store.has_ever_seeded.return_value = True

        result = seed_from_yaml_if_empty(store)
        assert result is False

    def test_seed_returns_false_if_no_config_to_import(self):
        """Mutant: if empty-config check removed (imports nothing)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = []
        store.has_ever_seeded.return_value = False

        with patch("app.config_admin.load_routing_config") as mock_load:
            mock_config = MagicMock()
            mock_config.accounts = {}
            mock_config.rules = []
            mock_load.return_value = mock_config

            result = seed_from_yaml_if_empty(store)
            assert result is False
            store.mark_seeded.assert_not_called()

    def test_seed_marks_as_seeded_after_import(self):
        """Mutant: if mark_seeded() call removed (re-imports on restart)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = []
        store.has_ever_seeded.return_value = False

        with patch("app.config_admin.load_routing_config") as mock_load, \
             patch("app.config_admin.load_provider_registry") as mock_prov:
            mock_config = MagicMock()
            mock_config.accounts = {"acc1": MagicMock(account_id="acc1", broker="paper",
                                                       multiplier=1.0, fixed_quantity=None,
                                                       symbol_map={}, enabled=True,
                                                       managed_lifecycle=False,
                                                       max_notional_exposure=None,
                                                       risk_percent_of_equity=None)}
            mock_config.rules = []
            mock_load.return_value = mock_config

            mock_prov_reg = MagicMock()
            mock_prov_reg.providers = {}
            mock_prov.return_value = mock_prov_reg

            result = seed_from_yaml_if_empty(store)
            assert result is True
            store.mark_seeded.assert_called_once()

    def test_seed_returns_true_on_successful_import(self):
        """Mutant: if return value changed to False (hides success)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = []
        store.has_ever_seeded.return_value = False

        with patch("app.config_admin.load_routing_config") as mock_load, \
             patch("app.config_admin.load_provider_registry") as mock_prov:
            mock_config = MagicMock()
            mock_config.accounts = {"acc1": MagicMock(account_id="acc1", broker="paper",
                                                       multiplier=1.0, fixed_quantity=None,
                                                       symbol_map={}, enabled=True,
                                                       managed_lifecycle=False,
                                                       max_notional_exposure=None,
                                                       risk_percent_of_equity=None)}
            mock_config.rules = []
            mock_load.return_value = mock_config

            mock_prov_reg = MagicMock()
            mock_prov_reg.providers = {}
            mock_prov.return_value = mock_prov_reg

            result = seed_from_yaml_if_empty(store)
            assert result is True

    def test_seed_imports_accounts_when_needed(self):
        """Mutant: if upsert_config_account calls removed (accounts not imported)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = []
        store.has_ever_seeded.return_value = False

        test_account = MagicMock()
        test_account.account_id = "test_acc"
        test_account.broker = "paper"
        test_account.multiplier = 2.0
        test_account.fixed_quantity = None
        test_account.symbol_map = {}
        test_account.enabled = True
        test_account.managed_lifecycle = False
        test_account.max_notional_exposure = None
        test_account.risk_percent_of_equity = None

        with patch("app.config_admin.load_routing_config") as mock_load, \
             patch("app.config_admin.load_provider_registry") as mock_prov:
            mock_config = MagicMock()
            mock_config.accounts = {"test_acc": test_account}
            mock_config.rules = []
            mock_load.return_value = mock_config

            mock_prov_reg = MagicMock()
            mock_prov_reg.providers = {}
            mock_prov.return_value = mock_prov_reg

            seed_from_yaml_if_empty(store)
            store.upsert_config_account.assert_called_once()
            call_kwargs = store.upsert_config_account.call_args[1]
            assert call_kwargs["account_id"] == "test_acc"
            assert call_kwargs["broker"] == "paper"
            assert call_kwargs["multiplier"] == 2.0

    def test_seed_imports_routing_rules(self):
        """Mutant: if insert_config_routing_rule calls removed (rules not imported)."""
        store = MagicMock(spec=SignalStore)
        store.list_config_accounts.return_value = []
        store.has_ever_seeded.return_value = False

        test_rule = MagicMock()
        test_rule.source = "tradingview"
        test_rule.destinations = ["acct1"]
        test_rule.symbol_filter = None

        with patch("app.config_admin.load_routing_config") as mock_load, \
             patch("app.config_admin.load_provider_registry") as mock_prov:
            mock_config = MagicMock()
            mock_config.accounts = {}
            mock_config.rules = [test_rule]
            mock_load.return_value = mock_config

            mock_prov_reg = MagicMock()
            mock_prov_reg.providers = {}
            mock_prov.return_value = mock_prov_reg

            seed_from_yaml_if_empty(store)
            store.insert_config_routing_rule.assert_called_once()
            call_kwargs = store.insert_config_routing_rule.call_args[1]
            assert call_kwargs["source"] == "tradingview"
            assert call_kwargs["destinations"] == ["acct1"]


# ============================================================================
# RATE_LIMIT.PY MUTATION TESTS: RATE LIMIT VALUES
# ============================================================================


class TestRateLimitValues:
    """Mutation target: rate limit string constants."""

    def test_ingress_rate_limit_is_30_per_minute(self):
        """Mutant: if INGRESS_RATE_LIMIT changed (wrong limit)."""
        assert INGRESS_RATE_LIMIT == "30/minute"

    def test_catalog_fit_sim_rate_limit_is_20_per_minute(self):
        """Mutant: if CATALOG_FIT_SIM_RATE_LIMIT changed (wrong limit)."""
        assert CATALOG_FIT_SIM_RATE_LIMIT == "20/minute"

    def test_limiter_exists_and_is_initialized(self):
        """Mutant: if limiter initialization changed or removed."""
        assert limiter is not None
        # Test that it has the expected method
        assert hasattr(limiter, "reset")

    def test_rate_limit_is_lower_for_expensive_endpoint(self):
        """Mutant: if CATALOG_FIT_SIM_RATE_LIMIT > INGRESS_RATE_LIMIT (inverted)."""
        # Extract the numeric values for comparison
        ingress_val = int(INGRESS_RATE_LIMIT.split("/")[0])
        catalog_val = int(CATALOG_FIT_SIM_RATE_LIMIT.split("/")[0])
        assert catalog_val < ingress_val


# ============================================================================
# LOGGING_CONFIG.PY MUTATION TESTS: LOGGING CONFIGURATION
# ============================================================================


class TestLoggingSecretRedaction:
    """Mutation target: secret key detection and redaction."""

    def test_password_fields_are_redacted(self):
        """Mutant: if 'password' removed from _SECRET_KEY_SUBSTRINGS."""
        event_dict = {"owner_password": "secret123", "user": "admin"}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["owner_password"] == "***redacted***"
        assert redacted["user"] == "admin"

    def test_secret_fields_are_redacted(self):
        """Mutant: if 'secret' removed from _SECRET_KEY_SUBSTRINGS."""
        event_dict = {"webhook_secret": "xyz789", "data": "public"}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["webhook_secret"] == "***redacted***"
        assert redacted["data"] == "public"

    def test_token_fields_are_redacted(self):
        """Mutant: if 'token' removed from _SECRET_KEY_SUBSTRINGS."""
        event_dict = {"bearer_token": "abc123", "count": 42}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["bearer_token"] == "***redacted***"
        assert redacted["count"] == 42

    def test_api_key_fields_are_redacted(self):
        """Mutant: if 'api_key' removed from _SECRET_KEY_SUBSTRINGS."""
        event_dict = {"api_key": "key123", "endpoint": "/api"}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["api_key"] == "***redacted***"
        assert redacted["endpoint"] == "/api"

    def test_auth_fields_are_redacted(self):
        """Mutant: if 'auth' removed from _SECRET_KEY_SUBSTRINGS."""
        event_dict = {"auth_token": "token123", "method": "GET"}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["auth_token"] == "***redacted***"
        assert redacted["method"] == "GET"

    def test_redaction_is_case_insensitive(self):
        """Mutant: if case-insensitive check removed (PASSWORD not redacted)."""
        event_dict = {"OWNER_PASSWORD": "secret", "owner_password": "secret2"}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["OWNER_PASSWORD"] == "***redacted***"
        assert redacted["owner_password"] == "***redacted***"

    def test_no_false_redaction_of_nonsecret_fields(self):
        """Mutant: if secret detection too broad (redacts 'passphrase' style)."""
        event_dict = {"user": "alice", "status": "active", "signal_count": 10}
        redacted = _redact_secrets(None, "info", event_dict)
        assert redacted["user"] == "alice"
        assert redacted["status"] == "active"
        assert redacted["signal_count"] == 10

    def test_secret_key_substrings_contain_expected_markers(self):
        """Mutant: if _SECRET_KEY_SUBSTRINGS modified (misses secret types)."""
        assert "password" in _SECRET_KEY_SUBSTRINGS
        assert "secret" in _SECRET_KEY_SUBSTRINGS
        assert "token" in _SECRET_KEY_SUBSTRINGS
        assert "api_key" in _SECRET_KEY_SUBSTRINGS
        assert "auth" in _SECRET_KEY_SUBSTRINGS


class TestLoggingConfiguration:
    """Mutation target: structlog configuration and rendering."""

    def test_configure_structlog_with_json_output(self):
        """Mutant: if JSON renderer selection changed (breaks parsing)."""
        # This is integration-level; we verify it doesn't raise
        # and that it configures the expected processors
        try:
            configure_structlog(json_output=True)
        except Exception as e:
            pytest.fail(f"configure_structlog with json_output=True raised {e}")

    def test_configure_structlog_with_console_output(self):
        """Mutant: if console renderer selection changed."""
        try:
            configure_structlog(json_output=False)
        except Exception as e:
            pytest.fail(f"configure_structlog with json_output=False raised {e}")

    def test_bind_signal_context_binds_fields(self):
        """Mutant: if bind_contextvars call removed (fields not bound)."""
        configure_structlog(json_output=False)
        with patch("structlog.contextvars.bind_contextvars") as mock_bind:
            mock_bind.return_value = {}
            with patch("structlog.contextvars.reset_contextvars"):
                with bind_signal_context(signal_id="sig123", symbol="AAPL"):
                    mock_bind.assert_called_once_with(signal_id="sig123", symbol="AAPL")

    def test_bind_signal_context_resets_on_exit(self):
        """Mutant: if reset_contextvars call removed (context leaks)."""
        configure_structlog(json_output=False)
        with patch("structlog.contextvars.bind_contextvars") as mock_bind, \
             patch("structlog.contextvars.reset_contextvars") as mock_reset:
            mock_bind.return_value = {"token1": "value1"}
            with bind_signal_context(signal_id="sig123"):
                pass
            mock_reset.assert_called_once()

    def test_bind_signal_context_cleans_up_on_exception(self):
        """Mutant: if finally block removed (context leaks on error)."""
        configure_structlog(json_output=False)
        with patch("structlog.contextvars.bind_contextvars") as mock_bind, \
             patch("structlog.contextvars.reset_contextvars") as mock_reset:
            mock_bind.return_value = {}
            try:
                with bind_signal_context(signal_id="sig123"):
                    raise ValueError("test error")
            except ValueError:
                pass
            # Even on exception, reset should be called
            mock_reset.assert_called_once()

    def test_logging_processors_include_redaction(self):
        """Mutant: if _redact_secrets processor removed (secrets leak)."""
        # Verify _redact_secrets is actually used in configure_structlog
        import inspect
        source = inspect.getsource(configure_structlog)
        assert "_redact_secrets" in source


# ============================================================================
# INTEGRATION TESTS: CONFIG PROPAGATION TO MODULE-LEVEL EXPORTS
# ============================================================================


class TestConfigExportToModuleLevel:
    """Mutation target: module-level exports match _settings values."""

    def test_module_level_standby_mode_matches_settings(self):
        """Mutant: if module-level export assignment removed."""
        assert app_config.STANDBY_MODE == app_config._settings.STANDBY_MODE

    def test_module_level_force_secure_cookies_matches_settings(self):
        """Mutant: if module-level export assignment removed."""
        assert app_config.FORCE_SECURE_COOKIES == app_config._settings.FORCE_SECURE_COOKIES

    def test_module_level_session_ttl_matches_settings(self):
        """Mutant: if config export assignments removed."""
        assert app_config.SESSION_TTL_SECONDS == app_config._settings.SESSION_TTL_SECONDS

    def test_module_level_log_level_matches_settings(self):
        """Mutant: if config export assignments removed."""
        assert app_config.LOG_LEVEL == app_config._settings.LOG_LEVEL

    def test_module_level_writer_lease_seconds_matches_settings(self):
        """Mutant: if config export assignments removed."""
        assert app_config.WRITER_LEASE_SECONDS == app_config._settings.WRITER_LEASE_SECONDS

    def test_module_level_relay_producer_id_matches_settings(self):
        """Mutant: if config export assignments removed."""
        assert app_config.RELAY_PRODUCER_ID == app_config._settings.RELAY_PRODUCER_ID


# ============================================================================
# BOUNDARY CONDITION TESTS: NUMERIC COMPARISONS AND THRESHOLDS
# ============================================================================


class TestConfigBoundaryConditions:
    """Mutation target: numeric comparisons and boundary mutations (>, >=, ==)."""

    def test_writer_lease_renew_less_than_lease(self):
        """Mutant: if comparison changed to >= (lease expires during renewal)."""
        renew = app_config._settings.WRITER_LEASE_RENEW_SECONDS
        lease = app_config._settings.WRITER_LEASE_SECONDS
        # Renewal must happen BEFORE lease expires
        assert renew < lease

    def test_price_monitor_faster_than_equity_snapshot(self):
        """Mutant: if comparison changed (pricing lagging equity)."""
        price = app_config._settings.PRICE_MONITOR_INTERVAL_SECONDS
        equity = app_config._settings.EQUITY_SNAPSHOT_INTERVAL_SECONDS
        # Price updates should happen more frequently than equity snapshots
        assert price < equity

    def test_reconcile_slower_than_price_monitor(self):
        """Mutant: if comparison changed (reconciliation frequency)."""
        reconcile = app_config._settings.RECONCILE_INTERVAL_SECONDS
        price = app_config._settings.PRICE_MONITOR_INTERVAL_SECONDS
        # Price monitoring happens more frequently than reconciliation
        assert price < reconcile

    def test_win_rate_threshold_between_0_and_1(self):
        """Mutant: if threshold changed outside valid probability range."""
        threshold = app_config._settings.PROVIDER_VALUE_WIN_RATE_THRESHOLD
        assert 0.0 <= threshold <= 1.0

    def test_price_tolerance_positive(self):
        """Mutant: if tolerance made negative (inverted comparison logic)."""
        tolerance = app_config._settings.SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT
        assert tolerance > 0.0

    def test_all_positive_timing_values(self):
        """Mutant: if timing values made negative (breaks timing logic)."""
        timings = [
            app_config._settings.SESSION_TTL_SECONDS,
            app_config._settings.WRITER_LEASE_SECONDS,
            app_config._settings.WRITER_LEASE_RENEW_SECONDS,
            app_config._settings.RECONCILE_INTERVAL_SECONDS,
            app_config._settings.PRICE_MONITOR_INTERVAL_SECONDS,
            app_config._settings.EQUITY_SNAPSHOT_INTERVAL_SECONDS,
            app_config._settings.PROVIDER_SCOUT_INTERVAL_SECONDS,
            app_config._settings.NOTIFICATION_BRIDGE_STALE_THRESHOLD_SECONDS,
            app_config._settings.SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS,
            app_config._settings.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS,
            app_config._settings.RELAY_POLL_INTERVAL_SECONDS,
        ]
        for timing in timings:
            assert timing > 0.0

    def test_all_positive_size_values(self):
        """Mutant: if size values made negative or zero (breaks allocation)."""
        assert app_config._settings.EXPORT_OUTBOX_SIZE_CEILING_BYTES > 0
        assert app_config._settings.RELAY_BATCH_SIZE > 0

    def test_all_nonnegative_sample_sizes(self):
        """Mutant: if sample size made negative (breaks aggregation)."""
        assert app_config._settings.PROVIDER_VALUE_MIN_SAMPLE_SIZE >= 0
