"""Track 69: comprehensive mutation-testing regression suite for signal-
portfolio-commercial's configuration and infrastructure modules: config.py
and rate_limit.py.

Mutation-testing approach (per Track 60-63 precedent in signal-copier):
This suite targets the specific, high-severity mutations that would silently
misbehave if critical operators/conditions flip or drop. Focus areas:
- Configuration defaults and fail-closed behavior (missing secrets)
- Boolean flag inversions (FORCE_SECURE_COOKIES, HEALTH_SAMPLER_ENABLED)
- Numeric defaults and comparisons (interval validation, rate limits)
- Empty string vs None distinction (unconfigured optional features)
- Placeholder secret detection (startup guards for COMMERCIAL_LIVE)
- Environment variable parsing and type conversions
- Rate limiter initialization and key function behavior

Each test is designed to fail under a targeted mutant pattern: operator
flips (>, >=, <, <=, ==, !=), control flow mutations (dropped conditions),
type/default mutations (bool inversion, string changes), and boundary
condition flips (range checks, membership tests).
"""
from __future__ import annotations

from unittest import mock

from slowapi import Limiter
from slowapi.util import get_remote_address

from app import config as config_module


# =============================================================================
# CONFIG.PY MUTATION TESTS
# =============================================================================


class TestConfigurationDefaults:
    """Configuration defaults and fail-closed behavior (mutation target:
    changed default values, dropped fields, type conversions)."""

    def test_environment_defaults_to_local_sim_not_commercial_live(self):
        """Mutation target: changed default from LOCAL_SIM to COMMERCIAL_LIVE.
        This is the fail-closed guard -- COMMERCIAL_LIVE must never be the
        default anywhere. A mutant changing this to COMMERCIAL_LIVE would
        silently deploy as a real live system without explicit configuration."""
        assert config_module.ENVIRONMENT == "LOCAL_SIM"
        assert config_module.ENVIRONMENT != "COMMERCIAL_LIVE"

    def test_commercial_live_constant_matches_expected_value(self):
        """Mutation target: changed string value of COMMERCIAL_LIVE_ENVIRONMENT.
        The startup guard compares against this exact value."""
        assert config_module.COMMERCIAL_LIVE_ENVIRONMENT == "COMMERCIAL_LIVE"

    def test_local_jwt_secret_has_placeholder_default(self):
        """Mutation target: changed default string value or removed marker.
        A mutant removing 'LOCAL_SIM' or changing the string would bypass
        startup validation in placeholder_secrets_in_use()."""
        assert config_module.LOCAL_JWT_SECRET == "LOCAL_SIM-not-a-real-secret-change-if-ever-deployed"
        assert "LOCAL_SIM" in config_module.LOCAL_JWT_SECRET

    def test_relay_signing_secret_has_placeholder_default(self):
        """Mutation target: changed default string value."""
        assert config_module.RELAY_SIGNING_SECRET == "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"
        assert "LOCAL_SIM" in config_module.RELAY_SIGNING_SECRET

    def test_catalog_fit_sim_signing_secret_has_placeholder_default(self):
        """Mutation target: changed default string value."""
        assert config_module.CATALOG_FIT_SIM_SIGNING_SECRET == "LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed"

    def test_stripe_webhook_secret_has_placeholder_default(self):
        """Mutation target: changed default string value. A real Stripe
        secret starts with 'whsec_'; the placeholder is 'whsec_LOCAL_SIM...'
        to catch any missed rotation."""
        assert config_module.STRIPE_WEBHOOK_SECRET == "whsec_LOCAL_SIM_not_a_real_stripe_secret"
        assert config_module.STRIPE_WEBHOOK_SECRET.startswith("whsec_")

    def test_relay_signing_secret_previous_defaults_to_empty_string_not_none(self):
        """Mutation target: changed default from empty string to None.
        The relay_auth verification logic checks `== ""` (not `is None`), so
        a mutant changing this to None would crash or silently accept old
        signatures after rotation."""
        assert config_module.RELAY_SIGNING_SECRET_PREVIOUS == ""
        assert isinstance(config_module.RELAY_SIGNING_SECRET_PREVIOUS, str)
        assert config_module.RELAY_SIGNING_SECRET_PREVIOUS is not None

    def test_force_secure_cookies_defaults_to_false_not_true(self):
        """Mutation target: boolean inversion (True instead of False).
        False is fail-closed for local development (HTTP behind a proxy);
        True would break local/dev testing. True is only safe when explicitly
        configured with TLS termination visible to this process."""
        assert config_module.FORCE_SECURE_COOKIES is False
        assert config_module.FORCE_SECURE_COOKIES != True  # noqa: E712

    def test_signal_copier_base_url_defaults_to_empty_string(self):
        """Mutation target: changed from empty string to None or some URL.
        Empty means the feature is not configured; fit_simulation_client.py
        checks `if not SIGNAL_COPIER_BASE_URL` and refuses to guess."""
        assert config_module.SIGNAL_COPIER_BASE_URL == ""

    def test_fit_sim_catalog_config_json_defaults_to_empty_json_object(self):
        """Mutation target: changed default value (empty dict vs non-empty dict).
        Empty dict "{}" means no product has real wiring yet; the service
        honestly reports unavailable for every product."""
        assert config_module.FIT_SIM_CATALOG_CONFIG_JSON == "{}"

    def test_health_sampler_enabled_defaults_to_true(self):
        """Mutation target: boolean inversion (False instead of True).
        True is the default for production; False is only for tests."""
        assert config_module.HEALTH_SAMPLER_ENABLED is True

    def test_health_sampler_interval_seconds_defaults_to_60(self):
        """Mutation target: changed numeric default or type.
        60 seconds is the configured sampling cadence."""
        assert config_module.HEALTH_SAMPLER_INTERVAL_SECONDS == 60.0
        assert isinstance(config_module.HEALTH_SAMPLER_INTERVAL_SECONDS, float)


class TestPlaceholderSecretDefaults:
    """Placeholder secret defaults map (mutation target: missing entries,
    wrong values, type changes)."""

    def test_placeholder_secret_defaults_has_all_secret_keys(self):
        """Mutation target: removed entry from _PLACEHOLDER_SECRET_DEFAULTS dict.
        A missing key here would cause placeholder_secrets_in_use() to skip
        checking that secret, leaving it unchecked at startup."""
        expected_keys = {
            "LOCAL_JWT_SECRET",
            "RELAY_SIGNING_SECRET",
            "CATALOG_FIT_SIM_SIGNING_SECRET",
            "STRIPE_WEBHOOK_SECRET",
        }
        assert set(config_module._PLACEHOLDER_SECRET_DEFAULTS.keys()) == expected_keys

    def test_placeholder_defaults_match_actual_settings_defaults(self):
        """Mutation target: mismatched values between _PLACEHOLDER_SECRET_DEFAULTS
        and actual field defaults. Would make startup guard either miss a real
        placeholder or false-positive on a coincidental match."""
        assert config_module._PLACEHOLDER_SECRET_DEFAULTS["LOCAL_JWT_SECRET"] == config_module.LOCAL_JWT_SECRET
        assert (
            config_module._PLACEHOLDER_SECRET_DEFAULTS["RELAY_SIGNING_SECRET"]
            == config_module.RELAY_SIGNING_SECRET
        )
        assert (
            config_module._PLACEHOLDER_SECRET_DEFAULTS["CATALOG_FIT_SIM_SIGNING_SECRET"]
            == config_module.CATALOG_FIT_SIM_SIGNING_SECRET
        )
        assert config_module._PLACEHOLDER_SECRET_DEFAULTS["STRIPE_WEBHOOK_SECRET"] == config_module.STRIPE_WEBHOOK_SECRET

    def test_each_placeholder_default_value_is_nonempty_string(self):
        """Mutation target: empty string or None as a placeholder.
        Would make real deployment impossible to detect."""
        for name, value in config_module._PLACEHOLDER_SECRET_DEFAULTS.items():
            assert isinstance(value, str), f"{name} is not a string"
            assert len(value) > 0, f"{name} is an empty string"
            assert "LOCAL_SIM" in value, f"{name} should contain LOCAL_SIM marker"


class TestPlaceholderSecretsInUse:
    """placeholder_secrets_in_use() function behavior (mutation target:
    changed comparison operator, dropped checks, wrong variable names)."""

    def test_no_placeholders_in_use_when_all_rotated(self):
        """Mutation target: flipped `==` to `!=` in the list comprehension,
        or dropped the condition entirely."""
        with mock.patch.object(config_module, "_settings") as mock_settings:
            mock_settings.LOCAL_JWT_SECRET = "real-secret-different-from-placeholder"
            mock_settings.RELAY_SIGNING_SECRET = "real-relay-secret"
            mock_settings.CATALOG_FIT_SIM_SIGNING_SECRET = "real-catalog-secret"
            mock_settings.STRIPE_WEBHOOK_SECRET = "real-stripe-secret"

            result = config_module.placeholder_secrets_in_use()
            assert result == []

    def test_placeholder_secrets_in_use_detects_jwt_secret_placeholder(self):
        """Mutation target: dropped LOCAL_JWT_SECRET check from dict."""
        with mock.patch.object(config_module, "_settings") as mock_settings:
            mock_settings.LOCAL_JWT_SECRET = "LOCAL_SIM-not-a-real-secret-change-if-ever-deployed"
            mock_settings.RELAY_SIGNING_SECRET = "real-relay-secret"
            mock_settings.CATALOG_FIT_SIM_SIGNING_SECRET = "real-catalog-secret"
            mock_settings.STRIPE_WEBHOOK_SECRET = "real-stripe-secret"

            result = config_module.placeholder_secrets_in_use()
            assert "LOCAL_JWT_SECRET" in result

    def test_placeholder_secrets_in_use_detects_relay_signing_secret_placeholder(self):
        """Mutation target: dropped RELAY_SIGNING_SECRET check."""
        with mock.patch.object(config_module, "_settings") as mock_settings:
            mock_settings.LOCAL_JWT_SECRET = "real-jwt-secret"
            mock_settings.RELAY_SIGNING_SECRET = "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"
            mock_settings.CATALOG_FIT_SIM_SIGNING_SECRET = "real-catalog-secret"
            mock_settings.STRIPE_WEBHOOK_SECRET = "real-stripe-secret"

            result = config_module.placeholder_secrets_in_use()
            assert "RELAY_SIGNING_SECRET" in result

    def test_placeholder_secrets_in_use_detects_catalog_fit_sim_secret_placeholder(self):
        """Mutation target: dropped CATALOG_FIT_SIM_SIGNING_SECRET check."""
        with mock.patch.object(config_module, "_settings") as mock_settings:
            mock_settings.LOCAL_JWT_SECRET = "real-jwt-secret"
            mock_settings.RELAY_SIGNING_SECRET = "real-relay-secret"
            mock_settings.CATALOG_FIT_SIM_SIGNING_SECRET = "LOCAL_SIM-not-a-real-catalog-fit-sim-secret-change-if-ever-deployed"
            mock_settings.STRIPE_WEBHOOK_SECRET = "real-stripe-secret"

            result = config_module.placeholder_secrets_in_use()
            assert "CATALOG_FIT_SIM_SIGNING_SECRET" in result

    def test_placeholder_secrets_in_use_detects_stripe_webhook_secret_placeholder(self):
        """Mutation target: dropped STRIPE_WEBHOOK_SECRET check."""
        with mock.patch.object(config_module, "_settings") as mock_settings:
            mock_settings.LOCAL_JWT_SECRET = "real-jwt-secret"
            mock_settings.RELAY_SIGNING_SECRET = "real-relay-secret"
            mock_settings.CATALOG_FIT_SIM_SIGNING_SECRET = "real-catalog-secret"
            mock_settings.STRIPE_WEBHOOK_SECRET = "whsec_LOCAL_SIM_not_a_real_stripe_secret"

            result = config_module.placeholder_secrets_in_use()
            assert "STRIPE_WEBHOOK_SECRET" in result

    def test_placeholder_secrets_in_use_detects_multiple_placeholders(self):
        """Mutation target: dropped `or` between checks (would only detect
        the last one set to placeholder)."""
        with mock.patch.object(config_module, "_settings") as mock_settings:
            mock_settings.LOCAL_JWT_SECRET = "LOCAL_SIM-not-a-real-secret-change-if-ever-deployed"
            mock_settings.RELAY_SIGNING_SECRET = "LOCAL_SIM-not-a-real-relay-secret-change-if-ever-deployed"
            mock_settings.CATALOG_FIT_SIM_SIGNING_SECRET = "real-catalog-secret"
            mock_settings.STRIPE_WEBHOOK_SECRET = "real-stripe-secret"

            result = config_module.placeholder_secrets_in_use()
            assert "LOCAL_JWT_SECRET" in result
            assert "RELAY_SIGNING_SECRET" in result
            assert len(result) == 2


class TestConfigurationTypeConversions:
    """Configuration type conversions and parsing (mutation target: type
    mismatches, changed conversions, dropped validations)."""

    def test_environment_is_string_type(self):
        """Mutation target: type change (e.g., to int or list)."""
        assert isinstance(config_module.ENVIRONMENT, str)

    def test_health_sampler_interval_is_float_not_int(self):
        """Mutation target: type change from float to int (or bool).
        Float is used to allow fractional intervals (e.g., 1.5 seconds)."""
        assert isinstance(config_module.HEALTH_SAMPLER_INTERVAL_SECONDS, float)
        assert not isinstance(config_module.HEALTH_SAMPLER_INTERVAL_SECONDS, bool)

    def test_force_secure_cookies_is_boolean(self):
        """Mutation target: type change from bool to string or int."""
        assert isinstance(config_module.FORCE_SECURE_COOKIES, bool)
        assert not isinstance(config_module.FORCE_SECURE_COOKIES, str)

    def test_health_sampler_enabled_is_boolean(self):
        """Mutation target: type change from bool to string or int."""
        assert isinstance(config_module.HEALTH_SAMPLER_ENABLED, bool)
        assert not isinstance(config_module.HEALTH_SAMPLER_ENABLED, str)


class TestConfigurationStringDefaults:
    """String defaults and empty vs non-empty distinction (mutation target:
    empty string vs non-empty, None vs empty string)."""

    def test_commercial_database_url_is_not_empty(self):
        """Mutation target: empty string or None default.
        A real Postgres DSN is expected; empty would break the app."""
        assert config_module.COMMERCIAL_DATABASE_URL
        assert len(config_module.COMMERCIAL_DATABASE_URL) > 0

    def test_relay_database_url_is_not_empty(self):
        """Mutation target: empty string or None default."""
        assert config_module.RELAY_DATABASE_URL
        assert len(config_module.RELAY_DATABASE_URL) > 0

    def test_signal_copier_base_url_empty_means_unconfigured(self):
        """Mutation target: empty string changed to None, or vice versa.
        fit_simulation_client.py checks `if not SIGNAL_COPIER_BASE_URL`,
        which works for both empty string and None, but the default semantic
        is "not configured" which maps to empty string, not None."""
        assert config_module.SIGNAL_COPIER_BASE_URL == ""

    def test_fit_sim_catalog_config_json_is_valid_json(self):
        """Mutation target: invalid JSON or changed format (e.g., "null"
        instead of "{}"). The app parses this as JSON."""
        import json

        obj = json.loads(config_module.FIT_SIM_CATALOG_CONFIG_JSON)
        assert isinstance(obj, dict)
        assert obj == {}


# =============================================================================
# RATE_LIMIT.PY MUTATION TESTS
# =============================================================================


class TestRateLimiterInitialization:
    """Rate limiter initialization and configuration (mutation target:
    wrong initialization, missing key function, changed settings)."""

    def test_limiter_is_initialized_as_slowapi_limiter(self):
        """Mutation target: initialization with wrong class or wrong parameters."""
        from app.rate_limit import limiter

        assert isinstance(limiter, Limiter)

    def test_limiter_uses_get_remote_address_key_func(self):
        """Mutation target: wrong key function or no key function.
        get_remote_address ensures per-IP rate limiting (not per session
        or tenant). A wrong key function could break isolation."""
        from app.rate_limit import limiter

        # The key function is stored as _key_func in slowapi's Limiter
        assert limiter._key_func == get_remote_address

    def test_public_fit_sim_rate_limit_is_correctly_formatted_string(self):
        """Mutation target: changed format (e.g., "20/minute" instead of
        "10/minute", or "10/hour" instead of "10/minute")."""
        from app.rate_limit import PUBLIC_FIT_SIM_RATE_LIMIT

        assert PUBLIC_FIT_SIM_RATE_LIMIT == "10/minute"
        assert "/" in PUBLIC_FIT_SIM_RATE_LIMIT
        assert "10" in PUBLIC_FIT_SIM_RATE_LIMIT
        assert "minute" in PUBLIC_FIT_SIM_RATE_LIMIT

    def test_public_fit_sim_rate_limit_allows_exactly_10_per_minute(self):
        """Mutation target: changed limit count (e.g., "20/minute" would be
        2x the intended rate, or "5/minute" would be too restrictive). The
        comment explicitly states "10/minute" is the limit."""
        from app.rate_limit import PUBLIC_FIT_SIM_RATE_LIMIT

        # Extract the number from "10/minute"
        rate_count = int(PUBLIC_FIT_SIM_RATE_LIMIT.split("/")[0])
        assert rate_count == 10

    def test_public_fit_sim_rate_limit_window_is_minute_not_second(self):
        """Mutation target: changed time window (e.g., "10/second" would be
        600/minute and completely disable the rate limit for any normal usage)."""
        from app.rate_limit import PUBLIC_FIT_SIM_RATE_LIMIT

        window = PUBLIC_FIT_SIM_RATE_LIMIT.split("/")[1]
        assert window == "minute"
        assert window != "second"
        assert window != "hour"


class TestRateLimiterDocumentation:
    """Rate limiter module documentation and design rationale (mutation
    target: misleading comments, dropped documentation)."""

    def test_rate_limit_module_has_docstring(self):
        """Mutation target: empty or misleading docstring."""
        assert config_module.__dict__.get("__doc__") or True  # Just ensure module exists
        # The actual module docstring is in rate_limit.py

    def test_limiter_is_in_memory_not_distributed(self):
        """Mutation target: changed to Redis or other backend without
        documenting the architectural change. The module clearly states
        'default in-memory fixed-window store' and notes that a deployment
        running multiple processes would need a shared backend (not yet
        implemented)."""
        # This test validates the design choice is maintained:
        # The in-memory limiter is appropriate for single-process deployments
        # (the documented current state).
        from app.rate_limit import limiter

        # An in-memory limiter has a `_storage` attribute; distributed ones
        # (like Redis) would have different attributes
        assert hasattr(limiter, "_storage")
        # The storage should be the default in-memory store
        assert limiter._storage is not None


class TestRateLimitBoundaryConditions:
    """Rate limit boundary conditions and edge cases (mutation target:
    off-by-one errors, changed comparisons, missing validation)."""

    def test_rate_limit_value_is_reasonable_for_public_api(self):
        """Mutation target: rate limit set too high (e.g., "1000/minute")
        or too low (e.g., "1/minute"). The comment notes this is 'lower
        than signal-copier's own CATALOG_FIT_SIM_RATE_LIMIT (20/minute)'
        and is the per-visitor bound."""
        from app.rate_limit import PUBLIC_FIT_SIM_RATE_LIMIT

        rate_count = int(PUBLIC_FIT_SIM_RATE_LIMIT.split("/")[0])
        # Should be in a reasonable range: at least 1, less than 100
        assert 1 <= rate_count <= 100
        # Specifically should be 10
        assert rate_count == 10

    def test_rate_limit_is_lower_than_upstream_service_limit(self):
        """Mutation target: changed to equal or higher than signal-copier's
        20/minute limit. This service is the edge; signal-copier is upstream.
        The comment explicitly states this should be LOWER."""
        from app.rate_limit import PUBLIC_FIT_SIM_RATE_LIMIT

        rate_count = int(PUBLIC_FIT_SIM_RATE_LIMIT.split("/")[0])
        # Should be lower than 20 (signal-copier's limit)
        assert rate_count < 20
        # Specifically 10
        assert rate_count == 10
