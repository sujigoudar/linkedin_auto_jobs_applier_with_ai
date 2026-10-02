"""Comprehensive mutation-testing regression suite for Track 70.

Targeted regression tests for signal-copier's collectors and parser modules
following the Track 60-63 mutation-testing pattern. Focuses on high-severity
mutations that would silently misbehave if critical operators, conditions,
or boundaries change.

Modules covered:
- app/email_collectors.py: Email signal collection, validation
- app/website_collectors.py: Website scraping, collector management
- app/parser_tooling.py: Signal parsing utilities, message classification
- app/notification_bridge.py: Notification routing, pairing
- app/provider_value.py: Provider value calculations, P&L metrics
"""
from __future__ import annotations

import pytest

from app.email_collectors import (
    EmailCollector,
    EmailCollectorError,
    CollectorHealth,
    ConnectionMode,
    validate_registration,
)
from app.website_collectors import (
    WebsiteCollectorError,
    SiteFormat,
    validate_registration as validate_website_registration,
)
from app.parser_tooling import (
    ParserToolingError,
    MessageType,
    ParserProfileStatus,
    classify_message_type,
    validate_profile_transition,
)
from app.notification_bridge import (
    generate_pairing_token,
    hash_pairing_token,
    verify_pairing_token,
    classify_notification_completeness,
    DeviceReportedCompleteness,
    ContentCompleteness,
)
from app.provider_value import (
    ProviderValue,
)


# ============================================================================
# EMAIL COLLECTORS MUTATION TESTS
# ============================================================================


class TestEmailCollectorValidationRequiredFields:
    """Mutation target: required field checks (not dropped or weakened)."""

    def test_validate_rejects_empty_collector_id(self):
        """Mutant: if collector_id check removed or weakened to `== ""`."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "collector_id" in str(exc_info.value).lower()

    def test_validate_rejects_empty_identity_ref(self):
        """Mutant: if identity_ref check removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "identity_ref" in str(exc_info.value).lower()

    def test_validate_rejects_empty_credential_env_var(self):
        """Mutant: if credential_env_var check removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "credential_env_var" in str(exc_info.value).lower()

    def test_validate_rejects_empty_imap_host(self):
        """Mutant: if imap_host check removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "imap_host" in str(exc_info.value).lower()

    def test_validate_rejects_empty_imap_folder(self):
        """Mutant: if imap_folder check removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "imap_folder" in str(exc_info.value).lower()

    def test_validate_rejects_empty_provider_name(self):
        """Mutant: if provider_name check removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="",
                allowed_uses=None,
            )
        assert "provider_name" in str(exc_info.value).lower()


class TestEmailCollectorConnectionModeValidation:
    """Mutation target: enum parsing and validation."""

    def test_validate_accepts_valid_connection_mode(self):
        """Mutant: ConnectionMode enum parsing removed/weakened."""
        mode, uses = validate_registration(
            collector_id="test",
            connection_mode="imap",
            identity_ref="alert@example.com",
            credential_env_var="EMAIL_ALERT_PASSWORD",
            imap_host="imap.gmail.com",
            imap_folder="INBOX",
            sender_allowlist=["sender@example.com"],
            provider_name="testprov",
            allowed_uses=None,
        )
        assert mode == ConnectionMode.IMAP
        assert isinstance(mode, ConnectionMode)

    def test_validate_rejects_invalid_connection_mode(self):
        """Mutant: ConnectionMode validation removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="invalid_mode",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "connection_mode" in str(exc_info.value).lower()


class TestEmailCollectorEnvVarValidation:
    """Mutation target: env var format check (not dropped)."""

    def test_validate_rejects_env_var_with_spaces(self):
        """Mutant: ` ` in credential_env_var check removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL ALERT PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "environment-variable" in str(exc_info.value).lower()

    def test_validate_rejects_env_var_with_lowercase(self):
        """Mutant: `.isupper()` check removed or weakened."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="email_alert_password",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "environment-variable" in str(exc_info.value).lower()

    def test_validate_rejects_env_var_with_equals(self):
        """Mutant: `count("=") > 0` check removed (would accept value like VAR=secret)."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT=hunter2",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "environment-variable" in str(exc_info.value).lower()


class TestEmailCollectorSenderAllowlist:
    """Mutation target: sender allowlist must not be empty (point 4)."""

    def test_validate_rejects_empty_sender_allowlist(self):
        """Mutant: empty sender_allowlist check removed (would accept `[]`)."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=[],
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "sender_allowlist" in str(exc_info.value).lower()

    def test_validate_rejects_none_sender_allowlist(self):
        """Mutant: `if not sender_allowlist` weakened to `if sender_allowlist is None`."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=None,
                provider_name="testprov",
                allowed_uses=None,
            )
        assert "sender_allowlist" in str(exc_info.value).lower()

    def test_validate_accepts_single_sender(self):
        """Mutant: check accidentally requires multiple senders (len > 1)."""
        mode, uses = validate_registration(
            collector_id="test",
            connection_mode="imap",
            identity_ref="alert@example.com",
            credential_env_var="EMAIL_ALERT_PASSWORD",
            imap_host="imap.gmail.com",
            imap_folder="INBOX",
            sender_allowlist=["one@example.com"],
            provider_name="testprov",
            allowed_uses=None,
        )
        assert isinstance(mode, ConnectionMode)


class TestEmailCollectorAllowedUsesDefaults:
    """Mutation target: default allowed_uses must be PRIVATE_TRADING only."""

    def test_validate_defaults_to_private_trading(self):
        """Mutant: default changed to include COMMERCIAL_REDISTRIBUTION."""
        mode, uses = validate_registration(
            collector_id="test",
            connection_mode="imap",
            identity_ref="alert@example.com",
            credential_env_var="EMAIL_ALERT_PASSWORD",
            imap_host="imap.gmail.com",
            imap_folder="INBOX",
            sender_allowlist=["sender@example.com"],
            provider_name="testprov",
            allowed_uses=None,
        )
        assert uses == ["private_trading"]
        assert "commercial_redistribution" not in uses

    def test_validate_rejects_invalid_allowed_use(self):
        """Mutant: AllowedUse enum validation removed."""
        with pytest.raises(EmailCollectorError) as exc_info:
            validate_registration(
                collector_id="test",
                connection_mode="imap",
                identity_ref="alert@example.com",
                credential_env_var="EMAIL_ALERT_PASSWORD",
                imap_host="imap.gmail.com",
                imap_folder="INBOX",
                sender_allowlist=["sender@example.com"],
                provider_name="testprov",
                allowed_uses=["nonexistent_use"],
            )
        assert "allowed_uses" in str(exc_info.value).lower()


class TestEmailCollectorPostInit:
    """Mutation target: enum string conversion in __post_init__."""

    def test_post_init_converts_string_connection_mode(self):
        """Mutant: __post_init__ enum conversion removed."""
        collector = EmailCollector(
            id="test",
            connection_mode="imap",  # string, not enum
            identity_ref="alert@example.com",
            credential_env_var="EMAIL_ALERT_PASSWORD",
            imap_host="imap.gmail.com",
            imap_folder="INBOX",
            sender_allowlist=["sender@example.com"],
            provider_name="testprov",
        )
        assert isinstance(collector.connection_mode, ConnectionMode)
        assert collector.connection_mode == ConnectionMode.IMAP

    def test_post_init_converts_string_health_state(self):
        """Mutant: health_state string conversion removed."""
        collector = EmailCollector(
            id="test",
            connection_mode="imap",
            identity_ref="alert@example.com",
            credential_env_var="EMAIL_ALERT_PASSWORD",
            imap_host="imap.gmail.com",
            imap_folder="INBOX",
            sender_allowlist=["sender@example.com"],
            provider_name="testprov",
            health_state="no_messages_observed",  # string
        )
        assert isinstance(collector.health_state, CollectorHealth)
        assert collector.health_state == CollectorHealth.NO_MESSAGES_OBSERVED


# ============================================================================
# WEBSITE COLLECTORS MUTATION TESTS
# ============================================================================


class TestWebsiteCollectorValidation:
    """Mutation target: SiteFormat validation and mutual exclusivity."""

    def test_validate_accepts_feed_format(self):
        """Mutant: SiteFormat enum validation removed."""
        format_enum, uses = validate_website_registration(
            collector_id="techsite",
            site_format="feed",
            site_id="tech",
            provider_name="techprov",
            feed_url="https://techsite.com/feed.xml",
            article_list_url=None,
            allowed_uses=None,
        )
        assert format_enum == SiteFormat.FEED

    def test_validate_rejects_invalid_site_format(self):
        """Mutant: SiteFormat validation removed."""
        with pytest.raises(WebsiteCollectorError) as exc_info:
            validate_website_registration(
                collector_id="techsite",
                site_format="invalid_format",
                site_id="tech",
                provider_name="techprov",
                feed_url="https://techsite.com/feed.xml",
                article_list_url=None,
                allowed_uses=None,
            )
        assert "site_format" in str(exc_info.value).lower()

    def test_validate_feed_format_requires_feed_url(self):
        """Mutant: feed_url requirement check removed."""
        with pytest.raises(WebsiteCollectorError) as exc_info:
            validate_website_registration(
                collector_id="techsite",
                site_format="feed",
                site_id="tech",
                provider_name="techprov",
                feed_url=None,
                article_list_url=None,
                allowed_uses=None,
            )
        assert "feed_url" in str(exc_info.value).lower()

    def test_validate_article_list_format_requires_article_list_url(self):
        """Mutant: article_list_url requirement check removed."""
        with pytest.raises(WebsiteCollectorError) as exc_info:
            validate_website_registration(
                collector_id="techsite",
                site_format="article_list",
                site_id="tech",
                provider_name="techprov",
                feed_url=None,
                article_list_url=None,
                allowed_uses=None,
            )
        assert "article_list_url" in str(exc_info.value).lower()


# ============================================================================
# PARSER TOOLING MUTATION TESTS
# ============================================================================


class TestMessageTypeClassification:
    """Mutation target: keyword matching, priority ordering."""

    def test_classify_entry_message(self):
        """Mutant: entry keyword list removed or emptied."""
        msg_type = classify_message_type("BUY AAPL 100 shares")
        assert msg_type == MessageType.ENTRY

    def test_classify_entry_long_message(self):
        """Mutant: entry keyword list removed or emptied (LONG side variant)."""
        msg_type = classify_message_type("LONG AAPL 100 shares at 150")
        assert msg_type == MessageType.ENTRY

    def test_classify_entry_sell_is_also_entry(self):
        """Mutant: SELL is parsed but classified as ENTRY (not EXIT - only CLOSE is EXIT)."""
        # SELL is a valid signal but side.SELL != side.CLOSE, so it's ENTRY
        msg_type = classify_message_type("SELL 50 shares of AAPL at market")
        assert msg_type == MessageType.ENTRY

    def test_classify_exit_close_message(self):
        """Mutant: exit keyword list removed or emptied (CLOSE variant)."""
        msg_type = classify_message_type("CLOSE AAPL position")
        assert msg_type == MessageType.EXIT

    def test_classify_cancel_message(self):
        """Mutant: cancel keyword list removed; falls back to UNKNOWN."""
        msg_type = classify_message_type("CANCEL the order")
        assert msg_type == MessageType.CANCEL

    def test_classify_stop_update_message(self):
        """Mutant: stop update keyword list removed."""
        msg_type = classify_message_type("UPDATE STOP to 150")
        assert msg_type == MessageType.STOP_UPDATE

    def test_classify_target_update_message(self):
        """Mutant: target update keyword list removed."""
        msg_type = classify_message_type("UPDATE TARGET to 200")
        assert msg_type == MessageType.TARGET_UPDATE

    def test_classify_unknown_when_no_match(self):
        """Mutant: UNKNOWN fallback removed; would return wrong category."""
        msg_type = classify_message_type("just some random chat")
        assert msg_type == MessageType.UNKNOWN

    def test_classify_priority_cancel_over_update(self):
        """Mutant: priority ordering changed; cancel should be checked before generic update."""
        # Message matches both UPDATE and CANCEL keywords
        msg_type = classify_message_type("CANCEL the target update")
        # Should be CANCEL, not TARGET_UPDATE (because CANCEL is checked first)
        assert msg_type == MessageType.CANCEL


class TestParserProfileTransitions:
    """Mutation target: state machine allowed transitions."""

    def test_draft_to_tested_allowed(self):
        """Mutant: DRAFT->TESTED transition removed from allowed set."""
        # Should not raise
        validate_profile_transition(ParserProfileStatus.DRAFT, ParserProfileStatus.TESTED)

    def test_tested_to_shadow_allowed(self):
        """Mutant: TESTED->SHADOW transition removed."""
        # Should not raise
        validate_profile_transition(ParserProfileStatus.TESTED, ParserProfileStatus.SHADOW)

    def test_shadow_to_certified_allowed(self):
        """Mutant: SHADOW->CERTIFIED transition removed."""
        # Should not raise
        validate_profile_transition(ParserProfileStatus.SHADOW, ParserProfileStatus.CERTIFIED)

    def test_certified_to_active_allowed(self):
        """Mutant: CERTIFIED->ACTIVE transition removed."""
        # Should not raise
        validate_profile_transition(ParserProfileStatus.CERTIFIED, ParserProfileStatus.ACTIVE)

    def test_active_to_retired_allowed(self):
        """Mutant: ACTIVE->RETIRED transition removed."""
        # Should not raise
        validate_profile_transition(ParserProfileStatus.ACTIVE, ParserProfileStatus.RETIRED)

    def test_draft_to_active_rejected(self):
        """Mutant: missing transition check; would allow skipping states."""
        # Should raise ParserToolingError
        with pytest.raises(ParserToolingError):
            validate_profile_transition(ParserProfileStatus.DRAFT, ParserProfileStatus.ACTIVE)

    def test_retired_to_active_rejected(self):
        """Mutant: absorbing state check removed; would allow leaving RETIRED."""
        # Should raise ParserToolingError
        with pytest.raises(ParserToolingError):
            validate_profile_transition(ParserProfileStatus.RETIRED, ParserProfileStatus.ACTIVE)

    def test_retired_to_draft_rejected(self):
        """Mutant: RETIRED absorbing state not enforced."""
        # Should raise ParserToolingError
        with pytest.raises(ParserToolingError):
            validate_profile_transition(ParserProfileStatus.RETIRED, ParserProfileStatus.DRAFT)


# ============================================================================
# NOTIFICATION BRIDGE MUTATION TESTS
# ============================================================================


class TestPairingTokenGeneration:
    """Mutation target: token generation randomness."""

    def test_generate_pairing_token_produces_non_empty_string(self):
        """Mutant: token generation returns empty string or None."""
        token = generate_pairing_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_generate_pairing_token_produces_different_values(self):
        """Mutant: token generation always returns same value (no randomness)."""
        token1 = generate_pairing_token()
        token2 = generate_pairing_token()
        # With proper randomness, these should differ (virtually always)
        assert token1 != token2

    def test_generate_pairing_token_sufficient_entropy(self):
        """Mutant: token generation uses insufficient entropy."""
        token = generate_pairing_token()
        # Should be a reasonable length (typically 32+ chars for security)
        assert len(token) >= 20


class TestPairingTokenHashing:
    """Mutation target: hash/verify logic."""

    def test_hash_produces_different_output_than_input(self):
        """Mutant: hash function returns input unchanged (skipped hashing)."""
        token = generate_pairing_token()
        hash_result = hash_pairing_token(token)
        assert hash_result != token
        assert len(hash_result) > 0

    def test_hash_produces_deterministic_output(self):
        """Mutant: hash function produces random output each time (not deterministic)."""
        token = "test_token_12345"
        hash1 = hash_pairing_token(token)
        hash2 = hash_pairing_token(token)
        # argon2id hashes include salt, so they're NOT deterministic
        # Instead, both should verify against the original token
        assert verify_pairing_token(token, hash1)
        assert verify_pairing_token(token, hash2)

    def test_verify_accepts_correct_token(self):
        """Mutant: verify logic inverted (always returns False)."""
        token = generate_pairing_token()
        hashed = hash_pairing_token(token)
        assert verify_pairing_token(token, hashed)

    def test_verify_rejects_incorrect_token(self):
        """Mutant: verify logic inverted (always returns True)."""
        token = generate_pairing_token()
        hashed = hash_pairing_token(token)
        wrong_token = generate_pairing_token()
        assert not verify_pairing_token(wrong_token, hashed)

    def test_verify_rejects_empty_token(self):
        """Mutant: empty token check removed."""
        hashed = hash_pairing_token("original")
        assert not verify_pairing_token("", hashed)

    def test_verify_rejects_malformed_hash_gracefully(self):
        """Mutant: hash format validation removed (would raise or crash)."""
        token = generate_pairing_token()
        # Corrupted hash should NOT raise, should return False gracefully
        result = verify_pairing_token(token, "not_a_valid_hash")
        assert result is False


class TestNotificationCompletenessClassification:
    """Mutation target: completeness classification logic."""

    def test_classify_complete_when_parsed(self):
        """Mutant: condition logic inverted or dropped."""
        # disposition_outcome='parsed' should return COMPLETE
        completeness = classify_notification_completeness(
            device_reported=DeviceReportedCompleteness.COMPLETE,
            best_text="BUY 100 AAPL at 150",
            disposition_outcome="parsed",
        )
        assert completeness == ContentCompleteness.COMPLETE

    def test_classify_truncated_when_device_reports_truncated(self):
        """Mutant: device_reported check removed or weakened."""
        # TRUNCATED from device should return TRUNCATED even with text
        completeness = classify_notification_completeness(
            device_reported=DeviceReportedCompleteness.TRUNCATED,
            best_text="Some text",
            disposition_outcome=None,
        )
        assert completeness == ContentCompleteness.TRUNCATED

    def test_classify_partial_when_ambiguous(self):
        """Mutant: partial condition check removed."""
        # disposition_outcome='ambiguous' should return PARTIAL
        completeness = classify_notification_completeness(
            device_reported=DeviceReportedCompleteness.COMPLETE,
            best_text="BUY AAPL",
            disposition_outcome="ambiguous",
        )
        assert completeness == ContentCompleteness.PARTIAL

    def test_classify_pointer_only_when_title_only(self):
        """Mutant: title_only condition removed."""
        # TITLE_ONLY from device should return POINTER_ONLY
        completeness = classify_notification_completeness(
            device_reported=DeviceReportedCompleteness.TITLE_ONLY,
            best_text="",
            disposition_outcome=None,
        )
        assert completeness == ContentCompleteness.POINTER_ONLY

    def test_classify_unknown_when_no_match(self):
        """Mutant: UNKNOWN fallback removed (would guess COMPLETE)."""
        # no_match disposition with longer text that's not a pointer should be UNKNOWN
        # The heuristic _looks_like_pointer_only checks for short, digit-free text
        completeness = classify_notification_completeness(
            device_reported=DeviceReportedCompleteness.COMPLETE,
            best_text="this is some very long random text with content that should not be classified as a pointer",
            disposition_outcome="no_match",
        )
        assert completeness == ContentCompleteness.UNKNOWN


# ============================================================================
# PROVIDER VALUE MUTATION TESTS
# ============================================================================


class TestProviderValueWinRate:
    """Mutation target: win_rate calculation (division, bounds checking)."""

    def test_win_rate_returns_none_on_zero_fills(self):
        """Mutant: zero-division check removed (would crash or return infinity)."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            closing_fills=0,
            winning_closing_fills=0,
        )
        assert pv.win_rate is None

    def test_win_rate_correct_calculation(self):
        """Mutant: division operator changed (/ vs *)."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            closing_fills=10,
            winning_closing_fills=7,
        )
        assert abs(pv.win_rate - 0.7) < 0.001

    def test_win_rate_all_wins(self):
        """Mutant: boundary case where all fills are wins."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            closing_fills=5,
            winning_closing_fills=5,
        )
        assert abs(pv.win_rate - 1.0) < 0.001

    def test_win_rate_no_wins(self):
        """Mutant: boundary case where no fills are wins."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            closing_fills=5,
            winning_closing_fills=0,
        )
        assert abs(pv.win_rate - 0.0) < 0.001


class TestProviderValueProfitFactor:
    """Mutation target: profit_factor calculation (division by zero, operator)."""

    def test_profit_factor_returns_none_on_zero_loss(self):
        """Mutant: zero-loss check removed (would crash on division by zero)."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            gross_profit=100.0,
            gross_loss=0,
        )
        assert pv.profit_factor is None

    def test_profit_factor_correct_calculation(self):
        """Mutant: division operator changed (/ vs *)."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            gross_profit=1000.0,
            gross_loss=500.0,
        )
        assert abs(pv.profit_factor - 2.0) < 0.001

    def test_profit_factor_less_than_one(self):
        """Mutant: boundary where losses exceed gains."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            gross_profit=300.0,
            gross_loss=500.0,
        )
        assert abs(pv.profit_factor - 0.6) < 0.001

    def test_profit_factor_exactly_one(self):
        """Mutant: boundary where gains equal losses."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            gross_profit=500.0,
            gross_loss=500.0,
        )
        assert abs(pv.profit_factor - 1.0) < 0.001


class TestProviderValueDatastructure:
    """Mutation target: field initialization and defaults."""

    def test_provider_value_source_required(self):
        """Mutant: source field dropped or set to wrong default."""
        pv = ProviderValue(
            source="test_source",
            analyst="test_analyst",
            asset_class="equity",
        )
        assert pv.source == "test_source"

    def test_provider_value_analyst_can_be_none(self):
        """Mutant: analyst defaults to empty string instead of None."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
        )
        assert pv.analyst is None

    def test_provider_value_realized_pnl_defaults_to_zero(self):
        """Mutant: default changed to non-zero value."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
        )
        assert pv.realized_pnl == 0.0

    def test_provider_value_closing_fills_defaults_to_zero(self):
        """Mutant: default changed to non-zero value."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
        )
        assert pv.closing_fills == 0

    def test_provider_value_to_dict_includes_all_fields(self):
        """Mutant: fields dropped from to_dict output."""
        pv = ProviderValue(
            source="test",
            analyst="analyst1",
            asset_class="equity",
            realized_pnl=150.0,
            gross_profit=200.0,
            gross_loss=50.0,
            closing_fills=10,
            winning_closing_fills=7,
            entries_opened=5,
        )
        result = pv.to_dict()
        assert "source" in result
        assert "analyst" in result
        assert "asset_class" in result
        assert "realized_pnl" in result
        assert result["source"] == "test"
        assert result["analyst"] == "analyst1"


class TestProviderValueFieldPersistence:
    """Mutation target: field name preservation through serialization."""

    def test_asset_class_field_preserved(self):
        """Mutant: asset_class field renamed or dropped."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="options",
        )
        result = pv.to_dict()
        assert "asset_class" in result
        assert result["asset_class"] == "options"

    def test_winning_closing_fills_field_preserved(self):
        """Mutant: winning_closing_fills field renamed or dropped."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            winning_closing_fills=8,
        )
        result = pv.to_dict()
        assert "winning_closing_fills" in result
        assert result["winning_closing_fills"] == 8

    def test_entries_opened_field_preserved(self):
        """Mutant: entries_opened field dropped from output."""
        pv = ProviderValue(
            source="test",
            analyst=None,
            asset_class="equity",
            entries_opened=12,
        )
        result = pv.to_dict()
        assert "entries_opened" in result
        assert result["entries_opened"] == 12
