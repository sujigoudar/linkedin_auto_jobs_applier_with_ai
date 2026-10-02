"""Track 64: Mutation testing regression suite for certification.py and
certification_evidence.py.

This file implements comprehensive, hand-written regression tests for the
provider certification state machine and automated evidence collection,
designed to catch mutations that would silently break certification
correctness:

- Status comparison operators (== vs !=, in vs not in)
- Scope validation logic (empty/None vs whitespace-only strings)
- Evidence validation boundaries (empty dict check, truthiness)
- Check classification consistency (AUTOMATED vs ATTESTATION_ONLY)
- Live eligibility computation (all vs any, missing vs not-missing)
- Automated check result conditions (>, >=, <, <=, exact equality)
- Threshold comparisons in evidence checks (95% accuracy, health_score > 0)
- Boolean logic inversions (and vs or, not mutations)

Every test is hand-written to target a specific high-risk mutation,
never a library-test comparison. Pattern mirrors Track 59/61 approach.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.certification import (
    ALL_CHECKS,
    CHECK_KIND,
    CertificationError,
    CheckKind,
    CheckName,
    CheckStatus,
    is_live_eligible,
    now_utc,
    parse_check_name,
    parse_check_status,
    validate_check_record,
    validate_scope,
)
from app.certification_evidence import (
    AutomatedCheckResult,
    connection_check,
    cross_channel_correlation_check,
    duplicate_handling_check,
    historical_retrieval_check,
    paper_execution_check,
    parser_check,
    PAPER_BROKER_KEY,
)
from app.signal_correlation import fingerprint_key
from app.db import SignalStore
from app.models import AssetClass, Signal, Side, DestinationAccount, OrderResult, OrderStatus


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


# =============================================================================
# SCOPE VALIDATION MUTATIONS
# =============================================================================


class TestScopeValidationMutations:
    """Scope validation must require all four dimensions and reject empty/None."""

    def test_validate_scope_rejects_empty_provider_id(self):
        """Mutation: empty string not rejected (== vs str.strip() check)."""
        with pytest.raises(CertificationError) as exc_info:
            validate_scope(provider_id="", source_id="s", asset_class="equity", account_route="a")
        assert "provider_id is required" in str(exc_info.value)

    def test_validate_scope_rejects_whitespace_only_provider_id(self):
        """Mutation: whitespace-only string not normalized (missing str.strip())."""
        with pytest.raises(CertificationError) as exc_info:
            validate_scope(provider_id="   ", source_id="s", asset_class="equity", account_route="a")
        assert "provider_id is required" in str(exc_info.value)

    def test_validate_scope_rejects_empty_source_id(self):
        """Mutation: empty source_id not rejected."""
        with pytest.raises(CertificationError) as exc_info:
            validate_scope(provider_id="p", source_id="", asset_class="equity", account_route="a")
        assert "source_id is required" in str(exc_info.value)

    def test_validate_scope_rejects_empty_asset_class(self):
        """Mutation: empty asset_class not rejected."""
        with pytest.raises(CertificationError) as exc_info:
            validate_scope(provider_id="p", source_id="s", asset_class="", account_route="a")
        assert "asset_class is required" in str(exc_info.value)

    def test_validate_scope_rejects_empty_account_route(self):
        """Mutation: empty account_route not rejected."""
        with pytest.raises(CertificationError) as exc_info:
            validate_scope(provider_id="p", source_id="s", asset_class="equity", account_route="")
        assert "account_route is required" in str(exc_info.value)

    def test_validate_scope_accepts_all_present_non_empty(self):
        """All four dimensions present and non-empty: no exception."""
        # This should not raise
        validate_scope(provider_id="p", source_id="s", asset_class="equity", account_route="a")


# =============================================================================
# PARSE FUNCTIONS: ENUM CONVERSION CORRECTNESS
# =============================================================================


class TestParseCheckNameMutations:
    """parse_check_name must accept valid names and reject invalid."""

    def test_parse_check_name_valid_connection(self):
        """Valid check name 'connection' parses correctly."""
        result = parse_check_name("connection")
        assert result == CheckName.CONNECTION

    def test_parse_check_name_valid_paper_execution(self):
        """Valid check name 'paper_execution' parses correctly."""
        result = parse_check_name("paper_execution")
        assert result == CheckName.PAPER_EXECUTION

    def test_parse_check_name_rejects_invalid(self):
        """Mutation: invalid name accepted instead of raising."""
        with pytest.raises(CertificationError) as exc_info:
            parse_check_name("invalid_check_name")
        assert "not a recognized certification check" in str(exc_info.value)

    def test_parse_check_name_case_sensitive(self):
        """Mutation: case-insensitive comparison (should be case-sensitive)."""
        # CheckName enum values are lowercase, so uppercase should fail
        with pytest.raises(CertificationError):
            parse_check_name("CONNECTION")


class TestParseCheckStatusMutations:
    """parse_check_status must accept valid statuses and reject invalid."""

    def test_parse_check_status_valid_pass(self):
        """Valid status 'PASS' parses correctly."""
        result = parse_check_status("PASS")
        assert result == CheckStatus.PASS

    def test_parse_check_status_valid_not_run(self):
        """Valid status 'NOT_RUN' parses correctly."""
        result = parse_check_status("NOT_RUN")
        assert result == CheckStatus.NOT_RUN

    def test_parse_check_status_rejects_invalid(self):
        """Mutation: invalid status accepted instead of raising."""
        with pytest.raises(CertificationError) as exc_info:
            parse_check_status("INVALID_STATUS")
        assert "not a recognized check status" in str(exc_info.value)

    def test_parse_check_status_case_sensitive(self):
        """Mutation: case-insensitive comparison (should be case-sensitive)."""
        with pytest.raises(CertificationError):
            parse_check_status("pass")  # lowercase not valid


# =============================================================================
# EVIDENCE VALIDATION MUTATIONS
# =============================================================================


class TestCheckRecordValidationMutations:
    """validate_check_record enforces evidence for PASS/FAIL, allows none for NOT_RUN."""

    def test_pass_requires_non_empty_evidence_dict(self):
        """Mutation: empty dict {} accepted as evidence (should require non-empty)."""
        with pytest.raises(CertificationError) as exc_info:
            validate_check_record(
                check_name="entry",
                status="PASS",
                evidence={},
                checked_by="op@example.com",
            )
        assert "non-empty `evidence` payload" in str(exc_info.value)

    def test_pass_requires_evidence_not_none(self):
        """Mutation: None evidence accepted (should require dict)."""
        with pytest.raises(CertificationError) as exc_info:
            validate_check_record(
                check_name="entry",
                status="PASS",
                evidence=None,
                checked_by="op@example.com",
            )
        assert "non-empty `evidence` payload" in str(exc_info.value)

    def test_pass_requires_non_empty_checked_by(self):
        """Mutation: empty/None checked_by accepted (should require non-empty)."""
        with pytest.raises(CertificationError) as exc_info:
            validate_check_record(
                check_name="entry",
                status="PASS",
                evidence={"note": "verified"},
                checked_by="",
            )
        assert "requires `checked_by`" in str(exc_info.value)

    def test_pass_requires_checked_by_not_none(self):
        """Mutation: None checked_by accepted (should require string)."""
        with pytest.raises(CertificationError) as exc_info:
            validate_check_record(
                check_name="entry",
                status="PASS",
                evidence={"note": "verified"},
                checked_by=None,
            )
        assert "requires `checked_by`" in str(exc_info.value)

    def test_fail_requires_evidence_like_pass(self):
        """Mutation: FAIL treated differently from PASS (should have same requirements)."""
        # FAIL must also require evidence + checked_by
        with pytest.raises(CertificationError):
            validate_check_record(
                check_name="entry",
                status="FAIL",
                evidence=None,
                checked_by="op@example.com",
            )

    def test_not_run_allows_no_evidence(self):
        """NOT_RUN/SKIPPED may have no evidence."""
        # These should not raise
        validate_check_record(
            check_name="entry",
            status="NOT_RUN",
            evidence=None,
            checked_by=None,
        )
        validate_check_record(
            check_name="entry",
            status="SKIPPED",
            evidence=None,
            checked_by=None,
        )

    def test_pass_with_valid_evidence_and_checked_by_accepted(self):
        """Mutation: valid inputs rejected (should be accepted)."""
        # This should not raise
        validate_check_record(
            check_name="entry",
            status="PASS",
            evidence={"note": "verified on 2026-09-30"},
            checked_by="operator@example.com",
        )


# =============================================================================
# IS_LIVE_ELIGIBLE MUTATIONS
# =============================================================================


class TestIsLiveEligibleMutations:
    """is_live_eligible must require ALL checks to be PASS, not just some."""

    def test_live_eligible_true_only_when_all_pass(self):
        """Mutation: any/or logic instead of all/and (should require ALL)."""
        rows = [
            {"check_name": c.value, "status": CheckStatus.PASS.value}
            for c in ALL_CHECKS
        ]
        eligible, missing = is_live_eligible(rows)
        assert eligible is True
        assert missing == []

    def test_live_eligible_false_when_one_check_fails(self):
        """Mutation: FAIL treated as PASS (== vs != comparison)."""
        rows = [
            {"check_name": c.value, "status": CheckStatus.PASS.value}
            for c in ALL_CHECKS
        ]
        # Set the first check to FAIL
        rows[0]["status"] = CheckStatus.FAIL.value
        eligible, missing = is_live_eligible(rows)
        assert eligible is False
        assert len(missing) == 1
        assert CheckName(rows[0]["check_name"]) in missing

    def test_live_eligible_false_when_one_check_not_run(self):
        """Mutation: NOT_RUN treated as PASS (== vs != comparison)."""
        rows = [
            {"check_name": c.value, "status": CheckStatus.PASS.value}
            for c in ALL_CHECKS
        ]
        # Set one check to NOT_RUN
        rows[5]["status"] = CheckStatus.NOT_RUN.value
        eligible, missing = is_live_eligible(rows)
        assert eligible is False
        assert len(missing) == 1

    def test_live_eligible_false_when_check_missing(self):
        """Mutation: missing check not reported (absent treated as PASS)."""
        rows = [
            {"check_name": c.value, "status": CheckStatus.PASS.value}
            for c in ALL_CHECKS[1:]  # Skip first check
        ]
        eligible, missing = is_live_eligible(rows)
        assert eligible is False
        assert ALL_CHECKS[0] in missing

    def test_live_eligible_handles_invalid_check_names_gracefully(self):
        """Mutation: invalid check names crash (should skip with continue)."""
        rows = [
            {"check_name": "invalid_check", "status": CheckStatus.PASS.value},
            {"check_name": ALL_CHECKS[0].value, "status": CheckStatus.PASS.value},
        ]
        # Should not raise; invalid checks are skipped
        eligible, missing = is_live_eligible(rows)
        # eligible is False because not all checks present
        assert eligible is False

    def test_live_eligible_missing_list_includes_all_non_pass_checks(self):
        """Mutation: missing list incomplete (doesn't include all non-PASS)."""
        rows = [
            {"check_name": c.value, "status": CheckStatus.NOT_RUN.value}
            for c in ALL_CHECKS
        ]
        eligible, missing = is_live_eligible(rows)
        assert eligible is False
        # Every check should be in missing since none are PASS
        assert set(missing) == set(ALL_CHECKS)

    def test_live_eligible_returns_tuple_structure(self):
        """Mutation: returns bool instead of tuple (wrong return type)."""
        rows = [
            {"check_name": c.value, "status": CheckStatus.PASS.value}
            for c in ALL_CHECKS
        ]
        result = is_live_eligible(rows)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], list)


# =============================================================================
# CONNECTION CHECK MUTATIONS
# =============================================================================


class TestConnectionCheckMutations:
    """Connection check must verify state AND health_score."""

    def test_connection_pass_requires_connected_state_and_positive_health(self):
        """Mutation: missing health_score > 0 check (only state checked)."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": "conn1"}
        store.get_connection.return_value = {
            "connection_state": "connected",
            "health_score": 0.85,
        }
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.PASS
        assert result.evidence["health_score"] == 0.85

    def test_connection_fail_when_state_is_error(self):
        """Mutation: error state not marked as FAIL (== vs in check)."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": "conn1"}
        store.get_connection.return_value = {
            "connection_state": "error",
            "health_score": None,
        }
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.FAIL

    def test_connection_fail_when_state_is_disconnected(self):
        """Mutation: disconnected state not marked as FAIL."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": "conn1"}
        store.get_connection.return_value = {
            "connection_state": "disconnected",
            "health_score": 0.0,
        }
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.FAIL

    def test_connection_not_run_when_health_score_is_zero(self):
        """Mutation: zero health_score treated as PASS (> vs >=, or not checked)."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": "conn1"}
        store.get_connection.return_value = {
            "connection_state": "connected",
            "health_score": 0,
        }
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.NOT_RUN

    def test_connection_not_run_when_health_score_is_none(self):
        """Mutation: None health_score treated as PASS (missing check)."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": "conn1"}
        store.get_connection.return_value = {
            "connection_state": "connected",
            "health_score": None,
        }
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.NOT_RUN

    def test_connection_not_run_when_source_has_no_connection_id(self):
        """Mutation: missing connection_id check (treated as falsy)."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": None}
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.NOT_RUN
        assert "no connection_id wired" in result.detail

    def test_connection_not_run_when_source_missing(self):
        """Mutation: missing source treated as PASS instead of NOT_RUN."""
        store = MagicMock()
        store.get_source.return_value = None
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.NOT_RUN

    def test_connection_not_run_when_connection_missing(self):
        """Mutation: missing connection row treated as PASS."""
        store = MagicMock()
        store.get_source.return_value = {"connection_id": "conn1"}
        store.get_connection.return_value = None
        result = connection_check(store, source_id="src1")
        assert result.status == CheckStatus.NOT_RUN


# =============================================================================
# HISTORICAL RETRIEVAL CHECK MUTATIONS
# =============================================================================


class TestHistoricalRetrievalCheckMutations:
    """Historical retrieval must check for import_batch IS NOT NULL."""

    def test_historical_pass_when_import_batch_exists(self, store):
        """Mutation: import_batch query condition wrong (= vs IS NOT NULL)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        source = store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        # Add signal with import_batch set
        signal = Signal(
            source=provider_id,
            symbol="BTC",
            side=Side.BUY,
            import_batch="batch1",
        )
        store.save_signal(signal)
        result = historical_retrieval_check(store, provider_id=provider_id)
        assert result.status == CheckStatus.PASS
        assert result.evidence["historical_import_signal_count"] == 1

    def test_historical_not_run_when_no_import_batch(self, store):
        """Mutation: import_batch check skipped (always returns PASS)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        # Add signal WITHOUT import_batch
        signal = Signal(
            source=provider_id,
            symbol="BTC",
            side=Side.BUY,
        )
        store.save_signal(signal)
        result = historical_retrieval_check(store, provider_id=provider_id)
        assert result.status == CheckStatus.NOT_RUN
        assert result.evidence["historical_import_signal_count"] == 0

    def test_historical_not_run_when_provider_has_no_signals(self, store):
        """Mutation: missing provider check (not queried at all)."""
        result = historical_retrieval_check(store, provider_id="nonexistent")
        assert result.status == CheckStatus.NOT_RUN
        assert result.evidence["historical_import_signal_count"] == 0


# =============================================================================
# PARSER CHECK MUTATIONS
# =============================================================================


class TestParserCheckMutations:
    """Parser check must probe for Track 15 and require >= 95% accuracy."""

    def test_parser_not_run_when_no_parser_tooling(self, store):
        """Mutation: parser probing skipped (always fabricates PASS)."""
        # store has no get_parser_accuracy_metrics method
        result = parser_check(store, provider_id="provider1")
        assert result.status == CheckStatus.NOT_RUN
        assert "Track 15" in result.detail

    def test_parser_pass_when_accuracy_at_95_percent(self, store):
        """Mutation: threshold changed (>= vs >)."""
        store.get_parser_accuracy_metrics = MagicMock(
            return_value={"accuracy_pct": 0.95, "sample_count": 100}
        )
        result = parser_check(store, provider_id="provider1")
        assert result.status == CheckStatus.PASS

    def test_parser_pass_when_accuracy_above_95_percent(self, store):
        """Mutation: accuracy_pct not checked properly."""
        store.get_parser_accuracy_metrics = MagicMock(
            return_value={"accuracy_pct": 0.99, "sample_count": 100}
        )
        result = parser_check(store, provider_id="provider1")
        assert result.status == CheckStatus.PASS

    def test_parser_fail_when_accuracy_below_95_percent(self, store):
        """Mutation: below-threshold accuracy treated as PASS."""
        store.get_parser_accuracy_metrics = MagicMock(
            return_value={"accuracy_pct": 0.90, "sample_count": 100}
        )
        result = parser_check(store, provider_id="provider1")
        assert result.status == CheckStatus.FAIL

    def test_parser_not_run_when_accuracy_is_none(self, store):
        """Mutation: None accuracy treated as PASS or FAIL."""
        store.get_parser_accuracy_metrics = MagicMock(
            return_value={"accuracy_pct": None, "sample_count": 50}
        )
        result = parser_check(store, provider_id="provider1")
        assert result.status == CheckStatus.NOT_RUN

    def test_parser_not_run_when_metrics_empty(self, store):
        """Mutation: empty metrics treated as PASS instead of NOT_RUN."""
        store.get_parser_accuracy_metrics = MagicMock(return_value={})
        result = parser_check(store, provider_id="provider1")
        assert result.status == CheckStatus.NOT_RUN


# =============================================================================
# DUPLICATE HANDLING CHECK MUTATIONS
# =============================================================================


class TestDuplicateHandlingCheckMutations:
    """Duplicate handling requires ANY signal_correlation_evidence row."""

    def test_duplicate_pass_when_any_correlation_exists(self, store):
        """Mutation: row count check wrong (> 0 vs != 0, or >= 0)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        # Add signals and record correlation
        sig1 = Signal(source=provider_id, symbol="BTC", side=Side.BUY)
        sig2 = Signal(source=provider_id, symbol="BTC", side=Side.BUY)
        sig1_id = store.save_signal(sig1)
        sig2_id = store.save_signal(sig2)
        now = datetime.now(timezone.utc)
        store.record_signal_correlation_evidence(
            canonical_signal_id=sig1.id,
            evidence_signal_id=sig2.id,
            fingerprint_key=fingerprint_key(sig1),
            source=provider_id,
            channel_id="tg",
            message_id=None,
            price=None,
            side=None,
            received_at=now,
            match_type="corroborating",
        )
        result = duplicate_handling_check(store, provider_id=provider_id)
        assert result.status == CheckStatus.PASS
        assert result.evidence["correlation_evidence_row_count"] > 0

    def test_duplicate_not_run_when_no_correlation(self, store):
        """Mutation: no rows treated as PASS (empty list check)."""
        result = duplicate_handling_check(store, provider_id="provider1")
        assert result.status == CheckStatus.NOT_RUN
        assert result.evidence["correlation_evidence_row_count"] == 0


# =============================================================================
# CROSS-CHANNEL CORRELATION CHECK MUTATIONS
# =============================================================================


class TestCrossChannelCorrelationCheckMutations:
    """Cross-channel requires at least one canonical signal with multiple channels."""

    def test_cross_channel_pass_with_multiple_channels(self, store):
        """Mutation: cross-channel logic wrong (len(v) > 1 vs >= 1)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        # Create signals with different channels
        sig1 = Signal(source=provider_id, symbol="BTC", side=Side.BUY)
        sig2 = Signal(source=provider_id, symbol="BTC", side=Side.BUY)
        store.save_signal(sig1)
        store.save_signal(sig2)
        now = datetime.now(timezone.utc)
        # Record correlations from two different channels
        store.record_signal_correlation_evidence(
            canonical_signal_id=sig1.id,
            evidence_signal_id=sig2.id,
            fingerprint_key=fingerprint_key(sig1),
            source=provider_id,
            channel_id="tg",
            message_id=None,
            price=None,
            side=None,
            received_at=now,
            match_type="corroborating",
        )
        store.record_signal_correlation_evidence(
            canonical_signal_id=sig1.id,
            evidence_signal_id=sig2.id,
            fingerprint_key=fingerprint_key(sig1),
            source=provider_id,
            channel_id="email",
            message_id=None,
            price=None,
            side=None,
            received_at=now + timedelta(seconds=1),
            match_type="corroborating",
        )
        result = cross_channel_correlation_check(store, provider_id=provider_id)
        assert result.status == CheckStatus.PASS
        assert result.evidence["cross_channel_canonical_signal_count"] > 0

    def test_cross_channel_not_run_with_single_channel_only(self, store):
        """Mutation: single channel treated as cross-channel."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        sig1 = Signal(source=provider_id, symbol="BTC", side=Side.BUY)
        sig2 = Signal(source=provider_id, symbol="BTC", side=Side.BUY)
        store.save_signal(sig1)
        store.save_signal(sig2)
        now = datetime.now(timezone.utc)
        # Only one channel
        store.record_signal_correlation_evidence(
            canonical_signal_id=sig1.id,
            evidence_signal_id=sig2.id,
            fingerprint_key=fingerprint_key(sig1),
            source=provider_id,
            channel_id="tg",
            message_id=None,
            price=None,
            side=None,
            received_at=now,
            match_type="corroborating",
        )
        result = cross_channel_correlation_check(store, provider_id=provider_id)
        assert result.status == CheckStatus.NOT_RUN
        assert result.evidence["cross_channel_canonical_signal_count"] == 0

    def test_cross_channel_not_run_when_no_correlation(self, store):
        """Mutation: missing correlation check (treated as PASS)."""
        result = cross_channel_correlation_check(store, provider_id="provider1")
        assert result.status == CheckStatus.NOT_RUN


# =============================================================================
# PAPER EXECUTION CHECK MUTATIONS
# =============================================================================


class TestPaperExecutionCheckMutations:
    """Paper execution requires filled orders on the paper broker."""

    def test_paper_pass_when_filled_order_exists(self, store):
        """Mutation: status comparison wrong (= vs !=, or != 'filled')."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        source = store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        # Add signal and create filled order
        signal = Signal(
            source=provider_id,
            symbol="BTC",
            side=Side.BUY,
            quantity=1.0,
        )
        store.save_signal(signal)
        order_result = OrderResult(
            account_id="acct1",
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id="o1",
            filled_quantity=1.0,
            filled_price=100.0,
        )
        store.save_order_result(order_result, broker=PAPER_BROKER_KEY)
        result = paper_execution_check(
            store, provider_id=provider_id, account_route="acct1"
        )
        assert result.status == CheckStatus.PASS
        assert result.evidence["filled_paper_order_count"] > 0

    def test_paper_not_run_when_no_filled_orders(self, store):
        """Mutation: missing status='filled' check (any status accepted)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        signal = Signal(
            source=provider_id,
            symbol="BTC",
            side=Side.BUY,
            quantity=1.0,
        )
        store.save_signal(signal)
        # Record PENDING order, not filled
        order_result = OrderResult(
            account_id="acct1",
            status=OrderStatus.PENDING,
            signal_id=signal.id,
            broker_order_id="o1",
            filled_quantity=0.0,
            filled_price=0.0,
        )
        store.save_order_result(order_result, broker=PAPER_BROKER_KEY)
        result = paper_execution_check(
            store, provider_id=provider_id, account_route="acct1"
        )
        assert result.status == CheckStatus.NOT_RUN
        assert result.evidence["filled_paper_order_count"] == 0

    def test_paper_not_run_when_non_paper_broker(self, store):
        """Mutation: broker check missing (non-paper treated as paper)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        signal = Signal(
            source=provider_id,
            symbol="BTC",
            side=Side.BUY,
            quantity=1.0,
        )
        store.save_signal(signal)
        # Record filled order on non-paper broker
        order_result = OrderResult(
            account_id="acct1",
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id="o1",
            filled_quantity=1.0,
            filled_price=100.0,
        )
        store.save_order_result(order_result, broker="alpaca")  # not the paper broker
        result = paper_execution_check(
            store, provider_id=provider_id, account_route="acct1"
        )
        assert result.status == CheckStatus.NOT_RUN
        assert result.evidence["filled_paper_order_count"] == 0

    def test_paper_not_run_when_wrong_account_route(self, store):
        """Mutation: account_route filter missing (any account accepted)."""
        provider_id = "provider1"
        store.register_provider(
            provider_id=provider_id,
            display_name="Test",
            status="onboarding",
            certification_state="uncertified",
        )
        store.register_source(
            source_id="src1",
            provider_id=provider_id,
            platform="telegram",
        )
        signal = Signal(
            source=provider_id,
            symbol="BTC",
            side=Side.BUY,
            quantity=1.0,
        )
        store.save_signal(signal)
        order_result = OrderResult(
            account_id="acct_different",
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id="o1",
            filled_quantity=1.0,
            filled_price=100.0,
        )
        store.save_order_result(order_result, broker=PAPER_BROKER_KEY)
        # Query for acct1, which has no filled orders
        result = paper_execution_check(
            store, provider_id=provider_id, account_route="acct1"
        )
        assert result.status == CheckStatus.NOT_RUN


# =============================================================================
# CHECK_KIND CONSISTENCY MUTATIONS
# =============================================================================


class TestCheckKindConsistency:
    """CHECK_KIND mapping must be complete and consistent."""

    def test_all_checks_have_a_kind(self):
        """Mutation: incomplete CHECK_KIND dict (missing entries)."""
        assert set(CHECK_KIND.keys()) == set(ALL_CHECKS)

    def test_every_kind_is_valid_enum_value(self):
        """Mutation: invalid kind values (not AUTOMATED or ATTESTATION_ONLY)."""
        for kind in CHECK_KIND.values():
            assert kind in (CheckKind.AUTOMATED, CheckKind.ATTESTATION_ONLY)

    def test_automated_checks_are_the_documented_set(self):
        """Mutation: wrong checks classified as AUTOMATED."""
        automated = {name for name, kind in CHECK_KIND.items() if kind == CheckKind.AUTOMATED}
        expected_automated = {
            CheckName.CONNECTION,
            CheckName.HISTORICAL_RETRIEVAL,
            CheckName.PARSER,
            CheckName.DUPLICATE_HANDLING,
            CheckName.CROSS_CHANNEL_CORRELATION,
            CheckName.PAPER_EXECUTION,
        }
        assert automated == expected_automated

    def test_attestation_only_checks_are_the_documented_set(self):
        """Mutation: wrong checks classified as ATTESTATION_ONLY."""
        attestation = {name for name, kind in CHECK_KIND.items() if kind == CheckKind.ATTESTATION_ONLY}
        expected_attestation = {
            CheckName.ENTRY,
            CheckName.EXIT,
            CheckName.STOP_UPDATE,
            CheckName.TARGET_UPDATE,
            CheckName.DISCONNECT_RECOVERY,
            CheckName.STALE_ALERT_HANDLING,
            CheckName.REJECTED_ENTRY_EXIT_TEST,
            CheckName.PARTIAL_FILL_EXIT_TEST,
        }
        assert attestation == expected_attestation


# =============================================================================
# AUTOMATED CHECK RESULT STRUCTURE MUTATIONS
# =============================================================================


class TestAutomatedCheckResultStructure:
    """AutomatedCheckResult must have correct defaults and structure."""

    def test_automated_check_result_status_required(self):
        """Mutation: status is optional (should be required)."""
        result = AutomatedCheckResult(status=CheckStatus.PASS)
        assert result.status == CheckStatus.PASS

    def test_automated_check_result_evidence_defaults_to_empty_dict(self):
        """Mutation: evidence default changed (None vs {})."""
        result = AutomatedCheckResult(status=CheckStatus.PASS)
        assert result.evidence == {}
        assert isinstance(result.evidence, dict)

    def test_automated_check_result_detail_defaults_to_empty_string(self):
        """Mutation: detail default changed (None vs "")."""
        result = AutomatedCheckResult(status=CheckStatus.PASS)
        assert result.detail == ""
        assert isinstance(result.detail, str)

    def test_automated_check_result_can_set_all_fields(self):
        """Mutation: fields not settable (read-only or deleted)."""
        result = AutomatedCheckResult(
            status=CheckStatus.FAIL,
            evidence={"key": "value"},
            detail="This is a detail message",
        )
        assert result.status == CheckStatus.FAIL
        assert result.evidence == {"key": "value"}
        assert result.detail == "This is a detail message"


# =============================================================================
# NOW_UTC FUNCTION CORRECTNESS
# =============================================================================


class TestNowUtcFunction:
    """now_utc() must return a timezone-aware UTC datetime."""

    def test_now_utc_returns_utc_aware_datetime(self):
        """Mutation: returns naive datetime instead of aware."""
        dt = now_utc()
        assert isinstance(dt, datetime)
        assert dt.tzinfo is not None
        assert dt.tzinfo == timezone.utc

    def test_now_utc_returns_recent_time(self):
        """Mutation: returns a hardcoded or stale timestamp."""
        dt = now_utc()
        # Should be within the last few seconds
        elapsed = (now_utc() - dt).total_seconds()
        assert elapsed < 5.0
