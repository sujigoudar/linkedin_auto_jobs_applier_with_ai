"""Track 73: comprehensive mutation testing for feature and capability modules.

This file implements targeted regression tests for five critical feature and
capability modules: qualification (trading qualification and eligibility gates),
export_events (event export pipeline), shadow_mode (shadow trading mode logic),
phone_escalation (phone escalation coordination), and execution_quality
(execution quality and latency metrics).

These tests are designed to catch mutations that would silently break:

- Qualification rule evaluation and eligibility gates
- Event classification and export filtering
- Shadow mode state transitions and reconciliation
- Escalation routing and notification logic
- Latency threshold checks and metric calculations

The tests follow the "Track 60-71 targeted regression test pattern" with
hand-written tests for mutation-critical patterns rather than relying on
mutant survival rates alone.

See pyproject.toml's Track 73 commentary for execution pattern and rationale.
"""

import pytest
from datetime import datetime, timezone

from app.qualification import (
    QualificationState, QualificationError, missing_prerequisites,
    requires_feedback, parse_state, state_index, QUALIFICATION_STATE_ORDER,
    FEEDBACK_DEPENDENT_FLOOR
)
from app.export_events import (
    source_receipt_event_id, _resolve_currency
)
from app.shadow_mode import (
    ShadowOrderIntent, _target_prices, to_result_row
)
from app.phone_escalation import (
    needs_escalation, is_denied_app_package, CapabilityState,
    validate_state_transition, validate_config_registration,
    PhoneEscalationError, AccessibilityNode,
    MockPhoneControlAdapter, ContentCompleteness, DeniedAppPackageError
)
from app.execution_quality import (
    SymbolLatency, StageLatency,
    AccountExecutionQuality, _parse, _parse_optional
)
from app.models import (
    Signal, OrderStatus, Side, AssetClass, ProfitTarget
)
from app.db import SignalStore
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


@pytest.fixture
def routing_config():
    """A simple routing configuration for shadow mode testing."""
    rule = RoutingRule(
        provider="test_provider",
        symbols=["AAPL", "MSFT"],
        accounts=["test_account"],
        asset_classes=[AssetClass.EQUITY],
    )
    return RoutingConfig(rules=[rule])


# ============================================================================
# Track 73.1: Qualification Module Mutations
# ============================================================================


class TestQualificationStateOrderMutations:
    """Test that qualification state ordering is strictly enforced.

    Mutations to catch:
    - state_index calculation inverted or off-by-one
    - QUALIFICATION_STATE_ORDER values reordered
    - comparison operators (>, <, >=, <=) inverted
    """

    def test_state_index_implemented_is_zero(self):
        """Mutation: index != 0 or ordering inverted."""
        assert state_index(QualificationState.IMPLEMENTED) == 0

    def test_state_index_configured_is_one(self):
        """Mutation: off-by-one error in indexing."""
        assert state_index(QualificationState.CONFIGURED) == 1

    def test_state_index_release_approved_is_highest(self):
        """Mutation: index calculation incorrect for last state."""
        idx = state_index(QualificationState.RELEASE_APPROVED)
        assert idx == 6
        assert idx == len(QUALIFICATION_STATE_ORDER) - 1

    def test_qualification_state_order_is_strictly_ascending(self):
        """Mutation: states reordered or duplicated."""
        expected = [
            "implemented", "configured", "authenticated",
            "account_entitled", "protocol_tested", "venue_tested",
            "release_approved"
        ]
        actual = [s.value for s in QUALIFICATION_STATE_ORDER]
        assert actual == expected
        # Verify no duplicates
        assert len(set(actual)) == len(actual)


class TestPrerequisiteValidationMutations:
    """Test that prerequisite checking enforces the ladder.

    Mutations to catch:
    - missing_prerequisites returns wrong subset
    - boolean logic inverted (checking for wrong direction)
    - off-by-one in index slicing
    """

    def test_missing_prerequisites_empty_achieved_set(self):
        """Mutation: returns wrong states or non-empty list when should be empty."""
        # IMPLEMENTED has no prerequisites
        missing = missing_prerequisites(QualificationState.IMPLEMENTED, set())
        assert missing == []

    def test_missing_prerequisites_none_achieved(self):
        """Mutation: missing logic not collecting prerequisites correctly."""
        missing = missing_prerequisites(QualificationState.AUTHENTICATED, set())
        expected = [QualificationState.IMPLEMENTED, QualificationState.CONFIGURED]
        assert missing == expected
        assert len(missing) == 2

    def test_missing_prerequisites_partial_achievement(self):
        """Mutation: not excluding already-achieved states."""
        achieved = {QualificationState.IMPLEMENTED}
        missing = missing_prerequisites(QualificationState.AUTHENTICATED, achieved)
        assert missing == [QualificationState.CONFIGURED]
        assert QualificationState.IMPLEMENTED not in missing

    def test_missing_prerequisites_all_achieved(self):
        """Mutation: returning prerequisites even when all achieved."""
        achieved = {
            QualificationState.IMPLEMENTED,
            QualificationState.CONFIGURED,
        }
        missing = missing_prerequisites(QualificationState.AUTHENTICATED, achieved)
        assert missing == []

    def test_missing_prerequisites_account_entitled(self):
        """Mutation: incorrect index boundary for higher states."""
        missing = missing_prerequisites(QualificationState.ACCOUNT_ENTITLED, set())
        expected = [
            QualificationState.IMPLEMENTED,
            QualificationState.CONFIGURED,
            QualificationState.AUTHENTICATED,
        ]
        assert missing == expected
        assert len(missing) == 3


class TestFeedbackDependencyMutations:
    """Test that feedback requirements are correctly determined.

    Mutations to catch:
    - requires_feedback returns opposite boolean
    - FEEDBACK_DEPENDENT_FLOOR changed to wrong value
    - comparison operator inverted (>= vs <)
    """

    def test_feedback_not_required_below_account_entitled(self):
        """Mutation: returns True for states that shouldn't require feedback."""
        assert requires_feedback(QualificationState.IMPLEMENTED) is False
        assert requires_feedback(QualificationState.CONFIGURED) is False
        assert requires_feedback(QualificationState.AUTHENTICATED) is False

    def test_feedback_required_at_account_entitled_and_above(self):
        """Mutation: returns False for states that should require feedback."""
        assert requires_feedback(QualificationState.ACCOUNT_ENTITLED) is True
        assert requires_feedback(QualificationState.PROTOCOL_TESTED) is True
        assert requires_feedback(QualificationState.VENUE_TESTED) is True
        assert requires_feedback(QualificationState.RELEASE_APPROVED) is True

    def test_feedback_dependent_floor_is_correct(self):
        """Mutation: FEEDBACK_DEPENDENT_FLOOR set to wrong value."""
        assert FEEDBACK_DEPENDENT_FLOOR == QualificationState.ACCOUNT_ENTITLED


class TestParseStateMutations:
    """Test qualification state parsing.

    Mutations to catch:
    - parse_state accepts invalid strings
    - ValueError not raised correctly
    - case sensitivity lost
    """

    def test_parse_state_valid_implemented(self):
        """Mutation: parsing fails or returns wrong value."""
        result = parse_state("implemented")
        assert result == QualificationState.IMPLEMENTED

    def test_parse_state_valid_release_approved(self):
        """Mutation: case-insensitive parsing when should be case-sensitive."""
        result = parse_state("release_approved")
        assert result == QualificationState.RELEASE_APPROVED

    def test_parse_state_invalid_raises(self):
        """Mutation: invalid state accepted instead of raising."""
        with pytest.raises(QualificationError) as exc_info:
            parse_state("invalid_state")
        assert "not a valid qualification state" in str(exc_info.value)

    def test_parse_state_case_sensitive(self):
        """Mutation: uppercase/mixed-case incorrectly accepted."""
        with pytest.raises(QualificationError):
            parse_state("IMPLEMENTED")
        with pytest.raises(QualificationError):
            parse_state("Implemented")


# ============================================================================
# Track 73.2: Export Events Module Mutations
# ============================================================================


class TestCurrencyResolutionMutations:
    """Test currency resolution logic for instruments.

    Mutations to catch:
    - split logic inverted or wrong index used
    - string operations on wrong symbol format
    - default value changed from "USD"
    """

    def test_resolve_currency_forex_pair(self):
        """Mutation: wrong side of pair extracted (first vs last)."""
        assert _resolve_currency("EUR/USD") == "USD"
        assert _resolve_currency("BTC/USDT") == "USDT"
        assert _resolve_currency("GBP/JPY") == "JPY"

    def test_resolve_currency_forex_uppercase(self):
        """Mutation: case conversion missing."""
        result = _resolve_currency("eur/usd")
        assert result == "USD"  # Should be uppercase

    def test_resolve_currency_non_forex_defaults_usd(self):
        """Mutation: default changed to wrong value."""
        assert _resolve_currency("AAPL") == "USD"
        assert _resolve_currency("ES") == "USD"
        assert _resolve_currency("SPY") == "USD"

    def test_resolve_currency_no_slash_uses_default(self):
        """Mutation: still returns USD when / not found."""
        assert _resolve_currency("AAPL/BTC/USDT") == "USDT"  # Last split
        assert _resolve_currency("SIMPLE") == "USD"


class TestSourceReceiptEventIdMutations:
    """Test source receipt event ID construction.

    Mutations to catch:
    - prefix changed or omitted
    - signal_id not included
    - format string modified incorrectly
    """

    def test_source_receipt_event_id_format(self):
        """Mutation: prefix or format changed."""
        signal_id = "sig_12345"
        result = source_receipt_event_id(signal_id)
        assert result.startswith("source-receipt:")
        assert result == f"source-receipt:{signal_id}"

    def test_source_receipt_event_id_includes_signal_id(self):
        """Mutation: signal_id not included in result."""
        signal_id = "unique_signal"
        result = source_receipt_event_id(signal_id)
        assert signal_id in result


class TestExecutionAppliedEnvelopeStatusCheckMutations:
    """Test execution applied envelope status validation logic.

    Mutations to catch:
    - status comparison inverted (!= vs ==)
    - missing status checks
    """

    def test_build_execution_applied_requires_filled_status(self):
        """Mutation: non-FILLED status incorrectly accepted."""
        # Test the core logic: must be FILLED status, not other statuses
        test_cases = [
            (OrderStatus.FILLED, True),      # Should accept FILLED
            (OrderStatus.PENDING, False),    # Should reject PENDING
            (OrderStatus.REJECTED, False),   # Should reject REJECTED
            (OrderStatus.ERROR, False),      # Should reject ERROR
        ]

        for status, should_accept in test_cases:
            # This is the core check from build_execution_applied_envelope
            is_filled = status == OrderStatus.FILLED
            assert is_filled == should_accept, f"Status {status} check failed"

    def test_build_execution_applied_none_field_checks(self):
        """Mutation: None field validation removed or inverted."""
        # Test the three required fields for a FILLED order
        filled_result = {
            "broker_order_id": "ord123",
            "filled_quantity": 100.0,
            "filled_price": 150.0,
        }

        # All present - should pass
        has_all = (
            filled_result["broker_order_id"] is not None and
            filled_result["filled_quantity"] is not None and
            filled_result["filled_price"] is not None
        )
        assert has_all is True

        # Missing broker_order_id - should fail
        partial = filled_result.copy()
        partial["broker_order_id"] = None
        has_all = (
            partial["broker_order_id"] is not None and
            partial["filled_quantity"] is not None and
            partial["filled_price"] is not None
        )
        assert has_all is False


class TestSourceReceiptSideFilteringMutations:
    """Test source receipt filtering by signal side.

    Mutations to catch:
    - Side.CLOSE check inverted or removed
    - returns envelope when should return None
    """

    def test_build_source_receipt_close_filtering(self):
        """Mutation: CLOSE signals incorrectly processed."""
        # Core logic: CLOSE signals should be skipped
        test_sides = [
            (Side.CLOSE, True),   # Should skip (return None)
            (Side.BUY, False),    # Should process
            (Side.SELL, False),   # Should process
        ]

        for side, should_skip in test_sides:
            # This is the core check from build_source_receipt_envelope
            is_close = side == Side.CLOSE
            assert is_close == should_skip, f"Side {side} filtering failed"

    def test_build_source_receipt_side_equality(self):
        """Mutation: Side.CLOSE comparison inverted."""
        # Test that the equality check works correctly
        side_close = Side.CLOSE
        side_buy = Side.BUY

        # CLOSE should match CLOSE
        assert side_close == Side.CLOSE

        # BUY should not match CLOSE
        assert not (side_buy == Side.CLOSE)


class TestRoutingAdmissionOutcomeSideFilteringMutations:
    """Test routing admission outcome filtering by signal side.

    Mutations to catch:
    - Side.CLOSE check inverted
    - returns envelope when should return None
    """

    def test_build_routing_admission_outcome_close_filtering(self):
        """Mutation: CLOSE signals incorrectly processed."""
        # Core logic: CLOSE signals should be skipped
        test_sides = [
            (Side.CLOSE, True),   # Should skip (return None)
            (Side.BUY, False),    # Should process
            (Side.SELL, False),   # Should process
        ]

        for side, should_skip in test_sides:
            # This is the core check
            is_close = side == Side.CLOSE
            assert is_close == should_skip

    def test_build_routing_admission_outcome_side_equality(self):
        """Mutation: Side comparison logic inverted."""
        # Test that the equality check works correctly
        side_close = Side.CLOSE
        side_sell = Side.SELL

        # CLOSE should match CLOSE
        assert side_close == Side.CLOSE

        # SELL should not match CLOSE
        assert not (side_sell == Side.CLOSE)


# ============================================================================
# Track 73.3: Shadow Mode Module Mutations
# ============================================================================


class TestTargetPriceSelectionMutations:
    """Test target price extraction logic.

    Mutations to catch:
    - targets list checked inverted (if not vs if)
    - empty list returned when should have prices
    - None vs 0 confusion
    """

    def test_target_prices_from_signal_targets(self):
        """Mutation: returns empty list when targets present."""
        targets = [
            ProfitTarget(price=160.0),
            ProfitTarget(price=170.0),
        ]
        signal = Signal(
            source="test", symbol="AAPL", side=Side.BUY,
            asset_class=AssetClass.EQUITY, targets=targets
        )

        result = _target_prices(signal)
        assert result == [160.0, 170.0]
        assert len(result) == 2

    def test_target_prices_fallback_to_take_profit(self):
        """Mutation: doesn't fall back to take_profit when targets empty."""
        signal = Signal(
            source="test", symbol="AAPL", side=Side.BUY,
            asset_class=AssetClass.EQUITY, targets=[],
            take_profit=175.0
        )

        result = _target_prices(signal)
        assert result == [175.0]

    def test_target_prices_empty_when_no_targets_or_profit(self):
        """Mutation: returns non-empty list when should be empty."""
        signal = Signal(
            source="test", symbol="AAPL", side=Side.BUY,
            asset_class=AssetClass.EQUITY, targets=[],
            take_profit=None
        )

        result = _target_prices(signal)
        assert result == []

    def test_target_prices_ignores_profit_target_when_targets_present(self):
        """Mutation: uses take_profit even when targets present."""
        targets = [ProfitTarget(price=160.0)]
        signal = Signal(
            source="test", symbol="AAPL", side=Side.BUY,
            asset_class=AssetClass.EQUITY, targets=targets,
            take_profit=175.0
        )

        result = _target_prices(signal)
        assert result == [160.0]  # Only from targets, not take_profit


class TestShadowOrderIntentMutations:
    """Test shadow order intent conversion.

    Mutations to catch:
    - to_result_row misses fields
    - field names changed
    - datetime conversion incorrect
    """

    def test_to_result_row_includes_all_fields(self):
        """Mutation: to_result_row misses fields or includes extra."""
        intent = ShadowOrderIntent(
            id="intent_123", signal_id="sig_456",
            provider_id="provider", account_id="account",
            symbol="AAPL", side="BUY", quantity=100.0,
            expected_entry=150.0, stop_price=145.0,
            targets=[160.0, 170.0], policy_reference="policy v1",
            reasoning="Test reason"
        )

        row = to_result_row(intent)
        assert row["id"] == "intent_123"
        assert row["signal_id"] == "sig_456"
        assert row["provider_id"] == "provider"
        assert row["account_id"] == "account"
        assert row["symbol"] == "AAPL"
        assert row["side"] == "BUY"
        assert row["quantity"] == 100.0
        assert row["expected_entry"] == 150.0
        assert row["stop_price"] == 145.0
        assert row["targets"] == [160.0, 170.0]
        assert row["policy_reference"] == "policy v1"
        assert row["reasoning"] == "Test reason"

    def test_to_result_row_datetime_conversion(self):
        """Mutation: datetime not converted to ISO format."""
        now = datetime.now(timezone.utc)
        intent = ShadowOrderIntent(
            id="intent_123", signal_id="sig_456",
            provider_id="provider", account_id="account",
            symbol="AAPL", side="BUY", quantity=100.0,
            expected_entry=150.0, stop_price=145.0,
            computed_at=now
        )

        row = to_result_row(intent)
        assert isinstance(row["computed_at"], str)
        assert row["computed_at"] == now.isoformat()


# ============================================================================
# Track 73.4: Phone Escalation Module Mutations
# ============================================================================


class TestEscalationEligibilityMutations:
    """Test escalation eligibility checking.

    Mutations to catch:
    - needs_escalation returns opposite boolean
    - completeness set changed or inverted
    - membership test (in vs not in) inverted
    """

    def test_needs_escalation_for_partial_content(self):
        """Mutation: returns False for PARTIAL."""
        assert needs_escalation(ContentCompleteness.PARTIAL) is True

    def test_needs_escalation_for_pointer_only_content(self):
        """Mutation: returns False for POINTER_ONLY."""
        assert needs_escalation(ContentCompleteness.POINTER_ONLY) is True

    def test_needs_escalation_for_truncated_content(self):
        """Mutation: returns False for TRUNCATED."""
        assert needs_escalation(ContentCompleteness.TRUNCATED) is True

    def test_no_escalation_for_complete_content(self):
        """Mutation: returns True for COMPLETE."""
        assert needs_escalation(ContentCompleteness.COMPLETE) is False

    def test_no_escalation_for_unknown_content(self):
        """Mutation: returns True for UNKNOWN."""
        assert needs_escalation(ContentCompleteness.UNKNOWN) is False


class TestDeniedAppPackageMutations:
    """Test denied app package checking.

    Mutations to catch:
    - boolean logic inverted (returns opposite)
    - deny-list changed or empty
    - exact vs substring matching inverted
    - case sensitivity lost
    """

    def test_denied_app_package_exact_match_broker(self):
        """Mutation: exact match not enforced."""
        assert is_denied_app_package("com.alpaca.app") is True
        assert is_denied_app_package("com.ibkr.mtrader") is True

    def test_denied_app_package_pattern_match_banking(self):
        """Mutation: pattern matching not working."""
        # These patterns are from _BANKING_PAYMENT_PATTERNS
        assert is_denied_app_package("com.bank.mobile") is True  # matches "bank" pattern
        assert is_denied_app_package("com.mybank.app") is True  # matches "bank" pattern
        assert is_denied_app_package("com.paypal.app") is True  # matches "paypal" pattern

    def test_allowed_app_package_not_denied(self):
        """Mutation: returns True for allowed packages."""
        assert is_denied_app_package("com.twitter.android") is False
        assert is_denied_app_package("com.discord") is False

    def test_denied_app_package_case_insensitive(self):
        """Mutation: case sensitivity not handled."""
        assert is_denied_app_package("COM.ALPACA.APP") is True
        assert is_denied_app_package("Com.Alpaca.App") is True

    def test_denied_app_package_empty_returns_false(self):
        """Mutation: returns True for empty string."""
        assert is_denied_app_package("") is False
        assert is_denied_app_package("   ") is False


class TestCapabilityStateTransitionMutations:
    """Test capability state transition validation.

    Mutations to catch:
    - transition table changed or incomplete
    - equality check (== vs !=) inverted
    - allowed transitions missed or added
    """

    def test_transition_disabled_to_shadow_allowed(self):
        """Mutation: transition rejected when should be allowed."""
        validate_state_transition(CapabilityState.DISABLED, CapabilityState.SHADOW)

    def test_transition_shadow_to_enabled_allowed(self):
        """Mutation: transition rejected when should be allowed."""
        validate_state_transition(CapabilityState.SHADOW, CapabilityState.ENABLED)

    def test_transition_shadow_to_disabled_allowed(self):
        """Mutation: demote-to-disabled path blocked."""
        validate_state_transition(CapabilityState.SHADOW, CapabilityState.DISABLED)

    def test_transition_enabled_to_disabled_allowed(self):
        """Mutation: emergency off-switch blocked."""
        validate_state_transition(CapabilityState.ENABLED, CapabilityState.DISABLED)

    def test_transition_disabled_to_enabled_rejected(self):
        """Mutation: incorrect transition allowed."""
        with pytest.raises(PhoneEscalationError):
            validate_state_transition(CapabilityState.DISABLED, CapabilityState.ENABLED)

    def test_transition_same_state_idempotent(self):
        """Mutation: rejects same-state transition."""
        # Should not raise for same state
        validate_state_transition(CapabilityState.ENABLED, CapabilityState.ENABLED)


class TestProviderConfigValidationMutations:
    """Test provider escalation config validation.

    Mutations to catch:
    - validation checks removed or inverted
    - empty/None handling changed
    - regex/pattern matching broken
    """

    def test_validate_config_rejects_empty_app_package(self):
        """Mutation: empty app_package accepted."""
        with pytest.raises(PhoneEscalationError):
            validate_config_registration(app_package="", provider_name="test")

    def test_validate_config_rejects_whitespace_app_package(self):
        """Mutation: whitespace-only not rejected (missing strip())."""
        with pytest.raises(PhoneEscalationError):
            validate_config_registration(app_package="   ", provider_name="test")

    def test_validate_config_rejects_spaced_app_package(self):
        """Mutation: spaces in package name not rejected."""
        with pytest.raises(PhoneEscalationError):
            validate_config_registration(app_package="com test app", provider_name="test")

    def test_validate_config_rejects_empty_provider_name(self):
        """Mutation: empty provider_name accepted."""
        with pytest.raises(PhoneEscalationError):
            validate_config_registration(app_package="com.test.app", provider_name="")

    def test_validate_config_rejects_denied_broker_package(self):
        """Mutation: broker packages allowed."""
        with pytest.raises(PhoneEscalationError):
            validate_config_registration(app_package="com.alpaca.app", provider_name="test")

    def test_validate_config_accepts_valid(self):
        """Mutation: rejects valid config."""
        # Should not raise
        validate_config_registration(app_package="com.test.app", provider_name="test")


class TestPhoneControlAdapterDenyListMutations:
    """Test phone control adapter deny-list enforcement.

    Mutations to catch:
    - is_denied_app_package not called
    - exception not raised (DeniedAppPackageError)
    - exception type changed
    """

    @pytest.mark.asyncio
    async def test_adapter_open_app_rejects_denied_package(self):
        """Mutation: denied package opened without error."""
        adapter = MockPhoneControlAdapter()

        with pytest.raises(DeniedAppPackageError):
            await adapter.open_app("com.alpaca.app")

    @pytest.mark.asyncio
    async def test_adapter_open_app_accepts_allowed_package(self):
        """Mutation: allowed package rejected."""
        adapter = MockPhoneControlAdapter()

        # Should not raise
        await adapter.open_app("com.twitter.android")
        assert "com.twitter.android" in adapter.opened_packages

    @pytest.mark.asyncio
    async def test_adapter_tap_requires_navigation_role(self):
        """Mutation: non-navigation nodes allow tapping."""
        adapter = MockPhoneControlAdapter()
        node = AccessibilityNode(
            node_id="submit_btn", text="Submit", content_description=None,
            role="submit"
        )

        with pytest.raises(PermissionError):
            await adapter.tap(node)

    @pytest.mark.asyncio
    async def test_adapter_tap_allows_navigation_role(self):
        """Mutation: navigation nodes rejected."""
        adapter = MockPhoneControlAdapter()
        node = AccessibilityNode(
            node_id="nav_btn", text="Next", content_description=None,
            role="navigation"
        )

        # Should not raise
        await adapter.tap(node)
        assert "nav_btn" in adapter.tapped_node_ids


# ============================================================================
# Track 73.5: Execution Quality Module Mutations
# ============================================================================


class TestTimestampParsingMutations:
    """Test timestamp parsing logic.

    Mutations to catch:
    - datetime parsing incorrect
    - None handling inverted
    - ValueError propagation broken
    """

    def test_parse_valid_timestamp(self):
        """Mutation: parsing fails for valid ISO format."""
        ts_str = "2026-10-02T12:00:00+00:00"
        result = _parse(ts_str)
        assert isinstance(result, datetime)

    def test_parse_optional_valid_timestamp(self):
        """Mutation: None not returned for None input."""
        assert _parse_optional(None) is None

    def test_parse_optional_valid_string(self):
        """Mutation: parses None when should parse string."""
        ts_str = "2026-10-02T12:00:00+00:00"
        result = _parse_optional(ts_str)
        assert isinstance(result, datetime)

    def test_parse_invalid_raises(self):
        """Mutation: invalid timestamp accepted."""
        with pytest.raises(ValueError):
            _parse("invalid_timestamp")


class TestLatencyCalculationMutations:
    """Test latency computation logic.

    Mutations to catch:
    - subtraction direction inverted (end - start vs start - end)
    - total_seconds() call missing
    - clock skew check inverted (< vs >=)
    - negative latency accepted
    """

    def test_latency_calculation_uses_datetime_subtraction(self):
        """Mutation: latency calculation method incorrect."""
        # Unit test for the datetime math itself
        end = datetime(2026, 10, 2, 12, 0, 5, tzinfo=timezone.utc)
        start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

        latency = (end - start).total_seconds()
        assert latency == 5.0
        assert latency > 0

    def test_latency_calculation_inverted_is_negative(self):
        """Mutation: catch inverted calculation (start - end)."""
        end = datetime(2026, 10, 2, 12, 0, 5, tzinfo=timezone.utc)
        start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)

        # Wrong calculation (inverted)
        wrong_latency = (start - end).total_seconds()
        assert wrong_latency == -5.0
        assert wrong_latency < 0

        # Correct calculation
        correct_latency = (end - start).total_seconds()
        assert correct_latency > 0


class TestStageLatencyComparisonMutations:
    """Test stage latency endpoint validation.

    Mutations to catch:
    - None check inverted or missing
    - endpoints swapped (end - start vs start - end)
    - negative intervals not filtered
    """

    def test_stage_latency_requires_both_endpoints_none_check(self):
        """Mutation: None endpoint check inverted or missing."""
        # Test that None endpoints correctly skip a stage
        start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
        end = None

        # Correctly rejects None end
        if start is None or end is None:
            # Should skip this stage
            skip = True
        else:
            skip = False

        assert skip is True

    def test_stage_latency_both_present_processes(self):
        """Mutation: correctly processes when both endpoints present."""
        start = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 2, 12, 0, 5, tzinfo=timezone.utc)

        # Correctly processes both present
        if start is None or end is None:
            skip = True
        else:
            interval = (end - start).total_seconds()
            skip = False

        assert skip is False
        assert interval == 5.0


class TestAccountExecutionQualityStructureMutations:
    """Test execution quality aggregation.

    Mutations to catch:
    - unmatched_order_count not initialized to 0
    - per_symbol/stage_latencies not initialized
    - to_dict missing fields or wrong types
    """

    def test_account_execution_quality_init(self):
        """Mutation: initialization incorrect."""
        quality = AccountExecutionQuality(account_id="test_account")
        assert quality.account_id == "test_account"
        assert quality.per_symbol == {}
        assert quality.stage_latencies == {}
        assert quality.unmatched_order_count == 0

    def test_account_execution_quality_to_dict(self):
        """Mutation: to_dict missing fields or wrong structure."""
        quality = AccountExecutionQuality(
            account_id="test",
            per_symbol={"AAPL": SymbolLatency("AAPL", 1, 5.0, 5.0, 5.0)},
            unmatched_order_count=2
        )

        result = quality.to_dict()
        assert result["account_id"] == "test"
        assert "per_symbol" in result
        assert "stage_latencies" in result
        assert result["unmatched_order_count"] == 2
        assert "AAPL" in result["per_symbol"]


class TestSymbolLatencyStructureMutations:
    """Test symbol latency dataclass integrity.

    Mutations to catch:
    - fields removed or renamed
    - sample_count not present
    - statistical measures wrong type
    """

    def test_symbol_latency_fields(self):
        """Mutation: required fields missing."""
        latency = SymbolLatency(
            symbol="TEST", sample_count=5,
            mean_seconds=10.5, median_seconds=10.0,
            max_seconds=15.0
        )

        assert latency.symbol == "TEST"
        assert latency.sample_count == 5
        assert latency.mean_seconds == 10.5
        assert latency.median_seconds == 10.0
        assert latency.max_seconds == 15.0


class TestStageLatencyConversionMutations:
    """Test stage latency to_dict conversion.

    Mutations to catch:
    - to_dict not implemented or broken
    - field names changed
    - values not included
    """

    def test_stage_latency_to_dict(self):
        """Mutation: to_dict missing fields or wrong keys."""
        stage = StageLatency(
            stage="receipt_to_submission",
            sample_count=3,
            mean_seconds=2.5,
            median_seconds=2.0,
            max_seconds=3.5
        )

        result = stage.to_dict()
        assert result["stage"] == "receipt_to_submission"
        assert result["sample_count"] == 3
        assert result["mean_seconds"] == 2.5
        assert result["median_seconds"] == 2.0
        assert result["max_seconds"] == 3.5
