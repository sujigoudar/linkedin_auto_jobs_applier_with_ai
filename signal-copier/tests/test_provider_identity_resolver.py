"""Tests for email-to-ProviderIdentity deterministic mapping."""
from app.transports.provider_identity import (
    InboxProviderConfig,
    ProviderIdentityResolver,
    ProviderSenderMapping,
)


class TestProviderIdentityResolver:
    """Test email sender → provider mapping."""

    def test_exact_sender_match(self):
        """Exact email address match takes precedence."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "alice@example.com",
            "provider_alice",
            analyst_id="alice"
        )

        result = resolver.resolve("alice@example.com", "signals")

        assert result.provider == "provider_alice"
        assert result.analyst == "alice"
        assert not result.needs_review
        assert "alice@example.com" in result.reason

    def test_domain_match_fallback(self):
        """Domain match (@example.com) used when sender not found."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "@tradingview.com",
            "tradingview"
        )

        result = resolver.resolve("alerts@tradingview.com", "signals")

        assert result.provider == "tradingview"
        assert result.analyst == "alerts"
        assert not result.needs_review

    def test_unknown_sender_needs_review(self):
        """Unknown sender marked for manual review."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "@example.com",
            "example"
        )

        result = resolver.resolve("unknown@other.com", "signals")

        assert result.provider is None
        assert result.needs_review
        assert "Unknown sender" in result.reason

    def test_disabled_sender_needs_review(self):
        """Disabled mapping returns NEEDS_REVIEW."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "alice@example.com",
            "provider_alice",
            enabled=False
        )

        result = resolver.resolve("alice@example.com", "signals")

        assert result.needs_review
        assert "disabled" in result.reason.lower()

    def test_analyst_extraction_from_sender(self):
        """Analyst name extracted from email local part."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "@example.com",
            "example"
        )

        # Test various formats
        tests = [
            ("alice@example.com", "alice"),
            ("bob_smith@example.com", "bob_smith"),
            ("alerts_trader1@example.com", "trader1"),  # alerts_ prefix removed
            ("signals_xyz@example.com", "xyz"),  # signals_ prefix removed
        ]

        for sender, expected_analyst in tests:
            result = resolver.resolve(sender, "signals")
            assert result.analyst == expected_analyst, f"Failed for {sender}"

    def test_inbox_role_isolation(self):
        """Different inbox roles can have different provider mappings."""
        resolver = ProviderIdentityResolver()

        # Map same sender to different providers for different inboxes
        resolver.add_mapping("signals", "@broker.com", "broker_signals")
        resolver.add_mapping("operations", "@broker.com", "broker_ops")

        signals_result = resolver.resolve("alerts@broker.com", "signals")
        ops_result = resolver.resolve("alerts@broker.com", "operations")

        assert signals_result.provider == "broker_signals"
        assert ops_result.provider == "broker_ops"

    def test_no_config_for_inbox_role(self):
        """Unconfigured inbox role returns NEEDS_REVIEW."""
        resolver = ProviderIdentityResolver()
        # No configuration added for "reports" inbox

        result = resolver.resolve("test@example.com", "reports")

        assert result.needs_review
        assert "No provider mapping" in result.reason

    def test_default_provider_when_configured(self):
        """Unknown sender uses default provider if configured."""
        config = InboxProviderConfig(
            inbox_role="signals",
            default_provider="default_provider",
            require_known_sender=False
        )
        resolver = ProviderIdentityResolver(configs=[config])

        result = resolver.resolve("unknown@random.com", "signals")

        assert result.provider == "default_provider"
        assert not result.needs_review
        assert "default provider" in result.reason.lower()

    def test_exact_sender_overrides_domain(self):
        """Exact sender match takes precedence over domain match."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping("signals", "@example.com", "example_general")
        resolver.add_mapping("signals", "special@example.com", "example_special")

        result = resolver.resolve("special@example.com", "signals")

        assert result.provider == "example_special"

    def test_analyst_override(self):
        """Explicit analyst_id overrides extracted name."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "alerts@tradingview.com",
            "tradingview",
            analyst_id="tradingview_trader"
        )

        result = resolver.resolve("alerts@tradingview.com", "signals")

        assert result.analyst == "tradingview_trader"

    def test_multiple_senders_same_provider(self):
        """Multiple senders can map to the same provider."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping("signals", "@tradingview.com", "tradingview")
        resolver.add_mapping("signals", "@investingcom.com", "investing_com")

        tv_result = resolver.resolve("alerts@tradingview.com", "signals")
        ic_result = resolver.resolve("alerts@investingcom.com", "signals")

        assert tv_result.provider == "tradingview"
        assert ic_result.provider == "investing_com"
        assert tv_result.provider != ic_result.provider

    def test_invalid_sender_format_no_crash(self):
        """Invalid sender format handled gracefully."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping("signals", "@example.com", "example")

        result = resolver.resolve("not_an_email", "signals")

        # Should handle gracefully (no analyst extraction but provider lookup still works)
        assert result.needs_review
        assert result.analyst is None

    def test_domain_without_leading_at(self):
        """Domain patterns must start with @."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping("signals", "@tradingview.com", "tradingview")

        # This sender should match @tradingview.com pattern
        result = resolver.resolve("alerts@tradingview.com", "signals")
        assert result.provider == "tradingview"

    def test_configuration_from_list(self):
        """Initialize resolver with config list."""
        configs = [
            InboxProviderConfig(
                inbox_role="signals",
                senders={
                    "@provider1.com": ProviderSenderMapping(
                        sender_pattern="@provider1.com",
                        provider_id="provider1"
                    )
                }
            ),
            InboxProviderConfig(
                inbox_role="operations",
                senders={
                    "@provider2.com": ProviderSenderMapping(
                        sender_pattern="@provider2.com",
                        provider_id="provider2"
                    )
                }
            ),
        ]

        resolver = ProviderIdentityResolver(configs=configs)

        signals_result = resolver.resolve("alert@provider1.com", "signals")
        ops_result = resolver.resolve("error@provider2.com", "operations")

        assert signals_result.provider == "provider1"
        assert ops_result.provider == "provider2"

    def test_require_known_sender_flag(self):
        """require_known_sender controls NEEDS_REVIEW behavior."""
        # When require_known_sender=True (default)
        resolver1 = ProviderIdentityResolver([
            InboxProviderConfig(
                inbox_role="signals",
                require_known_sender=True
            )
        ])
        result1 = resolver1.resolve("unknown@random.com", "signals")
        assert result1.needs_review

        # When require_known_sender=False and no default
        resolver2 = ProviderIdentityResolver([
            InboxProviderConfig(
                inbox_role="signals",
                require_known_sender=False,
                default_provider=None
            )
        ])
        result2 = resolver2.resolve("unknown@random.com", "signals")
        assert result2.needs_review

    def test_disabled_domain_mapping(self):
        """Disabled domain mappings return NEEDS_REVIEW."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping(
            "signals",
            "@disabled.com",
            "disabled_provider",
            enabled=False
        )

        result = resolver.resolve("alert@disabled.com", "signals")

        assert result.needs_review
        assert "disabled" in result.reason.lower()

    def test_case_sensitivity(self):
        """Email matching is case-sensitive (as per RFC 5321)."""
        resolver = ProviderIdentityResolver()
        resolver.add_mapping("signals", "alert@example.com", "example")

        # Lowercase should match
        lower_result = resolver.resolve("alert@example.com", "signals")
        assert lower_result.provider == "example"

        # Different case won't match exact sender (domain match might work)
        upper_result = resolver.resolve("ALERT@EXAMPLE.COM", "signals")
        # This will need_review because exact match is case-sensitive
        # and domain match is also case-sensitive
        assert upper_result.needs_review

    def test_complete_email_to_signal_workflow(self):
        """Integration test: complete email → ProviderIdentity workflow."""
        resolver = ProviderIdentityResolver()

        # Configure mappings for a real scenario
        resolver.add_mapping("signals", "@tradingview.com", "tradingview")
        resolver.add_mapping("signals", "@broker.com", "broker")
        resolver.add_mapping("signals", "vip@custom.com", "vip_trader", analyst_id="vip")

        # Test multiple signals from different sources
        tv_signal = resolver.resolve("alerts@tradingview.com", "signals")
        broker_signal = resolver.resolve("orders@broker.com", "signals")
        vip_signal = resolver.resolve("vip@custom.com", "signals")
        unknown_signal = resolver.resolve("unknown@unknown.com", "signals")

        assert tv_signal.provider == "tradingview"
        assert tv_signal.analyst == "alerts"

        assert broker_signal.provider == "broker"
        assert broker_signal.analyst == "orders"

        assert vip_signal.provider == "vip_trader"
        assert vip_signal.analyst == "vip"

        assert unknown_signal.needs_review
        assert unknown_signal.provider is None
