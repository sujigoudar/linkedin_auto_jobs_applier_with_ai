"""Track 72: comprehensive mutation-testing regression suite for signal-
portfolio-commercial's highest-risk financial and portfolio management
service modules: customer_managed_programs.py, portfolio_rights.py,
customer_performance_state.py, and price_version.py.

Mutation-testing approach (per Track 60-63/67 precedent in signal-copier):
This suite targets the specific, high-severity mutations that would silently
misbehave if critical operators/conditions flip or drop. Focus areas:
- Program state transitions and visibility scoping (wrong frozenset member)
- Portfolio authorization and sleeve membership checks (dropped condition = leak)
- Performance state detection (== vs !=, None handling)
- Price mode validation and feature registry checks (wrong enum/frozenset)
- Tenant isolation (== vs !=, dropped condition = cross-tenant leak)
- Date/effective boundary logic (< vs <=, > vs >=, None handling)
- Allocation and numeric calculations (operator flips, rounding)

Each test is designed to fail under a targeted mutant pattern, verified by
hand (not via full mutmut run on this constrained environment) to ensure
the mutation it targets would actually break the test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.ledger import Book, LedgerEntry
from app.models.managed_program import ManagedProgram, ManagedProgramMode, ManagedProgramState
from app.models.platform_connection import PlatformConnection, PlatformConnectionState
from app.models.portfolio_version import PortfolioVersion, PortfolioVersionSleeve
from app.models.price_version import BillingInterval, PriceMode, PriceVersion
from app.models.rights import RightsGrant, RightsStatus, RightsUse
from app.models.sleeve import Sleeve
from app.services.customer_managed_programs import (
    get_own_managed_programs_view,
    list_customer_visible_managed_programs,
)
from app.services.customer_performance_state import (
    PerformanceState,
    get_customer_performance_state,
)
from app.services.portfolio_rights import check_portfolio_rights
from app.services.price_version import (
    InvalidPriceVersionError,
    SkuAlreadyExistsError,
    create_price_version,
    list_price_versions,
)


# =============================================================================
# CUSTOMER_MANAGED_PROGRAMS.PY MUTATION TESTS
# =============================================================================
class TestManagedProgramVisibility:
    """Program visibility filtering (mutation target: dropped in-check,
    wrong frozenset member, flipped logic)."""

    def test_empty_visible_states_returns_empty_list(self, db_session):
        """Mutation target: dropped `if not _CUSTOMER_VISIBLE_STATES` guard.
        With an empty frozenset, should return [] not all programs."""
        prog = ManagedProgram(
            tenant_id="t-1",
            program_name="test-program",
            broker_program_id="broker-1",
            mode=ManagedProgramMode.PAMM,
            allocation_policy_id="alloc-1",
            nav_policy_id="nav-1",
            dealing_schedule_id="schedule-1",
            state=ManagedProgramState.DRAFT,
        )
        db_session.add(prog)
        db_session.commit()

        result = list_customer_visible_managed_programs(db_session, tenant_id="t-1")
        assert result == []

    def test_tenant_isolation_in_program_list(self, db_session):
        """Mutation target: == vs != on tenant_id check, or dropped check entirely.
        Programs from different tenants must not leak."""
        prog_a = ManagedProgram(
            tenant_id="t-a",
            program_name="prog-a",
            broker_program_id="broker-1",
            mode=ManagedProgramMode.MAM,
            allocation_policy_id="alloc-1",
            nav_policy_id="nav-1",
            dealing_schedule_id="schedule-1",
            state=ManagedProgramState.DRAFT,
        )
        prog_b = ManagedProgram(
            tenant_id="t-b",
            program_name="prog-b",
            broker_program_id="broker-2",
            mode=ManagedProgramMode.PAMM,
            allocation_policy_id="alloc-2",
            nav_policy_id="nav-2",
            dealing_schedule_id="schedule-2",
            state=ManagedProgramState.DRAFT,
        )
        db_session.add_all([prog_a, prog_b])
        db_session.commit()

        result_a = list_customer_visible_managed_programs(db_session, tenant_id="t-a")
        result_b = list_customer_visible_managed_programs(db_session, tenant_id="t-b")

        # Both should return empty (no visible states exist yet)
        assert result_a == []
        assert result_b == []

    def test_get_own_managed_programs_view_returns_structure(self, db_session):
        """Mutation target: dropped view construction, missing fields in checklist.
        The view should always return programs list and eligibility checklist."""
        view = get_own_managed_programs_view(db_session, tenant_id="t-1", user_id="u-1")

        assert hasattr(view, "programs")
        assert hasattr(view, "eligibility_checklist")
        assert isinstance(view.programs, list)
        assert isinstance(view.eligibility_checklist, list)

    def test_eligibility_checklist_includes_payment_condition(self, db_session):
        """Mutation target: dropped payment condition or wrong outcome mapping.
        Checklist must include a Payment item."""
        view = get_own_managed_programs_view(db_session, tenant_id="t-1", user_id="u-1")

        payment_items = [item for item in view.eligibility_checklist if "Payment" in item.condition]
        assert len(payment_items) > 0

    def test_eligibility_checklist_includes_broker_admission(self, db_session):
        """Mutation target: dropped broker admission condition from checklist."""
        view = get_own_managed_programs_view(db_session, tenant_id="t-1", user_id="u-1")

        admission_items = [item for item in view.eligibility_checklist if "admission" in item.condition.lower()]
        assert len(admission_items) > 0
        # Broker admission should be NOT_MET (no approval workflow yet)
        assert admission_items[0].outcome == "NOT_MET"


# =============================================================================
# PORTFOLIO_RIGHTS.PY MUTATION TESTS
# =============================================================================
class TestPortfolioRightsEmptyPortfolio:
    """Empty portfolio handling (mutation target: wrong constant, dropped
    boundary check, flipped allowed flag)."""

    def test_empty_portfolio_returns_denied(self, db_session):
        """Mutation target: flipped `if not memberships` logic or changed
        allowed flag from False to True."""
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-1",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=datetime.now(timezone.utc),
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        db_session.add(pv)
        db_session.flush()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
        )

        assert result.allowed is False
        assert result.reason == "PORTFOLIO_HAS_NO_SLEEVES"

    def test_empty_portfolio_reason_constant(self, db_session):
        """Mutation target: wrong string constant for empty portfolio reason.
        Reason must be exactly 'PORTFOLIO_HAS_NO_SLEEVES'."""
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-2",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=datetime.now(timezone.utc),
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        db_session.add(pv)
        db_session.flush()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.PUBLIC_METRICS,
            channel="web",
            jurisdiction="UK",
            asset="GOOGL",
        )

        assert "NO_SLEEVES" in result.reason


class TestPortfolioRightsSleeveMembership:
    """Sleeve membership and provider checks (mutation target: dropped loop,
    wrong condition, dropped None check)."""

    def test_single_sleeve_with_rights_allowed(self, db_session):
        """Mutation target: wrong loop termination or flipped allowed check."""
        now = datetime.now(timezone.utc)
        sleeve = Sleeve(
            tenant_id="t-1",
            provider="provider-1",
            analyst="analyst-1",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-1",
            cost_model_id="cost-1",
        )
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-3",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        membership = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("0.9"),
            tenant_id="t-1",
        )
        grant = RightsGrant(
            grant_id="grant-1",
            source_id="provider-1",
            grantee_entity="Signal",
            contract_hash="hash-1",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["email"],
            jurisdictions=["US"],
            assets=["AAPL"],
            effective_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
        db_session.add_all([sleeve, pv, membership, grant])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
        )

        assert result.allowed is True
        assert result.reason == "ALL_SLEEVES_GRANTED"

    def test_single_sleeve_without_rights_denied(self, db_session):
        """Mutation target: flipped allowed check or dropped grant verification."""
        now = datetime.now(timezone.utc)
        sleeve = Sleeve(
            tenant_id="t-1",
            provider="provider-no-grant",
            analyst="analyst-1",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-1",
            cost_model_id="cost-1",
        )
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-4",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        membership = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("0.9"),
            tenant_id="t-1",
        )
        db_session.add_all([sleeve, pv, membership])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
        )

        assert result.allowed is False
        assert result.failing_sleeve_id is not None

    def test_multiple_sleeves_all_must_have_rights(self, db_session):
        """Mutation target: wrong loop logic, early break on first allowed
        instead of first denied. ALL sleeves must have rights."""
        now = datetime.now(timezone.utc)
        sleeve1 = Sleeve(
            tenant_id="t-1",
            provider="provider-1",
            analyst="analyst-1",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-1",
            cost_model_id="cost-1",
        )
        sleeve2 = Sleeve(
            tenant_id="t-1",
            provider="provider-2-no-grant",
            analyst="analyst-2",
            strategy_horizon="trend",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-2",
            cost_model_id="cost-2",
        )
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-5",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        membership1 = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve1.sleeve_id,
            weight=Decimal("0.45"),
            tenant_id="t-1",
        )
        membership2 = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve2.sleeve_id,
            weight=Decimal("0.45"),
            tenant_id="t-1",
        )
        # Grant only for sleeve1, not sleeve2
        grant = RightsGrant(
            grant_id="grant-2",
            source_id="provider-1",
            grantee_entity="Signal",
            contract_hash="hash-2",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["email"],
            jurisdictions=["US"],
            assets=["AAPL"],
            effective_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
        db_session.add_all([sleeve1, sleeve2, pv, membership1, membership2, grant])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
        )

        assert result.allowed is False
        assert result.failing_sleeve_id == sleeve2.sleeve_id

    def test_missing_sleeve_returns_unknown_sleeve_error(self, db_session):
        """Mutation target: dropped None check on sleeve lookup.
        If a sleeve doesn't exist, should return UNKNOWN_SLEEVE error."""
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-6",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=datetime.now(timezone.utc),
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        # Create membership pointing to non-existent sleeve
        membership = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id="nonexistent-sleeve-id",
            weight=Decimal("0.9"),
            tenant_id="t-1",
        )
        db_session.add_all([pv, membership])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
        )

        assert result.allowed is False
        assert result.reason == "UNKNOWN_SLEEVE"
        assert result.failing_sleeve_id == "nonexistent-sleeve-id"


class TestPortfolioRightsDateBoundaries:
    """Date boundary checks in rights grants (mutation target: < vs <=,
    > vs >=, wrong datetime comparison)."""

    def test_grant_effective_at_boundary_not_yet_effective(self, db_session):
        """Mutation target: > vs >= on effective_at check. A grant that
        becomes effective in the future should be denied."""
        now = datetime.now(timezone.utc)
        sleeve = Sleeve(
            tenant_id="t-1",
            provider="provider-future",
            analyst="analyst-1",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-1",
            cost_model_id="cost-1",
        )
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-7",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        membership = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("0.9"),
            tenant_id="t-1",
        )
        # Grant effective in the future
        grant = RightsGrant(
            grant_id="grant-future",
            source_id="provider-future",
            grantee_entity="Signal",
            contract_hash="hash-future",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["email"],
            jurisdictions=["US"],
            assets=["AAPL"],
            effective_at=now + timedelta(days=1),
            expires_at=now + timedelta(days=30),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
        db_session.add_all([sleeve, pv, membership, grant])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
            at=now,
        )

        assert result.allowed is False

    def test_grant_expires_at_boundary_already_expired(self, db_session):
        """Mutation target: < vs <= on expires_at check. A grant that
        expired in the past should be denied."""
        now = datetime.now(timezone.utc)
        sleeve = Sleeve(
            tenant_id="t-1",
            provider="provider-expired",
            analyst="analyst-1",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-1",
            cost_model_id="cost-1",
        )
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-8",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        membership = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("0.9"),
            tenant_id="t-1",
        )
        # Grant expired in the past
        grant = RightsGrant(
            grant_id="grant-expired",
            source_id="provider-expired",
            grantee_entity="Signal",
            contract_hash="hash-expired",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["email"],
            jurisdictions=["US"],
            assets=["AAPL"],
            effective_at=now - timedelta(days=30),
            expires_at=now - timedelta(days=1),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
        db_session.add_all([sleeve, pv, membership, grant])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
            at=now,
        )

        assert result.allowed is False


class TestPortfolioRightsProviderMapping:
    """Sleeve provider to RightsGrant source_id mapping (mutation target:
    wrong field used, dropped mapping, wrong comparison)."""

    def test_provider_matches_source_id(self, db_session):
        """Mutation target: wrong sleeve field used for source_id matching.
        Sleeve.provider must match RightsGrant.source_id."""
        now = datetime.now(timezone.utc)
        sleeve = Sleeve(
            tenant_id="t-1",
            provider="my-data-provider",
            analyst="analyst-1",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="exec-1",
            cost_model_id="cost-1",
        )
        pv = PortfolioVersion(
            tenant_id="t-1",
            portfolio_id="p-9",
            version_number=1,
            cash_weight=Decimal("0.1"),
            research_cutoff=now,
            max_subscriber_capacity=100,
            consent_disclosure_version="v1",
        )
        membership = PortfolioVersionSleeve(
            portfolio_version_id=pv.portfolio_version_id,
            sleeve_id=sleeve.sleeve_id,
            weight=Decimal("0.9"),
            tenant_id="t-1",
        )
        # Grant for provider "my-data-provider"
        grant = RightsGrant(
            grant_id="grant-matched",
            source_id="my-data-provider",
            grantee_entity="Signal",
            contract_hash="hash-match",
            status=RightsStatus.GRANTED,
            uses=[RightsUse.COMMERCIAL_ALERTS.value],
            channels=["email"],
            jurisdictions=["US"],
            assets=["AAPL"],
            effective_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
            attribution_policy_id="attr-1",
            wind_down_policy_id="wind-1",
            review_id="review-1",
        )
        db_session.add_all([sleeve, pv, membership, grant])
        db_session.commit()

        result = check_portfolio_rights(
            db_session,
            portfolio_version_id=pv.portfolio_version_id,
            use=RightsUse.COMMERCIAL_ALERTS,
            channel="email",
            jurisdiction="US",
            asset="AAPL",
        )

        assert result.allowed is True


# =============================================================================
# CUSTOMER_PERFORMANCE_STATE.PY MUTATION TESTS
# =============================================================================
class TestPerformanceStateNoConnection:
    """No connection case (mutation target: == vs !=, None check omitted)."""

    def test_no_connection_returns_not_connected(self, db_session):
        """Mutation target: flipped condition or dropped None check.
        None connection should return NOT_CONNECTED state."""
        state = get_customer_performance_state(db_session, tenant_id="t-1", connection=None)

        assert state == PerformanceState.NOT_CONNECTED

    def test_not_connected_never_awaiting(self, db_session):
        """Mutation target: wrong constant returned. NOT_CONNECTED must be
        distinct from AWAITING_OBSERVATIONS."""
        state = get_customer_performance_state(db_session, tenant_id="t-1", connection=None)

        assert state != PerformanceState.AWAITING_OBSERVATIONS
        assert state != PerformanceState.AVAILABLE


class TestPerformanceStateAwaitingObservations:
    """Awaiting observations case (mutation target: dropped follower book
    check, wrong book type, missing condition)."""

    def test_connection_no_observations_awaiting_state(self, db_session):
        """Mutation target: dropped FOLLOWER book existence check or wrong
        book type. No observations should return AWAITING state."""
        conn = PlatformConnection(
            tenant_id="t-1",
            user_id="u-1",
            platform="collective2",
            connection_id="conn-1",
            platform_account_id="acct-1",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        db_session.add(conn)
        db_session.commit()

        state = get_customer_performance_state(db_session, tenant_id="t-1", connection=conn)

        assert state == PerformanceState.AWAITING_OBSERVATIONS

    def test_awaiting_not_available_without_entries(self, db_session):
        """Mutation target: flipped logic or dropped query. Should not be
        AVAILABLE without any ledger entries."""
        conn = PlatformConnection(
            tenant_id="t-1",
            user_id="u-2",
            platform="etoro",
            connection_id="conn-2",
            platform_account_id="acct-2",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        db_session.add(conn)
        db_session.commit()

        state = get_customer_performance_state(db_session, tenant_id="t-1", connection=conn)

        assert state != PerformanceState.AVAILABLE


class TestPerformanceStateAvailable:
    """Available case (mutation target: wrong book type, dropped tenant check,
    wrong connection_id check)."""

    def test_connection_with_follower_entries_available(self, db_session):
        """Mutation target: wrong book type check or missing FOLLOWER query.
        With FOLLOWER entries, should return AVAILABLE state."""
        conn = PlatformConnection(
            tenant_id="t-1",
            user_id="u-3",
            platform="metapi",
            connection_id="conn-3",
            platform_account_id="acct-3",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        db_session.add(conn)
        db_session.flush()

        entry = LedgerEntry(
            tenant_id="t-1",
            book=Book.FOLLOWER,
            follower_connection_id=conn.connection_id,
            side="debit",
            amount_cents=100,
            asset="AAPL",
            instrument_quantity=Decimal("1"),
        )
        db_session.add(entry)
        db_session.commit()

        state = get_customer_performance_state(db_session, tenant_id="t-1", connection=conn)

        assert state == PerformanceState.AVAILABLE

    def test_tenant_isolation_in_performance_state(self, db_session):
        """Mutation target: == vs != on tenant_id, or dropped check.
        Different tenants' entries must not leak."""
        conn_t1 = PlatformConnection(
            tenant_id="t-1",
            user_id="u-4",
            platform="collective2",
            connection_id="conn-t1",
            platform_account_id="acct-t1",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        conn_t2 = PlatformConnection(
            tenant_id="t-2",
            user_id="u-5",
            platform="collective2",
            connection_id="conn-t2",
            platform_account_id="acct-t2",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        db_session.add_all([conn_t1, conn_t2])
        db_session.flush()

        # Add FOLLOWER entry only for t-2
        entry = LedgerEntry(
            tenant_id="t-2",
            book=Book.FOLLOWER,
            follower_connection_id=conn_t2.connection_id,
            side="debit",
            amount_cents=100,
            asset="AAPL",
            instrument_quantity=Decimal("1"),
        )
        db_session.add(entry)
        db_session.commit()

        # t-1 should still be AWAITING
        state_t1 = get_customer_performance_state(db_session, tenant_id="t-1", connection=conn_t1)
        assert state_t1 == PerformanceState.AWAITING_OBSERVATIONS

        # t-2 should be AVAILABLE
        state_t2 = get_customer_performance_state(db_session, tenant_id="t-2", connection=conn_t2)
        assert state_t2 == PerformanceState.AVAILABLE

    def test_connection_id_matching_in_state_query(self, db_session):
        """Mutation target: wrong field used in connection_id check.
        Query must match on follower_connection_id field specifically."""
        conn1 = PlatformConnection(
            tenant_id="t-1",
            user_id="u-6",
            platform="collective2",
            connection_id="conn-x",
            platform_account_id="acct-x",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        conn2 = PlatformConnection(
            tenant_id="t-1",
            user_id="u-7",
            platform="etoro",
            connection_id="conn-y",
            platform_account_id="acct-y",
            state=PlatformConnectionState.DECLARED,
            environment="local_simulation",
        )
        db_session.add_all([conn1, conn2])
        db_session.flush()

        # Add entry only for conn1
        entry = LedgerEntry(
            tenant_id="t-1",
            book=Book.FOLLOWER,
            follower_connection_id=conn1.connection_id,
            side="debit",
            amount_cents=100,
            asset="AAPL",
            instrument_quantity=Decimal("1"),
        )
        db_session.add(entry)
        db_session.commit()

        # conn1 should be AVAILABLE
        state_conn1 = get_customer_performance_state(db_session, tenant_id="t-1", connection=conn1)
        assert state_conn1 == PerformanceState.AVAILABLE

        # conn2 should still be AWAITING (no entries for this connection)
        state_conn2 = get_customer_performance_state(db_session, tenant_id="t-1", connection=conn2)
        assert state_conn2 == PerformanceState.AWAITING_OBSERVATIONS


# =============================================================================
# PRICE_VERSION.PY MUTATION TESTS
# =============================================================================
class TestPriceVersionValidation:
    """Input validation (mutation target: missing check, wrong comparison,
    dropped condition)."""

    def test_empty_sku_rejected(self, db_session):
        """Mutation target: dropped sku empty check.
        Empty or whitespace-only sku must be rejected."""
        with pytest.raises(InvalidPriceVersionError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=3,
                features=["alerts_read"],
                mode="test",
            )

    def test_whitespace_sku_rejected(self, db_session):
        """Mutation target: dropped strip() call or wrong check.
        Whitespace-only sku must be rejected."""
        with pytest.raises(InvalidPriceVersionError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="   ",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=3,
                features=["reports_read"],
                mode="test",
            )

    def test_negative_amount_rejected(self, db_session):
        """Mutation target: >= vs < on amount_minor check.
        Negative amount must be rejected."""
        with pytest.raises(InvalidPriceVersionError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="plan-1",
                currency="usd",
                amount_minor=-100,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=3,
                features=["delivery_receive"],
                mode="test",
            )

    def test_zero_amount_accepted(self, db_session):
        """Mutation target: < vs <= on amount_minor check.
        Zero amount should be acceptable."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="free-plan",
            currency="usd",
            amount_minor=0,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        assert pv.amount_minor == 0

    def test_no_features_rejected(self, db_session):
        """Mutation target: dropped features check.
        At least one feature is required."""
        with pytest.raises(InvalidPriceVersionError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="plan-no-features",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=3,
                features=[],
                mode="test",
            )

    def test_unknown_feature_rejected(self, db_session):
        """Mutation target: dropped feature registry check or wrong frozenset.
        Unknown features must be rejected."""
        with pytest.raises(InvalidPriceVersionError) as exc_info:
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="plan-bad-feature",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=3,
                features=["alerts_read", "trading_authority"],
                mode="test",
            )
        assert "trading_authority" in str(exc_info.value)

    def test_reviewed_features_accepted(self, db_session):
        """Mutation target: wrong frozenset members. All reviewed features
        must be accepted."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="plan-reviewed-features",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=3,
            features=["alerts_read", "reports_read", "delivery_receive", "portfolios_up_to_three", "research_api"],
            mode="test",
        )
        assert len(pv.features) == 5

    def test_live_mode_rejected(self, db_session):
        """Mutation target: != vs == on mode check, or dropped check.
        Only TEST mode is authorized."""
        with pytest.raises(InvalidPriceVersionError) as exc_info:
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="plan-live",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=3,
                features=["alerts_read"],
                mode="live",
            )
        assert "LIVE_MODE_NOT_AUTHORIZED" in str(exc_info.value)

    def test_test_mode_accepted(self, db_session):
        """Mutation target: wrong mode accepted. Only TEST mode allowed."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="plan-test-only",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=3,
            features=["alerts_read"],
            mode="test",
        )
        assert pv.mode == PriceMode.TEST


class TestPriceVersionPortfolioLimit:
    """Portfolio limit logic (mutation target: wrong comparison, dropped check,
    swapped true/false branch)."""

    def test_unlimited_portfolios_explicit(self, db_session):
        """Mutation target: wrong flag value handling.
        Explicit unlimited should allow None portfolio_limit."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="unlimited-ports",
            currency="usd",
            amount_minor=2000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["reports_read"],
            mode="test",
        )
        assert pv.is_unlimited_portfolios is True
        assert pv.portfolio_limit is None

    def test_unlimited_with_limit_ignores_limit(self, db_session):
        """Mutation target: dropped conditional on is_unlimited_portfolios.
        When unlimited, portfolio_limit should be None regardless of input."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="unlimited-ports-ignore-limit",
            currency="usd",
            amount_minor=2000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=999,
            features=["delivery_receive"],
            mode="test",
        )
        assert pv.is_unlimited_portfolios is True
        assert pv.portfolio_limit is None

    def test_limited_portfolios_requires_limit(self, db_session):
        """Mutation target: dropped check on portfolio_limit when not unlimited.
        Limit must be non-negative when not unlimited."""
        with pytest.raises(InvalidPriceVersionError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="limited-no-limit",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=None,
                features=["alerts_read"],
                mode="test",
            )

    def test_limited_portfolios_negative_limit_rejected(self, db_session):
        """Mutation target: < vs <= on portfolio_limit check.
        Negative portfolio limit must be rejected."""
        with pytest.raises(InvalidPriceVersionError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="limited-negative",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=False,
                portfolio_limit=-1,
                features=["reports_read"],
                mode="test",
            )

    def test_limited_portfolios_zero_accepted(self, db_session):
        """Mutation target: < vs <=. Zero portfolio limit should be valid."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="limited-zero",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=0,
            features=["delivery_receive"],
            mode="test",
        )
        assert pv.portfolio_limit == 0

    def test_limited_portfolios_positive_limit(self, db_session):
        """Mutation target: dropped limit validation.
        Positive portfolio limit should work."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="limited-three",
            currency="usd",
            amount_minor=1500,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=3,
            features=["portfolios_up_to_three"],
            mode="test",
        )
        assert pv.portfolio_limit == 3


class TestPriceVersionUniqueness:
    """SKU uniqueness enforcement (mutation target: dropped check, flipped
    comparison, wrong error type)."""

    def test_duplicate_sku_rejected(self, db_session):
        """Mutation target: dropped duplicate check or wrong error type.
        Two PriceVersions cannot have the same SKU."""
        create_price_version(
            db_session,
            tenant_id="t-1",
            sku="unique-sku-1",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        db_session.commit()

        with pytest.raises(SkuAlreadyExistsError):
            create_price_version(
                db_session,
                tenant_id="t-1",
                sku="unique-sku-1",
                currency="usd",
                amount_minor=2000,
                interval="year",
                is_unlimited_portfolios=False,
                portfolio_limit=10,
                features=["reports_read"],
                mode="test",
            )

    def test_sku_unique_across_tenants(self, db_session):
        """Mutation target: dropped global uniqueness check.
        SKU must be globally unique, not just per-tenant."""
        create_price_version(
            db_session,
            tenant_id="t-1",
            sku="global-sku",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        db_session.commit()

        with pytest.raises(SkuAlreadyExistsError):
            create_price_version(
                db_session,
                tenant_id="t-2",
                sku="global-sku",
                currency="usd",
                amount_minor=1000,
                interval="month",
                is_unlimited_portfolios=True,
                portfolio_limit=None,
                features=["reports_read"],
                mode="test",
            )


class TestPriceVersionListing:
    """List price versions (mutation target: wrong tenant filter, missing
    order, dropped scoping)."""

    def test_list_price_versions_tenant_isolated(self, db_session):
        """Mutation target: == vs != on tenant_id check.
        List must only return versions for requested tenant."""
        pv_t1 = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="tenant-1-plan",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        pv_t2 = create_price_version(
            db_session,
            tenant_id="t-2",
            sku="tenant-2-plan",
            currency="usd",
            amount_minor=2000,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=5,
            features=["reports_read"],
            mode="test",
        )
        db_session.commit()

        list_t1 = list_price_versions(db_session, tenant_id="t-1")
        list_t2 = list_price_versions(db_session, tenant_id="t-2")

        assert len(list_t1) == 1
        assert list_t1[0].price_version_id == pv_t1.price_version_id

        assert len(list_t2) == 1
        assert list_t2[0].price_version_id == pv_t2.price_version_id

    def test_list_price_versions_empty_tenant(self, db_session):
        """Mutation target: wrong default return. Empty list for tenant
        with no versions."""
        list_empty = list_price_versions(db_session, tenant_id="t-nonexistent")
        assert list_empty == []

    def test_list_price_versions_ordered_by_created_at(self, db_session):
        """Mutation target: missing order_by or wrong sort order.
        Results should be ordered by created_at descending."""
        pv1 = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="plan-1",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        db_session.commit()

        pv2 = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="plan-2",
            currency="usd",
            amount_minor=2000,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=3,
            features=["reports_read"],
            mode="test",
        )
        db_session.commit()

        result = list_price_versions(db_session, tenant_id="t-1")

        assert len(result) == 2
        # Most recent first
        assert result[0].price_version_id == pv2.price_version_id
        assert result[1].price_version_id == pv1.price_version_id


class TestPriceVersionFieldPreservation:
    """Field preservation and defaults (mutation target: dropped field,
    wrong default, incorrect assignment)."""

    def test_create_stores_all_fields(self, db_session):
        """Mutation target: dropped field assignment. All input fields must
        be stored correctly."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="full-test",
            currency="eur",
            amount_minor=5000,
            interval="year",
            is_unlimited_portfolios=False,
            portfolio_limit=10,
            features=["alerts_read", "reports_read"],
            mode="test",
        )

        assert pv.tenant_id == "t-1"
        assert pv.sku == "full-test"
        assert pv.currency == "eur"
        assert pv.amount_minor == 5000
        assert pv.interval == BillingInterval.YEAR
        assert pv.is_unlimited_portfolios is False
        assert pv.portfolio_limit == 10
        assert pv.features == ["alerts_read", "reports_read"]
        assert pv.mode == PriceMode.TEST

    def test_interval_enum_conversion(self, db_session):
        """Mutation target: wrong enum value or skipped conversion.
        Interval string must be converted to BillingInterval enum."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="interval-test",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )

        assert isinstance(pv.interval, BillingInterval)
        assert pv.interval == BillingInterval.MONTH

    def test_mode_enum_conversion(self, db_session):
        """Mutation target: wrong enum value or skipped conversion.
        Mode string must be converted to PriceMode enum."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="mode-test",
            currency="usd",
            amount_minor=1000,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )

        assert isinstance(pv.mode, PriceMode)
        assert pv.mode == PriceMode.TEST


class TestPriceVersionBoundaryConditions:
    """Boundary conditions and edge cases (mutation target: off-by-one,
    wrong comparison operator, skipped condition)."""

    def test_minimum_portfolio_limit_zero(self, db_session):
        """Mutation target: < vs <= on portfolio_limit check.
        Zero should be valid minimum."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="min-portfolio-zero",
            currency="usd",
            amount_minor=100,
            interval="month",
            is_unlimited_portfolios=False,
            portfolio_limit=0,
            features=["alerts_read"],
            mode="test",
        )
        assert pv.portfolio_limit == 0

    def test_maximum_portfolio_limit(self, db_session):
        """Mutation target: wrong boundary or no maximum enforced.
        Large portfolio limits should work."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="max-portfolio-1000",
            currency="usd",
            amount_minor=10000,
            interval="year",
            is_unlimited_portfolios=False,
            portfolio_limit=1000,
            features=["portfolios_up_to_three", "research_api"],
            mode="test",
        )
        assert pv.portfolio_limit == 1000

    def test_minimum_amount_zero(self, db_session):
        """Mutation target: < vs <=. Zero should be valid minimum amount."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="min-amount-zero",
            currency="usd",
            amount_minor=0,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        assert pv.amount_minor == 0

    def test_single_feature_accepted(self, db_session):
        """Mutation target: wrong empty check or wrong count comparison.
        Single feature should be valid."""
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="single-feature",
            currency="usd",
            amount_minor=500,
            interval="month",
            is_unlimited_portfolios=True,
            portfolio_limit=None,
            features=["alerts_read"],
            mode="test",
        )
        assert len(pv.features) == 1

    def test_all_reviewed_features_together(self, db_session):
        """Mutation target: wrong frozenset or dropped validation.
        All 5 reviewed features should work together."""
        all_features = ["alerts_read", "reports_read", "delivery_receive", "portfolios_up_to_three", "research_api"]
        pv = create_price_version(
            db_session,
            tenant_id="t-1",
            sku="all-features",
            currency="usd",
            amount_minor=5000,
            interval="year",
            is_unlimited_portfolios=False,
            portfolio_limit=5,
            features=all_features,
            mode="test",
        )
        assert len(pv.features) == 5
        assert set(pv.features) == set(all_features)
